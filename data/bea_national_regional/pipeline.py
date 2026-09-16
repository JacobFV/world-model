"""bea_national_regional: BEA NIPA flat files, state regional ZIP tables and county API tables.

Shards are recognised from their recorded request URL (``files`` acquisition): the NIPA
``TablesRegister.txt``/``SeriesRegister.txt`` registers (which must precede the data files),
``NipaData{A,Q,M}.txt``, state ``/regional/zip/<TABLE>.zip`` archives (only the
``*__ALL_AREAS_*.csv`` members are read) and Regional API ``GetData`` JSON responses.
Scale multipliers are applied explicitly; the source unit and multiplier are kept.
"""
from decimal import Decimal, InvalidOperation
import fnmatch
import json
import math
import re
import zipfile
from worldmodel.raw_readers import iter_rows
from worldmodel.source_helpers import STATE_FIPS
from worldmodel.util import digest

DATASET = 'bea_national_regional'
STATE_CODES = {code for code in STATE_FIPS.values() if int(code) <= 56}
DEFAULT_STATE_TABLES = ['SAGDP1', 'SAGDP2', 'SAGDP4', 'SAGDP9', 'SAINC1', 'SAINC4', 'SAINC5N', 'SAINC7N', 'SAINC30',
                        'SAINC35', 'SAINC50', 'SAINC51', 'SAINC91', 'SAPCE3', 'SAPCE4',
                        'SQGDP1', 'SQGDP2', 'SQGDP9', 'SQINC1', 'SQINC4', 'SQINC5N', 'SQINC35']
# Industry-dimensioned table families (TableName without the SA/SQ/CA prefix) -> metric.
INDUSTRY_TABLES = {'GDP2': 'gdp', 'GDP3': 'taxes_on_production_and_imports_less_subsidies',
                   'GDP4': 'compensation_of_employees', 'GDP5': 'subsidies', 'GDP6': 'taxes_on_production_and_imports',
                   'GDP7': 'gross_operating_surplus', 'GDP8': 'real_gdp_quantity_index', 'GDP9': 'real_gdp',
                   'GDP11': 'real_gdp_growth_contribution',
                   'INC5N': 'earnings_by_place_of_work', 'INC5S': 'earnings_by_place_of_work',
                   'INC5H': 'earnings_by_place_of_work', 'INC6N': 'compensation_of_employees',
                   'INC6S': 'compensation_of_employees', 'INC7N': 'wages_and_salaries',
                   'INC7S': 'wages_and_salaries', 'INC7H': 'wages_and_salaries'}
# Category-dimensioned table families -> metric.
CATEGORY_TABLES = {'PCE1': 'personal_consumption_expenditures', 'PCE2': 'per_capita_personal_consumption_expenditures',
                   'PCE3': 'personal_consumption_expenditures', 'PCE4': 'personal_consumption_expenditures',
                   'PCE5': 'personal_consumption_expenditures_growth_contribution'}
UNIT_BASES = {'dollars': 'USD', 'current dollars': 'USD', 'chained 2017 dollars': 'USD_chained_2017',
              'quantity index': 'index_2017_100', 'percentage points': 'percentage_points', 'percent change': 'percent',
              'percent': 'percent', 'number of persons': 'persons', 'persons': 'persons', 'number of jobs': 'jobs',
              'jobs': 'jobs', 'ratio': 'ratio', 'index': 'index_2017_100'}
PREFIX_SCALES = {'thousands': 1e3, 'millions': 1e6, 'billions': 1e9}
SUPPRESSED = {'(D)': 'suppressed_to_avoid_disclosure', '(T)': 'suppressed_to_avoid_disclosure',
              '(L)': 'below_display_threshold'}
NOT_AVAILABLE = {'(NA)', '(NM)', '(X)', '(S)', '---', '', '(NA)*'}
COUNTY_METRICS = {('CAINC1', '1'): 'personal_income', ('CAINC1', '2'): 'population',
                  ('CAINC1', '3'): 'per_capita_personal_income', ('CAINC4', '50'): 'wages_and_salaries'}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_') or 'blank'


