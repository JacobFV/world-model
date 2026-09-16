"""FRED/ALFRED evidence emission shared by the fred_* datasets (identical copies; see tests).

Each dataset keeps its own copy because dataset code snapshots only capture local files.
Full artifacts come in two shapes:
- FRED API ``series/observations`` JSON shards (one per series x real-time window) whose request
  parameters name ``series_id``/``realtime_start``/``realtime_end``; every observation carries its
  ALFRED real-time period.
- fredgraph.csv shards (current vintage only) with columns ``observation_date`` and the series id.
"""
import csv
from datetime import date, timedelta
import json
import math
import re

FIRST_REALTIME, LAST_REALTIME = '1776-07-04', '9999-12-31'
MISSING = ('', '.', 'NA', 'N/A', 'null')
SCALE_PREFIX = {1e3: 'thousand', 1e6: 'million', 1e9: 'billion', 1e12: 'trillion', 1: None}


def add_months(day, months):
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


def period(value, frequency, frequency_long=''):
    """Half-open [valid_from, valid_to) for a FRED observation date and frequency code."""
    day = date.fromisoformat(value)
    code = (frequency or 'D').split(',')[0].strip().upper()
    if code.startswith('W') or code == 'BW':
        span = 14 if code == 'BW' or 'biweekly' in frequency_long.lower() else 7
        if 'ending' in frequency_long.lower():
            return (day - timedelta(days=span - 1)).isoformat(), (day + timedelta(days=1)).isoformat()
        return day.isoformat(), (day + timedelta(days=span)).isoformat()
    months = {'M': 1, 'Q': 3, 'SA': 6, 'A': 12}.get(code)
    if months:
        return day.isoformat(), add_months(day, months).isoformat()
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def vintage_units(meta, realtime_start):
    """Units published for one vintage.

    ALFRED observations carry no units and chained-dollar/index series are rebased at benchmark
    revisions, so a vintage's level is denominated in the base period current at that vintage.
    `units_history` (from build_config.py --units-history) holds those published units per real-time
    period; without it the series' present units are used and levels are not comparable across
    rebasings.
    """
    history = meta.get('units_history') or []
    for item in history:
        if item['realtime_start'] <= realtime_start <= item['realtime_end']:
            return item
    if history and realtime_start < history[0]['realtime_start']:
        return history[0]  # History predating ALFRED metadata: earliest published units.
    return history[-1] if history else None


def emitted_units(meta, realtime_start):
    """(unit, multiplier, source_units, base_period) for one vintage, honouring estimation overrides."""
    item = vintage_units(meta, realtime_start)
    source_units = item['source_units'] if item else meta.get('source_units')
    multiplier = item['multiplier'] if item else meta.get('multiplier', 1)
    unit = item['unit'] if item else meta['unit']
    base = item.get('base_period') if item else None
    pattern = meta.get('estimation_unit')
    if pattern:
        scale = meta.get('unit_scale', 1)
        pattern_base = re.search(r'\d{4}(?:_\d{4})?', pattern)
        if base and pattern_base and pattern_base[0] != base:
            # A rebased vintage is not the requirement's unit: name it after its own published units,
            # so cross-base levels can never be matched or compared as if they were the same unit.
            unit = '_'.join(filter(None, (SCALE_PREFIX.get(scale), item['unit'])))
        else:
            unit = pattern
        multiplier = multiplier / scale
    return unit, multiplier, source_units, base


def numeric(raw, multiplier):
    if raw is None or str(raw).strip() in MISSING:
        return None
    value = float(str(raw).replace(',', '')) * multiplier
    if not math.isfinite(value):
        raise ValueError('Nonfinite FRED value')
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


