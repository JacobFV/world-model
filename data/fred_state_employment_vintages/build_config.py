#!/usr/bin/env python3
"""Regenerate config.json: the state x supersector CES SAE series that carry ALFRED vintages.

Two things this script establishes rather than assumes.

**Which id form is archived.** FRED lists the same BLS SAE series under a short alias and under
the structured BLS id, and the two are not interchangeable in ALFRED. ``SMU48000003000000001``
answers ``series/vintagedates`` with *"does not exist in ALFRED but may exist in FRED"*, while
``TXMFGN`` answers with 229 vintage dates from 2007-06-19. Coverage is per-series patchy in both
directions (``TXINFO`` has 229 vintages, ``CAINFO`` is not in ALFRED at all), so ids are
**discovered** from the FRED release-112 catalogue by title and then verified one request at a
time, never constructed from a template and hoped for.

**How deep each series goes.** A complete panel is only real time back to the shallowest series
it contains, so ``vintage_count``/``first_vintage`` are recorded per series and the coverage
block reports the binding constraint. As of 2026-09-17 that constraint is Information, whose
state NSA series enter ALFRED on 2011-11-22; every other supersector starts 2007-06-19.

Usage::

    FRED_API_KEY=... python3 data/fred_state_employment_vintages/build_config.py            # write config.json
    FRED_API_KEY=... python3 data/fred_state_employment_vintages/build_config.py --check     # report only
    python3 data/fred_state_employment_vintages/build_config.py --emit-combinations          # for dataset.json

``--emit-combinations`` prints the ``acquisition.combinations`` array, so the declaration and
config.json cannot drift apart.
"""
import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://api.stlouisfed.org/fred/'
SAE_RELEASE = 112          # State Employment and Unemployment (BLS CES State and Area)
FIRST_REALTIME, LAST_REALTIME = '1776-07-04', '9999-12-31'

#: The nine CES supersectors published for every state-equivalent, plus total nonfarm.
#: Mining/logging/construction is deliberately absent; see the module docstring of pipeline.py.
SUPERSECTORS = {
    'Total Nonfarm': ('total_nonfarm', '00000000'),
    'Manufacturing': ('manufacturing', '30000000'),
    'Trade, Transportation, and Utilities': ('trade_transportation_utilities', '40000000'),
    'Information': ('information', '50000000'),
    'Financial Activities': ('financial_activities', '55000000'),
    'Professional and Business Services': ('professional_business_services', '60000000'),
    'Education and Health Services: Private Education and Health Services':
        ('education_health_services', '65000000'),
    'Leisure and Hospitality': ('leisure_hospitality', '70000000'),
    'Other Services': ('other_services', '80000000'),
    'Government': ('government', '90000000'),
}
#: Carried where published; used only to check the residual, never to build the panel.
CHECK_SECTORS = {
    'Construction': ('construction', '20000000'),
    'Mining and Logging': ('mining_logging', '10000000'),
    'Mining, Logging, and Construction': ('mining_logging_construction', '15000000'),
}

STATE_FIPS = {
    'Alabama': '01', 'Alaska': '02', 'Arizona': '04', 'Arkansas': '05', 'California': '06',
    'Colorado': '08', 'Connecticut': '09', 'Delaware': '10', 'District of Columbia': '11',
    'Florida': '12', 'Georgia': '13', 'Hawaii': '15', 'Idaho': '16', 'Illinois': '17',
    'Indiana': '18', 'Iowa': '19', 'Kansas': '20', 'Kentucky': '21', 'Louisiana': '22',
    'Maine': '23', 'Maryland': '24', 'Massachusetts': '25', 'Michigan': '26', 'Minnesota': '27',
    'Mississippi': '28', 'Missouri': '29', 'Montana': '30', 'Nebraska': '31', 'Nevada': '32',
    'New Hampshire': '33', 'New Jersey': '34', 'New Mexico': '35', 'New York': '36',
    'North Carolina': '37', 'North Dakota': '38', 'Ohio': '39', 'Oklahoma': '40', 'Oregon': '41',
    'Pennsylvania': '42', 'Rhode Island': '44', 'South Carolina': '45', 'South Dakota': '46',
    'Tennessee': '47', 'Texas': '48', 'Utah': '49', 'Vermont': '50', 'Virginia': '51',
    'Washington': '53', 'West Virginia': '54', 'Wisconsin': '55', 'Wyoming': '56',
}
TITLE = re.compile(r'^All Employees: (.+?) in ([A-Za-z .\-]+?)(?: \(DISCONTINUED\))?$')

MIN_INTERVAL = 0.7    # FRED answers 429 to bursts; the acquisition block uses 1.5 req/s per host.
_last_request = [0.0]


def api(path, key, **params):
    params.update(api_key=key, file_type='json')
    url = API + path + '?' + urllib.parse.urlencode(params)
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 400:     # "does not exist" / "does not exist in ALFRED": a real answer.
                return {'error_message': error.read()[:200].decode('utf8', 'replace')}
            time.sleep(min(60, 2 ** attempt))
        except Exception:
            time.sleep(min(60, 2 ** attempt))
    raise SystemExit(f'FRED request failed after retries: {path} {params.get("series_id", "")}')


def release_catalog(key):
    out, offset = [], 0
    while True:
        page = api('release/series', key, release_id=SAE_RELEASE, limit=1000, offset=offset)
        out += page['seriess']
        offset += len(page['seriess'])
        if offset >= page['count'] or not page['seriess']:
            return out


