#!/usr/bin/env python3
"""Regenerate config.json: the **monthly** LAUS county alias series and the ALFRED vintages they carry.

Derived from ``data/fred_county_vintages/build_config.py``. The county-level survey in
``docs/county-vintages.md`` found that LAUS is the one county program FRED publishes under two id
forms with different archives, and that the *alias* form is the deep one: ``TXHARR1URN`` (unemployment
rate) and ``AKALEU0LFN`` (civilian labor force) carry 229-264 monthly vintages from 2005-06-08 and
2007-06, while the structured ``LAUCN…`` forms the annual dataset uses open only in 2017 and 2019.
This file discovers the alias families; ``fred_county_vintages`` keeps the annual ones.

Three things this script establishes rather than assumes, as in the annual dataset.

**Which series are in a family.** One FRED release (116, Unemployment in States and Local Areas),
narrowed to monthly NSA series whose id ends in the family's alias suffix. The alias id carries **no
area code** -- ``CTFAIR1URN`` names Connecticut and an abbreviation, not a FIPS -- so membership is
settled by whether a county code can be established for the series at all (below); a series with no
code is skipped and listed, which is how the release's 18 Federal Reserve district aggregates
(``D1URN``, ``DKYLFN``) leave the dataset.

**Which county a series describes.** In three steps, strongest first, and never by reading a code out
of a title:

1. **GeoFRED**, which publishes these alias ids directly: ``geofred/series/group`` for an alias seed
   answers with the county group (1224 Unemployment Rate, 656 Civilian Labor Force, both monthly), and
   ``geofred/regional/data`` over every reference period of the group returns ``{series_id, code,
   region}``. This is the same published geography the annual dataset uses; it lists the alias ids
   rather than the ``LAUCN…`` ones, which is why the annual LAUS families had to fall back on their
   own ids.
2. **The other measure's group, by alias base.** ``SDSHAN3URN`` and ``SDSHAN3LFN`` are the same BLS
   area under the same FRED alias base; where only one of the two groups lists the base, the code
   carries across. This is an id-level join, not a name one.
3. **The structured ``LAUCN<fips>…`` series published under the same place label in the same
   release.** FRED gives one place label (the text after " in ") to every series it publishes for an
   area, and the structured LAUS ids carry the Census FIPS. The code still comes from an id; the label
   is only the join key, and the join is *checked*: for every alias series where both GeoFRED and a
   structured twin exist, the two codes must agree, and ``config.json`` records how many did. A label
   that more than one structured FIPS claims is not used for anything.

Where GeoFRED publishes a code that Census has since retired (46113 Shannon County SD, 02270 Wade
Hampton Census Area AK), ``CODE_CHANGES`` resolves it to the successor, exactly as the annual dataset
does and for the same reason: a rename leaves the territory unchanged and a place should have one
subject across datasets.

**How deep each family goes, and in what units.** One series per state is probed with
``series/vintagedates`` (depth) and with ``series`` over the whole real-time window (units as published
in each vintage). The labor force family needs the escalation the annual dataset built for rebased
real GDP: **FRED re-expressed county labor force from "Thousands of Persons" to "Persons" on
2016-03-18**, and the observations changed with it (``CTFAIR1LFN`` for 2015-06 is ``489.537`` in the
2016-03-01 vintage and ``489537`` in the 2016-03-18 one), so a vintage's multiplier is not the
series' multiplier. When any sampled series changes units across vintages -- *or* when the release
catalogue does not give every series in the family the same current units -- the full units history is
fetched for **every** series and stored per series where it differs from the family's common one.

ALFRED membership is not probed per series: the acquisition requests the full real-time window and
FRED answers ``400 "does not exist in ALFRED"`` for a series it does not archive, which the
declaration records as skipped (``skip_statuses: [400]``). ``measure_coverage.py`` reports them.

Usage::

    FRED_API_KEY=... python3 data/fred_county_laus_monthly_vintages/build_config.py   # write config.json
    python3 data/fred_county_laus_monthly_vintages/build_config.py --emit-parameters  # for dataset.json
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
RELEASE = 116

#: One entry per family. ``alias_suffix`` selects members from the release catalogue (monthly, NSA);
#: the area code never comes from the id, which carries none.
FAMILIES = {
    'laus_monthly_unemployment_rate': {
        'release_id': RELEASE, 'frequency': 'M', 'alias_suffix': 'URN',
        'metric': 'unemployment_rate', 'program': 'laus',
        'originating_source': 'U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics '
                              '(monthly, not seasonally adjusted)'},
    'laus_monthly_labor_force': {
        'release_id': RELEASE, 'frequency': 'M', 'alias_suffix': 'LFN',
        'metric': 'labor_force', 'program': 'laus',
        'originating_source': 'U.S. Bureau of Labor Statistics, Local Area Unemployment Statistics '
                              '(monthly, not seasonally adjusted)'},
}

#: Structured LAUS county ids, whose area code is the Census FIPS: the join target of step 3.
STRUCTURED = re.compile(r'^LAUCN(\d{5})0{8}\d{2}A?$')

#: FRED unit strings -> (unit, multiplier, base_period). Anything else stops the build.
UNITS = {
    'Thousands of Persons': ('persons', 1000.0, None),
    'Persons': ('persons', 1.0, None),
    'Percent': ('percent', 1.0, None),
}
PLACE = re.compile(r'^(?P<measure>.+?) (?:in|for) (?:the )?(?P<place>.+?)(?: \(DISCONTINUED\))?$')
FIPS = re.compile(r'^\d{5}$')

#: Census county code changes that GeoFRED's map layer predates; see the annual dataset for the source.
#: Census Bureau, "Substantial Changes to Counties and County Equivalent Entities: 1970-Present"
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
    """Catalogue series belonging to one family: monthly, NSA, id ending in the alias suffix."""
    suffix = family['alias_suffix']
    return {series['id']: series for series in catalog
            if series['frequency_short'] == family['frequency']
            and series['seasonal_adjustment_short'] == 'NSA' and series['id'].endswith(suffix)}


def structured_places(catalog):
    """{place label: fips} from the release's structured ``LAUCN<fips>…`` ids. Ambiguous labels drop out."""
    claims = collections.defaultdict(set)
    for series in catalog:
        match = STRUCTURED.match(series['id'])
        place = PLACE.match(series['title'])
        if match and place:
            claims[place['place']].add(match[1])
    ambiguous = {place: sorted(codes) for place, codes in claims.items() if len(codes) > 1}
    return {place: next(iter(codes)) for place, codes in claims.items() if len(codes) == 1}, ambiguous


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


