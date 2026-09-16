"""ECB euro reference rates and Eurostat SDMX TSV datasets -> evidence observations.

Shards: ``eurofxref-hist.zip`` (wide CSV, one column per quote currency) and one
gzip TSV per Eurostat dataset (``freq,unit,...,geo\\TIME_PERIOD`` then one column
per period; values ``123.4 p`` carry flags, ``:`` is missing). Scale prefixes in
Eurostat unit codes (MIO_, THS_, KTOE, *_MEUR) are applied to values; the source
unit code is kept in ``dimensions.unit_code``. Optional per-dataset dimension
filters come from ``parameters.filters``.
"""
import csv
import gzip
import io
import re
import zipfile
from datetime import date, timedelta

from .iso3166 import ISO2

DATASET = 'ecb_eurostat'
EUROSTAT_TO_ISO2 = {'EL': 'GR', 'UK': 'GB'}
FLAGS_NULL = {'c': 'confidential', 'n': 'not_significant', 'z': 'not_applicable', 'u': 'low_reliability'}

NA_ITEMS = {'B1GQ': 'gdp', 'B1G': 'gross_value_added', 'P3': 'final_consumption_expenditure',
            'P31_S14_S15': 'household_npish_final_consumption', 'P31_S14': 'household_final_consumption',
            'P3_S13': 'government_final_consumption', 'P5G': 'gross_capital_formation',
            'P51G': 'gross_fixed_capital_formation', 'P52_P53': 'changes_in_inventories_and_valuables',
            'P6': 'exports_goods_services', 'P7': 'imports_goods_services', 'B11': 'external_balance_goods_services',
            'D1': 'compensation_of_employees', 'B2A3G': 'gross_operating_surplus_mixed_income',
            'D21X31': 'taxes_less_subsidies_on_products', 'YA1': 'statistical_discrepancy_expenditure'}
DATASET_METRIC = {'nama_10r_2gdp': 'regional_gdp', 'nama_10r_3gdp': 'regional_gdp', 'prc_hicp_midx': 'hicp_index',
                  'nrg_bal_c': 'energy_balance', 'sts_inpr_m': 'industrial_production_index',
                  'irt_lt_mcby_m': 'long_term_interest_rate'}


def period(value):
    value = value.strip()
    if re.fullmatch(r'\d{4}', value):
        y = int(value)
        return f'{y:04d}-01-01', f'{y + 1:04d}-01-01', 'A'
    m = re.fullmatch(r'(\d{4})-?S([12])', value)
    if m:
        y = int(m[1])
        return (f'{y:04d}-01-01', f'{y:04d}-07-01', 'S') if m[2] == '1' else (f'{y:04d}-07-01', f'{y + 1:04d}-01-01', 'S')
    m = re.fullmatch(r'(\d{4})-?Q([1-4])', value)
    if m:
        y, q = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if q == 4 else date(y, 3 * q + 1, 1)
        return date(y, 3 * q - 2, 1).isoformat(), end.isoformat(), 'Q'
    m = re.fullmatch(r'(\d{4})-?M?(\d{2})', value)
    if m and 1 <= int(m[2]) <= 12:
        y, mo = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        return f'{y:04d}-{mo:02d}-01', end.isoformat(), 'M'
    m = re.fullmatch(r'(\d{4})-?W(\d{2})', value)
    if m:
        start = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        return start.isoformat(), (start + timedelta(days=7)).isoformat(), 'W'
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        d = date.fromisoformat(value)
        return d.isoformat(), (d + timedelta(days=1)).isoformat(), 'D'
    raise ValueError(f'Unsupported Eurostat time period: {value!r}')


def unit_of(code):
    """Map a Eurostat unit code to (unit, scale)."""
    m = re.fullmatch(r'I(\d{2})(?:_.*)?', code)
    if m:
        return f'index_20{m[1]}_100', 1
    m = re.fullmatch(r'PD(\d{2})_(EUR|NAC)', code)
    if m:
        return f'index_20{m[1]}_100', 1
    m = re.fullmatch(r'(?:CP|CLV\d{2}|PYP)_M(EUR|NAC)', code)
    if m:
        return ('EUR' if m[1] == 'EUR' else 'national_currency'), 1e6
    m = re.fullmatch(r'(?:CP|CLV\d{2})_(EUR|NAC)_HAB', code)
    if m:
        return ('EUR' if m[1] == 'EUR' else 'national_currency') + '_per_capita', 1
    table = {'MIO_EUR': ('EUR', 1e6), 'MIO_NAC': ('national_currency', 1e6), 'MIO_PPS_EST': ('PPS', 1e6),
             'EUR_HAB': ('EUR_per_capita', 1), 'PPS_EST_HAB': ('PPS_per_capita', 1), 'PPS_HAB_EU27_2020': ('percent_of_eu27_average', 1),
             'MIO_PPS_EU27_2020': ('PPS', 1e6), 'PPS_EU27_2020_HAB': ('PPS_per_capita', 1),
             'EUR_HAB_EU27_2020': ('percent_of_eu27_average', 1),
             'THS_PER': ('persons', 1e3), 'PC_ACT': ('percent_of_labour_force', 1), 'PC_GDP': ('percent_of_gdp', 1),
             'KTOE': ('tonne_oil_equivalent', 1e3), 'TJ': ('terajoule', 1), 'GWH': ('gigawatt_hour', 1), 'THS_T': ('tonne', 1e3),
             'CLV_PCH_PRE': ('percent_change_previous_period', 1), 'CLV_PCH_SM': ('percent_change_same_period_previous_year', 1),
             'PCH_PRE': ('percent_change_previous_period', 1), 'PCH_SM': ('percent_change_same_period_previous_year', 1),
             'RCH_A': ('percent_change_year_on_year', 1), 'RCH_M': ('percent_change_month_on_month', 1),
             'PC': ('percent', 1), 'PERCENT': ('percent', 1), 'NR': ('number', 1)}
    return table.get(code, (code.lower() or 'as_published', 1))


