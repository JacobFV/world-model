"""13F holdings as compact, dated arrays for the actors assay.

Every row is one reported holding from an *original* 13F-HR filing in ``sec_13f_history``:
manager (filer CIK), security (CUSIP), quarter end, shares, value, and the filing date on which
the row became public (``observed_at`` of the normalized record, which the adapter takes from the
filing). Amendments (13F-HR/A) are excluded and counted: an amendment can restate or add holdings
long after the quarter, and the actors assay treats a quarter as known through its original
filing only. When a manager filed more than one original 13F-HR for a quarter, the earliest
filing wins and the others are counted.

The arrays are a deterministic cache of one pinned ``sec_13f_history`` version, written beside
the assay's reports (``embedding_reports/scratch/cache``); the assay report pins that version, so
the cache adds no provenance of its own and can always be rebuilt.

Requires numpy.
"""
from array import array
import gzip
import json
import re
import time
from pathlib import Path

import numpy as np

from ..estimation.loaders import catalog_ref, _records_path

DATASET = 'sec_13f_history'
FIELDS = {
    'subject': re.compile(r'"subject":"sec:cik:(\d+)"'),
    'object': re.compile(r'"object":"cusip:([0-9A-Z]{9})"'),
    'valid_from': re.compile(r'"valid_from":"(\d{4})-(\d{2})-(\d{2})'),
    'observed_at': re.compile(r'"observed_at":"(\d{4})-(\d{2})-(\d{2})'),
    'shares': re.compile(r'"shares":(-?[0-9.eE+]+)'),
    'value': re.compile(r'"value_usd":(-?[0-9.eE+]+)'),
    'form': re.compile(r'"form":"([^"]+)"'),
    'accession': re.compile(r'"accession":"([^"]+)"'),
}
FIRST_QUARTER = (2013, 2)     # coverage becomes complete with 2013Q2 (about one million holdings per quarter)


def quarter_index(year, month):
    return (int(year) - FIRST_QUARTER[0]) * 4 + (int(month) - 1) // 3 - (FIRST_QUARTER[1] - 1)


def quarter_end(index):
    total = index + (FIRST_QUARTER[1] - 1)
    year, q = FIRST_QUARTER[0] + total // 4, total % 4 + 1
    return f'{year}-{q * 3:02d}-{(31 if q in (1, 4) else 30):02d}'


def cache_path(store, ref):
    return Path(store.root) / 'embedding_reports' / 'scratch' / 'cache' / f'holdings-{ref["version"]}.npz'


def build(store, ref=None, *, log=print):
    """Extract every original 13F-HR holding into arrays; returns the cache path."""
    ref = ref or catalog_ref(store, DATASET)
    path = cache_path(store, ref)
    if path.exists():
        return path, json.loads(str(np.load(path, allow_pickle=False)['meta']))
    started = time.time()
    managers, securities, accessions = {}, {}, {}
    # Typed arrays: tens of millions of rows must not become Python objects on a shared machine.
    rows = {'manager': array('i'), 'security': array('i'), 'quarter': array('h'), 'shares': array('d'),
            'value': array('d'), 'filed': array('i'), 'accession': array('i')}
    counts = {'lines': 0, 'holdings': 0, 'amendments_excluded': 0, 'before_first_quarter': 0, 'unparsed': 0}
    first_accession = {}          # (manager, quarter) -> earliest accession seen
    later_filings = set()
    source = _records_path(store, ref)
    with gzip.open(source, 'rt', encoding='utf-8') as stream:
        for line in stream:
            counts['lines'] += 1
            if '"predicate":"reported_holding"' not in line:
                continue
            form = FIELDS['form'].search(line)
            if form is None or form.group(1) != '13F-HR':
                counts['amendments_excluded'] += 1
                continue
            parts = {k: FIELDS[k].search(line) for k in ('subject', 'object', 'valid_from', 'observed_at', 'shares',
                                                          'value', 'accession')}
            if any(v is None for k, v in parts.items() if k not in ('shares',)):
                counts['unparsed'] += 1
                continue
            year, month, _ = parts['valid_from'].groups()
            quarter = quarter_index(year, month)
            if quarter < 0:
                counts['before_first_quarter'] += 1
                continue
            manager = managers.setdefault(parts['subject'].group(1), len(managers))
            key = (manager, quarter)
            accession = accessions.setdefault(parts['accession'].group(1), len(accessions))
            filed = int(''.join(parts['observed_at'].groups()))
            known = first_accession.get(key)
            if known is None:
                first_accession[key] = (filed, accession)
            elif known[1] != accession:
                if (filed, accession) < known:
                    first_accession[key] = (filed, accession)
                later_filings.add(key)
            rows['manager'].append(manager)
            rows['security'].append(securities.setdefault(parts['object'].group(1), len(securities)))
            rows['quarter'].append(quarter)
            rows['shares'].append(float(parts['shares'].group(1)) if parts['shares'] else float('nan'))
            rows['value'].append(float(parts['value'].group(1)))
            rows['filed'].append(filed)
            rows['accession'].append(accession)
            counts['holdings'] += 1
            if counts['holdings'] % 10_000_000 == 0 and log:
                log(f'  {counts["holdings"]:,} holdings, {round(time.time() - started)}s')
    manager = np.frombuffer(rows['manager'], dtype=np.int32)
    quarter = np.frombuffer(rows['quarter'], dtype=np.int16)
    accession = np.frombuffer(rows['accession'], dtype=np.int32)
    # Keep only rows from the earliest original filing of each (manager, quarter).
    keep = np.ones(len(manager), dtype=bool)
    if later_filings:
        winner = np.full(len(accessions), -1, dtype=np.int64)
        for key, (_, acc) in first_accession.items():
            if key in later_filings:
                winner[acc] = 1
        # An accession loses if it belongs to a contested (manager, quarter) and is not that key's winner.
        key_code = manager.astype(np.int64) * 1000 + quarter
        contested_codes = np.array(sorted(m * 1000 + q for m, q in later_filings), dtype=np.int64)
        contested = np.isin(key_code, contested_codes)
        keep = ~contested | (winner[accession] == 1)
    counts['duplicate_original_filings_rows_dropped'] = int((~keep).sum())
    arrays = {'manager': manager[keep], 'security': np.frombuffer(rows['security'], dtype=np.int32)[keep],
              'quarter': quarter[keep], 'shares': np.frombuffer(rows['shares'], dtype=np.float64)[keep],
              'value': np.frombuffer(rows['value'], dtype=np.float64)[keep],
              'filed': np.frombuffer(rows['filed'], dtype=np.int32)[keep]}
    order = np.lexsort((arrays['security'], arrays['manager'], arrays['quarter']))
    arrays = {k: v[order] for k, v in arrays.items()}
    meta = {'input': dict(ref), 'counts': counts, 'managers': len(managers), 'securities': len(securities),
            'quarters': int(arrays['quarter'].max()) + 1, 'first_quarter_end': quarter_end(0),
            'seconds': round(time.time() - started)}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrays, manager_ids=np.array(sorted(managers, key=managers.get)),
             security_ids=np.array(sorted(securities, key=securities.get)), meta=np.array(json.dumps(meta)))
    return path, meta


def load(path):
    data = np.load(path, allow_pickle=False)
    return {k: data[k] for k in data.files if k != 'meta'}, json.loads(str(data['meta']))
