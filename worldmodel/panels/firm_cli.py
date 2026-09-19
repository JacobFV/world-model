"""``wm firm-panel``: one issuer's filed history, and the panel's coverage."""
import json

from . import firm

COMMANDS = {'firm-panel'}

NOT_ESTABLISHED = [
    'Values are as filed. The SEC states the data sets are extracted as filed and not audited by it.',
    'A vintage list is what the filer reported, not a correction history: a restated number carries no reason.',
    'Institutional ownership counts only 13F long share positions of managers above the reporting threshold; '
    'shorts, non-13F holders and the shares of holders below the threshold are not in it.',
    'An issuer with no LEI row is an issuer GLEIF does not tie to a CIK, not an issuer without an LEI.',
    'Federal contract obligations are not attached: no published crosswalk ties a SAM UEI to a CIK or LEI.',
]


def add_commands(sub):
    command = sub.add_parser('firm-panel', help='Read the firm panel: one issuer x fiscal period history, or coverage')
    command.add_argument('action', choices=('lookup', 'coverage', 'status'),
                         help='lookup: one issuer\'s periods; coverage: linked shares; status: published versions')
    command.add_argument('issuer', nargs='?', help='CIK (any zero padding), sec:cik:<10>, or an LEI')
    command.add_argument('--as-of', help='Report each value as it was known on this date (YYYY-MM-DD)')
    command.add_argument('--periods', type=int, default=40, help='Most recent fiscal periods to show (lookup)')
    command.add_argument('--full', action='store_true', help='Emit the whole panel rows, vintages included')


def _ref(store, stage):
    try:
        return store.latest('firm_panel', stage=stage)
    except (OSError, ValueError, KeyError) as error:
        raise ValueError(f'firm_panel/{stage} is not published under {store.root}: build it with '
                         f'`wm run firm_panel` (see docs/firm-panel.md)') from error


def _lines(store, stage):
    directory = store.version_dir(_ref(store, stage))
    plain = directory / 'records.jsonl'
    return firm.read_lines(plain if plain.exists() else directory / 'records.jsonl.gz')


def _rows(store, stage, needle=None):
    for line in _lines(store, stage):
        if needle is None or needle in line:
            yield json.loads(line)


def resolve_issuer(store, issuer):
    """``(cik, link row or None)`` for a CIK or an LEI."""
    text = str(issuer).strip()
    digits = text.rsplit(':', 1)[-1]
    if digits.isdigit():
        cik = digits.zfill(10)
        link = next(_rows(store, 'links', f'"id":"firm_panel:link:{cik}"'), None)
        return cik, link
    link = next(_rows(store, 'links', f'"lei":"{text.upper()}"'), None)
    if link is None:
        raise ValueError(f'No CIK for {issuer!r}: give a CIK, or an LEI the links stage ties to one')
    return link['cik'], link


def _summary(vintages, as_of):
    chosen = firm.value_as_of(vintages, as_of) if as_of else (vintages[-1] if vintages else None)
    if chosen is None:
        return None
    return {'value': chosen['value'], 'unit': chosen['unit'], 'available_at': chosen['available_at'],
            'revision': chosen['revision'], 'accession': chosen['accession'], 'concept': chosen['concept'],
            'vintages': len(vintages), 'first_value': vintages[0]['value'],
            **({'as_of': chosen['as_of']} if 'as_of' in chosen else {})}


def lookup(store, issuer, as_of=None, periods=40, full=False):
    cik, link = resolve_issuer(store, issuer)
    rows = sorted(_rows(store, 'panel', f'"id":"firm_panel:{cik}:'), key=lambda row: row['period_end'])
    if not rows:
        raise ValueError(f'No firm_panel rows for CIK {cik}')
    if as_of:
        rows = [row for row in rows if row['available_at'] <= as_of]
    shown = rows[-periods:]
    ownership = [row for row in _rows(store, 'ownership', f'"id":"firm_panel:ownership:{cik}:')]
    result = {'cik': cik, 'label': rows[-1].get('label'), 'sic': rows[-1].get('sic'),
              'panel': _ref(store, 'panel'), 'as_of': as_of, 'periods_in_panel': len(rows),
              'identity': {'lei': link['lei'] if link else None, 'cusips': (link or {}).get('cusips'),
                           'available_at': (link or {}).get('available_at'),
                           'basis': (link or {}).get('basis')},
              'periods': [{'period_end': row['period_end'], 'fiscal_year': row['fiscal_year'],
                           'fiscal_period': row['fiscal_period'], 'form': row['form'], 'first_filed': row['first_filed'],
                           'filings_for_period': row['filings_for_period'],
                           'instants': {metric: _summary(vintages, as_of) for metric, vintages in row['instants'].items()},
                           'durations': {metric: {q: _summary(v, as_of) for q, v in by_q.items()}
                                         for metric, by_q in row['durations'].items()},
                           'institutional_ownership': row['institutional_ownership'],
                           'federal_contracts': row['federal_contracts'], 'linked': row['linked']}
                          for row in shown],
              'institutional_ownership_quarters': [
                  {k: v for k, v in row.items() if k not in ('schema', 'evidence', 'cusips')}
                  for row in sorted(ownership, key=lambda row: row['quarter_end'])[-periods:]],
              'what_this_does_not_establish': NOT_ESTABLISHED}
    if full:
        result['rows'] = shown
    return result


def coverage(store):
    report = firm.coverage(_rows(store, 'panel'), [row for row in _rows(store, 'ownership') if row.get('cik')])
    construction = {stage: next((row for row in _rows(store, stage, '":construction"') if 'construction' in row), None)
                    for stage in ('links', 'ownership', 'panel')}
    return {'panel': _ref(store, 'panel'), 'coverage': report,
            'construction': {stage: (row or {}).get('construction') for stage, row in construction.items()},
            'federal_contracts': firm.CONTRACTS_NOT_LINKED, 'rules': firm.RULES}


def status(store):
    out = {}
    for stage in ('links', 'ownership', 'panel'):
        ref = _ref(store, stage)
        manifest = store.manifest(ref)
        out[stage] = {'version': ref['version'], 'rows': next(iter(manifest['outputs'].values()))['rows'],
                      'bytes': next(iter(manifest['outputs'].values()))['bytes'],
                      'inputs': [{'dataset': i['dataset'], 'version': i['version']} for i in manifest['inputs']]}
    return out


def execute(args, catalog, store, project, reference):
    if args.action == 'coverage':
        return coverage(store)
    if args.action == 'status':
        return status(store)
    if not args.issuer:
        raise ValueError('firm-panel lookup needs an issuer: a CIK or an LEI')
    return lookup(store, args.issuer, as_of=args.as_of, periods=args.periods, full=args.full)