def resolve_unit(dataset, keys):
    """Unit for a row; datasets without a unit dimension get their documented unit."""
    code = keys.get('unit', '')
    if code:
        return unit_of(code)
    if dataset.startswith('ext_') and keys.get('indic_et', '').startswith('TRD_VAL'):
        return 'EUR', 1e6  # EU trade values are published in million EUR
    if dataset == 'irt_lt_mcby_m':
        return 'percent_per_annum', 1
    return 'as_published', 1


def geography(code):
    """Return (entity_id, entity_type, label, parent entity id or None, aggregate)."""
    iso2 = EUROSTAT_TO_ISO2.get(code, code)
    if len(code) == 2 and iso2 in ISO2:
        return 'iso3:' + ISO2[iso2][0], 'country', ISO2[iso2][1], None, False
    if re.fullmatch(r'[A-Z]{2}[0-9A-Z]{1,3}', code) and not code.startswith(('EU', 'EA', 'EFTA', 'EEA')) \
            and EUROSTAT_TO_ISO2.get(code[:2], code[:2]) in ISO2:
        parent = 'iso3:' + ISO2[EUROSTAT_TO_ISO2.get(code[:2], code[:2])][0]
        return 'nuts2021:' + code, 'jurisdiction', code, parent, False
    return 'agg:eurostat:' + code, 'aggregate_cohort', code, None, True


def metric_of(dataset, keys):
    if dataset in ('nama_10_gdp', 'namq_10_gdp'):
        return NA_ITEMS.get(keys.get('na_item', ''), 'esa2010_' + keys.get('na_item', 'unknown').lower())
    if dataset == 'une_rt_m':
        return 'unemployment_rate' if keys.get('unit', '').startswith('PC') else 'unemployed_persons'
    if dataset.startswith('ext_'):
        return {'EXP': 'exports_value', 'IMP': 'imports_value', 'BAL': 'trade_balance', 'BAL_RT': 'trade_balance'}.get(
            keys.get('stk_flow', ''), 'trade_value')
    return DATASET_METRIC.get(dataset, dataset)


def _entity(entity_id, entity_type, label, observed_at, evidence, source='ecb', **attributes):
    return {'kind': 'entity', 'id': f'eurostat:entity:{source}:{entity_id}', 'entity_id': entity_id, 'entity_type': entity_type,
            'label': label, 'observed_at': observed_at, 'evidence': evidence,
            'attributes': {'source_dataset': DATASET, **attributes}}


def ecb_rates(context, shard, observed_at):
    with zipfile.ZipFile(shard['path']) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith('.csv'))
        with archive.open(member) as binary:
            reader = csv.reader(io.TextIOWrapper(binary, encoding='utf-8-sig', newline=''))
            header = [h.strip() for h in next(reader)]
            subject = 'agg:ecb:euro_area'
            first = True
            for number, row in enumerate(reader, 2):
                if not row or not row[0].strip():
                    continue
                evidence = context.raw_evidence(f'shard:{shard["index"]}/member:{member}/line:{number}')
                if first:
                    first = False
                    yield _entity(subject, 'aggregate_cohort', 'Euro area (ECB euro reference rates)', observed_at, evidence, aggregate=True)
                day = date.fromisoformat(row[0].strip())
                for currency, cell in zip(header[1:], row[1:]):
                    cell = cell.strip()
                    if not currency or cell in ('', 'N/A'):
                        continue
                    key = f'EXR.D.{currency}.EUR.SP00.A'
                    yield {'kind': 'observation', 'id': f'ecb:{key}:{day.isoformat()}', 'observed_at': observed_at,
                           'subject': subject, 'metric': 'exchange_rate_per_eur', 'value': float(cell),
                           'unit': f'{currency}_per_EUR', 'valid_from': day.isoformat(),
                           'valid_to': (day + timedelta(days=1)).isoformat(),
                           'dimensions': {'frequency': 'D', 'quote_currency': currency, 'base_currency': 'EUR', 'series_key': 'ECB:' + key},
                           'evidence': evidence, 'attributes': {'source_flow': 'ECB_EXR_reference_rate'}}


