#!/usr/bin/env python3
"""Regenerate config.json: the county-level FRED series families that carry ALFRED vintages.

Three things this script establishes rather than assumes.

**Which series are in a family.** Each family is one FRED release (its own catalogue, fetched with
``release/series``) narrowed either by the publisher's series-id code or, for the Census population
aliases that have no code, by the release's measure title. Ids are listed from FRED, never built from
a template and hoped for.

**Which county a series describes.** The FIPS code comes from FRED's published geography: GeoFRED's
``regional/data`` cross-sections, requested for every reference period of the family's series group,
return ``{series_id, code, region}`` for every county-equivalent. Where the publisher's own series id
also carries the area code (BLS LAUS ``LAUCN<fips>...``, BEA ``PCPI<fips>``/``PI<fips>``/``GDPALL<fips>``,
BLS QCEW ``ENU<fips>...``), that code is parsed from the id and must equal GeoFRED's for every series
both describe. GeoFRED's map layer predates two 2015 Census renames and still publishes 46113 and
02270 for Oglala Lakota County, SD and Kusilvak Census Area, AK; those two disagreements (and only
those, listed in ``CODE_CHANGES`` with their Census source) resolve to the id's new code, and a
GeoFRED-only series under an old code is relabelled to the successor, since a rename leaves the
territory unchanged and a place should have one subject across families. Any other
disagreement excludes the series and lists it. A series GeoFRED omits (for example the 2022
Connecticut planning regions) keeps the id's code and is marked ``fips_source = 'series_id'``. A
series with neither is skipped and listed. Titles are never parsed for a code.

**How deep each family goes, and in what units.** One series per state per family is probed with
``series/vintagedates`` (depth) and with ``series`` over the whole real-time window (units as published
in each vintage). A family whose sampled units change across vintages (real GDP is rebased from chained
2012 to chained 2017 dollars in 2023) has its units history fetched for **every** series, and the
history is stored once per family only if every series shares it. Exact per-series vintage counts
are measured from the acquired payload by ``measure_coverage.py``, not asserted here.

ALFRED membership is not probed per series: the acquisition requests the full real-time window and
FRED answers ``400 "does not exist in ALFRED"`` for a series it does not archive, which the
declaration records as skipped (``skip_statuses: [400]``). ``measure_coverage.py`` reports them.

Usage::

    FRED_API_KEY=... python3 data/fred_county_vintages/build_config.py          # write config.json
    python3 data/fred_county_vintages/build_config.py --emit-parameters         # for dataset.json
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API = 'https://api.stlouisfed.org/'
FIRST_REALTIME, LAST_REALTIME = '1776-07-04', '9999-12-31'

#: One entry per family. ``id_pattern`` selects members from the release catalogue by the publisher's
#: series-id code and captures the area code; ``measures`` selects by the catalogue title's measure
#: (the text before " in <place>") when the ids carry no code. ``geofred_key`` maps a member id to the
#: id GeoFRED lists for it: GeoFRED reports annual LAUS cross-sections under the monthly base series,
#: so the annual form's check key drops FRED's trailing ``A`` (the check is on the code, and a key that
#: GeoFRED does not list simply leaves the series with ``fips_source = 'series_id'``).
FAMILIES = {
    'population': {
        'release_id': 119, 'frequency': 'A', 'measures': ['Resident Population'],
        'metric': 'population', 'program': 'census_population_estimates',
        'originating_source': 'U.S. Census Bureau, Population Estimates Program (county resident population)'},
    'per_capita_personal_income': {
        'release_id': 175, 'frequency': 'A', 'id_pattern': r'^PCPI(\d{5})$',
        'metric': 'per_capita_personal_income', 'program': 'bea_regional',
        'originating_source': 'U.S. Bureau of Economic Analysis, Personal Income by County (CAINC1 line 3)'},
    'personal_income': {
        'release_id': 175, 'frequency': 'A', 'id_pattern': r'^PI(\d{5})$',
        'metric': 'personal_income', 'program': 'bea_regional',
        'originating_source': 'U.S. Bureau of Economic Analysis, Personal Income by County (CAINC1 line 1)'},
    'gdp': {
        'release_id': 397, 'frequency': 'A', 'id_pattern': r'^GDPALL(\d{5})$',
        'metric': 'gdp', 'program': 'bea_regional',
        'originating_source': 'U.S. Bureau of Economic Analysis, GDP by County (all industries, current dollars)'},
    'real_gdp': {
        'release_id': 397, 'frequency': 'A', 'id_pattern': r'^REALGDPALL(\d{5})$',
        'metric': 'real_gdp', 'program': 'bea_regional',
        'originating_source': 'U.S. Bureau of Economic Analysis, GDP by County (all industries, chained dollars)'},
    'private_establishments': {
        'release_id': 362, 'frequency': 'Q', 'id_pattern': r'^ENU(\d{5})20510$',
        'metric': 'establishment_count', 'program': 'qcew', 'ownership': 'private',
        'originating_source': 'U.S. Bureau of Labor Statistics, Quarterly Census of Employment and Wages '
                              '(private establishments, all industries)'},
    'laus_unemployment_rate': {
        'release_id': 116, 'frequency': 'A', 'id_pattern': r'^LAUCN(\d{5})0{8}03A$',
        'metric': 'unemployment_rate', 'program': 'laus', 'geofred_key': 'drop_trailing_A',
        'originating_source': 'U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics (annual average)'},
    'laus_unemployed': {
        'release_id': 116, 'frequency': 'A', 'id_pattern': r'^LAUCN(\d{5})0{8}04A$',
        'metric': 'unemployed', 'program': 'laus', 'geofred_key': 'drop_trailing_A',
        'originating_source': 'U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics (annual average)'},
    'laus_employed': {
        'release_id': 116, 'frequency': 'A', 'id_pattern': r'^LAUCN(\d{5})0{8}05A$',
        'metric': 'employment', 'program': 'laus', 'geofred_key': 'drop_trailing_A',
        'originating_source': 'U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics (annual average)'},
    'laus_labor_force': {
        'release_id': 116, 'frequency': 'A', 'id_pattern': r'^LAUCN(\d{5})0{8}06A$',
        'metric': 'labor_force', 'program': 'laus', 'geofred_key': 'drop_trailing_A',
        'originating_source': 'U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics (annual average)'},
}

#: FRED unit strings -> (unit, multiplier, base_period). Anything else stops the build.
UNITS = {
    'Thousands of Persons': ('persons', 1000.0, None),
    'Persons': ('persons', 1.0, None),
    'Percent': ('percent', 1.0, None),
    'Dollars': ('USD', 1.0, None),
    'Thousands of Dollars': ('USD', 1000.0, None),
    'Thousands of U.S. Dollars': ('USD', 1000.0, None),
    'Establishments': ('establishments', 1.0, None),
}
CHAINED = re.compile(r'^Thousands of Chained (\d{4}) U\.S\. Dollars$')
PLACE = re.compile(r'^(?P<measure>.+?) (?:in|for) (?:the )?(?P<place>.+?)(?: \(DISCONTINUED\))?$')
FIPS = re.compile(r'^\d{5}$')

#: Census county code changes that GeoFRED's map layer predates. GeoFRED still publishes the old code
#: for the renamed county's current series (``PI46102`` -> 46113), while the publisher's id carries the
#: new one. Only these disagreements are resolved (in favour of the id's code); any other disagreement
#: excludes the series and lists it. Source: Census Bureau, "Substantial Changes to Counties and County
#: Equivalent Entities: 1970-Present"
#: (https://www.census.gov/programs-surveys/geography/technical-documentation/county-changes.html).
CODE_CHANGES = {
    '46113': {'successor': '46102', 'effective': '2015-05-01',
              'change': 'Shannon County, SD renamed Oglala Lakota County, SD'},
    '02270': {'successor': '02158', 'effective': '2015-07-01',
              'change': 'Wade Hampton Census Area, AK renamed Kusilvak Census Area, AK'},
}

MIN_INTERVAL = 0.7    # FRED answers 429 to bursts; the acquisition block uses 1.5 req/s per host.
_last_request = [0.0]
CACHE = [None]        # --cache DIR: reuse answers across reruns (keyed without the API key).


def parse_units(text):
    if text in UNITS:
        return UNITS[text]
    match = CHAINED.match(text)
    if match:
        return f'USD_chained_{match[1]}', 1000.0, match[1]
    raise SystemExit(f'Unrecognised FRED units {text!r}: extend UNITS in build_config.py')


def api(path, key, **params):
    cache = None
    if CACHE[0]:
        name = hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
        cache = CACHE[0] / (name + '.json')
        if cache.exists():
            return json.loads(cache.read_text())
    answer = _api(path, key, **params)
    if cache and 'error_message' not in answer:
        cache.write_text(json.dumps(answer))
    return answer


def _api(path, key, **params):
    params.update(api_key=key, file_type='json')
    base = API + ('' if path.startswith('geofred/') else 'fred/')
    url = base + path + '?' + urllib.parse.urlencode(params)
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (400, 404):     # "does not exist" / "not in GeoFRED": a real answer.
                return {'error_message': error.read()[:200].decode('utf8', 'replace'), 'status': error.code}
            time.sleep(min(60, 2 ** attempt))
        except Exception:
            time.sleep(min(60, 2 ** attempt))
    raise SystemExit(f'FRED request failed after retries: {path} {params.get("series_id", "")}')


def release_catalog(key, release_id):
    out, offset = [], 0
    while True:
        page = api('release/series', key, release_id=release_id, limit=1000, offset=offset)
        out += page['seriess']
        offset += len(page['seriess'])
        if offset >= page['count'] or not page['seriess']:
            return out


def members(catalog, family):
    """Catalogue series belonging to one family, with the area code the id carries (or None)."""
    out = {}
    pattern = re.compile(family['id_pattern']) if family.get('id_pattern') else None
    for series in catalog:
        if series['frequency_short'] != family['frequency'] or series['seasonal_adjustment_short'] != 'NSA':
            continue
        if pattern:
            match = pattern.match(series['id'])
            if match:
                out[series['id']] = (series, match[1])
        else:
            match = PLACE.match(series['title'])
            if match and match['measure'] in family['measures']:
                out[series['id']] = (series, None)
    return out


def reference_dates(group):
    """Every reference period of a GeoFRED series group: yearly for A, each January for Q and M."""
    first, last = date.fromisoformat(group['min_date']), date.fromisoformat(group['max_date'])
    dates = [date(year, 1, 1).isoformat() for year in range(first.year, last.year + 1)]
    return sorted(set(dates) | {group['max_date']})


def geofred_codes(key, seed):
    """{series_id: (code, region)} over every reference period of the seed's GeoFRED county group."""
    answer = api('geofred/series/group', key, series_id=seed)
    group = answer.get('series_group')
    if not group or group.get('region_type') != 'county':
        raise SystemExit(f'{seed}: no GeoFRED county group ({answer})')
    codes = {}
    for when in reference_dates(group):
        page = api('geofred/regional/data', key, series_group=group['series_group'], region_type='county',
                   date=when, season=group['season'], units=group['units'],
                   frequency=group['frequency'][0].lower())
        for rows in ((page.get('meta') or {}).get('data') or {}).values():
            for row in rows:
                code, series_id = str(row['code']), row['series_id']
                if not FIPS.match(code):
                    raise SystemExit(f'GeoFRED group {group["series_group"]}: non-FIPS code {code!r} for {series_id}')
                if codes.get(series_id, (code,))[0] != code:
                    raise SystemExit(f'GeoFRED maps {series_id} to both {codes[series_id][0]} and {code}')
                codes[series_id] = (code, row['region'])
    return group, codes


