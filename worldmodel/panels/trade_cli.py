"""``wm trade-panel``: one reporter-partner-product history, and the panel's coverage."""
import json

from . import firm, trade

COMMANDS = {'trade-panel'}

NOT_ESTABLISHED = [
    'BACI values are CEPII reconciliations of both partners\' declarations, not either country\'s published statistic.',
    'The tariff column is the importer\'s MFN applied rate (a simple average of its national lines in the HS6 '
    'subheading), not the duty this flow paid: preferences, quotas, anti-dumping duties and the chapter 99 US '
    'surcharges are not in it.',
    'A missing rate means no exactly translatable published rate for that importer, year and code, not a zero tariff.',
    'HS1992 and HS2017 records are separate nomenclatures; only the tariff codes are translated between vintages, '
    'never the trade values.',
    'Gravity covariates are CEPII V202211 and stop in 2021; nothing here is a gravity estimate.',
]


def add_commands(sub):
    command = sub.add_parser('trade-panel', help='Read the trade panel: one reporter-partner-product history, or coverage')
    command.add_argument('action', choices=('lookup', 'coverage', 'status'),
                         help='lookup: one reporter x partner x HS6 history; coverage: joined shares; status: versions')
    command.add_argument('reporter', nargs='?', help='ISO3 code (USA) or iso3:USA')
    command.add_argument('partner', nargs='?', help='ISO3 code (CHN) or iso3:CHN')
    command.add_argument('product', nargs='?', help='HS6 code (850440)')
    command.add_argument('--nomenclature', choices=('hs92', 'hs17'), help='Restrict to one HS vintage')
    command.add_argument('--tariffs', action='store_true', help='Also show the importer schedules the history used')


def _ref(store, stage):
    try:
        return store.latest('trade_panel', stage=stage)
    except (OSError, ValueError, KeyError) as error:
        raise ValueError(f'trade_panel/{stage} is not published under {store.root}: build it with '
                         f'`wm run trade_panel` (see docs/trade-panel.md)') from error


def _lines(store, stage):
    directory = store.version_dir(_ref(store, stage))
    plain = directory / 'records.jsonl'
    return firm.read_lines(plain if plain.exists() else directory / 'records.jsonl.gz')


def country(value):
    text = str(value).strip()
    return text if ':' in text else f'iso3:{text.upper()}'


def lookup(store, reporter, partner, product, nomenclature=None, tariffs=False):
    reporter, partner = country(reporter), country(partner)
    code = str(product).split(':', 1)[-1]
    history = trade.history(_lines(store, 'panel'), reporter, partner, code, nomenclature=nomenclature)
    result = {'panel': _ref(store, 'panel'), 'reporter': reporter, 'partner': partner, 'product': code,
              'units': {'value_kusd': 'thousand current USD, FOB', 'quantity_t': 'metric tons',
                        'importer_mfn_applied_pct': 'percent'},
              'reading': (f'exports are the flow {reporter} -> {partner} and imports the flow {partner} -> {reporter}; '
                          'BACI reconciles both partners\' declarations into one value per direction'),
              'years': history, 'what_this_does_not_establish': NOT_ESTABLISHED}
    if tariffs:
        wanted = {slot[key]['record_id'] for slot in history for key in ('exports_tariff_source', 'imports_tariff_source')
                  if slot.get(key)}
        schedules = []
        for line in _lines(store, 'tariffs'):
            row = json.loads(line)
            if row['id'] in wanted:
                schedules.append({k: v for k, v in row.items() if k != 'rates_pct'} |
                                 {'rate_pct_for_product': row['rates_pct'].get(code)})
        result['importer_schedules'] = sorted(schedules, key=lambda row: (row['nomenclature'], row['year']))
    return result


def coverage(store):
    report = trade.coverage(json.loads(line) for line in _lines(store, 'panel'))
    construction = {}
    for stage in ('concordance', 'tariffs', 'panel'):
        for line in _lines(store, stage):
            if ':construction"' in line:
                construction[stage] = json.loads(line).get('construction')
    return {'panel': _ref(store, 'panel'), 'coverage': report, 'construction': construction, 'rules': trade.RULES}


def status(store):
    out = {}
    for stage in ('concordance', 'tariffs', 'panel'):
        ref = _ref(store, stage)
        manifest = store.manifest(ref)
        output = next(iter(manifest['outputs'].values()))
        out[stage] = {'version': ref['version'], 'rows': output['rows'], 'bytes': output['bytes'],
                      'inputs': [{'dataset': i['dataset'], 'version': i['version']} for i in manifest['inputs']]}
    return out


def execute(args, catalog, store, project, reference):
    if args.action == 'coverage':
        return coverage(store)
    if args.action == 'status':
        return status(store)
    if not (args.reporter and args.partner and args.product):
        raise ValueError('trade-panel lookup needs a reporter, a partner and an HS6 product code')
    return lookup(store, args.reporter, args.partner, args.product, nomenclature=args.nomenclature, tariffs=args.tariffs)
