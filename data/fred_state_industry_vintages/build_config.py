"""Refresh fred_state_industry_vintages series metadata, per-vintage units and acquisition combinations.

Run from the project root:  python3 data/fred_state_industry_vintages/build_config.py
Needs FRED_API_KEY (read from the environment or the project .env; never printed).
Stdlib only; three FRED requests per candidate series (metadata with the full real-time window,
vintage dates, and a row count), spaced <= 100/min. Pipelines never import this module; it only
rewrites config.json and dataset.json.

Series id rule (BLS State Area Employment, one series per state x supersector):
``SMS`` + state FIPS(2) + area ``00000`` + industry code(8) + data type ``01`` (All Employees).
FRED does not serve total nonfarm under that id, so the config names its published alias
(``{abbr}NA``) instead. Anything a candidate id does not resolve to is recorded in
``config['dropped']`` with FRED's own reason, never silently skipped.
"""
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
API = 'https://api.stlouisfed.org/fred/'
FIRST_REALTIME, LAST_REALTIME = '1776-07-04', '9999-12-31'
PAGE_LIMIT = 100000
SCALES = {'thousands': 1e3, 'millions': 1e6, 'billions': 1e9, 'trillions': 1e12}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def parse_units(units):
    """FRED units string -> (unit, multiplier, normalized?). Multipliers convert to base units.

    Deliberately narrow: this dataset publishes headcounts. An unrecognised units string is
    returned with ``normalized=False`` so build_config reports it instead of guessing a scale.
    """
    text = units.strip()
    multiplier = 1.0
    match = re.match(r'(Thousands|Millions|Billions|Trillions)(?: of (.*))?$', text)
    if match:
        multiplier, text = SCALES[match[1].lower()], (match[2] or 'Number')
    if text in ('Persons', 'Number of Persons'):
        return 'persons', multiplier, True
    if text in ('Number', 'Employees', 'Number of Employees'):
        return 'persons', multiplier, True
    if text == 'Percent':
        return 'percent', multiplier, True
    if m := re.fullmatch(r'Index (.+)=\s*100', text):
        return 'index_' + slug(m[1]) + '_100', multiplier, True
    return slug(text) or 'unknown', multiplier, False


def base_period(units):
    """Base period a published unit is denominated in, e.g. 'Index 2012=100' -> '2012'."""
    match = re.search(r'Index\s+(.+?)\s*=\s*100', units.strip())
    return slug(match[1]) if match else None


def load_key():
    if os.environ.get('FRED_API_KEY'):
        return os.environ['FRED_API_KEY']
    sys.path.insert(0, str(HERE.parents[1]))
    from worldmodel.env import load_project_env
    load_project_env(HERE.parents[1], os.environ)
    if not os.environ.get('FRED_API_KEY'):
        raise SystemExit('FRED_API_KEY is not set')
    return os.environ['FRED_API_KEY']


class Client:
    def __init__(self, key, spacing=0.65):
        self.key, self.spacing, self.last = key, spacing, 0.0

    def get(self, endpoint, **params):
        for attempt in range(6):
            wait = self.last + self.spacing - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            url = API + endpoint + '?' + urlencode({**params, 'file_type': 'json', 'api_key': self.key})
            try:
                with urlopen(Request(url, headers={'User-Agent': 'worldmodel-substrate/0.3 '
                                                                 'fred_state_industry_vintages config'}),
                             timeout=90) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code in (400, 404):
                    try:
                        return {'error': json.load(error).get('error_message', str(error.code))}
                    except ValueError:
                        return {'error': str(error.code)}
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt * 2)
            except OSError:
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt * 2)


def windows(vintage_dates, limit):
    """Split real-time history into windows holding <= limit vintage dates each."""
    if len(vintage_dates) <= limit:
        return [[FIRST_REALTIME, LAST_REALTIME]]
    starts = [vintage_dates[i] for i in range(limit, len(vintage_dates), limit)]
    bounds, begin = [], FIRST_REALTIME
    for start in starts:
        bounds.append([begin, (date.fromisoformat(start) - timedelta(days=1)).isoformat()])
        begin = start
    bounds.append([begin, LAST_REALTIME])
    return bounds


def series_id(fips, abbr, code, alias):
    """FRED id for one (state, industry): the published alias when config names one, else the SMS id."""
    return alias.format(abbr=abbr, fips=fips) if alias else f'SMS{fips}00000{code}01'


def expand(config):
    for code, metric, label, level, alias in config['industries']:
        for abbr, fips in config['states'].items():
            yield series_id(fips, abbr, code, alias), {
                'category': 'state_industry_employment', 'tier': 'vintages', 'metric': metric,
                'geography': 'geo:US:state:' + fips, 'industry_code': code, 'industry_label': label,
                'industry_level': level, 'state': abbr}


def copyright_holders(notes, sources):
    """Named holders (case-sensitive whole words) or a generic copyright mention."""
    names = [name for name in sources if re.search(r'(?<![A-Za-z])' + re.escape(name) + r'(?![A-Za-z])', notes)]
    return names or (['see series notes'] if re.search(r'copyright|©', notes, re.IGNORECASE) else [])


