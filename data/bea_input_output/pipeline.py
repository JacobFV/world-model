"""bea_input_output: BEA make/use, supply-use and requirements workbooks plus GDP-by-industry API tables.

Full sharded acquisitions only (the BEA sample was never acquired). Shards are
recognised from their recorded request URL: the static ``AllTablesIO.zip`` and
``AllTablesSUP.zip`` archives (workbooks nested inside ZIPs, read with the local
streaming XLSX reader) and one GDPbyIndustry API JSON response per table.
"""
from decimal import Decimal, InvalidOperation
import fnmatch
import json
import math
import re
import zipfile
from worldmodel.util import digest

DATASET = 'bea_input_output'
UNAVAILABLE = ('bea_input_output: no sample normalizer available; the BEA sample is unavailable. '
               'Acquire the full sharded artifact with `wm acquire bea_input_output --allow-network`.')

# Workbooks normalized by default. Sector tables are aggregations of summary tables;
# after-redefinition, purchaser-price and commodity/industry total-requirement
# variants are acquired but only normalized when listed in parameters.io_members.
DEFAULT_MEMBERS = [
    'IOMake_Before_Redefinitions_PRO_*_Summary.xlsx', 'IOMake_Before_Redefinitions_*_Detail*.xlsx',
    'IOUse_Before_Redefinitions_PRO_*_Summary.xlsx', 'IOUse_Before_Redefinitions_PRO_*_Detail.xlsx',
    'Supply_Tables_*_Summary.xlsx', 'Supply_*_DET.xlsx',
    'Use_Tables_Supply-Use_Framework_*_Summary.xlsx', 'Use_SUT_Framework_*_DET.xlsx',
    'CxI_DR_*_Summary.xlsx', 'CxI_DR_*_Detail.xlsx', 'IxI_TR_*_Summary.xlsx',
]

KINDS = {  # file prefix -> (kind, row axis, column axis, metric, framework)
    'iomake': ('make', 'industry', 'commodity', 'io_make_output', 'make_use'),
    'iouse': ('use', 'commodity', 'industry', 'io_use', 'make_use'),
    'supply': ('supply', 'commodity', 'industry', 'io_supply', 'supply_use'),
    'use': ('use', 'commodity', 'industry', 'io_use', 'supply_use'),
    'cxi': ('direct_requirements', 'commodity', 'industry', 'io_direct_requirement', 'make_use'),
    'cxc': ('total_requirements', 'commodity', 'commodity', 'io_total_requirement', 'supply_use'),
    'ixc': ('total_requirements', 'industry', 'commodity', 'io_total_requirement', 'supply_use'),
    'ixi': ('total_requirements', 'industry', 'industry', 'io_total_requirement', 'supply_use'),
}
ENTITY_TYPES = {'industry': 'industry', 'commodity': 'product'}
SUPPLY_ADJUSTMENTS = {'MCIF', 'MADJ', 'TRADE', 'TRANS', 'MDTY', 'TOP', 'SUB'}
SUPPRESSED = {'(D)', 'D', '(S)', '(NA)'}

GDP_BY_INDUSTRY_METRICS = {
    '1': 'value_added', '5': 'value_added_share_of_gdp', '6': 'value_added_component',
    '7': 'value_added_component_share', '8': 'real_value_added_quantity_index',
    '9': 'real_value_added_quantity_index_pct_change', '10': 'real_value_added', '11': 'value_added_price_index',
    '12': 'value_added_price_index_pct_change', '13': 'real_gdp_growth_contribution',
    '14': 'gdp_price_growth_contribution', '15': 'gross_output', '16': 'real_gross_output_quantity_index',
    '17': 'real_gross_output_quantity_index_pct_change', '18': 'gross_output_price_index',
    '19': 'gross_output_price_index_pct_change', '20': 'intermediate_inputs',
    '21': 'real_intermediate_inputs_quantity_index', '22': 'real_intermediate_inputs_quantity_index_pct_change',
    '23': 'intermediate_inputs_price_index', '24': 'intermediate_inputs_price_index_pct_change',
    '25': 'gross_output_component', '26': 'gross_output_component_share',
    '29': 'real_gross_output_growth_contribution', '30': 'gross_output_price_growth_contribution',
    '31': 'energy_inputs_quantity_index', '32': 'energy_inputs_quantity_growth_contribution',
    '33': 'energy_inputs_price_index', '34': 'energy_inputs_price_growth_contribution',
    '35': 'materials_inputs_quantity_index', '36': 'materials_inputs_quantity_growth_contribution',
    '37': 'materials_inputs_price_index', '38': 'materials_inputs_price_growth_contribution',
    '39': 'purchased_services_inputs_quantity_index', '40': 'purchased_services_inputs_quantity_growth_contribution',
    '41': 'purchased_services_inputs_price_index', '42': 'purchased_services_inputs_price_growth_contribution',
    '208': 'real_gross_output', '209': 'real_intermediate_inputs',
}
COMPONENT_TABLES = {'6', '7', '25', '26'}
QUARTERS = {'I': 1, 'II': 4, 'III': 7, 'IV': 10}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_') or 'blank'


