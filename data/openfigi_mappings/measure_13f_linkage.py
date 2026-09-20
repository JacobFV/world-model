"""Measure what share of the 13F holding rows reach an issuer, before and after this dataset.

``docs/firm-panel.md`` is the baseline: of 87,357,054 holding rows read, 1,511,440 (1.7%) are in a
CUSIP the GLEIF chain ties to a CIK, over 393 issuers of which 371 file no financial statements in
these sources. This script reproduces that number with the panel's own rules and then recomputes it
with the CUSIP -> issuer map this dataset supports, composing only published edges:

    cusip  --openfigi_cusip_figi-->  figi (US composite, or an instrument with no composite)
    figi   <--compositeFIGI--------  the MIC-scoped listing OpenFIGI answered for an SEC symbol
    ticker:<MIC>:<symbol>  <--issuer_listing--  sec:cik:<issuer>   (sec_issuer_reference)

and a second route for the symbols whose SEC exchange value is not a MIC (``OTC``, ``CBOE``,
blank), where the only available scope is OpenFIGI's own ``US`` composite.

Every hop is held to one-to-one and the breaks are counted, not merged. Nothing is written to the
shared index: this reads published artifacts and writes one JSON report.

    WORLD_MODEL_DATA=... python3 data/openfigi_mappings/measure_13f_linkage.py report.json
"""
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from worldmodel.panels.firm import link_maps, resolve_13f_filings, _holding_parts  # noqa: E402
from worldmodel.resolution.openfigi import identity_claim, listing_row  # noqa: E402

HOLDINGS = ('sec_13f_history', 'sec_ownership_datasets')
ISSUERS = 'sec_issuer_reference'
MAPPINGS = 'openfigi_mappings'
PANEL = 'firm_panel'


def _latest(data_root, dataset, stage='normalized'):
    path = Path(data_root) / dataset / 'manifests' / ('latest.json' if stage == 'normalized'
                                                      else f'stages/{stage}/latest.json')
    if not path.exists():
        path = Path(data_root) / dataset / 'manifests' / 'stages' / stage / 'latest.json'
    return json.loads(path.read_text())['version']


def _lines(data_root, dataset, stage='normalized', version=None):
    version = version or _latest(data_root, dataset, stage)
    path = Path(data_root) / dataset / 'artifacts' / stage / version / 'records.jsonl.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        yield from handle


def _one_to_one(pairs, counts, name):
    """``{left: right}`` keeping only the values that are one-to-one in both directions."""
    forward, backward = {}, {}
    for left, right in pairs:
        forward.setdefault(left, set()).add(right)
        backward.setdefault(right, set()).add(left)
    counts[name + '_left_not_unique'] = sum(1 for v in forward.values() if len(v) > 1)
    counts[name + '_right_not_unique'] = sum(1 for v in backward.values() if len(v) > 1)
    return {left: next(iter(right)) for left, right in forward.items()
            if len(right) == 1 and len(backward[next(iter(right))]) == 1}


def read_sec_issuers(data_root):
    """``({(mic, symbol): cik}, {cik: description})`` from sec_issuer_reference."""
    listings, issuers, refused = {}, {}, {}
    for line in _lines(data_root, ISSUERS):
        if '"issuer_listing"' in line:
            record = json.loads(line)
            if record.get('predicate') == 'issuer_listing':
                scope, _, symbol = (record['object'] or '').partition(':')[2].partition(':')
                listings.setdefault((scope, symbol), set()).add(record['subject'][len('sec:cik:'):])
        elif '"entity_id":"sec:cik:' in line:
            record = json.loads(line)
            if record.get('kind') == 'entity':
                attributes = record.get('attributes') or {}
                issuers[record['entity_id'][len('sec:cik:'):]] = {
                    'entity_type': record.get('entity_type'), 'label': record.get('label'),
                    'sec_entity_type': attributes.get('sec_entity_type'), 'sic': attributes.get('sic')}
    for key, ciks in listings.items():
        if len(ciks) > 1:
            refused[':'.join(key)] = sorted(ciks)
    return ({key: next(iter(ciks)) for key, ciks in listings.items() if len(ciks) == 1},
            issuers, refused)