class Emitter:
    """Stream evidence records; entity memory is bounded by the configured series list."""

    def __init__(self, context, dataset, series_meta):
        self.context, self.dataset, self.meta = context, dataset, series_meta
        self.entities = set()

    def entity(self, key, entity_type, label, evidence, observed_at, **attributes):
        if key in self.entities:
            return []
        self.entities.add(key)
        return [{'kind': 'entity', 'id': f'{self.dataset}:entity:{key}', 'entity_id': key, 'entity_type': entity_type,
                 'label': label, 'observed_at': observed_at, 'evidence': evidence,
                 'attributes': {'source_dataset': self.dataset, **attributes}}]

    def context_records(self, series_id, evidence, observed_at):
        meta = self.meta[series_id]
        geography = meta.get('geography', 'geo:US')
        out = self.entity('geo:US', 'country', 'United States', evidence, observed_at, iso3='USA')
        if geography != 'geo:US':
            fips = geography.rsplit(':', 1)[1]
            out += self.entity(geography, 'state', meta.get('geography_label') or f'US state FIPS {fips}', evidence,
                               observed_at, state_fips=fips)
            if out and out[-1]['entity_id'] == geography:
                out.append({'kind': 'assertion', 'id': f'{self.dataset}:within:{geography}', 'subject': geography,
                            'predicate': 'within', 'object': 'geo:US', 'observed_at': observed_at,
                            'evidence': evidence, 'attributes': {'source_dataset': self.dataset}})
        series_key = 'fred:' + series_id
        created = self.entity(series_key, 'economic_series', meta.get('title') or series_id, evidence, observed_at,
                              series_id=series_id, source_url='https://fred.stlouisfed.org/series/' + series_id,
                              metric=meta['metric'], unit=meta['unit'], source_units=meta.get('source_units'),
                              multiplier=meta.get('multiplier', 1), frequency=meta.get('frequency'),
                              seasonal_adjustment=meta.get('seasonal_adjustment'), category=meta.get('category'),
                              vintage_tier=meta.get('tier'), third_party_copyright=meta.get('third_party_copyright', []))
        if created:
            out += created
            out.append({'kind': 'assertion', 'id': f'{self.dataset}:describes:{series_id}', 'subject': series_key,
                        'predicate': 'describes_location', 'object': geography, 'observed_at': observed_at,
                        'evidence': evidence, 'attributes': {'source_dataset': self.dataset}})
        return out

    def observation(self, series_id, row, evidence, *, realtime_start, realtime_end, clipped, retrieved_at, tier):
        meta = self.meta[series_id]
        unit, multiplier, source_units, base = emitted_units(meta, realtime_start)
        value = numeric(row['value'], multiplier)
        valid_from, valid_to = period(row['date'], meta.get('frequency'), meta.get('frequency_long', ''))
        record = {'kind': 'observation', 'id': f'fred:obs:{series_id}:{row["date"]}:{realtime_start}',
                  'subject': meta.get('geography', 'geo:US'), 'metric': meta['metric'], 'value': value,
                  'unit': unit, 'valid_from': valid_from, 'valid_to': valid_to,
                  # Knowledge time: the first real-time day this value was published in (clipped for as_of tier).
                  'observed_at': realtime_start if realtime_start != FIRST_REALTIME else retrieved_at,
                  'dimensions': {'series_id': series_id, 'frequency': meta.get('frequency'),
                                 'seasonal_adjustment': meta.get('seasonal_adjustment'), 'vintage': realtime_start},
                  'evidence': evidence,
                  'attributes': {'source_dataset': self.dataset, 'series_id': series_id, 'source_series': series_id,
                                 'realtime_start': realtime_start,
                                 'realtime_end': realtime_end, 'realtime_start_clipped': clipped,
                                 'vintage_tier': tier, 'retrieved_at': retrieved_at}}
        # Units as published in THIS vintage: chained-dollar and index series are rebased at benchmark
        # revisions, so levels are only comparable across vintages sharing a base period.
        record['attributes'].update(source_units=source_units, unit_multiplier=multiplier)
        if base:
            record['attributes']['base_period'] = base
            record['dimensions']['base_period'] = base
        if meta.get('units_change_across_vintages'):
            record['attributes']['units_change_across_vintages'] = True
        if meta.get('third_party_copyright'):
            record['attributes']['third_party_copyright'] = meta['third_party_copyright']
        if value is None:
            record['missing_reason'] = 'not_available_in_vintage' if tier != 'current' else 'source_missing'
            record['attributes']['source_value'] = row['value']
        return record


def _load(shard):
    """Read one FRED response shard, rejecting a page that silently stopped short of `count`.

    A window with more than `limit` observations is requested as several offset slices; a slice
    that returns exactly `limit` rows is a full page and is checked by the per-window coverage
    test in ``api_records`` instead.
    """
    with open(shard['path'], 'rb') as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or 'observations' not in payload:
        raise ValueError(f'shard:{shard["index"]}: FRED payload lacks observations '
                         f'({payload.get("error_message") if isinstance(payload, dict) else "not an object"})')
    count, got = payload.get('count'), len(payload['observations'])
    limit, offset = payload.get('limit'), payload.get('offset', 0)
    if count is not None and offset + got < count and (limit is None or got < limit):
        raise ValueError(f'shard:{shard["index"]}: FRED response truncated ({offset + got} of {count}); '
                         'split the window')
    return payload


