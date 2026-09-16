"""IMF SDMX-CSV responses -> evidence observations.

Shards are SDMX-CSV documents: SDMX 3.0 (``STRUCTURE,STRUCTURE_ID,ACTION,<dims>,TIME_PERIOD,OBS_VALUE``)
or SDMX 2.1 dataonly (``DATAFLOW,<dims>,TIME_PERIOD,OBS_VALUE,<empty attributes>``).
Values are published in full units (the SCALE attribute is informational in the IMF
portal); units come from the series' unit/transformation codes. Countries use ISO3
codes (``iso3:``); IMF groups (G001 world, G110 advanced economies, ...) become
``agg:imf:<code>``.
"""
import re
from datetime import date, timedelta

from .iso3166 import ISO2

DATASET = 'imf_sdmx'
ISO3 = {iso3 for iso3, _ in ISO2.values()}
RETIRED_ISO3 = {'ANT', 'SCG', 'YUG', 'SUN', 'CSK', 'DDR', 'TMP', 'ZAR', 'ROM', 'BUR'}
IMF_ALIASES = {'KOS': 'XKX', 'UVK': 'XKX'}
NA_ITEMS = {'B1GQ': 'gdp', 'B11': 'external_balance_goods_services', 'P3': 'final_consumption_expenditure',
            'P3_S13': 'government_final_consumption', 'P3_S14': 'household_final_consumption', 'P3_S15': 'npish_final_consumption',
            'P5': 'gross_capital_formation', 'P51G': 'gross_fixed_capital_formation', 'P52': 'changes_in_inventories',
            'P6': 'exports_goods_services', 'P7': 'imports_goods_services'}
IMTS = {'XG_FOB_USD': 'goods_exports_fob', 'MG_CIF_USD': 'goods_imports_cif', 'TBG_USD': 'goods_trade_balance'}
IL = {'TRGMV_REVS': 'total_reserves_incl_gold_market_value', 'RXF11_REVS': 'foreign_exchange_reserves',
      'RGOLDMV_REVS': 'gold_reserves_market_value', 'RXDR_REVS': 'sdr_holdings', 'TRRPIMF_REVS': 'reserve_position_in_imf'}
UNIT_CODES = {'USD': 'USD', 'XDC': 'national_currency', 'XDR': 'SDR', 'EUR': 'EUR', 'POGDP_PT': 'percent_of_gdp',
              'IX': 'index', 'INDEX': 'index', 'FTO': 'fine_troy_ounce', 'PT': 'percent', 'PCH': 'percent_change',
              'INDEX_PCH': 'percent_change_previous_period', 'USD_PCH': 'percent_change_previous_period',
              'INDEX_PCHY': 'percent_change_year_on_year', 'USD_PCHY': 'percent_change_year_on_year'}


def period(value):
    value = value.strip()
    if re.fullmatch(r'\d{4}', value):
        y = int(value)
        return f'{y:04d}-01-01', f'{y + 1:04d}-01-01', 'A'
    m = re.fullmatch(r'(\d{4})-?Q([1-4])', value)
    if m:
        y, q = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if q == 4 else date(y, 3 * q + 1, 1)
        return date(y, 3 * q - 2, 1).isoformat(), end.isoformat(), 'Q'
    m = re.fullmatch(r'(\d{4})-M?(\d{2})', value)
    if m and 1 <= int(m[2]) <= 12:
        y, mo = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        return f'{y:04d}-{mo:02d}-01', end.isoformat(), 'M'
    m = re.fullmatch(r'(\d{4})-?S([12])', value)
    if m:
        y = int(m[1])
        return (f'{y:04d}-01-01', f'{y:04d}-07-01', 'S') if m[2] == '1' else (f'{y:04d}-07-01', f'{y + 1:04d}-01-01', 'S')
    m = re.fullmatch(r'(\d{4})-W(\d{2})', value)
    if m:
        start = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        return start.isoformat(), (start + timedelta(days=7)).isoformat(), 'W'
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', value) or re.fullmatch(r'(\d{4})-D?(\d{2})(\d{2})', value)
    if m:
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return d.isoformat(), (d + timedelta(days=1)).isoformat(), 'D'
    raise ValueError(f'Unsupported IMF time period: {value!r}')


def economy(code):
    code = IMF_ALIASES.get(code, code)
    if code in ISO3 or code in RETIRED_ISO3:
        return 'iso3:' + code, 'country', False
    return 'agg:imf:' + code, 'aggregate_cohort', True


def unit_of(flow, dims):
    transformation = dims.get('TYPE_OF_TRANSFORMATION') or dims.get('DATA_TRANSFORMATION') or ''
    unit = dims.get('UNIT', '')
    indicator = dims.get('INDICATOR', '')
    if flow == 'ER':
        return 'national_currency_per_USD'
    if flow == 'CPI':
        return 'index' if transformation == 'IX' else 'percent_change_year_on_year'
    if flow in ('MFS_IR',) or indicator.endswith('_PT') or transformation.endswith('_PT') and transformation != 'POGDP_PT':
        return 'percent'
    if flow == 'IMTS' or indicator.endswith('_USD'):
        return 'USD'
    for code in (transformation, unit):
        if code in UNIT_CODES:
            return UNIT_CODES[code]
    if flow == 'WEO':
        return weo_unit(indicator)
    return (transformation or unit or 'as_published').lower()