def read_openfigi(data_root, sec_listings, counts, rows_dir=None):
    """``{cusip: cik}`` composed from the published OpenFIGI answers and the SEC listing edges.

    With ``rows_dir`` the two mapping-specification row sets are written alongside, ready for
    ``wm link-identifiers --spec openfigi_cusip_figi`` and ``--spec figi_ticker``.
    """
    cusip_pairs, mic_pairs, us_pairs = [], [], []
    figi_composite = {}
    spec_rows = {'openfigi_cusip_figi': [], 'figi_ticker': []}
    for line in _lines(data_root, MAPPINGS):
        if '"openfigi_mapping"' not in line:
            continue
        record = json.loads(line)
        if record.get('predicate') != 'openfigi_mapping':
            continue
        counts['mapping_rows'] += 1
        subject, value = record['subject'], record['value']
        query, published = value['query'], value['published']
        figi = subject[len('figi:'):]
        figi_composite[figi] = published.get('compositeFIGI') or figi
        claim = identity_claim(value)
        if claim is not None:
            counts['cusip_claims'] += 1
            cusip_pairs.append((claim[1], figi))
            spec_rows['openfigi_cusip_figi'].append({'left': claim[1], 'right': figi})
            continue
        if query['id_type'] != 'TICKER':
            continue
        row = listing_row(subject, value)
        if row is not None:                       # a listing OpenFIGI scoped to a MIC itself
            counts['mic_scoped_listings'] += 1
            mic_pairs.append(((row['scope'], query['id_value']), figi))
            spec_rows['figi_ticker'].append(row)
        elif query.get('exch_code') == 'US' and value.get('figi_level') == 'us_composite':
            counts['us_composite_listings'] += 1
            us_pairs.append((query['id_value'], figi))
    if rows_dir:
        Path(rows_dir).mkdir(parents=True, exist_ok=True)
        for spec, rows in spec_rows.items():
            with (Path(rows_dir) / (spec + '.jsonl')).open('w', encoding='utf-8') as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True) + '\n')
            counts['rows_written_' + spec] = len(rows)

    cusip_figi = _one_to_one(cusip_pairs, counts, 'cusip_figi')
    counts['cusip_figi_links'] = len(cusip_figi)

    # Each route answers an SEC-published listing, so the CIK is the one the SEC printed for it.
    figi_cik_pairs, by_route = [], {'mic': 0, 'us': 0}
    for (mic, symbol), figi in mic_pairs:
        cik = sec_listings.get((mic, symbol))
        if cik:
            figi_cik_pairs.append((figi_composite.get(figi, figi), cik))
            by_route['mic'] += 1
    us_symbol_cik = {}
    for (scope, symbol), cik in sec_listings.items():
        us_symbol_cik.setdefault(symbol, set()).add(cik)
    for symbol, figi in us_pairs:
        ciks = us_symbol_cik.get(symbol) or set()
        if len(ciks) == 1:
            figi_cik_pairs.append((figi_composite.get(figi, figi), next(iter(ciks))))
            by_route['us'] += 1
    counts['listing_answers_by_route'] = by_route

    figi_cik, disagreements = {}, 0
    grouped = {}
    for figi, cik in figi_cik_pairs:
        grouped.setdefault(figi, set()).add(cik)
    for figi, ciks in grouped.items():
        if len(ciks) == 1:
            figi_cik[figi] = next(iter(ciks))
        else:
            disagreements += 1
    counts['figi_reached_by_several_ciks'] = disagreements
    counts['figi_cik_links'] = len(figi_cik)

    mapped = {}
    for number, figi in cusip_figi.items():
        cik = figi_cik.get(figi)
        if cik:
            mapped[number] = cik
    counts['cusip_cik_links'] = len(mapped)
    return mapped


def read_baseline(data_root):
    rows = [json.loads(line) for line in _lines(data_root, PANEL, stage='links')]
    return link_maps(rows)[1]


def read_panel_ciks(data_root):
    """CIKs that file financial statements in these sources, i.e. are not fund-only registrants."""
    ciks = set()
    for line in _lines(data_root, PANEL, stage='panel'):
        record = json.loads(line)
        if record.get('cik'):
            ciks.add(record['cik'])
    return ciks


