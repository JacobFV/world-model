"""ACS 2020-2024 5-year estimates (Census Data API JSON tables) -> county/tract entities and estimate observations.

Each raw shard is one API response: a JSON array whose first row is the header. County responses use
`get=group(TABLE)` (estimate E, estimate annotation EA, margin of error M, MOE annotation MA per variable);
tract responses use explicit `GEO_ID,<var>E,<var>M,...` lists. One observation per estimate variable with the 90%
margin of error in `attributes.moe90`. Census jam values (-666666666 etc.) and annotations become missing values
with explicit reasons. Dollar values are in 2024 inflation-adjusted USD. Valid time is the 60-month collection
period 2020-01-01 .. 2025-01-01 (period estimates, not point-in-time values).
"""
import re

PERIOD = ('2020-01-01', '2025-01-01')
VAR = re.compile(r'^([BC]\d{5}[A-Z]?)_(\d{3})E$')
UNITS = {'B01003': 'people', 'B01001': 'people', 'B01002': 'years', 'B19013': 'USD_2024', 'B19001': 'households',
         'B19301': 'USD_2024', 'B17001': 'people', 'B15003': 'people', 'B23025': 'people', 'B08301': 'workers',
         'B08303': 'workers', 'B25001': 'housing_units', 'B25003': 'housing_units', 'B25064': 'USD_2024_per_month',
         'B25077': 'USD_2024', 'C24030': 'people', 'C24010': 'people'}
NAMES = {'B01003_001': 'population', 'B01002_001': 'median_age', 'B19013_001': 'median_household_income',
         'B19301_001': 'per_capita_income', 'B25077_001': 'median_home_value', 'B25064_001': 'median_gross_rent',
         'B25001_001': 'housing_units', 'B25003_001': 'occupied_housing_units', 'B25003_002': 'owner_occupied_housing_units',
         'B25003_003': 'renter_occupied_housing_units', 'B23025_002': 'labor_force', 'B23025_004': 'employed_civilian_labor_force',
         'B23025_005': 'unemployed_civilian_labor_force', 'B17001_001': 'poverty_status_universe',
         'B17001_002': 'population_below_poverty', 'B15003_001': 'population_25_plus', 'B08301_001': 'workers_16_plus',
         'B08303_001': 'workers_not_working_from_home'}
JAM = {-666666666: 'estimate_not_computable', -999999999: 'estimate_not_computable', -888888888: 'not_applicable',
       -222222222: 'moe_not_computable', -333333333: 'median_in_open_ended_interval', -555555555: 'estimate_controlled_no_moe',
       -111111111: 'median_in_lowest_interval'}


def _number(value):
    if value in (None, '', 'null'):
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def run(context):
    if not context.raw_inputs:
        raise ValueError('acs_5yr_tables: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        emitted = set()  # ~3.2k counties, ~85k tracts, 52 states
        rows = context.raw_rows(index, format='json', table_header=True, max_json_bytes=268435456)
        for locator, row in rows:
            state, county, tract = row.get('state'), row.get('county'), row.get('tract')
            if not state or not county:
                continue
            evidence = context.raw_evidence(locator, index)
            if tract:
                geoid, layer, typ = state + county + tract, 'tract', 'location'
                parent = 'geo:US:county:' + state + county
            else:
                geoid, layer, typ = state + county, 'county', 'county'
                parent = 'geo:US:state:' + state
            subject = f'geo:US:{layer}:{geoid}'
            if subject not in emitted:
                emitted.add(subject)
                yield {'kind': 'entity', 'id': 'acs:entity:' + subject, 'entity_id': subject, 'entity_type': typ,
                       'label': row.get('NAME') or subject, 'observed_at': observed, 'evidence': evidence,
                       'attributes': {'geo_id': row.get('GEO_ID'), 'geography_vintage': 2024}}
                yield {'kind': 'assertion', 'id': 'acs:within:' + subject, 'subject': subject, 'predicate': 'within',
                       'object': parent, 'observed_at': observed, 'evidence': evidence, 'attributes': {'geography_vintage': 2024}}
            for column, raw in row.items():
                match = VAR.match(column)
                if not match:
                    continue
                table, line = match.groups()
                variable = f'{table}_{line}'
                estimate = _number(raw)
                moe = _number(row.get(variable + 'M'))
                attrs = {}
                reason = None
                if estimate is not None and estimate in JAM:
                    reason, estimate = JAM[estimate], None
                annotation = row.get(variable + 'EA')
                if annotation not in (None, ''):
                    attrs['estimate_annotation'] = annotation
                if moe is not None and moe in JAM:
                    attrs['moe_status'] = JAM[moe]
                elif moe is not None:
                    attrs['moe90'] = moe
                if row.get(variable + 'MA') not in (None, ''):
                    attrs['moe_annotation'] = row[variable + 'MA']
                record = {'kind': 'observation', 'id': f'acs5y24:{layer}:{geoid}:{variable}', 'subject': subject,
                          'metric': NAMES.get(variable, f'acs_{table.lower()}_{line}'), 'value': estimate,
                          'unit': UNITS.get(table, 'count'), 'valid_from': PERIOD[0], 'valid_to': PERIOD[1],
                          'observed_at': observed, 'evidence': evidence,
                          'dimensions': {'survey': 'acs5', 'period': '2020-2024', 'table': table, 'variable': variable + 'E'},
                          'attributes': attrs}
                if estimate is None:
                    record['missing_reason'] = reason or 'source_missing'
                yield record
