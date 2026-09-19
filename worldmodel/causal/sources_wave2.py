"""Wave-2 outcome and dose extractors: NOAA county damage, BEA county population, BACI HS1992 panels.

Same conventions as ``sources``: each extractor streams one pinned version, keeps only the rows its spec
names and writes a gzip TSV cache named by the input version and a digest of the spec. The returned
provenance names the input, the spec, the rows kept and the cache's SHA-256.
"""
import gzip
import json
from pathlib import Path

from .sources import _cache_path, _finish, _open, _payload, _read_cache

# -- NOAA storm events: county-coded damage -----------------------------------------------------------

NOAA_DAMAGE_SPEC = {'filter': 'metric in (storm_damage_property, storm_damage_crops), dimensions.location '
                              'geo:US:county:* (NWS-zone-coded damage cannot be attributed to a county and is skipped)',
                    'fields': 'county_fips, begin date (UTC, YYYY-MM-DD), metric, nominal USD, event type, record id'}


def extract_noaa_county_damage(store, ref, cache_dir):
    """Rows: ``county_fips, date, metric, usd, event_type, record_id``."""
    path = _cache_path(cache_dir, 'noaa_county_damage', ref, NOAA_DAMAGE_SPEC)
    if path.exists():
        return _read_cache(path, ref, NOAA_DAMAGE_SPEC)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.partial')
    rows = 0
    with _open(_payload(store, ref)) as src, gzip.open(tmp, 'wt') as out:
        for line in src:
            if b'storm_damage_' not in line:
                continue
            r = json.loads(line)
            if r.get('metric') not in ('storm_damage_property', 'storm_damage_crops') or r.get('value') is None:
                continue
            loc = (r.get('dimensions') or {}).get('location', '')
            if not loc.startswith('geo:US:county:'):
                continue
            out.write(f'{loc[14:]}\t{r["valid_from"][:10]}\t{r["metric"]}\t{float(r["value"])!r}\t'
                      f'{r["dimensions"].get("event_type", "")}\t{r["id"]}\n')
            rows += 1
    return _finish(tmp, path, ref, NOAA_DAMAGE_SPEC, rows)


def load_noaa_county_damage(cache):
    """``{county_fips: sorted [(date, usd)]}`` (property and crop rows kept separately; callers sum them)."""
    out = {}
    with gzip.open(cache, 'rt') as f:
        for line in f:
            fips, date, _, usd, _, _ = line.rstrip('\n').split('\t')
            out.setdefault(fips, []).append((date, float(usd)))
    for rows in out.values():
        rows.sort()
    return out


# -- BEA county population (CAINC1) --------------------------------------------------------------------

BEA_POP_SPEC = {'filter': 'metric == population, subject geo:US:county:* (BEA CAINC1), annual'}


def extract_bea_county_population(store, ref, cache_dir):
    """Rows: ``county_fips, year, persons, record_id``."""
    path = _cache_path(cache_dir, 'bea_county_population', ref, BEA_POP_SPEC)
    if path.exists():
        return _read_cache(path, ref, BEA_POP_SPEC)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.partial')
    rows = 0
    with _open(_payload(store, ref)) as src, gzip.open(tmp, 'wt') as out:
        for line in src:
            if b'"population"' not in line or b'geo:US:county:' not in line:
                continue
            r = json.loads(line)
            subject = r.get('subject', '')
            if r.get('metric') != 'population' or not subject.startswith('geo:US:county:') or not r.get('value'):
                continue
            out.write(f'{subject[14:]}\t{r["valid_from"][:4]}\t{float(r["value"])!r}\t{r["id"]}\n')
            rows += 1
    return _finish(tmp, path, ref, BEA_POP_SPEC, rows)


def load_bea_county_population(cache):
    out = {}
    with gzip.open(cache, 'rt') as f:
        for line in f:
            fips, year, value, _ = line.rstrip('\n').split('\t')
            out.setdefault(fips, {})[int(year)] = float(value)
    return out


# -- BACI HS1992 ---------------------------------------------------------------------------------------

def _field(line, marker, end=b'"'):
    i = line.find(marker)
    if i < 0:
        return None
    i += len(marker)
    return line[i:line.find(end, i)]