def clean_label(text):
    text = re.sub(r'\s*\d+/\s*$', '', str(text or '').strip())
    text = re.sub(r'\s*\([^)]*\)\s*$', '', text)
    return re.sub(r'^(equals|less|plus|addenda|addendum):\s*', '', text, flags=re.I).strip()


def finite(value, scale=1):
    """Apply a scale multiplier exactly (Decimal) and return a JSON-friendly number."""
    factor = Decimal(int(scale)) if scale >= 1 else Decimal(repr(scale))
    result = float(value * factor)
    if not math.isfinite(result):
        raise ValueError('Nonfinite BEA value')
    return int(result) if result.is_integer() and abs(result) < 2 ** 53 else result


def parse_unit(text, multiplier=None):
    base = str(text or '').strip().lower()
    scale = 1.0
    match = re.match(r'^(thousands|millions|billions) of (.*)$', base)
    if match:
        scale, base = PREFIX_SCALES[match.group(1)], match.group(2)
    if multiplier not in (None, ''):
        scale = 10.0 ** int(multiplier)
    if base not in UNIT_BASES:
        raise ValueError(f'{DATASET}: unmapped BEA unit {text!r}')
    return UNIT_BASES[base], scale


def year_period(label):
    match = re.fullmatch(r'(\d{4})(?::?Q([1-4])|M(\d{2}))?', label.strip())
    if not match:
        return None
    year = int(match.group(1))
    if match.group(2):
        month = 3 * int(match.group(2)) - 2
        end = (year + 1, 1) if month == 10 else (year, month + 3)
        return 'Q', f'{year}-{month:02d}-01', f'{end[0]}-{end[1]:02d}-01'
    if match.group(3):
        month = int(match.group(3))
        end = (year + 1, 1) if month == 12 else (year, month + 1)
        return 'M', f'{year}-{month:02d}-01', f'{end[0]}-{end[1]:02d}-01'
    return 'A', f'{year}-01-01', f'{year + 1}-01-01'


def run(context):
    coverage = getattr(context, 'raw_coverage', None)
    if coverage is None or not context.raw_inputs or coverage()['layout'] != 'shards':
        raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET})')
    info = coverage()
    params = context.parameters
    state = {'entities': set(), 'series': None, 'tables': {}, 'complete': info['complete'],
             'state_tables': params.get('state_tables', DEFAULT_STATE_TABLES),
             'nipa_calculation_types': params.get('nipa_calculation_types')}
    fallback = context.raw_receipt(0)['retrieved_at']
    for shard in context.raw_shards(0):
        url = str((shard.get('request') or {}).get('url') or shard.get('url') or '')
        path = url.split('?', 1)[0]
        name = path.rstrip('/').rsplit('/', 1)[-1]
        retrieved = shard.get('retrieved_at') or fallback
        if 'datasetname=regional' in url.lower():
            yield from county(context, shard, retrieved, state)
        elif name == 'TablesRegister.txt':
            for _, row in iter_rows([shard], {'format': 'csv'}):
                state['tables'][row['TableId']] = row['TableTitle']
        elif name == 'SeriesRegister.txt':
            yield from series_register(context, shard, retrieved, state)
        elif re.fullmatch(r'NipaData[AQM]\.txt', name):
            yield from nipa(context, shard, retrieved, state)
        elif '/regional/zip/' in path and name.lower().endswith('.zip'):
            yield from state_zip(context, shard, retrieved, state)
        else:
            raise ValueError(f'{DATASET}: unrecognized shard {shard["index"]}')


def entity(context, state, key, entity_type, label, locator, retrieved, **attributes):
    if key in state['entities']:
        return
    state['entities'].add(key)
    yield {'kind': 'entity', 'id': 'bea:' + digest(['entity', key]), 'entity_id': key, 'entity_type': entity_type,
           'label': label or key, 'observed_at': retrieved, 'evidence': context.raw_evidence(locator),
           'attributes': {'source_dataset': DATASET, 'complete_source': state['complete'], **attributes}}


