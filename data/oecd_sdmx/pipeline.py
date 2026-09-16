"""OECD SDMX-CSV (format=csvfile) responses -> evidence observations.

Columns: ``DATAFLOW,<dimensions>,TIME_PERIOD,OBS_VALUE,<attributes>``. UNIT_MULT
(power of ten) is applied to values; the unit comes from UNIT_MEASURE (and
BASE_PER for indices). REF_AREA ISO3 codes become ``iso3:``; OECD groupings
(OECD, EA20, G20, EU27_2020, ...) become ``agg:oecd:<code>``.
"""
import re
from datetime import date

from .iso3166 import ISO2

DATASET = 'oecd_sdmx'
ISO3 = {iso3 for iso3, _ in ISO2.values()}
UNITS = {'XDC': 'national_currency', 'USD': 'USD', 'EUR': 'EUR', 'USD_PPP': 'USD_ppp', 'USD_EXC': 'USD',
         'PA': 'percent_per_annum', 'PC': 'percent', 'GR': 'percent_change', 'PS': 'persons', 'PB': 'percent_balance',
         'PT_B1GQ': 'percent_of_gdp', 'PT_PB1GQ': 'percent_of_potential_gdp', 'PT_LF': 'percent_of_labour_force',
         'PT_LF_SUB': 'percent_of_labour_force', 'XDC_USD': 'national_currency_per_USD', 'USD_XDC': 'USD_per_national_currency',
         'USD_PPP_PS': 'USD_ppp_per_person', 'XDC_PS': 'national_currency_per_person', 'H': 'hours', 'JB': 'jobs',
         'PO': 'percent_of_output', 'BBL_US_D': 'barrels_per_day', 'USD_BBL_US': 'USD_per_barrel', 'H_WR': 'hours_per_worker',
         'PT_POP_Y15T74': 'percent_of_population_15_74', 'PT_TRD_W': 'percent_of_world_trade'}
QNA = {'B1GQ': 'gdp', 'P3': 'final_consumption_expenditure', 'P5': 'gross_capital_formation',
       'P51G': 'gross_fixed_capital_formation', 'P52': 'changes_in_inventories', 'P6': 'exports_goods_services',
       'P7': 'imports_goods_services', 'B11': 'external_balance_goods_services', 'D1': 'compensation_of_employees',
       'EMP': 'employment', 'B1GQ_POP': 'gdp_per_capita', 'POP': 'population'}
NAMED = {('DF_CLI', 'LI'): 'composite_leading_indicator', ('DF_CLI', 'BCICP'): 'business_confidence_index',
         ('DF_CLI', 'CCICP'): 'consumer_confidence_index', ('DF_FINMARK', 'SHARE'): 'share_price_index',
         ('DF_FINMARK', 'IRSTCI'): 'short_term_interest_rate_call_money', ('DF_FINMARK', 'IR3TIB'): 'interbank_rate_3m',
         ('DF_FINMARK', 'IRLT'): 'long_term_interest_rate', ('DF_FINMARK', 'CC'): 'exchange_rate_ncu_per_usd',
         ('DF_IALFS_UNE_M', 'UNE_LF_M'): 'unemployment_rate'}


def period(value):
    value = value.strip()
    if re.fullmatch(r'\d{4}', value):
        y = int(value)
        return f'{y:04d}-01-01', f'{y + 1:04d}-01-01'
    m = re.fullmatch(r'(\d{4})-Q([1-4])', value)
    if m:
        y, q = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if q == 4 else date(y, 3 * q + 1, 1)
        return date(y, 3 * q - 2, 1).isoformat(), end.isoformat()
    m = re.fullmatch(r'(\d{4})-(\d{2})', value)
    if m:
        y, mo = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        return f'{y:04d}-{mo:02d}-01', end.isoformat()
    m = re.fullmatch(r'(\d{4})-S([12])', value)
    if m:
        y = int(m[1])
        return (f'{y:04d}-01-01', f'{y:04d}-07-01') if m[2] == '1' else (f'{y:04d}-07-01', f'{y + 1:04d}-01-01')
    raise ValueError(f'Unsupported OECD time period: {value!r}')


def area(code):
    if code in ISO3:
        return 'iso3:' + code, 'country', False
    return 'agg:oecd:' + code, 'aggregate_cohort', True


