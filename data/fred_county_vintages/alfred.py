"""FRED/ALFRED evidence emission for ``fred_county_vintages``.

Derived from ``data/fred_state_employment_vintages/alfred.py``. The differences are exactly:

* Subjects are county-equivalents, ``geo:US:county:<SSCCC>``, with the code that
  ``build_config.py`` took from GeoFRED's published geography (or the publisher's id where GeoFRED
  is silent). The county entity sits ``within`` its state, which sits ``within`` the country.
* Series metadata is per family (``config.json``'s ``families``), not per series: 31,000 series share
  ten families, so a series carries only ``[family, fips, fips_source]``.
* One shard is one series over the whole real-time window in a single page. There is no
  window-splitting or offset merging, and a shard whose page stopped short of FRED's ``count`` is
  rejected rather than merged.
* Each observation says whether it is a **first release**: the earliest real-time period for its
  reference period, starting after the series' first ALFRED vintage. Rows present in that first
  vintage are the archive's opening snapshot of already-revised history
  (``attributes.realtime_start_clipped = true``) and are never first releases.
* The fredgraph.csv reader (current vintage only) is absent, as in the state dataset: a current-vintage
  code path would emit rows that are not real time.
"""
from datetime import date, timedelta
import json
import math

FIRST_REALTIME, LAST_REALTIME = '1776-07-04', '9999-12-31'
MISSING = ('', '.', 'NA', 'N/A', 'null')


def add_months(day, months):
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


def period(value, frequency):
    """Half-open [valid_from, valid_to) for a FRED observation date and frequency code."""
    day = date.fromisoformat(value)
    months = {'M': 1, 'Q': 3, 'SA': 6, 'A': 12}.get((frequency or '').upper())
    if months:
        return day.isoformat(), add_months(day, months).isoformat()
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def vintage_units(family, series_id, realtime_start):
    """Units published in one vintage (ALFRED observations carry none; real GDP is rebased)."""
    history = (family.get('units_history_exceptions') or {}).get(series_id) or family['units_history']
    for item in history:
        if item['realtime_start'] <= realtime_start <= item['realtime_end']:
            return item
    if realtime_start < history[0]['realtime_start']:
        return history[0]  # Observations predating ALFRED's metadata history: earliest published units.
    return history[-1]


def numeric(raw, multiplier):
    if raw is None or str(raw).strip() in MISSING:
        return None
    value = float(str(raw).replace(',', '')) * multiplier
    if not math.isfinite(value):
        raise ValueError('Nonfinite FRED value')
    if multiplier != 1:
        value = round(value, 9)   # Undo binary noise from the thousands multiplier: 3.372 * 1000 is 3372.0000000000005.
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


def code_scheme(family, fips):
    """BEA publishes combination areas (e.g. Virginia independent cities folded into a county) under
    county codes 900-999, which are not Census county FIPS codes."""
    if family.get('program') == 'bea_regional' and int(fips[2:]) >= 900:
        return 'bea_combination_area'
    return 'fips'