def eurostat_tsv(context, shard, dataset, observed_at, filters):
    emitted = set()
    with gzip.open(shard['path'], 'rt', encoding='utf-8-sig', newline='') as stream:
        header = stream.readline().rstrip('\r\n').split('\t')
        dims = header[0].split('\\')[0].split(',')
        periods = [p.strip() for p in header[1:]]
        parsed = [period(p) for p in periods]
        allowed = {k: set(v) for k, v in filters.items()}
        for number, line in enumerate(stream, 2):
            cells = line.rstrip('\r\n').split('\t')
            if len(cells) != len(header):
                raise ValueError(f'{DATASET}: shard:{shard["index"]}/line:{number} has {len(cells)} cells, header {len(header)}')
            keys = dict(zip(dims, (k.strip() for k in cells[0].split(','))))
            if any(keys.get(k) not in v for k, v in allowed.items() if k in keys):
                continue
            locator = f'shard:{shard["index"]}/line:{number}'
            evidence = context.raw_evidence(locator)
            geo_field = 'geo' if 'geo' in keys else ('reporter' if 'reporter' in keys else None)
            subject = None
            extra = {}
            for field in [f for f in (geo_field, 'partner') if f and f in keys]:
                entity_id, entity_type, label, parent, aggregate = geography(keys[field])
                if entity_id not in emitted:
                    emitted.add(entity_id)
                    yield _entity(entity_id, entity_type, label, observed_at, evidence, source=dataset, eurostat_code=keys[field], aggregate=aggregate)
                    if parent:
                        yield {'kind': 'assertion', 'id': f'eurostat:within:{dataset}:{entity_id}', 'subject': entity_id, 'predicate': 'within',
                               'object': parent, 'observed_at': observed_at, 'evidence': evidence,
                               'attributes': {'source_dataset': DATASET, 'basis': 'NUTS code country prefix'}}
                if field == geo_field:
                    subject = entity_id
                else:
                    extra['partner'] = entity_id
            if subject is None:
                raise ValueError(f'{DATASET}: {dataset} row lacks a geography dimension')
            unit_code = keys.get('unit', '')
            unit, scale = resolve_unit(dataset, keys)
            metric = metric_of(dataset, keys)
            dimensions = {'dataset': dataset, 'unit_code': unit_code,
                          **{k: v for k, v in keys.items() if k not in ('freq', 'geo', 'reporter', 'partner', 'unit')}, **extra}
            series = ','.join(cells[0].split(','))
            for (valid_from, valid_to, freq), label, cell in zip(parsed, periods, cells[1:]):
                cell = cell.strip()
                if not cell:
                    continue
                if cell.startswith(':'):
                    flag = cell[1:].strip()
                    if flag not in FLAGS_NULL:
                        continue  # plain ':' = not available; not emitted
                    value, reason = None, FLAGS_NULL[flag]
                else:
                    number_text, _, flag = cell.partition(' ')
                    value, reason = float(number_text) * scale, None
                    flag = flag.strip()
                record = {'kind': 'observation', 'id': f'eurostat:{dataset}:{series}:{label}', 'observed_at': observed_at,
                          'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
                          'valid_from': valid_from, 'valid_to': valid_to,
                          'dimensions': {'frequency': keys.get('freq', freq), **dimensions}, 'evidence': evidence,
                          'attributes': {'source_flow': dataset}}
                if reason:
                    record['missing_reason'] = reason
                if flag:
                    record['attributes']['flags'] = flag
                if scale != 1:
                    record['attributes']['unit_multiplier'] = scale
                yield record


def shard_dataset(shard):
    request = shard.get('request') or {}
    url = request.get('url') or shard.get('url') or ''
    m = re.search(r'/data/([A-Za-z0-9_]+)', url)
    if m:
        return m[1]
    if 'eurofxref' in url:
        return 'ecb_eurofxref'
    raise ValueError(f'{DATASET}: cannot identify source dataset of shard {shard["index"]} ({url})')


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else None
    if not coverage or coverage['sampled'] or coverage['layout'] != 'shards':
        raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET} --allow-network)')
    filters = context.parameters.get('filters', {})
    for shard in context.raw_shards():
        observed_at = shard.get('retrieved_at') or context.raw_receipt()['retrieved_at']
        dataset = shard_dataset(shard)
        if dataset == 'ecb_eurofxref':
            yield from ecb_rates(context, shard, observed_at)
        else:
            yield from eurostat_tsv(context, shard, dataset, observed_at, filters.get(dataset, {}))