def finite(value):
    if not math.isfinite(value):
        raise ValueError('Nonfinite BEA value')
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


def run(context):
    coverage = getattr(context, 'raw_coverage', None)
    if coverage is None or not context.raw_inputs:
        raise ValueError(UNAVAILABLE)
    info = coverage()
    if info['layout'] != 'shards':
        raise ValueError(UNAVAILABLE)
    patterns = context.parameters.get('io_members', DEFAULT_MEMBERS)
    state = {'entities': set(), 'industries': {}, 'complete': info['complete']}
    fallback = context.raw_receipt(0)['retrieved_at']
    for shard in context.raw_shards(0):
        url = str((shard.get('request') or {}).get('url') or shard.get('url') or '')
        retrieved = shard.get('retrieved_at') or fallback
        if 'gdpbyindustry' in url.lower():
            yield from gdp_by_industry(context, shard, retrieved, state)
        elif url.split('?', 1)[0].lower().endswith('.zip'):
            yield from workbooks(context, shard, retrieved, patterns, state)
        else:
            raise ValueError(f'{DATASET}: unrecognized shard {shard["index"]}')


def entity(context, state, key, entity_type, label, locator, retrieved, **attributes):
    if key in state['entities']:
        return
    state['entities'].add(key)
    yield {'kind': 'entity', 'id': 'bea_io:' + digest(['entity', key]), 'entity_id': key, 'entity_type': entity_type,
           'label': label or key, 'observed_at': retrieved, 'evidence': context.raw_evidence(locator),
           'attributes': {'source_dataset': DATASET, 'complete_source': state['complete'], **attributes}}


def describe(member):
    stem = re.sub(r'\s*\.xlsx$', '', member.rsplit('/', 1)[-1], flags=re.I).strip()
    parts = stem.lower().split('_')
    if parts[0] not in KINDS:
        raise ValueError(f'{DATASET}: unrecognized workbook {member}')
    kind, row_axis, column_axis, metric, framework = KINDS[parts[0]]
    level = ('detail' if {'detail', 'det'} & set(parts) else 'summary' if 'summary' in parts
             else 'sector' if 'sector' in parts else 'unknown')
    redefinitions = 'after' if 'after' in parts else 'before' if 'before' in parts else None
    price = 'purchasers' if 'pur' in parts else 'producers' if 'pro' in parts else None
    dimensions = {'level': level, 'framework': framework}
    if redefinitions:
        dimensions['redefinitions'] = redefinitions
    if price:
        dimensions['price_basis'] = price
    if kind == 'total_requirements':
        dimensions['matrix'] = parts[0]
    return {'kind': kind, 'row_axis': row_axis, 'column_axis': column_axis, 'metric': metric,
            'level': level, 'dimensions': dimensions, 'coefficient': kind.endswith('requirements'),
            'relations': kind in ('make', 'use') and framework == 'make_use' and redefinitions == 'before'
            and price in (None, 'producers')}


def code_class(code, axis):
    if code.startswith('T:') or code in ('VABAS', 'VAPRO') or (re.fullmatch(r'T0\d\d[A-Z]*', code)
                                                             and code not in ('T00OTOP', 'T00TOP', 'T00SUB')):
        return 'total'
    if re.fullmatch(r'V\d{3,5}', code) or code in ('T00OTOP', 'T00TOP', 'T00SUB'):
        return 'value_added'
    if re.fullmatch(r'F\d{2}[0-9A-Z]{1,4}', code):
        return 'final_use'
    if code in SUPPLY_ADJUSTMENTS:
        return 'supply_adjustment'
    return axis


