"""Three questions the catalog could not answer before WS-C, answered from published artifacts.

Q1  Federal vs state-and-local public employment per 1,000 residents, by state.
    opm_fedscope (location cells) x census_aspep (state and local units) x census_population.
Q2  Sworn police employment and March payroll per 1,000 residents, by county area.
    census_aspep (function code 062, county/municipal/township units) x census_population.
Q3  Which local-government functions grew and which shrank between two Censuses of Governments.
    census_aspep alone, 2012 vs 2022.
"""
import collections
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'data'
LOCAL_TYPES = {'county_government', 'municipal_government', 'township_government', 'special_district',
               'school_district'}


def records(dataset, stage='normalized'):
    version = json.load(open(ROOT / dataset / 'manifests' / 'latest.json'))['version']
    path = ROOT / dataset / 'artifacts' / stage / version / 'records.jsonl.gz'
    opener, path = (gzip.open, path) if path.exists() else (open, path.with_suffix(''))
    with opener(path, 'rt') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def state_population(year):
    out = {}
    for record in records('census_population'):
        if (record['kind'] == 'observation' and record['metric'] == 'population'
                and record['subject'].startswith('geo:US:state:')
                and record['dimensions'].keys() == {'vintage'}
                and record['valid_from'] == f'{year}-07-01'):
            out[record['subject']] = record['value']
    return out


def county_population(year):
    out = {}
    for record in records('census_population'):
        if (record['kind'] == 'observation' and record['metric'] == 'population'
                and record['subject'].startswith('geo:US:county:')
                and record['dimensions'].keys() == {'vintage'}
                and record['valid_from'] == f'{year}-07-01'):
            out[record['subject']] = record['value']
    return out


def aspep_units():
    """unit key -> (government_type, state key, county key)."""
    units, within = {}, {}
    for record in records('census_aspep'):
        if record['kind'] == 'entity' and record.get('entity_type') == 'government_agency':
            attributes = record['attributes']
            units[record['entity_id']] = (attributes.get('government_type'), attributes.get('fips_state'),
                                          attributes.get('fips_county'))
        elif record['kind'] == 'assertion' and record.get('predicate') == 'within':
            within.setdefault(record['subject'], set()).add(record['object'])
    return units, within


def q1(federal_month, aspep_year, population_year):
    units, _ = aspep_units()
    federal = collections.Counter()
    federal_pay = collections.Counter()
    federal_paid = collections.Counter()
    for record in records('opm_fedscope'):
        if record['kind'] != 'observation' or record['dimensions'].get('cell') != 'location':
            continue
        if record['dimensions'].get('period_label') != federal_month:
            continue
        if record['metric'] == 'federal_employees':
            federal[record['subject']] += record['value']
        elif record['metric'] == 'federal_annual_salary_total':
            federal_pay[record['subject']] += record['value']
        elif record['metric'] == 'federal_employees_with_published_salary':
            federal_paid[record['subject']] += record['value']
    state_local = collections.Counter()
    state_local_pay = collections.Counter()
    for record in records('census_aspep'):
        if record['kind'] != 'observation' or record['dimensions'].get('survey_year') != aspep_year:
            continue
        if record['dimensions'].get('function_code') != '000':
            continue
        if record['dimensions'].get('employment_status') not in ('full_time', 'part_time'):
            continue
        state = (units.get(record['subject'], (None, None, None))[1] or '')
        if not state:
            continue
        key = f'geo:US:state:{state}'
        if record['metric'] == 'government_employees':
            state_local[key] += record['value']
        elif record['metric'] == 'government_payroll':
            state_local_pay[key] += record['value']
    population = state_population(population_year)
    rows = []
    for key in sorted(set(federal) | set(state_local)):
        people = population.get(key)
        if not people or not key.startswith('geo:US:state:'):
            continue
        rows.append((key, federal[key], state_local[key], people,
                     1000 * federal[key] / people, 1000 * state_local[key] / people,
                     federal_pay[key] / max(federal_paid[key], 1),
                     12 * state_local_pay[key] / max(state_local[key], 1)))
    rows.sort(key=lambda r: -r[4])
    print(f'=== Q1  federal ({federal_month}) vs state+local (ASPEP {aspep_year}) employment per 1,000 residents '
          f'({population_year} population), {len(rows)} states')
    print(f'{"state":16}{"federal":>10}{"st+local":>10}{"fed/1k":>9}{"sl/1k":>9}{"fed mean $":>12}{"sl x12 $":>12}')
    for row in rows[:8] + [None] + rows[-5:]:
        if row is None:
            print('  ...')
            continue
        key, fed, sl, people, fed_rate, sl_rate, fed_mean, sl_mean = row
        print(f'{key:16}{fed:>10,}{sl:>10,}{fed_rate:>9.2f}{sl_rate:>9.2f}{fed_mean:>12,.0f}{sl_mean:>12,.0f}')
    total_fed, total_sl = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print('  "fed mean $" divides the published annual salary total by employees whose salary was published.')
    print('  "sl x12 $" is the March monthly-equivalent payroll per employee times 12: an annualization, not a')
    print('  published annual figure, and it includes part-time employees in both numerator and denominator.')
    print(f'  totals: federal {total_fed:,} state+local {total_sl:,} '
          f'ratio {total_sl / max(total_fed, 1):.2f} state+local per federal employee')
    return rows