def within(context, state, subject, target, locator, retrieved):
    key = ('within', subject, target)
    if key in state['entities']:
        return
    state['entities'].add(key)
    yield {'kind': 'assertion', 'id': 'bea:' + digest(['within', subject, target]), 'subject': subject,
           'predicate': 'within', 'object': target, 'observed_at': retrieved,
           'evidence': context.raw_evidence(locator), 'attributes': {'source_dataset': DATASET}}


def geography(context, state, fips, name, locator, retrieved):
    fips = str(fips).strip().strip('"').strip()
    name = re.sub(r'\s*\*+$', '', str(name or '').strip())
    yield from entity(context, state, 'geo:US', 'country', 'United States', locator, retrieved)
    if fips in ('00000', '00'):
        return 'geo:US'
    if len(fips) != 5 or not fips.isdigit():
        raise ValueError(f'{DATASET}: invalid BEA GeoFIPS {fips!r}')
    prefix = fips[:2]
    if fips.endswith('000') and prefix in STATE_CODES:
        key = 'geo:US:state:' + prefix
        yield from entity(context, state, key, 'state', name, locator, retrieved, fips=prefix)
        yield from within(context, state, key, 'geo:US', locator, retrieved)
        return key
    if fips.endswith('000') and int(prefix) >= 90:
        key = 'geo:US:bea_region:' + prefix
        yield from entity(context, state, key, 'aggregate_cohort', name, locator, retrieved, bea_region_fips=fips,
                          aggregate=True)
        yield from within(context, state, key, 'geo:US', locator, retrieved)
        return key
    key = 'geo:US:county:' + fips
    combined = '+' in name or (prefix == '51' and int(fips[2:]) >= 900)
    yield from entity(context, state, key, 'county', name, locator, retrieved, fips=fips,
                      bea_combined_area=combined)
    if prefix in STATE_CODES:
        yield from within(context, state, key, 'geo:US:state:' + prefix, locator, retrieved)
    return key


def value_of(text):
    text = str(text if text is not None else '').strip()
    if text in SUPPRESSED:
        return None, SUPPRESSED[text]
    if text in NOT_AVAILABLE:
        return None, None
    try:
        return Decimal(text.replace(',', '')), None
    except InvalidOperation:
        raise ValueError(f'{DATASET}: unrecognized BEA value {text[:20]!r}') from None


# ----- NIPA ---------------------------------------------------------------------------------------
def nipa_unit(metric_name, calculation, scale, label):
    multiplier = 10.0 ** -int(scale or 0)
    if calculation.startswith('Percent change'):
        return 'percent', 1.0
    if metric_name.startswith('Current Dollars') and 'Ratio' not in metric_name:
        return 'USD', multiplier
    if metric_name.startswith('Chained Dollars') and 'Ratio' not in metric_name:
        return 'USD_chained_2017', multiplier
    if metric_name in ('Fisher Price Index', 'Fisher Quantity Index', 'Fixed Weighted Price Index', 'Chained Dollar IPDs'):
        return 'index_2017_100', 1.0
    if metric_name in ('Quantity Contributions', 'Price Contributions'):
        return 'percentage_points', 1.0
    if metric_name == 'Current Dollar Shares' or (metric_name == 'Ratio' and 'percent' in label.lower()):
        return 'percent', 1.0
    if metric_name == 'Persons':
        return 'persons', multiplier
    if metric_name == 'Physical Quantity':
        return 'physical_units', multiplier
    if 'Ratio' in metric_name:
        return 'ratio', 1.0
    return 'source_units', multiplier