def units_history(rows):
    """Published units per real-time period, newest run last, from ``series`` metadata vintages.

    ALFRED observations carry no units. FRED restates a series' scale (TOTALSL went from billions
    to millions) and rebases indexes, so a vintage's level is denominated in whatever units were
    current *at that vintage*. Labelling every vintage with the series' present units is the bug
    that produced a 22.5% output gap: 11,612 of TOTALSL's 13,537 values were wrong by 1000x.
    """
    history = []
    for row in rows:
        unit, multiplier, normalized = parse_units(row['units'])
        item = {'realtime_start': row['realtime_start'], 'realtime_end': row['realtime_end'],
                'source_units': row['units'], 'unit': unit, 'multiplier': multiplier,
                'base_period': base_period(row['units']), 'unit_normalized': normalized}
        if history and all(history[-1][k] == item[k] for k in ('source_units', 'unit', 'multiplier')):
            history[-1]['realtime_end'] = item['realtime_end']   # Extend an unchanged run.
        else:
            history.append(item)
    return history


def combinations(config):
    """One request per (series, real-time window, offset slice).

    FRED caps a JSON response at limit=100000 observations, so a window with more rows is split
    into offset slices using the measured ``request_counts``.
    """
    counts = config.get('request_counts', {})
    out = []
    for identifier, entry in config['series'].items():
        for start, end in entry['windows']:
            total = counts.get(f'{identifier}|{start}|{end}')
            offsets = range(0, total, PAGE_LIMIT) if total else [0]
            for offset in offsets:
                out.append({'series_id': identifier, 'realtime_start': start, 'realtime_end': end, 'offset': offset})
    return out


def write(config):
    (HERE / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=False) + '\n')
    path = HERE / 'dataset.json'
    definition = json.loads(path.read_text())
    definition['acquisition']['combinations'] = combinations(config)
    path.write_text(json.dumps(definition, indent=2) + '\n')
    return definition


def main(update=False):
    """Resolve every candidate series and record its metadata, units history, windows and row count."""
    config = json.loads((HERE / 'config.json').read_text())
    client = Client(load_key())
    old, previously_dropped = config.get('series', {}), config.get('dropped', {})
    series, dropped, counts = {}, {}, dict(config.get('request_counts', {}))
    for number, (identifier, spec) in enumerate(expand(config), 1):
        if identifier in series:
            raise SystemExit('Duplicate series in spec: ' + identifier)
        if update and identifier in previously_dropped:
            dropped[identifier] = previously_dropped[identifier]
            continue
        cached = old.get(identifier)
        if update and cached and all(cached.get(key) == value for key, value in spec.items()):
            series[identifier] = cached
            continue
        # One request returns every metadata vintage, so units history comes free with the metadata.
        meta = client.get('series', series_id=identifier, realtime_start=FIRST_REALTIME, realtime_end=LAST_REALTIME)
        rows = meta.get('seriess') if isinstance(meta, dict) else None
        if not rows:
            dropped[identifier] = (meta or {}).get('error', 'no metadata')
            print('drop', identifier, dropped[identifier], file=sys.stderr)
            continue
        latest = rows[-1]
        history = units_history(rows)
        unit, multiplier, normalized = parse_units(latest['units'])
        entry = {**spec, 'title': latest['title'], 'source_units': latest['units'], 'unit': unit,
                 'multiplier': multiplier, 'unit_normalized': normalized,
                 'frequency': latest['frequency_short'], 'frequency_long': latest['frequency'],
                 'seasonal_adjustment': latest['seasonal_adjustment_short'],
                 'observation_start': latest['observation_start'], 'observation_end': latest['observation_end'],
                 'last_updated': latest['last_updated'],
                 'third_party_copyright': copyright_holders(latest.get('notes') or '', config['copyright_sources']),
                 'units_history': history,
                 'units_change_across_vintages': len({h['source_units'] for h in history}) > 1}
        dates = client.get('series/vintagedates', series_id=identifier, limit=10000)
        if 'error' in dates:
            dropped[identifier] = dates['error']
            print('drop', identifier, dates['error'], file=sys.stderr)
            continue
        entry['vintage_dates'] = len(dates['vintage_dates'])
        entry['windows'] = windows(dates['vintage_dates'], config['max_vintage_dates_per_request'])
        for start, end in entry['windows']:
            answer = client.get('series/observations', series_id=identifier, realtime_start=start, realtime_end=end,
                                limit=1)
            if 'error' in answer:
                print('count failed', identifier, start, end, answer['error'], file=sys.stderr)
                continue
            counts[f'{identifier}|{start}|{end}'] = answer.get('count', 0)
        series[identifier] = entry
        if number % 50 == 0:
            print(number, 'candidates,', len(series), 'resolved', file=sys.stderr)
    config['series'], config['dropped'], config['request_counts'] = series, dropped, counts
    definition = write(config)
    changed = {k: [h['source_units'] for h in v['units_history']]
               for k, v in series.items() if v['units_change_across_vintages']}
    print(json.dumps({'series': len(series), 'requests': len(definition['acquisition']['combinations']),
                      'observations': sum(counts.get(f'{k}|{w[0]}|{w[1]}', 0)
                                          for k, v in series.items() for w in v['windows']),
                      'vintage_dates_total': sum(v['vintage_dates'] for v in series.values()),
                      'series_with_changing_units': len(changed), 'changing_units_examples': dict(list(changed.items())[:10]),
                      'non_normalized_units': sorted({v['source_units'] for v in series.values()
                                                      if not v['unit_normalized']}),
                      'dropped': dropped}, indent=1))


if __name__ == '__main__':
    main(update='--update' in sys.argv)
