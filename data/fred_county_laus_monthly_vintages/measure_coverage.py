#!/usr/bin/env python3
"""Measure what the published fred_county_laus_monthly_vintages stage covers, and write coverage.json.

Nothing here is asserted from metadata: every number is counted from the normalized records (and the
raw receipt's list of series FRED refused with "does not exist in ALFRED").

* per family: series with rows, county-equivalents, states, series FRED refused, vintages per series
  (distinct ``realtime_start``), first and latest vintage, observation and first-release rows;
* per family and **reference month**: county-equivalents with a first release of a non-missing value
  (``dimensions.first_release``), against county-equivalents with any value for that month in any
  vintage. This is the number that decides how far back a real-time monthly panel can start, so it is
  kept at full monthly resolution in ``coverage.json`` and summarised by reference year for the
  README.

Usage::

    WORLD_MODEL_DATA=... python3 data/fred_county_laus_monthly_vintages/measure_coverage.py [--markdown]
"""
import argparse
import collections
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = 'fred_county_laus_monthly_vintages'


def data_root():
    if os.environ.get('WORLD_MODEL_DATA'):
        return Path(os.environ['WORLD_MODEL_DATA'])
    sys.path.insert(0, str(HERE.parents[1]))
    from worldmodel.resources import resource_roots
    return resource_roots()['data']


def measure(records, config, refused=()):
    families = config['families']
    series_family = {series_id: row[0] for series_id, row in config['series'].items()}
    vintages = collections.defaultdict(set)                  # series -> {realtime_start}
    counties = collections.defaultdict(set)                  # family -> {subject}
    first = collections.defaultdict(lambda: collections.defaultdict(set))    # family -> month -> {county}
    anyvalue = collections.defaultdict(lambda: collections.defaultdict(set))
    earliest_first = {}                                      # family -> earliest first-release vintage
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
        month = str(record['valid_from'])[:7]
        anyvalue[name][month].add(record['subject'])
        if dims.get('first_release'):
            first_rows[name] += 1
            first[name][month].add(record['subject'])
            if name not in earliest_first or dims['vintage'] < earliest_first[name]:
                earliest_first[name] = dims['vintage']
    out = {}
    for name in families:
        mine = [s for s, f in series_family.items() if f == name and s in vintages]
        per_series = [len(vintages[s]) for s in mine]
        starts = [min(vintages[s]) for s in mine]
        ends = [max(vintages[s]) for s in mine]
        months = sorted(set(first[name]) | set(anyvalue[name]))
        by_month = {month: {'first_release_counties': len(first[name].get(month, ())),
                            'counties_with_any_value': len(anyvalue[name].get(month, ()))}
                    for month in months}
        by_year = collections.defaultdict(list)
        for month, entry in by_month.items():
            by_year[month[:4]].append(entry['first_release_counties'])
        subjects = counties[name]
        out[name] = {
            'metric': families[name]['metric'], 'configured_series': families[name]['series'],
            'series_with_rows': len(per_series),
            'refused_not_in_alfred': sorted(s for s in refused if series_family.get(s) == name),
            'county_equivalents': len(subjects),
            'states_plus_dc': len({s.rsplit(':', 1)[1][:2] for s in subjects} - {'72'}),
            'observation_rows': rows[name], 'first_release_rows': first_rows[name],
            'vintages_per_series': {'min': min(per_series, default=0),
                                    'median': statistics.median(per_series) if per_series else 0,
                                    'max': max(per_series, default=0)},
            'first_vintage': {'earliest': min(starts, default=None), 'latest': max(starts, default=None)},
            'latest_vintage': max(ends, default=None),
            'earliest_first_release_vintage': earliest_first.get(name),
            'reference_months': {'first': months[0] if months else None, 'last': months[-1] if months else None,
                                 'with_a_first_release': sum(1 for m in by_month
                                                             if by_month[m]['first_release_counties'])},
            'first_month_with_a_first_release': next((m for m in months
                                                      if by_month[m]['first_release_counties']), None),
            'first_month_with_full_first_release': next(
                (m for m in months if by_month[m]['first_release_counties'] >= 0.95 * len(subjects)), None),
            'first_release_by_year': {year: {'months': len(values),
                                             'months_with_a_first_release': sum(1 for v in values if v),
                                             'counties_min': min(values), 'counties_median': statistics.median(values),
                                             'counties_max': max(values)}
                                      for year, values in sorted(by_year.items())},
            'first_release_by_month': by_month,
        }
    return out


def markdown(coverage):
    names = list(coverage)
    lines = ['| family | series | county-equivalents | states+DC | vintages/series (min-median-max) | '
             'first vintage | latest vintage | refused |',
             '| --- | ---: | ---: | ---: | --- | --- | --- | ---: |']
    for name, c in coverage.items():
        v = c['vintages_per_series']
        lines.append(f'| {name} | {c["series_with_rows"]:,} | {c["county_equivalents"]:,} | '
                     f'{c["states_plus_dc"]} | {v["min"]}-{v["median"]:g}-{v["max"]} | '
                     f'{c["first_vintage"]["earliest"]} | {c["latest_vintage"]} | '
                     f'{len(c["refused_not_in_alfred"])} |')
    years = sorted({y for c in coverage.values() for y in c['first_release_by_year']})
    lines += ['', '| reference year | ' + ' | '.join(f'{n} months / counties (median)' for n in names) + ' |',
              '| --- |' + ' ---: |' * len(names)]
    for year in years:
        cells = []
        for name in names:
            entry = coverage[name]['first_release_by_year'].get(year)
            cells.append('-' if not entry else
                         f'{entry["months_with_a_first_release"]}/{entry["months"]} · '
                         f'{entry["counties_median"]:,.0f}')
        lines.append(f'| {year} | ' + ' | '.join(cells) + ' |')
    # The archive opening, month by month: the numbers that decide where a monthly panel can start.
    opening = [m for m in sorted({m for c in coverage.values() for m in c['first_release_by_month']})
               if m < '2008-07']
    lines += ['', '| reference month | ' + ' | '.join(names) + ' |', '| --- |' + ' ---: |' * len(names)]
    for month in opening:
        cells = [f'{coverage[n]["first_release_by_month"].get(month, {}).get("first_release_counties", 0):,}'
                 for n in names]
        if any(cell != '0' for cell in cells):
            lines.append(f'| {month} | ' + ' | '.join(cells) + ' |')
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