def series_register(context, shard, retrieved, state):
    state['series'] = {}
    for locator, row in iter_rows([shard], {'format': 'csv'}):
        code = row['%SeriesCode'].strip()
        label = clean_label(row['SeriesLabel'])
        lines = [part.split(':', 1) for part in (row.get('TableId:LineNo') or '').split('|') if ':' in part]
        unit, multiplier = nipa_unit(row['MetricName'], row['CalculationType'], row['DefaultScale'], row['SeriesLabel'])
        measure = slug(row['MetricName']) + ('' if row['CalculationType'] == 'Level' else '_' + slug(row['CalculationType']))
        table, line = lines[0] if lines else (None, None)
        title = state['tables'].get(table) or ''
        state['series'][code] = (slug(label) or 'nipa_series', unit, multiplier, measure, table, line,
                                 'not seasonally adjusted' in title.lower(), row['CalculationType'])
        yield from entity(context, state, 'bea:nipa:' + code, 'economic_series', row['SeriesLabel'].strip(), locator,
                          retrieved, series_code=code, metric_name=row['MetricName'],
                          calculation_type=row['CalculationType'], default_scale=int(row['DefaultScale'] or 0),
                          table_lines=[f'{t}:{n}' for t, n in lines], unit=unit, table_title=title or None)


def nipa(context, shard, retrieved, state):
    if state['series'] is None:
        raise ValueError(f'{DATASET}: SeriesRegister.txt must precede NIPA data shards')
    yield from entity(context, state, 'geo:US', 'country', 'United States', f'shard:{shard["index"]}', retrieved)
    allowed = state['nipa_calculation_types']
    for locator, row in iter_rows([shard], {'format': 'csv'}):
        code = row['%SeriesCode'].strip()
        spec = state['series'].get(code)
        if spec is None:
            raise ValueError(f'{DATASET}: NIPA series {code} missing from SeriesRegister')
        metric, unit, multiplier, measure, table, line, nsa, calculation = spec
        if allowed is not None and calculation not in allowed:
            continue
        period = year_period(row['Period'])
        if period is None:
            raise ValueError(f'{DATASET}: invalid NIPA period at {locator}')
        frequency, start, end = period
        value, missing = value_of(row['Value'])
        if value is None and missing is None:
            continue
        dimensions = {'series_code': code, 'table_id': table, 'line_number': line, 'frequency': frequency,
                      'measure': measure}
        if nsa:
            dimensions['seasonal_adjustment'] = 'not_seasonally_adjusted'
        record = {'kind': 'observation', 'id': 'bea:' + digest(['nipa', code, row['Period']]), 'observed_at': retrieved,
                  'evidence': context.raw_evidence(locator), 'subject': 'geo:US', 'metric': metric,
                  'value': None if value is None else finite(value, multiplier), 'unit': unit,
                  'valid_from': start, 'valid_to': end, 'dimensions': dimensions}
        if missing:
            record['missing_reason'] = missing
        yield record


# ----- state regional ZIPs ----------------------------------------------------------------------------
def table_metric(table, description):
    family = table[2:]
    label = clean_label(description)
    if family in INDUSTRY_TABLES:
        return INDUSTRY_TABLES[family], {'industry': slug(label)}
    if family in CATEGORY_TABLES:
        return CATEGORY_TABLES[family], {'category': slug(label)}
    return slug(label), {}


