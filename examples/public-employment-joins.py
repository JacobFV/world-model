"""Measure WS-C coverage and join rates from the published artifacts. Deterministic keys only."""
import collections
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'data'
TARGETS = ('census_geography', 'census_population', 'usaspending', 'openfema', 'mit_election_returns', 'lehd_lodes')


def records(dataset):
    version = json.load(open(ROOT / dataset / 'manifests' / 'latest.json'))['version']
    path = ROOT / dataset / 'artifacts' / 'normalized' / version / 'records.jsonl.gz'
    with gzip.open(path, 'rt') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def geo_keys(dataset):
    keys = set()
    for record in records(dataset):
        if record['kind'] == 'entity':
            key = record.get('entity_id', record['id'])
            if key.startswith('geo:US:'):
                keys.add(key)
    return keys


def aspep():
    kinds = collections.Counter()
    metrics = collections.Counter()
    year_units = collections.defaultdict(set)
    year_obs = collections.Counter()
    year_basis = {}
    layouts = collections.Counter()
    flags = collections.Counter()
    types = collections.Counter()
    merged = collections.Counter()
    units, within = {}, collections.defaultdict(set)
    functions = collections.Counter()
    for record in records('census_aspep'):
        kinds[record['kind']] += 1
        if record['kind'] == 'entity' and record.get('entity_type') == 'government_agency':
            units[record['entity_id']] = record['attributes']
            types[record['attributes'].get('government_type')] += 1
        elif record['kind'] == 'assertion' and record.get('predicate') == 'within' \
                and record['subject'].startswith('aspep:unit:'):
            within[record['subject']].add(record['object'])
        elif record['kind'] == 'observation':
            metrics[record['metric']] += 1
            dimensions, attributes = record['dimensions'], record['attributes']
            year = dimensions.get('survey_year')
            year_obs[year] += 1
            year_units[year].add(record['subject'])
            year_basis[year] = dimensions.get('collection_basis')
            if attributes.get('record_layout'):
                layouts[(year, attributes['record_layout'])] += 1
            if 'data_flag_class' in attributes:
                flags[attributes['data_flag_class']] += 1
            if (attributes.get('source_rows_merged') or 1) > 1:
                merged[year] += 1
            if dimensions.get('function_code'):
                functions[dimensions['function_code']] += 1
    print('=== census_aspep')
    print('records', sum(kinds.values()), dict(kinds))
    print('observations by metric', dict(metrics.most_common()))
    print('unit entities', len(units), dict(types.most_common()))
    print('data flag classes', dict(flags.most_common()))
    print('distinct function codes', len(functions))
    print('observations merged from two source rows, by year', dict(merged))
    print()
    print(f'{"year":>5} {"basis":>20} {"units":>8} {"observations":>13} {"layout":>26}')
    for year in sorted(year_obs):
        layout = next((name for (y, name) in layouts if y == year), '-')
        print(f'{year:>5} {year_basis[year]:>20} {len(year_units[year]):>8,} {year_obs[year]:>13,} {layout:>26}')
    return units, within


