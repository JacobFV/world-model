#!/usr/bin/env python3
"""Measure what the published fred_county_vintages stage actually covers, and write coverage.json.

Nothing here is asserted from metadata: every number is counted from the normalized records (and the
raw receipt's list of series FRED refused with "does not exist in ALFRED").

* per family: series with rows, county-equivalents, states, series FRED refused, vintages per series
  (distinct ``realtime_start``), first and latest vintage;
* per family and reference year 2000-2024: county-equivalents with a **first release** of a non-missing
  value (``dimensions.first_release``), against county-equivalents with any value for that year in any
  vintage. For the quarterly establishment counts a year counts once all four quarters have a first
  release; ``any_quarter`` is reported beside it.

Usage::

    WORLD_MODEL_DATA=... python3 data/fred_county_vintages/measure_coverage.py [--markdown]
"""
import argparse
import collections
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = 'fred_county_vintages'
YEARS = range(2000, 2025)


def data_root():
    if os.environ.get('WORLD_MODEL_DATA'):
        return Path(os.environ['WORLD_MODEL_DATA'])
    sys.path.insert(0, str(HERE.parents[1]))
    from worldmodel.resources import resource_roots
    return resource_roots()['data']


def measure(records, config, refused=()):
    families = config['families']
    series_family = {series_id: row[0] for series_id, row in config['series'].items()}
    vintages = collections.defaultdict(set)                 # series -> {realtime_start}
    counties = collections.defaultdict(set)                  # family -> {subject}
    first = collections.defaultdict(lambda: collections.defaultdict(set))    # family -> (county, year) -> {period}
    anyvalue = collections.defaultdict(lambda: collections.defaultdict(set))
    rows = collections.Counter()
    first_rows = collections.Counter()
    for record in records:
        if record.get('kind') != 'observation':
            continue
        dims = record['dimensions']
        name, series_id = dims['family'], dims['series_id']
        rows[name] += 1
        vintages[series_id].add(dims['vintage'])
        counties[name].add(record['subject'])
        if record.get('value') is None:
            continue
        year = int(record['valid_from'][:4])
        if year not in YEARS:
            continue
        key = (record['subject'], year)
        anyvalue[name][key].add(record['valid_from'])
        if dims.get('first_release'):
            first_rows[name] += 1
            first[name][key].add(record['valid_from'])
    out = {}
    for name, family in families.items():
        per_series = [len(vintages[s]) for s, f in series_family.items() if f == name and s in vintages]
        starts = [min(vintages[s]) for s, f in series_family.items() if f == name and s in vintages]
        ends = [max(vintages[s]) for s, f in series_family.items() if f == name and s in vintages]
        periods = 4 if family['frequency'] == 'Q' else 1
        by_year = {}
        for year in YEARS:
            complete = sum(1 for (county, y), p in first[name].items() if y == year and len(p) >= periods)
            entry = {'first_release_counties': complete,
                     'counties_with_any_value': sum(1 for (county, y) in anyvalue[name] if y == year)}
            if periods > 1:
                entry['any_quarter'] = sum(1 for (county, y) in first[name] if y == year)
            by_year[str(year)] = entry
        subjects = counties[name]
        out[name] = {
            'metric': family['metric'], 'configured_series': family['series'],
            'series_with_rows': len(per_series),
            'refused_not_in_alfred': sorted(s for s in refused if series_family.get(s) == name),
            'county_equivalents': len(subjects),
            'states_plus_dc': len({s.rsplit(':', 1)[1][:2] for s in subjects} - {'72'}),
            'puerto_rico_municipios': sum(1 for s in subjects if s.rsplit(':', 1)[1].startswith('72')),
            'observation_rows': rows[name], 'first_release_rows': first_rows[name],
            'vintages_per_series': {'min': min(per_series, default=0), 'median': statistics.median(per_series)
                                    if per_series else 0, 'max': max(per_series, default=0)},
            'first_vintage': {'earliest': min(starts, default=None), 'latest': max(starts, default=None)},
            'latest_vintage': max(ends, default=None),
            'first_release_by_year': by_year,
        }
    return out


def markdown(coverage):
    lines = ['| family | series | county-equivalents | states+DC | vintages/series (min-median-max) | '
             'first vintage | refused |', '| --- | ---: | ---: | ---: | --- | --- | ---: |']
    for name, c in coverage.items():
        v = c['vintages_per_series']
        lines.append(f'| {name} | {c["series_with_rows"]:,} | {c["county_equivalents"]:,} | {c["states_plus_dc"]} | '
                     f'{v["min"]}-{v["median"]:g}-{v["max"]} | {c["first_vintage"]["earliest"]} | '
                     f'{len(c["refused_not_in_alfred"])} |')
    names = list(coverage)
    lines += ['', '| year | ' + ' | '.join(names) + ' |', '| --- |' + ' ---: |' * len(names)]
    for year in YEARS:
        cells = [f'{coverage[n]["first_release_by_year"][str(year)]["first_release_counties"]:,}' for n in names]
        lines.append(f'| {year} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--markdown', action='store_true')
    parser.add_argument('--output', type=Path, default=HERE / 'coverage.json')
    args = parser.parse_args()
    sys.path.insert(0, str(HERE.parents[1]))
    from worldmodel.store import Store
    store = Store(data_root(), raw_verify='size')   # `wm verify` does the full hash; this only counts.
    ref = store.latest(DATASET, 'normalized')
    config = json.loads((HERE / 'config.json').read_text())
    raw = store.latest_raw(DATASET)
    receipt = store.receipt(raw)
    skipped = ((receipt.get('source') or {}).get('acquisition') or {}).get('skipped') or {}
    refused = {((item.get('request') or {}).get('params') or {}).get('series_id') for item in skipped.values()}
    coverage = {'normalized_version': ref['version'], 'raw_artifact': raw['artifact'],
                'families': measure(store.records(ref, verify=False), config, refused)}
    args.output.write_text(json.dumps(coverage, indent=1) + '\n')
    print(f'wrote {args.output}', file=sys.stderr)
    if args.markdown:
        print(markdown(coverage['families']))


if __name__ == '__main__':
    main()
