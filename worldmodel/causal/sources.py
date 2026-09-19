"""Outcome panels extracted from pinned normalized datasets, streamed with bounded memory.

Each extractor reads one published version, keeps only the rows its spec names, and writes a
compact gzip TSV cache whose name carries the input version and a digest of the spec, so a cache
can never be mistaken for another version's. The returned ``provenance`` names the input, the spec,
the rows kept and the SHA-256 of the cache file.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path

from ..util import canonical


def _payload(store, ref):
    store.verify(ref, recursive=False)
    directory = store.version_dir(ref)
    for name in ('records.jsonl.gz', 'records.jsonl'):
        if (directory / name).exists():
            return directory / name
    raise FileNotFoundError(f'no records for {ref}')


def _open(path):
    return gzip.open(path, 'rb') if str(path).endswith('.gz') else open(path, 'rb')


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _cache_path(cache_dir, name, ref, spec):
    key = hashlib.sha256(canonical({'ref': ref, 'spec': spec})).hexdigest()[:16]
    return Path(cache_dir) / f'{name}-{ref["version"][:16]}-{key}.tsv.gz'


def _finish(tmp, path, ref, spec, rows):
    os.replace(tmp, path)
    return {'input': ref, 'spec': spec, 'rows': rows, 'cache': str(path), 'sha256': _file_sha(path)}


def _read_cache(path, ref, spec):
    rows = 0
    with gzip.open(path, 'rt') as f:
        for _ in f:
            rows += 1
    return {'input': ref, 'spec': spec, 'rows': rows, 'cache': str(path), 'sha256': _file_sha(path)}


# -- BLS: QCEW county annual totals and LAUS county monthly ------------------------------------------

BLS_SPEC = {
    'qcew': {'filter': 'dimensions.survey == QCEW and dimensions.agglvl_code == 70 (county, total covered, all '
                       'ownerships) and period_type == annual_average',
             'metrics': ['employment', 'establishment_count']},
    'laus': {'filter': 'dimensions.survey == LAUS and subject geo:US:county:* and period_type == month',
             'metrics': ['employment', 'labor_force', 'unemployment_rate']},
}


def extract_bls_county(store, ref, cache_dir):
    """County QCEW annual employment/establishments and LAUS monthly labour-force rows.

    Rows: ``survey, metric, county_fips, period (YYYY or YYYY-MM), value, record_id``. Null values
    (suppressed) are skipped.
    """
    path = _cache_path(cache_dir, 'bls_county', ref, BLS_SPEC)
    if path.exists():
        return _read_cache(path, ref, BLS_SPEC)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.partial')
    rows = 0
    qcew_key, laus_key, county = b'"agglvl_code":"70"', b'"survey":"LAUS"', b'geo:US:county:'
    with _open(_payload(store, ref)) as src, gzip.open(tmp, 'wt') as out:
        for line in src:
            if qcew_key in line:
                survey = 'QCEW'
            elif laus_key in line and county in line:
                survey = 'LAUS'
            else:
                continue
            r = json.loads(line)
            metric = r.get('metric')
            d = r.get('dimensions', {})
            if metric not in BLS_SPEC[survey.lower()]['metrics'] or r.get('value') is None:
                continue
            subject = r.get('subject', '')
            if not subject.startswith('geo:US:county:'):
                continue
            if survey == 'QCEW':
                if d.get('survey') != 'QCEW' or d.get('period_type') != 'annual_average':
                    continue
                period = r['valid_from'][:4]
            else:
                if d.get('period_type') != 'month':
                    continue
                period = r['valid_from'][:7]
            out.write(f'{survey}\t{metric}\t{subject[14:]}\t{period}\t{r["value"]!r}\t{r["id"]}\n')
            rows += 1
    return _finish(tmp, path, ref, BLS_SPEC, rows)


def load_bls_county(cache, survey, metric):
    """``{county_fips: {period: value}}`` for one survey and metric from an extraction cache."""
    out = {}
    with gzip.open(cache, 'rt') as f:
        for line in f:
            s, m, fips, period, value, _ = line.rstrip('\n').split('\t')
            if s == survey and m == metric:
                out.setdefault(fips, {})[period] = float(value)
    return out


# -- Census CBP county establishments ---------------------------------------------------------------

CBP_SPEC = {'filter': 'metric == establishment_count, subject geo:US:county:*, dimensions.program == cbp, '
                      'industry == all, legal_form == all', 'metric': 'establishment_count'}


def extract_cbp_county(store, ref, cache_dir):
    path = _cache_path(cache_dir, 'cbp_county', ref, CBP_SPEC)
    if path.exists():
        return _read_cache(path, ref, CBP_SPEC)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.partial')
    rows = 0
    with _open(_payload(store, ref)) as src, gzip.open(tmp, 'wt') as out:
        for line in src:
            if b'"establishment_count"' not in line or b'geo:US:county:' not in line:
                continue
            r = json.loads(line)
            d = r.get('dimensions', {})
            if r.get('metric') != 'establishment_count' or d.get('program') != 'cbp' or d.get('industry') != 'all' \
                    or d.get('legal_form', 'all') != 'all' or r.get('value') is None:
                continue
            subject = r.get('subject', '')
            if not subject.startswith('geo:US:county:'):
                continue
            out.write(f'CBP\testablishment_count\t{subject[14:]}\t{r["valid_from"][:4]}\t{r["value"]!r}\t{r["id"]}\n')
            rows += 1
    return _finish(tmp, path, ref, CBP_SPEC, rows)


# -- CEPII BACI imports by importer x HS6 x year -------------------------------------------------------

def extract_baci_imports(store, ref, cache_dir, importers):
    """Import value (thousand USD) and quantity (t) summed over exporters, per importer x HS6 x year.

    Rows: ``importer, hs6, year, value_kusd, quantity_t, flows, flows_without_quantity``.
    """
    spec = {'filter': 'metric == bilateral_trade_value; importer in the listed set; sum over exporters',
            'importers': sorted(importers)}
    path = _cache_path(cache_dir, 'baci_imports', ref, spec)
    if path.exists():
        return _read_cache(path, ref, spec)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    wanted = {i.encode() for i in importers}
    marker = b'"importer":"'
    totals = {}
    with _open(_payload(store, ref)) as src:
        for line in src:
            i = line.find(marker)
            if i < 0:
                continue
            j = line.find(b'"', i + len(marker))
            if line[i + len(marker):j] not in wanted:
                continue
            r = json.loads(line)
            if r.get('metric') != 'bilateral_trade_value' or r.get('value') is None:
                continue
            key = (r['dimensions']['importer'], r['dimensions']['product'].split(':', 1)[1], int(r['valid_from'][:4]))
            q = r.get('attributes', {}).get('quantity_t')
            acc = totals.get(key)
            if acc is None:
                acc = totals[key] = [0.0, 0.0, 0, 0]
            acc[0] += r['value']
            if q is None:
                acc[3] += 1
            else:
                acc[1] += q
            acc[2] += 1
    tmp = path.with_suffix('.partial')
    with gzip.open(tmp, 'wt') as out:
        for (imp, hs6, year), (v, q, n, nq) in sorted(totals.items()):
            out.write(f'{imp}\t{hs6}\t{year}\t{v!r}\t{q!r}\t{n}\t{nq}\n')
    return _finish(tmp, path, ref, spec, len(totals))


def load_baci_imports(cache):
    out = {}
    with gzip.open(cache, 'rt') as f:
        for line in f:
            imp, hs6, year, v, q, n, nq = line.rstrip('\n').split('\t')
            out[(imp, hs6, int(year))] = (float(v), float(q), int(n), int(nq))
    return out


# -- Event library ------------------------------------------------------------------------------------

def library_events(store, ref, types):
    """Stream event-library records of the given ``library_type`` values (a small dict per event)."""
    types = set(types)
    for record in store.records(ref):
        if record.get('event_type') in types:
            yield record