def workbooks(context, shard, retrieved, patterns, state):
    from .helpers import open_workbook, sheets, shared_strings  # Local package import (runner snapshot).
    with zipfile.ZipFile(shard['path']) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith('.xlsx')
                   and any(fnmatch.fnmatch(name.rsplit('/', 1)[-1], p) for p in patterns)]
        for member in sorted(members):
            spec = describe(member)
            book = open_workbook(archive, member)
            strings = shared_strings(book)
            listed = [(name, path) for name, path in sheets(book) if re.fullmatch(r'\d{4}', name or '')]
            latest = max((int(name) for name, _ in listed), default=None)
            for name, path in listed:
                prefix = f'shard:{shard["index"]}/member:{member}/member:{path}'
                yield from sheet(context, book, path, strings, int(name), int(name) == latest, spec, prefix,
                                 retrieved, state)


def detect(buffered):
    from .helpers import column_key
    number, values = buffered[-1]
    code = (values.get('A') or '').strip()
    if not code or re.search(r'\s', code):
        return None
    for position, (header_number, header) in enumerate(buffered[:-1]):
        codes = {str(v).strip() for column, v in header.items() if column not in ('A', 'B')}
        if code not in codes:
            continue
        after = buffered[position + 1] if position + 1 < len(buffered) - 1 else None
        label_row = after[1] if after else (buffered[position - 1][1] if position else {})
        columns = {}
        for column in sorted(set(header) | set(label_row), key=column_key):
            if column in ('A', 'B'):
                continue
            label = (label_row.get(column) or '').strip()
            column_code = (header.get(column) or '').strip() or ('T:' + slug(label) if label else '')
            if column_code:
                columns[column] = (column_code, label or column_code)
        title = ' '.join(str(v) for _, row in buffered[:position] for v in row.values()).lower()
        scale = 1e9 if 'billions' in title else 1e3 if 'thousands' in title else 1e6
        return {'start': number, 'columns': columns, 'scale': scale}
    return None


def sheet(context, book, path, strings, year, latest, spec, prefix, retrieved, state):
    from .helpers import sheet_rows
    buffered, layout = [], None
    level = spec['level']
    for number, values in sheet_rows(book, path, strings):
        if layout is None:
            buffered.append((number, values))
            layout = detect(buffered)
            if layout is None:
                if len(buffered) > 20:
                    raise ValueError(f'{DATASET}: IO table header not found in {prefix}')
                continue
            pending = [item for item in buffered if item[0] >= layout['start']]
        else:
            pending = [(number, values)]
        for row_number, row in pending:
            code = (row.get('A') or '').strip()
            label = (row.get('B') or '').strip()
            if not code and not label:
                continue
            if not code:
                if label.lower().startswith('note'):
                    continue
                code = 'T:' + slug(label)
            elif re.search(r'\s', code):
                continue  # Footnotes and notes below the table.
            row_class = code_class(code, spec['row_axis'])
            row_key = f'bea_io:{level}:{row_class}:{code}'
            locator = f'{prefix}/row:{row_number}'
            yield from entity(context, state, row_key, ENTITY_TYPES.get(row_class, 'aggregate_cohort'), label or code,
                              locator, retrieved, bea_code=code, level=level, code_class=row_class,
                              aggregate=row_class == 'total')
            for column, (column_code, column_label) in layout['columns'].items():
                text = row.get(column)
                if text is None or text.strip() in ('', '...'):
                    continue
                text = text.strip()
                if text in SUPPRESSED:
                    value, missing = None, 'suppressed'
                else:
                    try:
                        number = Decimal(text.replace(',', ''))
                    except InvalidOperation:
                        raise ValueError(f'{DATASET}: unrecognized IO cell value at {locator}/col:{column}') from None
                    if number == 0:
                        continue  # BEA publishes sparse tables; zero cells are not emitted.
                    missing = None
                    value = finite(float(number if spec['coefficient'] else number * int(layout['scale'])))
                column_class = code_class(column_code, spec['column_axis'])
                column_key_ = f'bea_io:{level}:{column_class}:{column_code}'
                cell = f'{locator}/col:{column}'
                yield from entity(context, state, column_key_, ENTITY_TYPES.get(column_class, 'aggregate_cohort'),
                                  column_label, cell, retrieved, bea_code=column_code, level=level,
                                  code_class=column_class, aggregate=column_class == 'total')
                identity = [spec['metric'], spec['dimensions'], year, code, column_code]
                record = {'kind': 'observation', 'id': 'bea_io:' + digest(identity), 'observed_at': retrieved,
                          'evidence': context.raw_evidence(cell), 'subject': row_key, 'metric': spec['metric'],
                          'value': value, 'unit': 'USD_per_USD' if spec['coefficient'] else 'USD',
                          'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01',
                          'dimensions': {**spec['dimensions'], 'year': year, 'row_code': code,
                                         'column_code': column_code, 'counterpart': column_key_},
                          'attributes': {} if spec['coefficient'] else {'source_multiplier': int(layout['scale'])}}
                if missing:
                    record['missing_reason'] = missing
                yield record
                if (spec['relations'] and latest and value is not None
                        and {row_class, column_class} == {'industry', 'commodity'}):
                    industry, commodity = (row_key, column_key_) if row_class == 'industry' else (column_key_, row_key)
                    predicate = 'produces' if spec['kind'] == 'make' else 'consumes'
                    yield {'kind': 'assertion', 'id': 'bea_io:' + digest(['relation', identity]),
                           'observed_at': retrieved, 'evidence': context.raw_evidence(cell), 'subject': industry,
                           'predicate': predicate, 'object': commodity, 'valid_from': f'{year}-01-01',
                           'valid_to': f'{year + 1}-01-01',
                           'attributes': {'year': year, 'value_usd': value, 'metric': spec['metric'],
                                          **spec['dimensions']}}