def units_history(key, series_id):
    answer = api('series', key, series_id=series_id, realtime_start=FIRST_REALTIME, realtime_end=LAST_REALTIME)
    history = []
    for row in answer.get('seriess') or []:
        unit, multiplier, base = parse_units(row['units'])
        item = {'realtime_start': row['realtime_start'], 'realtime_end': row['realtime_end'],
                'source_units': row['units'], 'unit': unit, 'multiplier': multiplier, 'base_period': base}
        if history and all(history[-1][k] == item[k] for k in ('source_units', 'unit', 'multiplier')):
            history[-1]['realtime_end'] = item['realtime_end']  # Extend an unchanged run.
        else:
            history.append(item)
    if not history:
        raise SystemExit(f'{series_id}: no series metadata ({answer.get("error_message")})')
    return history


def shared_codes(series):
    by_code = collections.defaultdict(list)
    for series_id, (_, fips, _) in sorted(series.items()):
        by_code[fips].append(series_id)
    return {code: ids for code, ids in sorted(by_code.items()) if len(ids) > 1}


def build_family(key, name, family, catalogs, verbose=True):
    found = members(catalogs[family['release_id']], family)
    if not found:
        raise SystemExit(f'{name}: no catalogue series matched')
    seed = sorted(found)[0]
    group, geo = geofred_codes(key, seed)
    series, skipped, labels, sources, resolved = {}, [], {}, collections.Counter(), []
    for series_id, (meta, id_code) in sorted(found.items()):
        lookup = series_id[:-1] if family.get('geofred_key') == 'drop_trailing_A' else series_id
        geo_code, region = geo.get(lookup, (None, None))
        if geo_code and id_code and geo_code != id_code:
            if CODE_CHANGES.get(geo_code, {}).get('successor') != id_code:
                skipped.append({'series_id': series_id, 'title': meta['title'],
                                'reason': f'id carries {id_code} but GeoFRED publishes {geo_code}'})
                continue
            resolved.append({'series_id': series_id, 'geofred_code': geo_code, 'id_code': id_code,
                             'rule': CODE_CHANGES[geo_code]['change']})
            geo_code = id_code
        elif geo_code in CODE_CHANGES and not id_code:
            # A rename, not a boundary change: the same territory under its current code, so a place has one
            # subject across families (the id-coded families already use the successor).
            resolved.append({'series_id': series_id, 'geofred_code': geo_code, 'id_code': None,
                             'rule': CODE_CHANGES[geo_code]['change']})
            geo_code = CODE_CHANGES[geo_code]['successor']
        fips = geo_code or id_code
        if not fips:
            skipped.append({'series_id': series_id, 'title': meta['title'],
                            'reason': 'not in the GeoFRED county group and the id carries no area code'})
            continue
        source = 'geofred+series_id' if geo_code and id_code else ('geofred' if geo_code else 'series_id')
        sources[source] += 1
        if region:
            labels[fips] = region
        else:
            match = PLACE.match(meta['title'])   # A display label only; the code came from the id.
            labels.setdefault(fips, match['place'] if match else meta['title'])
        series[series_id] = [name, fips, source]
    # Depth and units, sampled at one series per state.
    by_state = {}
    for series_id, (_, fips, _) in sorted(series.items(), key=lambda kv: kv[1][1]):
        by_state.setdefault(fips[:2], series_id)
    probes, histories = [], {}
    for state, series_id in sorted(by_state.items()):
        dates = api('series/vintagedates', key, series_id=series_id, limit=10000).get('vintage_dates') or []
        probes.append({'series_id': series_id, 'state_fips': state, 'vintages': len(dates),
                       'first_vintage': dates[0] if dates else None, 'last_vintage': dates[-1] if dates else None})
        histories[series_id] = units_history(key, series_id)
    changes = any(len({h['source_units'] for h in history}) > 1 for history in histories.values())
    exceptions = {}
    if changes:
        for number, series_id in enumerate(sorted(series), 1):
            if series_id not in histories:
                histories[series_id] = units_history(key, series_id)
            if verbose and number % 250 == 0:
                print(f'  {name}: units history {number}/{len(series)}', file=sys.stderr)
    shapes = collections.Counter(json.dumps([[h['source_units'], h['realtime_start']] for h in history])
                                 for history in histories.values())
    common_shape = shapes.most_common(1)[0][0]
    common = next(h for h in histories.values()
                  if json.dumps([[x['source_units'], x['realtime_start']] for x in h]) == common_shape)
    if changes:
        exceptions = {s: h for s, h in histories.items()
                      if json.dumps([[x['source_units'], x['realtime_start']] for x in h]) != common_shape}
    present = {h['unit'] for h in common}
    with_vintages = [p for p in probes if p['vintages']]
    entry = {
        **family, 'geofred_series_group': group['series_group'], 'geofred_title': group['title'],
        'source_units': common[-1]['source_units'], 'unit': common[-1]['unit'],
        'multiplier': common[-1]['multiplier'], 'frequency_long': group['frequency'],
        'units_history': common, 'units_history_verified': 'every series' if changes else
        f'sampled ({len(histories)} series, one per state); no sampled series changed units',
        'units_change_across_vintages': changes, 'units_history_exceptions': exceptions,
        'series': len(series), 'counties': len({v[1] for v in series.values()}),
        # Two series of one family on one code (e.g. a discontinued and a current series): listed, not merged.
        'shared_codes': shared_codes(series),
        'states': len({v[1][:2] for v in series.values()}), 'fips_sources': dict(sorted(sources.items())),
        'skipped': skipped, 'code_changes_resolved': resolved,
        'vintage_probe': {
            'sampled_series': len(probes), 'not_in_alfred': [p['series_id'] for p in probes if not p['vintages']],
            'min_vintages': min((p['vintages'] for p in with_vintages), default=0),
            'max_vintages': max((p['vintages'] for p in with_vintages), default=0),
            'earliest_first_vintage': min((p['first_vintage'] for p in with_vintages), default=None),
            'latest_first_vintage': max((p['first_vintage'] for p in with_vintages), default=None),
            'latest_vintage': max((p['last_vintage'] for p in with_vintages), default=None),
            'probes': probes},
    }
    if verbose:
        print(f'{name}: {len(series)} series, {entry["counties"]} codes, {len(skipped)} skipped, sources '
              f'{dict(sources)}, units {sorted(present)}, vintages {entry["vintage_probe"]["min_vintages"]}-'
              f'{entry["vintage_probe"]["max_vintages"]} from {entry["vintage_probe"]["earliest_first_vintage"]}',
              file=sys.stderr)
    return entry, series, labels