def fedscope():
    kinds = collections.Counter()
    metrics = collections.Counter()
    cells = collections.Counter()
    periods = collections.Counter()
    cubes = collections.Counter()
    agencies, occupations, places, duty = set(), set(), set(), set()
    geo_pointed = collections.Counter()
    for record in records('opm_fedscope'):
        kinds[record['kind']] += 1
        if record['kind'] == 'entity':
            key = record.get('entity_id', record['id'])
            if key.startswith('opm:agency:'):
                agencies.add(key)
            elif key.startswith('occ:opm:'):
                occupations.add(key)
            elif key.startswith('geo:US:state:'):
                places.add(key)
            elif key.startswith('opm:duty_location:'):
                duty.add(key)
        elif record['kind'] == 'observation':
            metrics[record['metric']] += 1
            dimensions = record['dimensions']
            cells[dimensions.get('cell')] += 1
            cubes[dimensions.get('cube')] += 1
            periods[(dimensions.get('cube'), dimensions.get('period_label'))] += 1
            target = dimensions.get('duty_geography') or (record['subject'] if dimensions.get('cell') == 'location' else None)
            if target:
                geo_pointed[target] += 1
    print()
    print('=== opm_fedscope')
    print('records', sum(kinds.values()), dict(kinds))
    print('observations by metric', dict(metrics.most_common()))
    print('observations by cell', dict(cells.most_common()))
    print('observations by cube', dict(cubes.most_common()))
    print('agency entities', len(agencies), 'occupation entities', len(occupations),
          'state entities', len(places), 'unjoined duty locations', len(duty))
    employment = sorted(p for (cube, p) in periods if cube == 'employment')
    flows = sorted(p for (cube, p) in periods if cube != 'employment')
    print('employment periods', len(employment), employment[0], '..', employment[-1])
    print('flow periods', len(set(flows)), sorted(set(flows)))
    state_targets = {k for k in geo_pointed if k.startswith('geo:US:state:')}
    other_targets = {k for k in geo_pointed if not k.startswith('geo:US:')}
    print('distinct geography targets', len(geo_pointed), 'states', len(state_targets),
          'unjoined', len(other_targets))
    print('unjoined duty-location keys', sorted(other_targets)[:12], '...')
    observations_state = sum(v for k, v in geo_pointed.items() if k in state_targets)
    print(f'observations pointing at a geo:US:state key: {observations_state:,} of '
          f'{sum(geo_pointed.values()):,} geographic observations = '
          f'{observations_state / max(sum(geo_pointed.values()), 1):.4f}')
    return state_targets, other_targets, geo_pointed


def main():
    targets = {}
    for dataset in TARGETS:
        try:
            targets[dataset] = geo_keys(dataset)
        except Exception as error:
            targets[dataset] = None
            print(f'{dataset}: unavailable ({error.__class__.__name__})')
    for dataset, keys in targets.items():
        if keys is not None:
            print(f'{dataset}: {len(keys):,} geo:US entity keys published')

    units, within = aspep()
    state_targets, other_targets, _ = fedscope()

    print()
    print('=== measured join rates, deterministic keys only')
    county_units = {u for u, group in within.items() if any(g.startswith('geo:US:county:') for g in group)}
    state_units = {u for u, group in within.items() if u not in county_units}
    print(f'census_aspep units: {len(units):,}; with any containment {len(within):,} '
          f'({len(within) / max(len(units), 1):.4f}); county-level {len(county_units):,} '
          f'({len(county_units) / max(len(units), 1):.4f}); state-only {len(state_units):,}')
    pointed_counties = {g for group in within.values() for g in group if g.startswith('geo:US:county:')}
    pointed_states = {g for group in within.values() for g in group if g.startswith('geo:US:state:')}
    print(f'distinct county keys pointed at {len(pointed_counties):,}; state keys {len(pointed_states)}')
    for dataset, keys in targets.items():
        if keys is None:
            continue
        county_hit = len(pointed_counties & keys)
        state_hit = len(pointed_states & keys)
        reach = sum(1 for group in within.values() if group & keys)
        print(f'  census_aspep -> {dataset}: counties {county_hit}/{len(pointed_counties)} = '
              f'{county_hit / max(len(pointed_counties), 1):.4f}; states {state_hit}/{len(pointed_states)} = '
              f'{state_hit / max(len(pointed_states), 1):.4f}; units reaching it {reach:,}/{len(units):,} = '
              f'{reach / max(len(units), 1):.4f}')
        fed_hit = len(state_targets & keys)
        print(f'  opm_fedscope -> {dataset}: states {fed_hit}/{len(state_targets)} = '
              f'{fed_hit / max(len(state_targets), 1):.4f}')


if __name__ == '__main__':
    main()