def baci_fields(line):
    """``(exporter, importer, product, year, value, quantity)`` from one BACI normalized observation (bytes).

    ``quantity`` is None when the record has no ``quantity_t``. Returns None for non-observation lines.
    """
    if b'"kind":"observation"' not in line or b'"bilateral_trade_value"' not in line:
        return None
    exporter = _field(line, b'"subject":"')
    importer = _field(line, b'"importer":"')
    product = _field(line, b'"product":"')
    year = _field(line, b'"valid_from":"', b'-')
    i = line.find(b'"value":')
    if i < 0:
        return None
    i += 8
    j = i
    while j < len(line) and line[j] not in b',}':
        j += 1
    raw = line[i:j]
    if raw == b'null' or not raw:
        return None
    quantity = None
    q = line.find(b'"quantity_t":')
    if q >= 0:
        q += 13
        k = q
        while k < len(line) and line[k] not in b',}':
            k += 1
        if line[q:k] != b'null':
            quantity = float(line[q:k])
    return exporter.decode(), importer.decode(), product.decode(), int(year), float(raw), quantity


def extract_baci92_imports(store, ref, cache_dir, importers, *, years):
    """BACI HS1992 imports summed over exporters, per importer x HS6 x year.

    Row layout of ``sources.extract_baci_imports`` (so ``sources.load_baci_imports`` reads it):
    ``importer, hs6, year, value_kusd, quantity_t, flows, flows_without_quantity``.
    """
    spec = {'filter': 'metric == bilateral_trade_value (HS1992); importer in the listed set; year in range; '
                      'summed over exporters', 'importers': sorted(importers), 'years': list(years)}
    path = _cache_path(cache_dir, 'baci92_imports', ref, spec)
    if path.exists():
        return _read_cache(path, ref, spec)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    wanted = {i.encode() for i in importers}
    marker = b'"importer":"'
    lo, hi = years
    totals = {}
    with _open(_payload(store, ref)) as src:
        for line in src:
            i = line.find(marker)
            if i < 0:
                continue
            j = line.find(b'"', i + 12)
            if line[i + 12:j] not in wanted:
                continue
            fields = baci_fields(line)
            if fields is None:
                continue
            _, importer, product, year, value, quantity = fields
            if not lo <= year <= hi:
                continue
            key = (importer, product.split(':', 1)[1], year)
            acc = totals.get(key)
            if acc is None:
                acc = totals[key] = [0.0, 0.0, 0, 0]
            acc[0] += value
            if quantity is None:
                acc[3] += 1
            else:
                acc[1] += quantity
            acc[2] += 1
    tmp = path.with_suffix('.partial')
    with gzip.open(tmp, 'wt') as out:
        for (imp, hs6, year), (v, q, n, nq) in sorted(totals.items()):
            out.write(f'{imp}\t{hs6}\t{year}\t{v!r}\t{q!r}\t{n}\t{nq}\n')
    return _finish(tmp, path, ref, spec, len(totals))


def extract_baci92_partner_share(store, ref, cache_dir, *, partner='iso3:USA'):
    """Each country's trade with one partner and with everyone else, by HS1992 chapter and year.

    Rows: ``direction, country, hs2, year, with_partner_kusd, with_others_kusd``. ``exports``: the country's
    exports to the partner and to all other importers; ``imports``: its imports from the partner and from
    all other exporters.
    """
    spec = {'filter': 'metric == bilateral_trade_value (HS1992); value summed by (country, HS2, year) separately '
                      'for flows with the partner and with all other countries', 'partner': partner}
    path = _cache_path(cache_dir, 'baci92_partner_share', ref, spec)
    if path.exists():
        return _read_cache(path, ref, spec)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    totals = {}
    with _open(_payload(store, ref)) as src:
        for line in src:
            fields = baci_fields(line)
            if fields is None:
                continue
            exporter, importer, product, year, value, _ = fields
            hs2 = product.split(':', 1)[1][:2]
            key = ('exports', exporter, hs2, year)
            acc = totals.get(key)
            if acc is None:
                acc = totals[key] = [0.0, 0.0]
            acc[0 if importer == partner else 1] += value
            key = ('imports', importer, hs2, year)
            acc = totals.get(key)
            if acc is None:
                acc = totals[key] = [0.0, 0.0]
            acc[0 if exporter == partner else 1] += value
    tmp = path.with_suffix('.partial')
    with gzip.open(tmp, 'wt') as out:
        for (direction, country, hs2, year), (a, b) in sorted(totals.items()):
            out.write(f'{direction}\t{country}\t{hs2}\t{year}\t{a!r}\t{b!r}\n')
    return _finish(tmp, path, ref, spec, len(totals))


def load_baci92_partner_share(cache, direction):
    """``{(country, hs2): {year: (with_partner, with_others)}}`` for one direction."""
    out = {}
    with gzip.open(cache, 'rt') as f:
        for line in f:
            d, country, hs2, year, a, b = line.rstrip('\n').split('\t')
            if d == direction:
                out.setdefault((country, hs2), {})[int(year)] = (float(a), float(b))
    return out