def q2(year, population_year, minimum_population=250000, only_county_governments=False):
    units, _ = aspep_units()
    police = collections.Counter()
    payroll = collections.Counter()
    for record in records('census_aspep'):
        if record['kind'] != 'observation' or record['dimensions'].get('survey_year') != year:
            continue
        if record['dimensions'].get('function_code') != '062':
            continue
        if record['dimensions'].get('employment_status') not in ('full_time', 'part_time'):
            continue
        government_type, _state, county = units.get(record['subject'], (None, None, None))
        if not county:
            continue
        if only_county_governments and government_type != 'county_government':
            continue
        key = f'geo:US:county:{county}'
        if record['metric'] == 'government_employees':
            police[key] += record['value']
        elif record['metric'] == 'government_payroll':
            payroll[key] += record['value']
    population = county_population(population_year)
    rows = [(key, police[key], payroll[key], population[key], 1000 * police[key] / population[key])
            for key in police if population.get(key, 0) >= minimum_population]
    rows.sort(key=lambda r: -r[4])
    print()
    scope = ('county governments only (sheriff offices; the assignment is exact by construction)'
             if only_county_governments
             else 'every local government assigned to the county (a consolidated city books all of its '
                  'officers to one county, so New York County is an artifact, not a rate)')
    print(f'=== Q2  sworn police employed by {scope}, ASPEP {year}, per 1,000 county residents, county '
          f'areas above {minimum_population:,} residents ({len(rows)} of {len(police)} counties with any '
          f'sworn police)')
    print(f'{"county":24}{"officers":>10}{"pop":>12}{"per 1k":>9}{"mean monthly $":>16}')
    for row in rows[:8] + [None] + rows[-5:]:
        if row is None:
            print('  ...')
            continue
        key, officers, pay, people, rate = row
        print(f'{key:24}{officers:>10,}{people:>12,}{rate:>9.2f}{pay / max(officers, 1):>16,.0f}')
    print(f'  all county areas with sworn police in {year}: {len(police):,}; '
          f'total sworn officers {sum(police.values()):,}')
    return rows


def q3(first, last):
    units, _ = aspep_units()
    totals = {first: collections.Counter(), last: collections.Counter()}
    for record in records('census_aspep'):
        if record['kind'] != 'observation' or record['metric'] != 'government_employees':
            continue
        year = record['dimensions'].get('survey_year')
        if year not in totals or record['dimensions'].get('employment_status') != 'full_time':
            continue
        if record['dimensions'].get('government_level') != 'local':
            continue
        function = record['dimensions'].get('function') or record['dimensions'].get('function_code')
        totals[year][function] += record['value']
    rows = []
    for function in sorted(set(totals[first]) | set(totals[last])):
        before, after = totals[first][function], totals[last][function]
        if before < 20000:
            continue
        rows.append((function, before, after, 100 * (after - before) / before))
    rows.sort(key=lambda r: r[3])
    print()
    print(f'=== Q3  full-time local-government employment by function, Census of Governments {first} vs {last} '
          f'(functions above 20,000 in {first})')
    print(f'{"function":52}{first:>12}{last:>12}{"change %":>10}')
    for function, before, after, change in rows:
        print(f'{function[:51]:52}{before:>12,}{after:>12,}{change:>10.1f}')
    return rows


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if which in ('all', 'q1'):
        q1(sys.argv[2] if len(sys.argv) > 2 else '202203', 2022, 2022)
    if which in ('all', 'q2'):
        q2(2023, 2023)
        q2(2023, 2023, only_county_governments=True)
    if which in ('all', 'q3'):
        q3(2012, 2022)