def candidates(catalog):
    """{(state, sector_title): series_metadata} for monthly NSA state-level All-Employees series."""
    out = {}
    for series in catalog:
        if series['seasonal_adjustment_short'] != 'NSA' or series['frequency_short'] != 'M':
            continue
        match = TITLE.match(series['title'])
        if not match:
            continue
        sector, place = match.group(1), match.group(2).strip()
        if place not in STATE_FIPS or (sector not in SUPERSECTORS and sector not in CHECK_SECTORS):
            continue
        key = (place, sector)
        if key in out:
            raise SystemExit(f'Two FRED series claim {key}: {out[key]["id"]} and {series["id"]}')
        out[key] = series
    return out


def build(key, verbose=True):
    found = candidates(release_catalog(key))
    series, skipped = {}, []
    for (place, sector), meta in sorted(found.items()):
        panel = sector in SUPERSECTORS
        industry, ces_code = (SUPERSECTORS if panel else CHECK_SECTORS)[sector]
        answer = api('series/vintagedates', key, series_id=meta['id'])
        dates = answer.get('vintage_dates') or []
        if not dates:
            skipped.append({'series_id': meta['id'], 'state': place, 'sector': sector,
                            'reason': answer.get('error_message', 'no vintage dates')})
            if verbose:
                print(f'  SKIP {meta["id"]:10s} {place} / {sector}: not in ALFRED', file=sys.stderr)
            continue
        series[meta['id']] = {
            'metric': 'employment', 'unit': 'jobs', 'multiplier': 1000.0,
            'source_units': meta['units'], 'frequency': 'M', 'frequency_long': meta['frequency'],
            'seasonal_adjustment': 'NSA', 'geography': f'geo:US:state:{STATE_FIPS[place]}',
            'state': place, 'title': meta['title'], 'tier': 'vintages', 'category': 'employment',
            'industry': industry, 'ces_industry_code': ces_code,
            'panel_role': 'panel' if panel else 'residual_check',
            'observation_start': meta['observation_start'], 'observation_end': meta['observation_end'],
            'vintage_count': len(dates), 'first_vintage': dates[0], 'last_vintage': dates[-1],
        }
        if verbose:
            print(f'  {meta["id"]:10s} {len(dates):4d} vintages {dates[0]}..{dates[-1]}  '
                  f'{place} / {industry}', file=sys.stderr)
    panel_series = {k: v for k, v in series.items() if v['panel_role'] == 'panel'}
    per_state = collections.Counter(v['state'] for v in panel_series.values())
    incomplete = {s: n for s, n in per_state.items() if n != len(SUPERSECTORS)}
    if incomplete:
        raise SystemExit(f'Incomplete panel coverage (expected {len(SUPERSECTORS)} per state): {incomplete}')
    # The panel is real time only back to its shallowest member: report the binding constraint
    # instead of the optimistic minimum, so a protocol cannot be declared earlier than the data.
    binding = max(panel_series.values(), key=lambda v: v['first_vintage'])
    return {
        'generated_by': 'data/fred_state_employment_vintages/build_config.py',
        'release_id': SAE_RELEASE,
        'realtime_window': {'start': FIRST_REALTIME, 'end': LAST_REALTIME},
        'panel_industries': sorted({v['industry'] for v in panel_series.values()}),
        'derived_industry': {
            'name': 'mining_logging_construction', 'ces_industry_code': '15000000',
            'rule': 'total_nonfarm minus the nine published supersectors, within one vintage',
            'why': 'Delaware, DC, Hawaii, Maryland and Nebraska publish no aliased mining/logging/'
                   'construction series, and the structured SMS...15000000...01 form that does cover '
                   'them only enters ALFRED in 2014. The residual is the only completion of the CES '
                   'supersector partition that keeps all 51 state-equivalents and the full vintage depth.',
        },
        'coverage': {
            'states': len(per_state), 'panel_series': len(panel_series),
            'residual_check_series': len(series) - len(panel_series),
            'min_vintages': min(v['vintage_count'] for v in panel_series.values()),
            'max_vintages': max(v['vintage_count'] for v in panel_series.values()),
            'binding_first_vintage': binding['first_vintage'],
            'binding_series': binding['title'],
            'latest_vintage': max(v['last_vintage'] for v in series.values()),
        },
        'skipped': skipped,
        'states': dict(sorted(STATE_FIPS.items())),
        'series': dict(sorted(series.items())),
    }


def combinations(config):
    window = config['realtime_window']
    return [{'series_id': series_id, 'realtime_start': window['start'], 'realtime_end': window['end'],
             'offset': 0} for series_id in sorted(config['series'])]


def api_key(here):
    key = os.environ.get('FRED_API_KEY')
    for candidate in (here.parents[1] / '.env', Path('.env')):
        if key or not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            if line.startswith('FRED_API_KEY='):
                key = line.split('=', 1)[1].strip()
    if not key:
        raise SystemExit('FRED_API_KEY is required')
    return key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='report coverage without writing config.json')
    parser.add_argument('--emit-combinations', action='store_true',
                        help='print the acquisition.combinations array for dataset.json')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    if args.emit_combinations:
        print(json.dumps(combinations(json.loads((here / 'config.json').read_text())), indent=6))
        return
    config = build(api_key(here), verbose=not args.check)
    print(json.dumps(config['coverage'], indent=2), file=sys.stderr)
    if not args.check:
        (here / 'config.json').write_text(json.dumps(config, indent=1) + '\n')
        print(f'wrote {here / "config.json"}: {len(config["series"])} series', file=sys.stderr)


if __name__ == '__main__':
    main()