def build(key, verbose=True):
    releases = sorted({family['release_id'] for family in FAMILIES.values()})
    catalogs, release_names = {}, {}
    for release_id in releases:
        catalogs[release_id] = release_catalog(key, release_id)
        release_names[release_id] = api('release', key, release_id=release_id)['releases'][0]['name']
        if verbose:
            print(f'release {release_id} ({release_names[release_id]}): {len(catalogs[release_id])} series',
                  file=sys.stderr)
    families, series, labels = {}, {}, {}
    for name, family in FAMILIES.items():
        entry, found, found_labels = build_family(key, name, family, catalogs, verbose)
        entry['release_name'] = release_names[family['release_id']]
        families[name] = entry
        for series_id, row in found.items():
            if series_id in series:
                raise SystemExit(f'{series_id} claimed by {series[series_id][0]} and {name}')
            series[series_id] = row
        for fips, label in found_labels.items():
            labels.setdefault(fips, label)
    # GeoFRED lists the annual LAUS unemployment rate and labor force under the monthly *alias* ids, so
    # their codes come from the BLS area code in the id. Report which of those codes GeoFRED confirms for
    # the same area in another family (e.g. LAUCN<fips>...05A), and list the ones nothing confirms.
    confirmed = {row[1] for row in series.values() if row[2] != 'series_id'}
    for name, entry in families.items():
        only_id = sorted({row[1] for row in series.values() if row[0] == name and row[2] == 'series_id'})
        entry['series_id_codes'] = {'count': len(only_id),
                                    'confirmed_by_geofred_in_another_family': sum(c in confirmed for c in only_id),
                                    'unconfirmed': [c for c in only_id if c not in confirmed]}
    return {
        'generated_by': 'data/fred_county_vintages/build_config.py',
        'generated_on': date.today().isoformat(),
        'realtime_window': {'start': FIRST_REALTIME, 'end': LAST_REALTIME},
        'families': families,
        'code_changes': CODE_CHANGES,
        'counties': dict(sorted(labels.items())),
        'series': dict(sorted(series.items())),
    }


