"""ACS 5-year PUMS -> weighted PUMA aggregates (no microdata rows are emitted).

Shared by `acs_pums` (2020-2024, 2020 PUMAs) and `acs_pums_2019` (2015-2019, 2010 PUMAs); the release is selected by
`parameters.acs_end_year` and `parameters.puma_vintage`. Streams every `psam_pus*.csv` (person, weight PWGTP) and
`psam_hus*.csv` (housing unit, weight WGTP) member with a column-index CSV reader (only needed columns are decoded),
accumulating weighted sums per PUMA. Column aliases cover layout changes (STATE/ST, TYPEHUGQ/TYPE). Dollar amounts
are adjusted to dollars of the final survey year with ADJINC (/1e6) or ADJHSG (/1e6). Medians are weighted medians
from fine histograms (income $1,000 bins, rent $10 bins, value $5,000 bins, top bins open-ended). Estimates carry
no margins of error (replicate weights are not used) and inherit PUMS top-coding. Evidence cites the raw CSV member.
"""
import csv
import io
import zipfile
from bisect import bisect_right

AGE = [(0, '0-17'), (18, '18-24'), (25, '25-34'), (35, '35-44'), (45, '45-54'), (55, '55-64'), (65, '65+')]
INCOME_BINS = [(-10**9, 'loss_or_zero'), (1, '1-9999'), (10000, '10000-24999'), (25000, '25000-49999'), (50000, '50000-74999'),
               (75000, '75000-99999'), (100000, '100000-149999'), (150000, '150000-249999'), (250000, '250000+')]
RENT_BINS = [(0, '0-499'), (500, '500-999'), (1000, '1000-1499'), (1500, '1500-1999'), (2000, '2000-2999'), (3000, '3000+')]
VALUE_BINS = [(0, '0-99999'), (100000, '100000-199999'), (200000, '200000-299999'), (300000, '300000-499999'),
              (500000, '500000-749999'), (750000, '750000-999999'), (1000000, '1000000+')]
COMMUTE_BINS = [(0, '0-14'), (15, '15-29'), (30, '30-44'), (45, '45-59'), (60, '60+')]
MODES = {'1': 'car_truck_van', '2': 'bus', '3': 'subway_elevated', '4': 'long_distance_train', '5': 'light_rail_streetcar',
         '6': 'ferry', '7': 'taxi', '8': 'motorcycle', '9': 'bicycle', '10': 'walked', '11': 'worked_from_home', '12': 'other'}
ESR = {'1': 'employed', '2': 'employed', '3': 'unemployed', '4': 'armed_forces', '5': 'armed_forces', '6': 'not_in_labor_force'}
TENURE = {'1': 'owned_with_mortgage', '2': 'owned_free_and_clear', '3': 'rented', '4': 'occupied_without_rent'}
PERSON_COLUMNS = (('STATE', 'ST'), 'PUMA', 'PWGTP', 'ADJINC', 'AGEP', 'ESR', 'PINCP', 'WAGP', 'NAICSP', 'SOCP',
                  'JWTRNS', 'JWMNP', 'SCHL', 'POVPIP', 'SEX')
HOUSING_COLUMNS = (('STATE', 'ST'), 'PUMA', 'WGTP', 'ADJINC', 'ADJHSG', 'NP', ('TYPEHUGQ', 'TYPE'), 'TEN', 'VACS', 'HINCP',
                   'GRNTP', 'VALP', 'SMOCP', 'GRPIP', 'OCPIP')


def _bin(bins, value):
    return bins[bisect_right([b[0] for b in bins], value) - 1][1]


def _education(code):
    code = int(code)
    if code <= 15:
        return 'less_than_high_school'
    if code <= 17:
        return 'high_school_or_equivalent'
    if code <= 20:
        return 'some_college_or_associate'
    return 'bachelors' if code == 21 else 'graduate_or_professional'


class _Area:
    __slots__ = ('sums', 'hist')

    def __init__(self):
        self.sums, self.hist = {}, {}

    def add(self, key, weight):
        self.sums[key] = self.sums.get(key, 0.0) + weight

    def histogram(self, name, bucket, weight):
        table = self.hist.setdefault(name, {})
        table[bucket] = table.get(bucket, 0) + weight


