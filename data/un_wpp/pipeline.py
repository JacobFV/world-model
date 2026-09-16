"""UN World Population Prospects 2024 (medium variant) -> compact evidence records.

Streams the two bulk CSV files (gzip) row by row. Countries/areas use ISO3 entity IDs
(`iso3:USA`); regional and income aggregates use `unwpp:loc:<LocID>` and are flagged
`aggregate`. Population counts published in thousands are converted to people.
WPP 2024 estimates cover 1950-2023; 2024-2100 values are medium-variant projections.
"""
import math

DATASET = 'un_wpp'
LAST_ESTIMATE_YEAR = 2023
READER = {'format': 'csv', 'encoding': 'utf-8-sig'}

# column -> (metric, unit, scale, dimensions, period) ; period 'midyear' = 1 July instant, 'year' = calendar-year flow
INDICATORS = {
    'TPopulation1July': ('population', 'people', 1000, {}, 'midyear'),
    'TPopulationMale1July': ('population', 'people', 1000, {'sex': 'male'}, 'midyear'),
    'TPopulationFemale1July': ('population', 'people', 1000, {'sex': 'female'}, 'midyear'),
    'PopDensity': ('population_density', 'people/km2', 1, {}, 'midyear'),
    'MedianAgePop': ('median_age', 'years', 1, {}, 'midyear'),
    'PopGrowthRate': ('population_growth_rate', 'percent', 1, {}, 'year'),
    'NatChange': ('natural_change', 'people', 1000, {}, 'year'),
    'Births': ('births', 'people', 1000, {}, 'year'),
    'Deaths': ('deaths', 'people', 1000, {}, 'year'),
    'NetMigrations': ('net_migration', 'people', 1000, {}, 'year'),
    'CBR': ('crude_birth_rate', 'per_1000_population', 1, {}, 'year'),
    'CDR': ('crude_death_rate', 'per_1000_population', 1, {}, 'year'),
    'CNMR': ('net_migration_rate', 'per_1000_population', 1, {}, 'year'),
    'TFR': ('total_fertility_rate', 'births_per_woman', 1, {}, 'year'),
    'SRB': ('sex_ratio_at_birth', 'males_per_100_females', 1, {}, 'year'),
    'LEx': ('life_expectancy_at_birth', 'years', 1, {}, 'year'),
    'LExMale': ('life_expectancy_at_birth', 'years', 1, {'sex': 'male'}, 'year'),
    'LExFemale': ('life_expectancy_at_birth', 'years', 1, {'sex': 'female'}, 'year'),
    'IMR': ('infant_mortality_rate', 'per_1000_live_births', 1, {}, 'year'),
    'Q5': ('under5_mortality_rate', 'per_1000_live_births', 1, {}, 'year'),
}
AGE_COLUMNS = {'PopMale': 'male', 'PopFemale': 'female'}


def _number(text):
    text = (text or '').strip()
    if not text:
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Nonfinite WPP value')
    return value


def _clean(value):
    value = round(value, 6)
    return int(value) if value.is_integer() else value


def _period(year, period):
    if period == 'midyear':
        return f'{year:04d}-07-01', f'{year:04d}-07-02'
    return f'{year:04d}-01-01', f'{year + 1:04d}-01-01'


def _location(row):
    iso3 = (row.get('ISO3_code') or '').strip()
    if iso3:
        return 'iso3:' + iso3, iso3, 'country', False
    loc = row['LocID'].strip()
    return 'unwpp:loc:' + loc, 'loc' + loc, 'location', True


def run(context):
    if not context.raw_inputs:
        raise ValueError('un_wpp: raw acquisition required (wm acquire un_wpp --allow-network)')
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        coverage = context.raw_coverage(index)
        if coverage.get('sampled'):
            raise ValueError('un_wpp has no sample adapter; acquire the full files')
        seen = set()  # at most a few hundred locations per raw input
        for locator, row in context.raw_rows(index, **READER):
            evidence = context.raw_evidence(locator, index)
            if row.get('Variant', 'Medium') != 'Medium':
                continue
            subject, key, entity_type, aggregate = _location(row)
            if subject not in seen:
                seen.add(subject)
                yield {'kind': 'entity', 'id': f'unwpp:entity:{key}', 'entity_id': subject,
                       'entity_type': entity_type, 'label': row['Location'].strip() or subject,
                       'observed_at': observed, 'evidence': evidence,
                       'attributes': {'aggregate': aggregate, 'unwpp_loc_id': int(row['LocID']),
                                      'location_type': row.get('LocTypeName') or None,
                                      'iso2': row.get('ISO2_code') or None}}
                parent = (row.get('ParentID') or '').strip()
                if parent:
                    # The parent aggregate is declared by its own rows in the same file.
                    yield {'kind': 'assertion', 'id': f'unwpp:within:{key}:loc{parent}', 'subject': subject,
                           'predicate': 'within', 'object': 'unwpp:loc:' + parent, 'observed_at': observed,
                           'evidence': evidence, 'attributes': {'relationship': 'wpp_geographic_parent'}}
            year = int(row['Time'])
            projection = year > LAST_ESTIMATE_YEAR
            if 'AgeGrp' in row:
                if aggregate and key != 'loc900':
                    continue  # age/sex detail for countries/areas and World only
                age = row['AgeGrp'].strip()
                start, end = _period(year, 'midyear')
                for column, sex in AGE_COLUMNS.items():
                    value = _number(row.get(column))
                    record = {'kind': 'observation', 'id': f'unwpp:{key}:pop:{sex}:{age}:{year}', 'subject': subject,
                              'metric': 'population', 'unit': 'people',
                              'value': None if value is None else _clean(value * 1000),
                              'valid_from': start, 'valid_to': end, 'observed_at': observed, 'evidence': evidence,
                              'dimensions': {'sex': sex, 'age_group': age, 'age_start': int(row['AgeGrpStart']),
                                             'age_span': int(row['AgeGrpSpan']), 'variant': 'medium'},
                              'attributes': {'projection': projection, 'source_column': column}}
                    if value is None:
                        record['missing_reason'] = 'source_missing'
                    yield record
                continue
            for column, (metric, unit, scale, dims, period) in INDICATORS.items():
                if column not in row:
                    continue
                value = _number(row[column])
                if value is None:
                    continue  # WPP leaves cells blank where an indicator is undefined
                start, end = _period(year, period)
                suffix = dims.get('sex', 'all')
                yield {'kind': 'observation', 'id': f'unwpp:{key}:{metric}:{suffix}:{year}', 'subject': subject,
                       'metric': metric, 'unit': unit, 'value': _clean(value * scale),
                       'valid_from': start, 'valid_to': end, 'observed_at': observed, 'evidence': evidence,
                       'dimensions': {**dims, 'variant': 'medium'},
                       'attributes': {'projection': projection, 'aggregate': aggregate, 'source_column': column}}
