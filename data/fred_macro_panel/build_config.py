"""Refresh fred_macro_panel series metadata, vintage windows and acquisition combinations.

Run from the project root:  python3 data/fred_macro_panel/build_config.py
Needs FRED_API_KEY (read from the environment or the project .env; never printed).
Stdlib only; ~1 FRED request per series (+1 per vintage-tier series), spaced <= 100/min.
Pipelines never import this module; it only rewrites config.json and dataset.json.
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
CURRENCIES = {'euro': 'EUR', 'japanese yen': 'JPY', 'chinese yuan renminbi': 'CNY', 'chinese yuan': 'CNY',
              'british pound sterling': 'GBP', 'british pound': 'GBP', 'canadian dollars': 'CAD',
              'canadian dollar': 'CAD', 'mexican pesos': 'MXN', 'mexican peso': 'MXN', 'south korean won': 'KRW',
              'indian rupees': 'INR', 'indian rupee': 'INR', 'brazilian reals': 'BRL', 'brazilian real': 'BRL',
              'swiss francs': 'CHF', 'swiss franc': 'CHF', 'australian dollar': 'AUD', 'u.s. dollar': 'USD',
              'u.k. pound sterling': 'GBP'}
SCALES = {'thousands': 1e3, 'millions': 1e6, 'billions': 1e9, 'trillions': 1e12}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def parse_units(units):
    """FRED units string -> (unit, multiplier, normalized?). Multipliers convert to base units."""
    text = units.strip()
    multiplier = 1.0
    match = re.match(r'(Thousands|Millions|Billions|Trillions)(?: of (.*))?$', text)
    if match:
        multiplier, text = SCALES[match[1].lower()], (match[2] or 'Number')
    if m := re.fullmatch(r'Level in (Thousands|Millions|Billions)', text):
        return 'count', SCALES[m[1].lower()], True
    text = re.sub(r'^(?:U\.S\.|US) ', '', text)
    if text in ('Dollars', 'Dollars, Seasonally Adjusted'):
        return 'USD', multiplier, True
    if m := re.fullmatch(r'(\d{4})-(\d{2}) CPI Adjusted Dollars', text):
        return f'USD_{m[1]}_{m[1][:2]}{m[2]}_cpi_adjusted', multiplier, True
    if text == 'Percent of GDP':
        return 'percent_of_gdp', multiplier, True
    if text == 'Rate':
        return 'rate', multiplier, True
    if m := re.fullmatch(r'Chained (\d{4}) Dollars', text):
        return f'USD_chained_{m[1]}', multiplier, True
    if m := re.fullmatch(r'(\d{4}) (?:U\.S\. )?Dollars', text):
        return f'USD_{m[1]}', multiplier, True
    if m := re.fullmatch(r'(\d{4}) C-CPI-U Dollars', text):
        return f'USD_{m[1]}_c_cpi_u', multiplier, True
    if text in ('Persons', 'Number', 'Number of Persons'):
        return ('persons' if 'Person' in text else 'count'), multiplier, True
    if text in ('Units', 'Number of Units'):
        return 'housing_units', multiplier, True
    if text == 'Percent' or text == 'Percent, Seasonally Adjusted':
        return 'percent', multiplier, True
    if text.startswith('Percent Change'):
        return 'percent_change_' + slug(text[len('Percent Change'):]), multiplier, True
    if m := re.fullmatch(r'Index (.+)=\s*100', text):
        return 'index_' + slug(m[1]) + '_100', multiplier, True
    if text.startswith('Index'):
        return slug(text), multiplier, True
    if m := re.fullmatch(r'Dollars per (.+)', text):
        return 'USD_per_' + slug(m[1]), multiplier, True
    if m := re.fullmatch(r'Dollars to One (.+)', text):
        code = CURRENCIES.get(m[1].lower())
        return ('USD_per_' + code, multiplier, True) if code else ('USD_per_' + slug(m[1]), multiplier, False)
    if m := re.fullmatch(r'(.+) to One U\.S\. Dollar', units.strip()):
        code = CURRENCIES.get(m[1].lower())
        return (code + '_per_USD', multiplier, True) if code else (slug(m[1]) + '_per_USD', multiplier, False)
    if text in ('Ratio', "Months' Supply", 'Hours', 'Weeks', 'Months'):
        return {'Ratio': 'ratio', "Months' Supply": 'months_supply', 'Hours': 'hours', 'Weeks': 'weeks',
                'Months': 'months'}[text], multiplier, True
    if text.startswith('+1 or 0'):
        return 'indicator_0_1', multiplier, True
    return slug(text) or 'unknown', multiplier, False


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
                with urlopen(Request(url, headers={'User-Agent': 'worldmodel-substrate/0.3 fred_macro_panel config'}),
                             timeout=60) as response:
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


def expand(config):
    for category, tier, series_id, metric in config['national']:
        yield series_id, {'category': category, 'tier': tier, 'metric': metric, 'geography': 'geo:US'}
    for category, tier, template, metric in config['state_families']:
        for abbr, fips in config['states'].items():
            yield template.format(abbr=abbr, fips=fips), {'category': category, 'tier': tier, 'metric': metric,
                                                          'geography': 'geo:US:state:' + fips, 'family': template}


def main(update=False):
    """Rebuild series metadata; with update=True reuse entries whose spec (tier, metric, geography) is unchanged."""
    config = json.loads((HERE / 'config.json').read_text())
    client = Client(load_key())
    old = config.get('series', {})
    series, dropped = {}, {}
    previously_dropped = config.get('dropped', {})
    for series_id, spec in expand(config):
        if series_id in series:
            raise SystemExit('Duplicate series in spec: ' + series_id)
        if update and series_id in previously_dropped:
            dropped[series_id] = previously_dropped[series_id]
            continue
        cached = old.get(series_id)
        if update and cached and all(cached.get(key) == value for key, value in spec.items()):
            series[series_id] = cached
            continue
        meta = client.get('series', series_id=series_id)
        if 'error' in meta or not meta.get('seriess'):
            dropped[series_id] = meta.get('error', 'no metadata')
            print('drop', series_id, dropped[series_id], file=sys.stderr)
            continue
        info = meta['seriess'][0]
        unit, multiplier, normalized = parse_units(info['units'])
        entry = {**spec, 'title': info['title'], 'source_units': info['units'], 'unit': unit,
                 'multiplier': multiplier, 'unit_normalized': normalized, 'frequency': info['frequency_short'],
                 'frequency_long': info['frequency'], 'seasonal_adjustment': info['seasonal_adjustment_short'],
                 'observation_start': info['observation_start'], 'last_updated': info['last_updated'],
                 'third_party_copyright': copyright_holders(info.get('notes') or '', config['copyright_sources'])}
        if spec['tier'] == 'vintages':
            dates = client.get('series/vintagedates', series_id=series_id, limit=10000)
            if 'error' in dates:
                dropped[series_id] = dates['error']
                continue
            entry['vintage_dates'] = len(dates['vintage_dates'])
            entry['windows'] = windows(dates['vintage_dates'], config['max_vintage_dates_per_request'])
        else:
            entry['windows'] = [[config['as_of'], LAST_REALTIME]]
        series[series_id] = entry
        if len(series) % 50 == 0:
            print(len(series), 'series', file=sys.stderr)
    config['series'] = series
    config['dropped'] = dropped
    (HERE / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=False) + '\n')
    definition_path = HERE / 'dataset.json'
    definition = json.loads(definition_path.read_text())
    definition['acquisition']['combinations'] = combinations(config)
    definition_path.write_text(json.dumps(definition, indent=2) + '\n')
    print(json.dumps({'series': len(series), 'requests': len(definition['acquisition']['combinations']),
                      'dropped': sorted(dropped), 'non_normalized_units': sorted(
                          {v['source_units'] for v in series.values() if not v['unit_normalized']})}, indent=1))


def base_period(units):
    """Base year/period a published unit is denominated in, e.g. 'Index 1982-1984=100' -> '1982_1984'."""
    text = units.strip()
    for pattern in (r'Index\s+(.+?)\s*=\s*100', r'Chained\s+(\d{4})\s+Dollars', r'of\s+(\d{4})\s+Dollars',
                    r'(\d{4}(?:-\d{2,4})?)\s+CPI Adjusted Dollars', r'^(\d{4})\s+(?:U\.S\. )?Dollars',
                    r'(\d{4})\s+C-CPI-U Dollars'):
        match = re.search(pattern, text)
        if match:
            return slug(match[1])
    return None


def units_history():
    """Record each series' published units per real-time vintage.

    ALFRED observations carry no units, and chained-dollar and index series are rebased at benchmark
    revisions, so a vintage's level is denominated in whatever base period was current then. Labelling
    every vintage with the series' present units makes cross-vintage levels incomparable.
    """
    config = json.loads((HERE / 'config.json').read_text())
    client = Client(load_key())
    for number, (series_id, entry) in enumerate(config['series'].items(), 1):
        answer = client.get('series', series_id=series_id, realtime_start=FIRST_REALTIME, realtime_end=LAST_REALTIME)
        rows = answer.get('seriess') if isinstance(answer, dict) else None
        if not rows:
            print('units history failed', series_id, answer.get('error', 'no rows'), file=sys.stderr)
            continue
        history = []
        for row in rows:
            unit, multiplier, normalized = parse_units(row['units'])
            item = {'realtime_start': row['realtime_start'], 'realtime_end': row['realtime_end'],
                    'source_units': row['units'], 'unit': unit, 'multiplier': multiplier,
                    'base_period': base_period(row['units']), 'unit_normalized': normalized}
            if history and all(history[-1][k] == item[k] for k in ('source_units', 'unit', 'multiplier')):
                history[-1]['realtime_end'] = item['realtime_end']  # Extend an unchanged run.
            else:
                history.append(item)
        entry['units_history'] = history
        entry['units_change_across_vintages'] = len({h['source_units'] for h in history}) > 1
        if number % 100 == 0:
            print(number, 'series', file=sys.stderr)
    (HERE / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=False) + '\n')
    changed = {k: [h['source_units'] for h in v['units_history']]
               for k, v in config['series'].items() if v.get('units_change_across_vintages')}
    print(json.dumps({'series_with_changing_units': len(changed),
                      'examples': dict(list(changed.items())[:10])}, indent=1))


def copyright_holders(notes, sources):
    """Named holders (case-sensitive whole words, so 'ICE' does not match 'price') or a generic copyright mention."""
    names = [name for name in sources if re.search(r'(?<![A-Za-z])' + re.escape(name) + r'(?![A-Za-z])', notes)]
    return names or (['see series notes'] if re.search(r'copyright|©', notes, re.IGNORECASE) else [])


def refresh(check_copyright=True):
    """Re-derive units from stored source_units and re-check flagged copyright notes (no full rebuild)."""
    config = json.loads((HERE / 'config.json').read_text())
    client = Client(load_key()) if check_copyright else None
    for series_id, entry in config['series'].items():
        entry['unit'], entry['multiplier'], entry['unit_normalized'] = parse_units(entry['source_units'])
        if client and entry.get('third_party_copyright'):
            meta = client.get('series', series_id=series_id)
            notes = (meta.get('seriess') or [{}])[0].get('notes') or ''
            entry['third_party_copyright'] = copyright_holders(notes, config['copyright_sources'])
    (HERE / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=False) + '\n')
    print(json.dumps({'non_normalized_units': sorted({v['source_units'] for v in config['series'].values()
                                                      if not v['unit_normalized']}),
                      'third_party_copyright': {k: v['third_party_copyright'] for k, v in config['series'].items()
                                                if v['third_party_copyright']}}, indent=1))


def validate():
    """Probe every as_of series with its real-time window (limit=1); drop series ALFRED rejects.

    Some FRED series (e.g. licensed indexes such as SP500) do not exist in ALFRED, so any
    realtime_start/realtime_end request fails with HTTP 400.
    """
    config = json.loads((HERE / 'config.json').read_text())
    client = Client(load_key())
    for series_id, entry in list(config['series'].items()):
        if entry['tier'] != 'as_of':
            continue
        start, end = entry['windows'][0]
        answer = client.get('series/observations', series_id=series_id, realtime_start=start, realtime_end=end, limit=1)
        if 'error' in answer:
            config['dropped'][series_id] = answer['error']
            del config['series'][series_id]
            print('drop', series_id, answer['error'], file=sys.stderr)
    (HERE / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=False) + '\n')
    definition_path = HERE / 'dataset.json'
    definition = json.loads(definition_path.read_text())
    definition['acquisition']['combinations'] = combinations(config)
    definition_path.write_text(json.dumps(definition, indent=2) + '\n')
    print(json.dumps({'series': len(config['series']), 'requests': len(definition['acquisition']['combinations']),
                      'dropped': config['dropped']}, indent=1))


PAGE_LIMIT = 100000


def combinations(config):
    """One request per (series, real-time window, offset slice).

    FRED caps a JSON response at limit=100000 observations, so windows with more rows are split
    into offset slices; `--counts` measures the row counts and records them in config['request_counts'].
    """
    counts = config.get('request_counts', {})
    out = []
    for series_id, entry in config['series'].items():
        for start, end in entry['windows']:
            total = counts.get(f'{series_id}|{start}|{end}')
            offsets = range(0, total, PAGE_LIMIT) if total else [0]
            for offset in offsets:
                out.append({'series_id': series_id, 'realtime_start': start, 'realtime_end': end, 'offset': offset})
    return out


def counts():
    """Measure rows per (series, window) with limit=1 and rewrite the offset-sliced combinations."""
    config = json.loads((HERE / 'config.json').read_text())
    client = Client(load_key())
    measured = dict(config.get('request_counts', {}))
    for number, (series_id, entry) in enumerate(config['series'].items(), 1):
        for start, end in entry['windows']:
            key = f'{series_id}|{start}|{end}'
            answer = client.get('series/observations', series_id=series_id, realtime_start=start, realtime_end=end,
                                limit=1)
            if 'error' in answer:
                print('count failed', key, answer['error'], file=sys.stderr)
                continue
            measured[key] = answer.get('count', 0)
        if number % 100 == 0:
            print(number, 'series counted', file=sys.stderr)
    config['request_counts'] = measured
    (HERE / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=False) + '\n')
    definition_path = HERE / 'dataset.json'
    definition = json.loads(definition_path.read_text())
    definition['acquisition']['combinations'] = combinations(config)
    definition_path.write_text(json.dumps(definition, indent=2) + '\n')
    oversized = {k: v for k, v in measured.items() if v > PAGE_LIMIT}
    print(json.dumps({'requests': len(definition['acquisition']['combinations']),
                      'windows_needing_slices': oversized,
                      'total_rows': sum(measured.values())}, indent=1))


if __name__ == '__main__':
    if '--refresh' in sys.argv:
        refresh()
    elif '--validate' in sys.argv:
        validate()
    elif '--counts' in sys.argv:
        counts()
    elif '--units-history' in sys.argv:
        units_history()
    else:
        main(update='--update' in sys.argv)