def state_zip(context, shard, retrieved, state):
    with zipfile.ZipFile(shard['path']) as archive:
        members = sorted(name for name in archive.namelist() if fnmatch.fnmatch(name, '*__ALL_AREAS_*.csv'))
    for member in members:
        table_name = member.rsplit('/', 1)[-1].split('__', 1)[0]
        if not any(fnmatch.fnmatch(table_name, pattern) for pattern in state['state_tables']):
            continue
        config = {'format': 'csv', 'members': [member], 'strict': False, 'encoding': 'latin-1'}
        for locator, row in iter_rows([shard], config):
            table = (row.get('TableName') or '').strip()
            if not table or not (row.get('LineCode') or '').strip():
                continue  # Footer notes.
            subject = yield from geography(context, state, row['GeoFIPS'], row['GeoName'], locator, retrieved)
            metric, extra = table_metric(table, row['Description'])
            unit, scale = parse_unit(row['Unit'])
            industry_code = (row.get('IndustryClassification') or '').strip()
            base = {'table': table, 'line_code': row['LineCode'].strip(), **extra}
            if 'industry' in extra and industry_code not in ('', '...'):
                base['naics'] = industry_code
            for column, text in row.items():
                if column is None or text is None:
                    continue
                period = year_period(column) if column[:1].isdigit() else None
                if period is None:
                    continue
                value, missing = value_of(text)
                if value is None and missing is None:
                    continue
                frequency, start, end = period
                record = {'kind': 'observation', 'id': 'bea:' + digest(['regional', table, base['line_code'],
                                                                         subject, column]),
                          'observed_at': retrieved, 'evidence': context.raw_evidence(locator), 'subject': subject,
                          'metric': metric, 'value': None if value is None else finite(value, scale), 'unit': unit,
                          'valid_from': start, 'valid_to': end, 'dimensions': {**base, 'frequency': frequency}}
                if scale != 1:
                    record['attributes'] = {'source_multiplier': int(scale)}
                if missing:
                    record['missing_reason'] = missing
                yield record


# ----- county Regional API ----------------------------------------------------------------------------
def county(context, shard, retrieved, state):
    with open(shard['path'], 'rb') as stream:
        payload = json.load(stream)
    api = payload.get('BEAAPI') if isinstance(payload, dict) else None
    results = (api or {}).get('Results')
    if not isinstance(results, dict) or results.get('Error') or api.get('Error') or not isinstance(results.get('Data'), list):
        raise ValueError(f'{DATASET}: BEA API error payload in shard {shard["index"]}')  # Never echo: payload holds UserID.
    statistic = results.get('Statistic') or ''
    for number, row in enumerate(results['Data']):
        locator = f'shard:{shard["index"]}/record:{number}'
        table, _, line = str(row['Code']).partition('-')
        subject = yield from geography(context, state, row['GeoFips'], row.get('GeoName'), locator, retrieved)
        if (table, line) in COUNTY_METRICS:
            metric, extra = COUNTY_METRICS[(table, line)], {}
        else:
            head, colon, tail = statistic.partition(':')
            if table[2:] in INDUSTRY_TABLES and colon:
                # e.g. "Private nonfarm earnings: Manufacturing", "Real GDP: Utilities"
                metric, extra = INDUSTRY_TABLES[table[2:]], {'industry': slug(clean_label(tail))}
            elif table[2:] in INDUSTRY_TABLES:
                # Summary lines inside industry tables, e.g. CAINC5N "Farm earnings (111-112)".
                metric, extra = slug(clean_label(statistic)), {}
            else:
                metric, extra = table_metric(table, tail if colon else statistic)
        unit, scale = parse_unit(row.get('CL_UNIT') or results.get('UnitOfMeasure'), row.get('UNIT_MULT'))
        period = year_period(str(row['TimePeriod']))
        if period is None:
            raise ValueError(f'{DATASET}: invalid county period at {locator}')
        value, missing = value_of(row.get('DataValue'))
        if value is None and missing is None:
            continue
        frequency, start, end = period
        record = {'kind': 'observation', 'id': 'bea:' + digest(['regional', table, line, subject, row['TimePeriod']]),
                  'observed_at': retrieved, 'evidence': context.raw_evidence(locator), 'subject': subject,
                  'metric': metric, 'value': None if value is None else finite(value, scale), 'unit': unit,
                  'valid_from': start, 'valid_to': end,
                  'dimensions': {'table': table, 'line_code': line, 'frequency': frequency, **extra}}
        if scale != 1:
            record['attributes'] = {'source_multiplier': int(scale)}
        if missing:
            record['missing_reason'] = missing
        yield record