def _check_window(window, coverage):
    """Every offset slice of one real-time window together must cover the window's row count."""
    if window is None or coverage['count'] is None:
        return
    if coverage['rows'] < coverage['count']:
        series, start, end = window
        raise ValueError(f'{series} {start}..{end}: offset slices cover {coverage["rows"]} of {coverage["count"]} '
                         'observations; rerun build_config.py --counts and re-acquire')


def api_records(context, dataset, series_meta, tier_of):
    """Yield records from FRED API observation shards, merging rows split at window boundaries.

    Rows whose real-time period ends exactly at a window end are held until the next window of the
    same series shows whether they continue (same date and value starting on the next window start).
    Held rows are bounded by one series' observation count.
    """
    emitter = Emitter(context, dataset, series_meta)
    index = 0
    current, held, previous_window = None, {}, None
    coverage = {'rows': 0, 'count': None}

    def emit(item):
        return emitter.observation(item['series'], item['row'], context.raw_evidence(item['locator'], index),
                                   realtime_start=item['start'], realtime_end=item['row']['realtime_end'],
                                   clipped=item['clipped'], retrieved_at=item['retrieved'], tier=item['tier'])

    for shard in context.raw_shards(index):
        params = (shard.get('request') or {}).get('params') or {}
        series = params.get('series_id')
        if series not in series_meta:
            raise ValueError(f'shard:{shard["index"]}: series {series!r} is not configured')
        window_start = params.get('realtime_start', FIRST_REALTIME)
        window_end = params.get('realtime_end', LAST_REALTIME)
        tier = tier_of(series)
        retrieved = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
        window = (series, window_start, window_end)
        if series != current:
            yield from map(emit, held.values())
            held, current = {}, series
        payload = _load(shard)
        yield from emitter.context_records(series, context.raw_evidence(f'shard:{shard["index"]}/record:0', index),
                                           retrieved)
        if window == previous_window:
            previous = {}  # Offset slice of the same window: keep accumulating into `held`.
        else:
            _check_window(previous_window, coverage)
            previous, held = held, {}
            coverage = {'rows': 0, 'count': payload.get('count')}
        previous_window = window
        coverage['rows'] += len(payload['observations'])
        for number, row in enumerate(payload['observations']):
            item = {'series': series, 'row': row, 'locator': f'shard:{shard["index"]}/record:{number}',
                    'start': row['realtime_start'], 'retrieved': retrieved, 'tier': tier,
                    'clipped': tier == 'as_of' and row['realtime_start'] == window_start}
            if row['realtime_start'] == window_start and window_start != FIRST_REALTIME:
                earlier = previous.pop(row['date'], None)
                if earlier and earlier['row']['value'] == row['value']:
                    item['start'], item['clipped'] = earlier['start'], earlier['clipped']  # Same vintage continues.
                elif earlier:
                    yield emit(earlier)
            if row['realtime_end'] == window_end and window_end != LAST_REALTIME:
                held[row['date']] = item  # May continue into the next window.
                continue
            yield emit(item)
        yield from map(emit, previous.values())  # Held rows that did not continue.
    _check_window(previous_window, coverage)
    yield from map(emit, held.values())


def graph_records(context, dataset, series_meta):
    """Yield records from fredgraph.csv shards (current vintage at retrieval)."""
    emitter = Emitter(context, dataset, series_meta)
    index = 0
    for shard in context.raw_shards(index):
        retrieved = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
        vintage = retrieved[:10]
        with open(shard['path'], encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            header = next(reader)
            if len(header) != 2 or header[0] not in ('observation_date', 'DATE') or header[1] not in series_meta:
                raise ValueError(f'shard:{shard["index"]}: unexpected fredgraph header {header}')
            series = header[1]
            yield from emitter.context_records(series, context.raw_evidence(f'shard:{shard["index"]}/line:2', index),
                                               retrieved)
            for line, values in enumerate(reader, 2):
                if not values:
                    continue
                if len(values) != 2:
                    raise ValueError(f'shard:{shard["index"]}/line:{line}: expected 2 fields')
                record = emitter.observation(series, {'date': values[0], 'value': values[1]},
                                             context.raw_evidence(f'shard:{shard["index"]}/line:{line}', index),
                                             realtime_start=vintage, realtime_end=LAST_REALTIME, clipped=True,
                                             retrieved_at=retrieved, tier='current')
                record['observed_at'] = retrieved
                yield record


def is_full(context):
    coverage = getattr(context, 'raw_coverage', None)
    if coverage is None:
        return False
    info = coverage()
    return not info['sampled'] and info['layout'] == 'shards'