def metric_of(flow, dims):
    measure = dims.get('MEASURE', '')
    if flow == 'DF_QNA':
        base = QNA.get(dims.get('TRANSACTION', ''), 'national_accounts_' + dims.get('TRANSACTION', '').lower())
        sector = dims.get('SECTOR', 'S1')
        return base if sector in ('S1', '') else f'{base}_{sector.lower()}'
    if flow == 'DF_PRICES_ALL':
        return 'cpi_index' if dims.get('UNIT_MEASURE') == 'IX' else 'cpi_inflation_yoy'
    if (flow, measure) in NAMED:
        return NAMED[(flow, measure)]
    prefix = {'DF_EO': 'eo', 'DF_BTS': 'business_tendency', 'DF_CS': 'consumer_opinion', 'DF_HOUSE_PRICES': 'house_price',
              'DF_PDB_ULC_Q': 'productivity', 'DF_MONAGG': 'monetary_aggregate', 'DF_CLI': 'cli', 'DF_FINMARK': 'financial_market'}
    return f'{prefix.get(flow, flow.lower())}_{measure.lower()}'


GROWTH = {'GY': 'percent_change_year_on_year', 'G1': 'percent_change_previous_period', 'GO1': 'percent_change_previous_period',
          'GOY': 'percent_change_year_on_year', 'GA': 'percent_change_annualised'}


def unit_of(row):
    code = (row.get('UNIT_MEASURE') or '').strip()
    transformation = (row.get('TRANSFORMATION') or '').strip()
    if transformation in GROWTH:
        return GROWTH[transformation]
    if code == 'PP':
        return 'percentage_points'
    if code == 'IX':
        base = (row.get('BASE_PER') or '').strip()
        return f'index_{base}_100' if base else 'index'
    return UNITS.get(code, code.lower() or 'as_published')


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else None
    if not coverage or coverage['sampled'] or coverage['layout'] != 'shards':
        raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET} --allow-network)')
    receipt = context.raw_receipt()
    shards = {shard['index']: shard for shard in context.raw_shards()}
    current = None
    for locator, row in context.raw_rows(format='csv'):
        index = int(locator.split('/', 1)[0].split(':')[1])
        if index != current:
            current, emitted = index, set()
            observed_at = shards[index].get('retrieved_at') or receipt['retrieved_at']
            names = list(row)
            dims_fields = names[names.index('DATAFLOW') + 1:names.index('TIME_PERIOD')]
        time_period = (row.get('TIME_PERIOD') or '').strip()
        raw_value = (row.get('OBS_VALUE') or '').strip()
        if not time_period:
            continue
        flow = re.search(r'@(DF_[A-Z0-9_]+)\(', row['DATAFLOW'])[1]
        dims = {field: (row.get(field) or '').strip() for field in dims_fields}
        subject, entity_type, aggregate = area(dims.get('REF_AREA', ''))
        evidence = context.raw_evidence(locator)
        if subject not in emitted:
            emitted.add(subject)
            yield {'kind': 'entity', 'id': f'oecd:entity:shard{index}:{subject}', 'entity_id': subject, 'entity_type': entity_type,
                   'label': dims.get('REF_AREA'), 'observed_at': observed_at, 'evidence': evidence,
                   'attributes': {'source_dataset': DATASET, 'oecd_code': dims.get('REF_AREA'), 'aggregate': aggregate}}
        valid_from, valid_to = period(time_period)
        series_key = '.'.join(dims.values())
        status = (row.get('OBS_STATUS') or '').strip()
        record = {'kind': 'observation', 'id': f'oecd:{flow}:{series_key}:{time_period}', 'observed_at': observed_at,
                  'subject': subject, 'metric': metric_of(flow, dims), 'unit': unit_of(row),
                  'valid_from': valid_from, 'valid_to': valid_to,
                  'dimensions': {'frequency': dims.get('FREQ', ''), 'series_key': f'{flow}:{series_key}',
                                 **{k.lower(): v for k, v in dims.items() if k not in ('REF_AREA', 'FREQ')}},
                  'evidence': evidence, 'attributes': {'source_flow': flow}}
        try:
            value = float(raw_value)
            if value != value or value in (float('inf'), float('-inf')):
                raise ValueError
        except ValueError:
            value = None
        if value is None:
            record['value'] = None
            record['missing_reason'] = 'source_status_' + status if status else 'source_missing'
        else:
            multiplier = (row.get('UNIT_MULT') or '').strip()
            if multiplier and multiplier != '0':
                value *= 10 ** int(multiplier)
                record['attributes']['unit_multiplier'] = int(multiplier)
            record['value'] = value
        if status and status != 'A':
            record['attributes']['obs_status'] = status
        yield record