class Emitter:
    """Stream evidence records; entity memory is bounded by the configured series and counties."""

    def __init__(self, dataset, config):
        self.dataset, self.config = dataset, config
        self.entities = set()

    def entity(self, key, entity_type, label, evidence, observed_at, **attributes):
        if key in self.entities:
            return []
        self.entities.add(key)
        return [{'kind': 'entity', 'id': f'{self.dataset}:entity:{key}', 'entity_id': key, 'entity_type': entity_type,
                 'label': label, 'observed_at': observed_at, 'evidence': evidence,
                 'attributes': {'source_dataset': self.dataset, **attributes}}]

    def within(self, subject, parent, evidence, observed_at):
        return {'kind': 'assertion', 'id': f'{self.dataset}:within:{subject}', 'subject': subject,
                'predicate': 'within', 'object': parent, 'observed_at': observed_at, 'evidence': evidence,
                'attributes': {'source_dataset': self.dataset}}

    def context_records(self, series_id, evidence, observed_at):
        name, fips, fips_source = self.config['series'][series_id]
        family = self.config['families'][name]
        state, county = f'geo:US:state:{fips[:2]}', f'geo:US:county:{fips}'
        out = self.entity('geo:US', 'country', 'United States', evidence, observed_at, iso3='USA')
        created = self.entity(state, 'state', f'US state FIPS {fips[:2]}', evidence, observed_at, state_fips=fips[:2])
        out += created + ([self.within(state, 'geo:US', evidence, observed_at)] if created else [])
        label = self.config['counties'].get(fips) or f'US county {fips}'
        created = self.entity(county, 'county', label, evidence,
                              observed_at, county_fips=fips, state_fips=fips[:2], code_scheme=code_scheme(family, fips))
        out += created + ([self.within(county, state, evidence, observed_at)] if created else [])
        series_key = 'fred:' + series_id
        created = self.entity(series_key, 'economic_series', f'{family["geofred_title"]} in {label}', evidence, observed_at,
                              series_id=series_id, source_url='https://alfred.stlouisfed.org/series?seid=' + series_id,
                              family=name, metric=family['metric'], unit=family['unit'],
                              source_units=family['source_units'], frequency=family['frequency'],
                              seasonal_adjustment='NSA', release_id=family['release_id'],
                              release_name=family.get('release_name'), program=family['program'],
                              originating_source=family['originating_source'],
                              geofred_series_group=family['geofred_series_group'], county_fips=fips,
                              fips_source=fips_source, vintage_tier='vintages')
        if created:
            out += created
            out.append({'kind': 'assertion', 'id': f'{self.dataset}:describes:{series_id}', 'subject': series_key,
                        'predicate': 'describes_location', 'object': county, 'observed_at': observed_at,
                        'evidence': evidence, 'attributes': {'source_dataset': self.dataset}})
        return out

    def observation(self, series_id, row, evidence, *, archive_start, first_release, retrieved_at):
        name, fips, fips_source = self.config['series'][series_id]
        family = self.config['families'][name]
        units = vintage_units(family, series_id, row['realtime_start'])
        value = numeric(row['value'], units['multiplier'])
        valid_from, valid_to = period(row['date'], family['frequency'])
        start = row['realtime_start']
        record = {'kind': 'observation', 'id': f'fred:obs:{series_id}:{row["date"]}:{start}',
                  'subject': f'geo:US:county:{fips}', 'metric': family['metric'], 'value': value,
                  'unit': units['unit'], 'valid_from': valid_from, 'valid_to': valid_to,
                  # Knowledge time: the first real-time day this value was published in.
                  'observed_at': start,
                  'dimensions': {'series_id': series_id, 'family': name, 'program': family['program'],
                                 'frequency': family['frequency'], 'seasonal_adjustment': 'NSA',
                                 'vintage': start, 'first_release': first_release},
                  'evidence': evidence,
                  'attributes': {'source_dataset': self.dataset, 'series_id': series_id, 'source_series': series_id,
                                 'realtime_start': start, 'realtime_end': row['realtime_end'],
                                 'series_first_vintage': archive_start,
                                 # The archive's opening snapshot: already-revised history, never a first release.
                                 'realtime_start_clipped': start == archive_start,
                                 'vintage_tier': 'vintages', 'retrieved_at': retrieved_at,
                                 'fips_source': fips_source, 'code_scheme': code_scheme(family, fips),
                                 'source_units': units['source_units'], 'unit_multiplier': units['multiplier']}}
        if family.get('ownership'):
            record['dimensions']['ownership'] = family['ownership']
        if units.get('base_period'):
            record['attributes']['base_period'] = units['base_period']
            record['dimensions']['base_period'] = units['base_period']
        if family.get('units_change_across_vintages'):
            record['attributes']['units_change_across_vintages'] = True
        if value is None:
            record['missing_reason'] = 'not_available_in_vintage'
            record['attributes']['source_value'] = row['value']
        return record


def load(shard):
    """Read one series' FRED response, rejecting a page that stopped short of FRED's ``count``."""
    with open(shard['path'], 'rb') as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or 'observations' not in payload:
        raise ValueError(f'shard:{shard["index"]}: FRED payload lacks observations '
                         f'({payload.get("error_message") if isinstance(payload, dict) else "not an object"})')
    count, got = payload.get('count'), len(payload['observations'])
    if payload.get('offset', 0) != 0 or (count is not None and got < count):
        raise ValueError(f'shard:{shard["index"]}: FRED response holds {got} of {count} observations '
                         f'from offset {payload.get("offset", 0)}; raise the request limit')
    if (payload.get('realtime_start'), payload.get('realtime_end')) != (FIRST_REALTIME, LAST_REALTIME):
        raise ValueError(f'shard:{shard["index"]}: not the full real-time window; vintages would be lost')
    return payload


def first_releases(observations):
    """(archive_start, {(date, realtime_start)} of first releases) for one series' rows."""
    if not observations:
        return None, set()
    archive_start = min(row['realtime_start'] for row in observations)
    earliest = {}
    for row in observations:
        if row['date'] not in earliest or row['realtime_start'] < earliest[row['date']]:
            earliest[row['date']] = row['realtime_start']
    return archive_start, {(day, start) for day, start in earliest.items() if start > archive_start}


def api_records(context, dataset, config):
    """Yield records from FRED API observation shards, one series per shard."""
    emitter = Emitter(dataset, config)
    for shard in context.raw_shards(0):
        params = (shard.get('request') or {}).get('params') or {}
        series_id = params.get('series_id')
        if series_id not in config['series']:
            raise ValueError(f'shard:{shard["index"]}: series {series_id!r} is not configured')
        retrieved = shard.get('retrieved_at') or context.raw_receipt(0)['retrieved_at']
        payload = load(shard)
        yield from emitter.context_records(series_id, context.raw_evidence(f'shard:{shard["index"]}/record:0', 0),
                                           retrieved)
        archive_start, first = first_releases(payload['observations'])
        for number, row in enumerate(payload['observations']):
            yield emitter.observation(series_id, row, context.raw_evidence(f'shard:{shard["index"]}/record:{number}', 0),
                                      archive_start=archive_start,
                                      first_release=(row['date'], row['realtime_start']) in first,
                                      retrieved_at=retrieved)


def is_full(context):
    coverage = getattr(context, 'raw_coverage', None)
    if coverage is None:
        return False
    info = coverage()
    return not info['sampled'] and info['layout'] == 'shards'