def write_config(path, config):
    """Indented JSON, except one line per series and county so the file stays reviewable."""
    head = {k: v for k, v in config.items() if k not in ('counties', 'series')}
    text = json.dumps(head, indent=1)[:-2]
    for field in ('counties', 'series'):
        rows = ',\n'.join(f'  {json.dumps(k)}: {json.dumps(v)}' for k, v in config[field].items())
        text += f',\n {json.dumps(field)}: {{\n{rows}\n }}'
    path.write_text(text + '\n}\n')


def parameters(config):
    return {'series_id': sorted(config['series'])}


def api_key(here):
    key = os.environ.get('FRED_API_KEY')
    candidates = [Path(os.environ['WORLD_MODEL_ENV_FILE'])] if os.environ.get('WORLD_MODEL_ENV_FILE') else []
    for candidate in candidates + [here.parents[1] / '.env', Path('.env')]:
        if key or not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            if line.startswith('FRED_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('"\'')
    if not key:
        raise SystemExit('FRED_API_KEY is required')
    return key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='report coverage without writing config.json')
    parser.add_argument('--emit-parameters', action='store_true',
                        help='print the acquisition.parameters object for dataset.json')
    parser.add_argument('--cache', type=Path, help='directory caching FRED answers across reruns')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)
        CACHE[0] = args.cache
    if args.emit_parameters:
        print(json.dumps(parameters(json.loads((here / 'config.json').read_text()))))
        return
    config = build(api_key(here), verbose=True)
    summary = {name: {k: family[k] for k in ('series', 'counties', 'states', 'fips_sources')}
               | {'skipped': len(family['skipped']), 'renames_resolved': len(family['code_changes_resolved'])}
               for name, family in config['families'].items()}
    print(json.dumps(summary, indent=1), file=sys.stderr)
    if not args.check:
        write_config(here / 'config.json', config)
        print(f'wrote {here / "config.json"}: {len(config["series"])} series', file=sys.stderr)


if __name__ == '__main__':
    main()