def _median(table, width):
    total = sum(table.values())
    if not total:
        return None
    running = 0
    for bucket in sorted(table):
        running += table[bucket]
        if running >= total / 2:
            return bucket * width + width / 2
    return None


def _members(context, index):
    for shard in context.raw_shards(index):
        if not zipfile.is_zipfile(shard['path']):
            continue
        with zipfile.ZipFile(shard['path']) as archive:
            for name in sorted(archive.namelist()):
                base = name.rsplit('/', 1)[-1].lower()
                if base.startswith('psam_p') and base.endswith('.csv'):
                    yield shard, archive, name, 'person'
                elif base.startswith('psam_h') and base.endswith('.csv'):
                    yield shard, archive, name, 'housing'


def _rows(archive, name, columns):
    csv.field_size_limit(1 << 24)
    with archive.open(name) as binary, io.TextIOWrapper(binary, encoding='latin-1', newline='') as text:
        reader = csv.reader(text)
        header = next(reader)
        positions = []
        for column in columns:
            options = column if isinstance(column, tuple) else (column,)
            positions.append(next((header.index(option) for option in options if option in header), None))
        for values in reader:
            yield [values[p] if p is not None and p < len(values) else '' for p in positions]


def run(context):
    if not context.raw_inputs:
        raise ValueError('acs_pums: raw acquisition required')
    release = {'end_year': int(context.parameters.get('acs_end_year', 2024)),
               'puma_vintage': int(context.parameters.get('puma_vintage', 2020))}
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        areas, sources = {}, {}
        for shard, archive, name, kind in _members(context, index):
            locator = f'shard:{shard["index"]}/member:{name}'
            if kind == 'person':
                for row in _rows(archive, name, PERSON_COLUMNS):
                    key = row[0].zfill(2) + row[1].zfill(5)
                    sources.setdefault(key, locator)
                    _person(areas.setdefault(key, _Area()), row)
            else:
                for row in _rows(archive, name, HOUSING_COLUMNS):
                    key = row[0].zfill(2) + row[1].zfill(5)
                    sources.setdefault(key, locator)
                    _housing(areas.setdefault(key, _Area()), row)
        for key in sorted(areas):
            yield from _emit(context, index, observed, key, areas[key], sources[key], release)


def _person(area, row):
    state, puma, weight, adjinc, age, esr, pincp, wagp, naicsp, socp, jwtrns, jwmnp, schl, povpip, sex = row
    weight = float(weight)
    if not weight:
        return
    factor = float(adjinc) / 1e6 if adjinc else 1.0
    area.add(('population', 'all'), weight)
    area.add(('population_by_age', _bin(AGE, int(age))), weight)
    area.add(('population_by_sex', 'male' if sex == '1' else 'female'), weight)
    if esr:
        area.add(('population_16_plus_by_employment_status', ESR.get(esr, esr)), weight)
    if pincp:
        income = float(pincp) * factor
        area.add(('persons_15_plus_by_personal_income', _bin(INCOME_BINS, income)), weight)
        area.add(('sum_personal_income', 'all'), weight * income)
        area.add(('persons_with_income_field', 'all'), weight)
    if esr in ('1', '2'):
        if wagp:
            area.add(('sum_wage_income_employed', 'all'), weight * float(wagp) * factor)
        if naicsp and naicsp[:2].isdigit():
            area.add(('employed_by_industry_sector', naicsp[:2]), weight)
        if socp and socp[:2].isdigit():
            area.add(('employed_by_occupation_group', socp[:2]), weight)
    if jwtrns:
        area.add(('workers_by_commute_mode', MODES.get(jwtrns.lstrip('0'), jwtrns)), weight)
        if jwmnp:
            minutes = int(jwmnp)
            area.add(('commuters_by_travel_time', _bin(COMMUTE_BINS, minutes)), weight)
            area.add(('sum_commute_minutes', 'all'), weight * minutes)
            area.add(('commuters_with_travel_time', 'all'), weight)
    if schl and int(age) >= 25:
        area.add(('population_25_plus_by_education', _education(schl)), weight)
    if povpip:
        area.add(('poverty_universe', 'all'), weight)
        if int(povpip) < 100:
            area.add(('population_below_poverty', 'all'), weight)


