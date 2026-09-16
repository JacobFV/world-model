"""Adapter from normalized crosswalk assertions to ``worldmodel.crosswalks``.

Every crosswalk assertion emitted by this dataset carries ``attributes.crosswalk`` plus
``source_system``, ``target_system``, ``source_code``, ``target_code``, ``weight``,
``weight_basis``, optional ``weights``/``reverse_weights`` ({basis: share}) and the record's
``valid_from``/``valid_to``. This module is kept identical in census_relationship_files,
cbsa_delineations and trade_concordances (datasets cannot import each other's code).

    from worldmodel.store import Store
    store = Store('data')
    records = store.records(store.latest('census_relationship_files'))
    walk = build_crosswalk(records, 'census_zcta520_county20')          # land-area weights
    walk.apportion({'00601': 100.0})
    # or, for load_concordance_csv:
    kwargs = write_concordance_csv(records, 'census_zcta520_county20', '/tmp/z.csv')
    walk = load_concordance_csv('/tmp/z.csv', **kwargs)
"""
import csv
from pathlib import Path

COLUMNS = ('source', 'target', 'weight', 'weight_error', 'basis', 'valid_from', 'valid_to', 'relationship')


def crosswalk_ids(records):
    """{crosswalk id: row count} for every crosswalk present in ``records``."""
    counts = {}
    for record in records:
        name = (record.get('attributes') or {}).get('crosswalk')
        if record.get('kind') == 'assertion' and name:
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def crosswalk_rows(records, crosswalk_id, *, basis=None, reverse=False, dated=True):
    """Yield (row, attributes) for one crosswalk.

    basis: pick a weight from ``attributes.weights`` (e.g. 'population', 'housing_units', 'land_area',
    'total_area'); default is the primary ``weight``. reverse=True swaps source/target and uses
    ``reverse_weights`` (a missing reverse weight stays None, never 1).
    """
    for record in records:
        attrs = record.get('attributes') or {}
        if record.get('kind') != 'assertion' or attrs.get('crosswalk') != crosswalk_id:
            continue
        source, target = attrs['source_code'], attrs['target_code']
        if reverse:
            source, target = target, source
            weights = attrs.get('reverse_weights') or {}
            key = basis or attrs.get('weight_key')
            weight = weights.get(key) if key else None
        elif basis:
            weight, key = (attrs.get('weights') or {}).get(basis), basis
        else:
            weight, key = attrs.get('weight'), attrs.get('weight_basis')
        row = {'source': source, 'target': target, 'weight': weight, 'weight_error': attrs.get('weight_error'),
               'basis': key, 'relationship': attrs.get('relationship'),
               'valid_from': record.get('valid_from') if dated else None,
               'valid_to': record.get('valid_to') if dated else None}
        yield row, attrs


def build_crosswalk(records, crosswalk_id, *, basis=None, reverse=False, dated=None):
    """Build a ``worldmodel.crosswalks.Crosswalk``.

    dated: None keeps validity only for crosswalks whose rows carry ``valid_to`` (true time-varying
    memberships such as CBSA delineations); True/False force it on/off.
    """
    from worldmodel.crosswalks import Crosswalk, CrosswalkError
    rows, meta = [], None
    for row, attrs in crosswalk_rows(records, crosswalk_id, basis=basis, reverse=reverse, dated=True):
        meta = meta or attrs
        rows.append(row)
    if not rows:
        raise CrosswalkError('No rows for crosswalk ' + crosswalk_id)
    if dated is None:
        dated = any(r['valid_to'] for r in rows)
    if not dated:
        for row in rows:
            row['valid_from'] = row['valid_to'] = None
    source_system, target_system = meta['source_system'], meta['target_system']
    if reverse:
        source_system, target_system = target_system, source_system
    bases = sorted({str(r['basis']) for r in rows if r['basis']})
    return Crosswalk(crosswalk_id + ('_reverse' if reverse else ''), source_system, target_system, rows,
                     weight_basis=basis or ('; '.join(bases) or None), source=meta.get('source_url'),
                     licence=meta.get('licence'), notes=meta.get('crosswalk_note'))


def write_concordance_csv(records, crosswalk_id, path, *, basis=None, reverse=False):
    """Write a CSV readable by ``crosswalks.load_concordance_csv``; returns its keyword arguments."""
    meta = None
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for row, attrs in crosswalk_rows(records, crosswalk_id, basis=basis, reverse=reverse):
            meta = meta or attrs
            writer.writerow({k: ('' if v is None else v) for k, v in row.items()})
    if meta is None:
        raise ValueError('No rows for crosswalk ' + crosswalk_id)
    systems = (meta['source_system'], meta['target_system'])
    if reverse:
        systems = systems[::-1]
    return {'id': crosswalk_id + ('_reverse' if reverse else ''), 'source_system': systems[0], 'target_system': systems[1],
            'source_column': 'source', 'target_column': 'target', 'weight_column': 'weight',
            'weight_basis': basis or meta.get('weight_basis'), 'source': meta.get('source_url'), 'licence': meta.get('licence')}