def weo_unit(indicator):
    if indicator.endswith(('_NGDP', '_NGDPD', '_NPGDP')) or indicator in ('LUR', 'PPPSH', 'NGSD_NGDP', 'NID_NGDP'):
        return 'percent_of_gdp' if indicator not in ('LUR', 'PPPSH') else 'percent'
    if indicator.endswith(('PCH', '_RPCH')) or indicator == 'PCPIEPCH':
        return 'percent_change'
    if indicator in ('NGDPD', 'BCA', 'NGDPDPC', 'PPPGDP', 'PPPPC'):
        return {'PPPGDP': 'international_dollar_ppp', 'PPPPC': 'international_dollar_ppp_per_capita',
                'NGDPDPC': 'USD_per_capita'}.get(indicator, 'USD')
    if indicator in ('LP', 'LE'):
        return 'persons'
    if indicator in ('PCPI', 'PCPIE', 'NGDP_D'):
        return 'index'
    if indicator in ('PPPEX',):
        return 'national_currency_per_international_dollar'
    if indicator.endswith('PC'):
        return 'national_currency_per_capita'
    return 'national_currency'


def metric_of(flow, dims):
    indicator = dims.get('INDICATOR', '')
    if flow == 'CPI':
        return 'consumer_price_index' if dims.get('TYPE_OF_TRANSFORMATION') == 'IX' else 'cpi_inflation_yoy'
    if flow == 'ER':
        return 'exchange_rate_ncu_per_usd'
    if flow == 'IMTS':
        return IMTS.get(indicator, 'imts_' + indicator.lower())
    if flow == 'IL':
        return IL.get(indicator, 'il_' + indicator.lower())
    if flow in ('ANEA', 'QNEA'):
        return NA_ITEMS.get(indicator, 'national_accounts_' + indicator.lower())
    prefix = {'BOP_AGG': 'bop', 'WEO': 'weo', 'MFS_IR': 'interest_rate', 'MFS_MA': 'monetary_aggregate', 'PCPS': 'commodity_price'}
    return f'{prefix.get(flow, flow.lower())}_{indicator.lower()}'


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else None
    if not coverage or coverage['sampled'] or coverage['layout'] != 'shards':
        raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET} --allow-network)')
    receipt = context.raw_receipt()
    shards = {shard['index']: shard for shard in context.raw_shards()}
    current, emitted, header = None, set(), None
    for locator, row in context.raw_rows(format='csv', strict=False):
        index = int(locator.split('/', 1)[0].split(':')[1])
        if index != current:
            current, emitted = index, set()
            observed_at = shards[index].get('retrieved_at') or receipt['retrieved_at']
            names = list(row)
            start = names.index('ACTION') + 1 if 'ACTION' in names else names.index('DATAFLOW') + 1
            dims_fields = names[start:names.index('TIME_PERIOD')]
            structure_field = 'STRUCTURE_ID' if 'STRUCTURE_ID' in names else 'DATAFLOW'
        time_period = (row.get('TIME_PERIOD') or '').strip()
        raw_value = (row.get('OBS_VALUE') or '').strip()
        if not time_period or not raw_value:
            continue  # series without observations (SDMX-CSV emits key-only rows)
        flow = re.match(r'[^:]+:([A-Z0-9_]+)\(', row[structure_field])[1]
        dims = {field: (row.get(field) or '').strip() for field in dims_fields}
        country = dims.get('COUNTRY', '')
        evidence = context.raw_evidence(locator)
        subject, entity_type, aggregate = economy(country)
        extra = {}
        entities = [(subject, entity_type, aggregate, country)]
        if dims.get('COUNTERPART_COUNTRY'):
            counterpart = economy(dims['COUNTERPART_COUNTRY'])
            extra['counterpart'] = counterpart[0]
            entities.append((*counterpart, dims['COUNTERPART_COUNTRY']))
        for entity_id, kind, is_aggregate, code in entities:
            if entity_id not in emitted:
                emitted.add(entity_id)
                yield {'kind': 'entity', 'id': f'imf:entity:shard{index}:{entity_id}', 'entity_id': entity_id, 'entity_type': kind,
                       'label': code, 'observed_at': observed_at, 'evidence': evidence,
                       'attributes': {'source_dataset': DATASET, 'imf_code': code, 'aggregate': is_aggregate}}
        valid_from, valid_to, _ = period(time_period)
        series_key = '.'.join(dims.values())
        frequency = dims.get('FREQUENCY') or dims.get('FREQ') or ''
        try:
            value = float(raw_value)
            if value != value or value in (float('inf'), float('-inf')):
                value = None
        except ValueError:
            value = None
        record = {'kind': 'observation', 'id': f'imf:{flow}:{series_key}:{time_period}', 'observed_at': observed_at,
                  'subject': subject, 'metric': metric_of(flow, dims), 'value': value, 'unit': unit_of(flow, dims),
                  'valid_from': valid_from, 'valid_to': valid_to,
                  'dimensions': {'frequency': frequency, 'series_key': f'{flow}:{series_key}',
                                 **{k.lower(): v for k, v in dims.items() if k not in ('COUNTRY', 'FREQUENCY', 'FREQ', 'COUNTERPART_COUNTRY')},
                                 **extra},
                  'evidence': evidence, 'attributes': {'source_flow': flow}}
        if value is None:
            record['missing_reason'] = 'non_numeric_source_value'
            record['attributes']['source_value'] = raw_value[:40]
        yield record