def _housing(area, row):
    state, puma, weight, adjinc, adjhsg, np, unit_type, ten, vacs, hincp, grntp, valp, smocp, grpip, ocpip = row
    weight = float(weight)
    if not weight or unit_type != '1':
        return  # group quarters records carry no housing-unit weight
    income_factor = float(adjinc) / 1e6 if adjinc else 1.0
    housing_factor = float(adjhsg) / 1e6 if adjhsg else 1.0
    area.add(('housing_units', 'all'), weight)
    if np == '0' or vacs:
        area.add(('vacant_housing_units', 'all'), weight)
        return
    area.add(('households', 'all'), weight)
    if ten:
        area.add(('households_by_tenure', TENURE.get(ten, ten)), weight)
    if hincp:
        income = float(hincp) * income_factor
        area.add(('households_by_income', _bin(INCOME_BINS, income)), weight)
        area.add(('sum_household_income', 'all'), weight * income)
        area.histogram('household_income', max(0, min(int(income // 1000), 1000)), weight)
    if grntp:
        rent = float(grntp) * housing_factor
        area.add(('renter_households_by_gross_rent', _bin(RENT_BINS, rent)), weight)
        area.add(('sum_gross_rent', 'all'), weight * rent)
        area.add(('renters_with_cash_rent', 'all'), weight)
        area.histogram('gross_rent', min(int(rent // 10), 1000), weight)
        if grpip:
            burden = int(grpip)
            area.add(('renter_households_by_rent_burden', '50_plus' if burden >= 50 else '30_49' if burden >= 30 else 'under_30'), weight)
    if valp and ten in ('1', '2'):
        value = float(valp) * housing_factor
        area.add(('owner_households_by_home_value', _bin(VALUE_BINS, value)), weight)
        area.add(('sum_home_value', 'all'), weight * value)
        area.add(('owners_with_value', 'all'), weight)
        area.histogram('home_value', min(int(value // 5000), 1000), weight)
        if smocp:
            area.add(('sum_owner_costs', 'all'), weight * float(smocp) * housing_factor)
            area.add(('owners_with_costs', 'all'), weight)
        if ocpip:
            burden = int(ocpip)
            area.add(('owner_households_by_cost_burden', '50_plus' if burden >= 50 else '30_49' if burden >= 30 else 'under_30'), weight)


COUNTS = {'population': ('population', 'people', None), 'population_by_age': ('population', 'people', 'age_group'),
          'population_by_sex': ('population', 'people', 'sex'),
          'population_16_plus_by_employment_status': ('population_16_plus', 'people', 'employment_status'),
          'persons_15_plus_by_personal_income': ('persons_with_income', 'people', 'personal_income_bin_usd'),
          'employed_by_industry_sector': ('employed_population', 'people', 'naics_sector'),
          'employed_by_occupation_group': ('employed_population', 'people', 'soc_major_group'),
          'workers_by_commute_mode': ('workers', 'people', 'commute_mode'),
          'commuters_by_travel_time': ('commuters', 'people', 'travel_time_minutes'),
          'population_25_plus_by_education': ('population_25_plus', 'people', 'educational_attainment'),
          'population_below_poverty': ('population_below_poverty', 'people', None),
          'housing_units': ('housing_units', 'housing_units', None), 'vacant_housing_units': ('vacant_housing_units', 'housing_units', None),
          'households': ('households', 'households', None), 'households_by_tenure': ('households', 'households', 'tenure'),
          'households_by_income': ('households', 'households', 'household_income_bin_usd'),
          'renter_households_by_gross_rent': ('renter_households', 'households', 'gross_rent_bin_usd_per_month'),
          'renter_households_by_rent_burden': ('renter_households', 'households', 'gross_rent_pct_income'),
          'owner_households_by_home_value': ('owner_households', 'households', 'home_value_bin_usd'),
          'owner_households_by_cost_burden': ('owner_households', 'households', 'owner_costs_pct_income')}
MEANS = {'mean_personal_income': ('sum_personal_income', 'persons_with_income_field', 'USD'),
         'mean_wage_income_employed': ('sum_wage_income_employed', 'employed', 'USD'),
         'mean_commute_time': ('sum_commute_minutes', 'commuters_with_travel_time', 'minutes'),
         'mean_household_income': ('sum_household_income', 'households', 'USD'),
         'mean_gross_rent': ('sum_gross_rent', 'renters_with_cash_rent', 'USD_per_month'),
         'mean_home_value': ('sum_home_value', 'owners_with_value', 'USD'),
         'mean_selected_monthly_owner_costs': ('sum_owner_costs', 'owners_with_costs', 'USD_per_month')}
MEDIANS = {'household_income': ('median_household_income', 1000, 'USD'), 'gross_rent': ('median_gross_rent', 10, 'USD_per_month'),
           'home_value': ('median_home_value', 5000, 'USD')}


def _dollars(unit, year):
    """'USD' -> 'USD_2024', 'USD_per_month' -> 'USD_2024_per_month'; other units unchanged."""
    return unit.replace('USD', f'USD_{year}', 1) if unit.startswith('USD') else unit


def _emit(context, index, observed, key, area, locator, release):
    end, puma_vintage = release['end_year'], release['puma_vintage']
    period = (f'{end - 4}-01-01', f'{end + 1}-01-01')
    subject = f'geo:US:puma{puma_vintage % 100:02d}:{key}'
    prefix = f'pums{end % 100:02d}:{key}'
    evidence = context.raw_evidence(locator, index)
    dims0 = {'survey': 'acs_pums_5yr', 'period': f'{end - 4}-{end}', 'puma_vintage': puma_vintage}

    def obs(rid, metric, value, unit, dims, **attrs):
        return {'kind': 'observation', 'id': f'{prefix}:{rid}', 'subject': subject, 'metric': metric, 'value': value,
                'unit': _dollars(unit, end), 'valid_from': period[0], 'valid_to': period[1], 'observed_at': observed,
                'evidence': evidence, 'dimensions': {**dims0, **dims},
                'attributes': {'estimate': 'pums_weighted', 'dollar_year': end, **attrs}}

    yield {'kind': 'entity', 'id': 'pums:entity:' + subject, 'entity_id': subject, 'entity_type': 'location',
           'label': f'{puma_vintage} PUMA {key[2:]} (state {key[:2]})', 'observed_at': observed, 'evidence': evidence,
           'attributes': {'state_fips': key[:2], 'puma': key[2:], 'puma_vintage': puma_vintage}}
    yield {'kind': 'assertion', 'id': f'pums:within:{puma_vintage % 100:02d}:{key}', 'subject': subject, 'predicate': 'within',
           'object': 'geo:US:state:' + key[:2], 'observed_at': observed, 'evidence': evidence, 'attributes': {}}
    employed = sum(v for (name, part), v in area.sums.items() if name == 'population_16_plus_by_employment_status' and part == 'employed')
    totals = {name: value for (name, part), value in area.sums.items() if part == 'all'}
    totals['employed'] = employed
    for (name, part), value in sorted(area.sums.items()):
        if name not in COUNTS:
            continue
        metric, unit, dimension = COUNTS[name]
        dims = {dimension: part} if dimension else {}
        yield obs(f'{name}:{part}', metric, round(value), unit, dims)
    for metric, (numerator, denominator, unit) in MEANS.items():
        if totals.get(denominator) and numerator in totals:
            yield obs(metric, metric, round(totals[numerator] / totals[denominator], 2), unit, {}, statistic='weighted_mean')
    for name, (metric, width, unit) in MEDIANS.items():
        value = _median(area.hist.get(name, {}), width)
        if value is not None:
            yield obs(metric, metric, value, unit, {}, statistic='weighted_median_from_histogram', bin_width=width)