def resolve_change(code, resolved, series_id, via):
    """GeoFRED still publishes two pre-2015 codes; map them onto the successor and record it."""
    if code in CODE_CHANGES:
        resolved.append({'series_id': series_id, 'geofred_code': code, 'via': via,
                         'successor': CODE_CHANGES[code]['successor'], 'rule': CODE_CHANGES[code]['change']})
        return CODE_CHANGES[code]['successor']
    return code


def build_family(key, name, family, catalog, geo_by_id, geo_by_base, places, verbose=True):
    """Resolve one family's membership and county codes; probe its depth and units."""
    found = members(catalog, family)
    if not found:
        raise SystemExit(f'{name}: no catalogue series matched')
    suffix = family['alias_suffix']
    series, skipped, labels, sources, resolved = {}, [], {}, collections.Counter(), []
    checked = {'both_available': 0, 'agreed': 0}
    for series_id, meta in sorted(found.items()):
        base = series_id[:-len(suffix)]
        place = PLACE.match(meta['title'])
        twin = places.get(place['place']) if place else None
        geo_code, region, via = None, None, None
        if series_id in geo_by_id:
            geo_code, region, via = *geo_by_id[series_id], 'geofred'
        elif base in geo_by_base:
            geo_code, region, via = *geo_by_base[base], 'geofred_alias_base'
        if geo_code and twin:
            checked['both_available'] += 1
            if resolve_change(geo_code, [], series_id, via) == twin:
                checked['agreed'] += 1
            else:
                skipped.append({'series_id': series_id, 'title': meta['title'],
                                'reason': f'GeoFRED publishes {geo_code} but the structured id for the same '
                                          f'place label carries {twin}'})
                continue
        if geo_code:
            fips = resolve_change(geo_code, resolved, series_id, via)
            source = via
        elif twin:
            fips, source, region = twin, 'release_structured_id', None
        else:
            skipped.append({'series_id': series_id, 'title': meta['title'],
                            'reason': 'not in the GeoFRED county group (under either alias measure) and no '
                                      'structured LAUCN series shares its place label'})
            continue
        sources[source] += 1
        if region:
            labels[fips] = region
        elif place:
            labels.setdefault(fips, place['place'])   # A display label only; the code came from an id.
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
    catalogue_units = {found[s]['units'] for s in series}
    changes = any(len({h['source_units'] for h in history}) > 1 for history in histories.values())
    # A family whose catalogue does not give every series the same current units cannot be described by
    # one sampled history either: the labor force aliases discontinued before 2016-03-18 never left
    # "Thousands of Persons".
    every_series = changes or len(catalogue_units) > 1
    if every_series:
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
    exceptions = {}
    if every_series:
        exceptions = {s: h for s, h in histories.items()
                      if json.dumps([[x['source_units'], x['realtime_start']] for x in h]) != common_shape}
    with_vintages = [p for p in probes if p['vintages']]
    entry = {
        **family, 'geofred_series_group': family['geofred_series_group'],
        'geofred_title': family['geofred_title'],
        'source_units': common[-1]['source_units'], 'unit': common[-1]['unit'],
        'multiplier': common[-1]['multiplier'], 'frequency_long': family['frequency_long'],
        'units_history': common,
        'units_history_verified': 'every series' if every_series else
        f'sampled ({len(histories)} series, one per state); no sampled series changed units and the release '
        f'catalogue gives them all the same units',
        'catalogue_units': sorted(catalogue_units),
        'units_change_across_vintages': changes, 'units_history_exceptions': exceptions,
        'series': len(series), 'counties': len({v[1] for v in series.values()}),
        # Two series of one family on one code (e.g. a discontinued and a current series): listed, not merged.
        'shared_codes': shared_codes(series),
        'states': len({v[1][:2] for v in series.values()}), 'fips_sources': dict(sorted(sources.items())),
        'code_check': {**checked, 'rule': 'where GeoFRED and a structured LAUCN id under the same place label '
                                          'both give a code, they must be equal'},
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
              f'{dict(sources)}, code check {checked}, units {sorted(catalogue_units)}, vintages '
              f'{entry["vintage_probe"]["min_vintages"]}-{entry["vintage_probe"]["max_vintages"]} from '
              f'{entry["vintage_probe"]["earliest_first_vintage"]}', file=sys.stderr)
    return entry, series, labels