def count_holdings(data_root, maps):
    """Holding rows and issuer-quarters each CUSIP->CIK map reaches, with the panel's own rules."""
    filings = {}
    for dataset in HOLDINGS:
        for line in _lines(data_root, dataset):
            if '"reported_13f_portfolio_value"' not in line:
                continue
            record = json.loads(line)
            if record.get('metric') != 'reported_13f_portfolio_value':
                continue
            dims, attributes = record.get('dimensions') or {}, record.get('attributes') or {}
            accession = dims.get('accession')
            if not accession or not str(dims.get('form') or '').startswith('13F-HR'):
                continue
            filings[accession] = (record['subject'], str(record['valid_from'])[:10],
                                  str(record['observed_at'])[:10], dims.get('form') or '',
                                  attributes.get('amendment_type'))
    kept, _ = resolve_13f_filings(filings)
    totals = {'holding_rows': 0, 'eligible_rows': 0}
    reached = {name: {'rows': 0, 'eligible_rows': 0, 'issuers': set(), 'issuer_quarters': set(),
                      'cusips': set()} for name in maps}
    for dataset in HOLDINGS:
        for line in _lines(data_root, dataset):
            if '"reported_holding"' not in line:
                continue
            start = line.find('"id":"')
            parts = _holding_parts(line[start + 6:line.find('"', start + 6)]) if start >= 0 else None
            if parts is None:
                continue
            accession, number, put_call, klass = parts
            totals['holding_rows'] += 1
            eligible = kept.get(accession) is not None and klass == 'SH' and put_call == '-'
            if eligible:
                totals['eligible_rows'] += 1
            period = filings[accession][1] if accession in filings else None
            for name, mapping in maps.items():
                cik = mapping.get(number)
                if cik is None:
                    continue
                reached[name]['rows'] += 1
                reached[name]['cusips'].add(number)
                if eligible:
                    reached[name]['eligible_rows'] += 1
                    reached[name]['issuers'].add(cik)
                    if period:
                        reached[name]['issuer_quarters'].add((cik, period))
    return totals, reached


def main():
    data_root = os.environ.get('WORLD_MODEL_DATA')
    if not data_root:
        raise SystemExit('Set WORLD_MODEL_DATA to the data root')
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('openfigi-linkage.json')
    counts = {'mapping_rows': 0, 'cusip_claims': 0, 'mic_scoped_listings': 0, 'us_composite_listings': 0}
    sec_listings, issuers, refused_symbols = read_sec_issuers(data_root)
    counts['sec_issuer_listings'] = len(sec_listings)
    counts['sec_symbols_on_several_ciks'] = refused_symbols
    print('sec listings', len(sec_listings), flush=True)
    after = read_openfigi(data_root, sec_listings, counts, rows_dir=os.environ.get('OPENFIGI_ROWS_DIR'))
    print('openfigi cusip->cik', len(after), counts, flush=True)
    before = read_baseline(data_root)
    print('baseline cusip->cik', len(before), flush=True)
    combined = dict(before)
    combined.update(after)
    panel_ciks = read_panel_ciks(data_root)
    totals, reached = count_holdings(data_root, {'before': before, 'after': after, 'combined': combined})

    report = {'totals': totals, 'counts': counts, 'maps': {}}
    for name, found in reached.items():
        operating = sorted(found['issuers'] & panel_ciks)
        report['maps'][name] = {
            'cusip_cik_links': len({'before': before, 'after': after, 'combined': combined}[name]),
            'cusips_with_a_holding_row': len(found['cusips']),
            'holding_rows': found['rows'],
            'holding_rows_share': round(found['rows'] / totals['holding_rows'], 6),
            'eligible_rows': found['eligible_rows'],
            'eligible_rows_share': round(found['eligible_rows'] / totals['eligible_rows'], 6),
            'issuers': len(found['issuers']),
            'issuer_quarters': len(found['issuer_quarters']),
            'issuers_filing_financial_statements': len(operating),
            'issuers_by_published_type': _tally(found['issuers'], issuers, 'entity_type'),
            'issuers_by_sec_entity_type': _tally(found['issuers'], issuers, 'sec_entity_type')}
    only_after = sorted(set(after) - set(before))
    disagree = sorted(number for number in set(after) & set(before) if after[number] != before[number])
    report['overlap'] = {'cusips_in_both': len(set(after) & set(before)),
                         'cusips_only_openfigi': len(only_after),
                         'cusips_only_gleif': len(set(before) - set(after)),
                         'cusips_where_the_two_routes_disagree': len(disagree),
                         'disagreement_sample': [{'cusip': c, 'gleif': before[c], 'openfigi': after[c]}
                                                 for c in disagree[:20]]}
    out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({'totals': totals, 'maps': report['maps'], 'overlap':
                      {k: v for k, v in report['overlap'].items() if k != 'disagreement_sample'}},
                     indent=1, default=str))


def _tally(ciks, issuers, field):
    out = {}
    for cik in ciks:
        key = str((issuers.get(cik) or {}).get(field))
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


if __name__ == '__main__':
    main()