def gdp_unit(note):
    text = (note or '').lower()
    if 'chain' in text and 'dollars' in text:
        return 'USD_chained_2017', 1e9 if 'billions' in text else 1e6 if 'millions' in text else 1
    if 'dollars' in text:
        return 'USD', 1e9 if 'billions' in text else 1e6 if 'millions' in text else 1
    if '=100' in text:
        return 'index_2017_100', 1
    if 'percentage points' in text:
        return 'percentage_points', 1
    if 'percent' in text:
        return 'percent', 1
    return 'source_units', 1


def gdp_by_industry(context, shard, retrieved, state):
    with open(shard['path'], 'rb') as stream:
        payload = json.load(stream)
    api = payload.get('BEAAPI') if isinstance(payload, dict) else None
    results = (api or {}).get('Results')
    if not isinstance(api, dict) or api.get('Error') or results is None:
        raise ValueError(f'{DATASET}: BEA API error payload in shard {shard["index"]}')
    results = results if isinstance(results, list) else [results]
    for position, result in enumerate(results):
        if result.get('Error') or not isinstance(result.get('Data'), list):
            raise ValueError(f'{DATASET}: BEA API error payload in shard {shard["index"]}')
        notes = {str(n.get('NoteRef')): n.get('NoteText') for n in result.get('Notes') or []}
        for number, row in enumerate(result['Data']):
            table = str(row['TableID'])
            locator = f'shard:{shard["index"]}/record:{number}' if position == 0 else \
                f'shard:{shard["index"]}/result:{position}/record:{number}'
            note_text = notes.get(table) or ''
            note = re.search(r'\[([^\]]+)\]', note_text)
            if note:
                unit, scale = gdp_unit(note.group(1))
            elif 'contributions' in note_text.lower():
                unit, scale = 'percentage_points', 1
            else:
                unit, scale = gdp_unit(None)
            code = str(row['Industry']).strip()
            description = str(row.get('IndustrYDescription', row.get('IndustryDescription')) or '').strip()
            key = 'bea_gdp_by_industry:' + code
            if table not in COMPONENT_TABLES:
                state['industries'].setdefault(code, description)
            yield from entity(context, state, key, 'industry', state['industries'].get(code, description), locator,
                              retrieved, bea_code=code)
            year, frequency = int(row['Year']), row['Frequency']
            if frequency == 'Q':
                month = QUARTERS[row['Quarter']]
                start = f'{year}-{month:02d}-01'
                end = f'{year + (month == 10)}-{1 if month == 10 else month + 3:02d}-01'
            else:
                start, end = f'{year}-01-01', f'{year + 1}-01-01'
            dimensions = {'table_id': table, 'frequency': frequency, 'industry': code}
            if table in COMPONENT_TABLES:
                component = slug(description)
                dimensions['component'] = 'total' if description == state['industries'].get(code) else component
            text = str(row['DataValue']).replace(',', '').strip()
            record = {'kind': 'observation', 'observed_at': retrieved, 'evidence': context.raw_evidence(locator),
                      'subject': key, 'metric': GDP_BY_INDUSTRY_METRICS.get(table, 'gdp_by_industry_table_' + table),
                      'unit': unit, 'valid_from': start, 'valid_to': end, 'dimensions': dimensions,
                      'id': 'bea_io:' + digest(['gdp_by_industry', table, frequency, start, code, description])}
            try:
                record['value'] = finite(float(Decimal(text) * int(scale)))
            except (InvalidOperation, ValueError):
                record['value'], record['missing_reason'] = None, 'suppressed_or_not_available'
            if scale != 1:
                record['attributes'] = {'source_multiplier': int(scale)}
            yield record