def build(key, verbose=True):
    catalog = release_catalog(key, RELEASE)
    release_name = api('release', key, release_id=RELEASE)['releases'][0]['name']
    if verbose:
        print(f'release {RELEASE} ({release_name}): {len(catalog)} series', file=sys.stderr)
    places, ambiguous = structured_places(catalog)
    if verbose:
        print(f'structured place labels: {len(places)} usable, {len(ambiguous)} claimed by more than one FIPS',
              file=sys.stderr)
    # GeoFRED, once per family, then indexed by alias base so a code can cross between the two measures.
    geo_by_id, geo_by_base, groups = {}, {}, {}
    for name, family in FAMILIES.items():
        seed = min(members(catalog, family))
        group, codes = geofred_codes(key, seed)
        groups[name] = group
        family['geofred_series_group'], family['geofred_title'] = group['series_group'], group['title']
        family['frequency_long'] = group['frequency']
        geo_by_id.update(codes)
        for series_id, value in codes.items():
            if series_id.endswith(family['alias_suffix']):
                geo_by_base.setdefault(series_id[:-len(family['alias_suffix'])], value)
        if verbose:
            print(f'{name}: GeoFRED group {group["series_group"]} ({group["title"]}, {group["frequency"]}), '
                  f'{len(codes)} series mapped', file=sys.stderr)
    families, series, labels = {}, {}, {}
    for name, family in FAMILIES.items():
        entry, found, found_labels = build_family(key, name, family, catalog, geo_by_id, geo_by_base, places,
                                                  verbose)
        entry['release_name'] = release_name
        families[name] = entry
        for series_id, row in found.items():
            if series_id in series:
                raise SystemExit(f'{series_id} claimed by {series[series_id][0]} and {name}')
            series[series_id] = row
        for fips, label in found_labels.items():
            labels.setdefault(fips, label)
    return {
        'generated_by': 'data/fred_county_laus_monthly_vintages/build_config.py',
        'generated_on': date.today().isoformat(),
        'realtime_window': {'start': FIRST_REALTIME, 'end': LAST_REALTIME},
        'structured_place_labels': {'usable': len(places), 'ambiguous': ambiguous},
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
    summary = {name: {k: family[k] for k in ('series', 'counties', 'states', 'fips_sources', 'code_check')}
               | {'skipped': len(family['skipped']), 'renames_resolved': len(family['code_changes_resolved'])}
               for name, family in config['families'].items()}
    print(json.dumps(summary, indent=1), file=sys.stderr)
    if not args.check:
        write_config(here / 'config.json', config)
        print(f'wrote {here / "config.json"}: {len(config["series"])} series', file=sys.stderr)


if __name__ == '__main__':
    main()
