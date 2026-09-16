"""NOAA NCEI Storm Events (bulk CSV) -> storm events, casualty/damage observations, fatalities and locations.

Three file families are acquired per year (details, fatalities, locations); each shard is recognized by its
header. Event IDs are NCEI EVENT_IDs (`noaa:storm_event:<EVENT_ID>`). Local begin/end times are converted to
UTC with the CZ_TIMEZONE offset. Damage strings such as '10.00K', '1.5M', '2B' are converted to USD
(nominal, as reported by NWS; not inflation adjusted). Casualty and damage observations are emitted only for
non-zero values; all reported values (including zeros) are also kept compactly in event attributes.
Geography: CZ_TYPE C -> `geo:US:county:<SSCCC>`; Z (public forecast zone) and M (marine zone) ->
`noaa:zone:<state fips>:<Z|M>:<zone>`.
"""
import re
from datetime import datetime, timedelta, timezone

READER = {'format': 'csv', 'encoding': 'latin-1', 'strict': False}
OFFSETS = {'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5, 'MST': -7, 'MDT': -6, 'PST': -8, 'PDT': -7, 'AKST': -9,
           'AKDT': -8, 'HST': -10, 'HDT': -9, 'AST': -4, 'ADT': -3, 'SST': -11, 'GST': 10, 'CHST': 10}
SCALE = {'H': 1e2, 'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
TZ = re.compile(r'^([A-Z]+)(-?\d+)?$')


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_') or 'unknown'


def _int(text):
    text = (text or '').strip()
    return int(float(text)) if text else None


def _float(text):
    text = (text or '').strip()
    if not text:
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def _damage(text):
    text = (text or '').strip().upper()
    if not text:
        return None
    match = re.fullmatch(r'([0-9.]*)([HKMBT]?)', text)
    if not match or not match.group(1).strip('.'):
        return 0 if match and match.group(2) else None
    value = float(match.group(1)) * SCALE.get(match.group(2), 1)
    return int(round(value))


def _moment(yearmonth, day, hhmm, zone):
    """Local timestamp -> (UTC ISO string, timezone_known)."""
    yearmonth, day, hhmm = yearmonth.strip(), int(day), (hhmm or '0').strip().zfill(4)
    local = datetime(int(yearmonth[:4]), int(yearmonth[4:6]), day, int(hhmm[:2]) % 24, int(hhmm[2:4]) % 60)
    match = TZ.match((zone or '').strip().upper())
    offset = None
    if match:
        offset = int(match.group(2)) if match.group(2) else OFFSETS.get(match.group(1))
    known = offset is not None
    moment = local - timedelta(hours=offset or 0)
    return moment.replace(tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), known


def _place(row):
    state = (row.get('STATE_FIPS') or '').strip().zfill(2)
    kind = (row.get('CZ_TYPE') or '').strip().upper()
    code = (row.get('CZ_FIPS') or '').strip().zfill(3)
    name = (row.get('CZ_NAME') or '').strip().title()
    if kind == 'C':
        return f'geo:US:county:{state}{code}', 'county', f'{name}, {row.get("STATE", "").strip().title()}'
    return f'noaa:zone:{state}:{kind or "X"}:{code}', 'location', f'NWS {"marine" if kind == "M" else "forecast"} zone {name}'


def _kind(row):
    if 'FATALITY_ID' in row:
        return 'fatality'
    if 'LOCATION_INDEX' in row:
        return 'location'
    return 'detail'


def _rows(context, index):
    """Tolerant streaming CSV reader: NCEI narratives contain unescaped quotes that the shared strict reader rejects.

    Yields (locator, row) with the same `shard:N/line:M` convention (M = physical line where the record starts).
    """
    import csv
    import gzip
    import io
    csv.field_size_limit(16 * 1024 * 1024)
    for shard in context.raw_shards(index):
        with open(shard['path'], 'rb') as probe:
            compressed = probe.read(2) == b'\x1f\x8b'
        binary = gzip.open(shard['path'], 'rb') if compressed else open(shard['path'], 'rb')
        with binary, io.TextIOWrapper(binary, encoding='latin-1', newline='') as text:
            reader = csv.reader(text, strict=False)
            header = next(reader, None)
            if not header:
                continue
            last = reader.line_num
            for values in reader:
                start, last = last + 1, reader.line_num
                if not values or values == ['']:
                    continue
                values = (values + [''] * len(header))[:len(header)]
                yield f'shard:{shard["index"]}/line:{start}', dict(zip(header, values))


def run(context):
    if not context.raw_inputs:
        raise ValueError('noaa_storm_events: raw acquisition required')
    emitted = set()  # geography entities only (counties + NWS zones, ~10k)
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        for locator, row in _rows(context, index):
            kind = _kind(row)
            evidence = context.raw_evidence(locator, index)
            if kind == 'detail':
                yield from _detail(row, evidence, observed, emitted)
            elif kind == 'fatality':
                yield from _fatality(row, evidence, observed)
            else:
                yield from _location(row, evidence, observed)


def _detail(row, evidence, observed, emitted):
    event_id = row['EVENT_ID'].strip()
    subject = 'noaa:storm_event:' + event_id
    place, place_type, place_label = _place(row)
    if place not in emitted:
        emitted.add(place)
        yield {'kind': 'entity', 'id': 'stormev:entity:' + place, 'entity_id': place, 'entity_type': place_type,
               'label': place_label, 'observed_at': observed, 'evidence': evidence,
               'attributes': {'nws_state': row.get('STATE', '').strip(), 'cz_type': row.get('CZ_TYPE', '').strip()}}
    zone = row.get('CZ_TIMEZONE', '')
    begin, known = _moment(row['BEGIN_YEARMONTH'], row['BEGIN_DAY'], row['BEGIN_TIME'], zone)
    end, _ = _moment(row['END_YEARMONTH'], row['END_DAY'], row['END_TIME'], zone)
    event_type = _slug(row['EVENT_TYPE'])
    values = {'deaths_direct': _int(row.get('DEATHS_DIRECT')), 'deaths_indirect': _int(row.get('DEATHS_INDIRECT')),
              'injuries_direct': _int(row.get('INJURIES_DIRECT')), 'injuries_indirect': _int(row.get('INJURIES_INDIRECT')),
              'damage_property': _damage(row.get('DAMAGE_PROPERTY')), 'damage_crops': _damage(row.get('DAMAGE_CROPS'))}
    attrs = {'event_type_label': row['EVENT_TYPE'].strip(), 'episode_id': _int(row.get('EPISODE_ID')), 'end_time': end,
             'wfo': row.get('WFO', '').strip(), 'source': row.get('SOURCE', '').strip() or None,
             'magnitude': _float(row.get('MAGNITUDE')), 'magnitude_type': row.get('MAGNITUDE_TYPE', '').strip() or None,
             'tor_f_scale': row.get('TOR_F_SCALE', '').strip() or None, 'tor_length_mi': _float(row.get('TOR_LENGTH')),
             'tor_width_yd': _float(row.get('TOR_WIDTH')), 'flood_cause': row.get('FLOOD_CAUSE', '').strip() or None,
             'begin_lat': _float(row.get('BEGIN_LAT')), 'begin_lon': _float(row.get('BEGIN_LON')),
             'end_lat': _float(row.get('END_LAT')), 'end_lon': _float(row.get('END_LON')),
             'data_source': row.get('DATA_SOURCE', '').strip() or None, 'timezone': zone.strip(), 'timezone_known': known,
             **{k: v for k, v in values.items()}}
    yield {'kind': 'event', 'id': f'stormev:event:{event_id}', 'event_type': event_type, 'occurred_at': begin,
           'participants': [subject, place], 'observed_at': observed, 'evidence': evidence,
           'attributes': {k: v for k, v in attrs.items() if v is not None}}
    for metric, unit in (('deaths_direct', 'people'), ('deaths_indirect', 'people'), ('injuries_direct', 'people'),
                         ('injuries_indirect', 'people'), ('damage_property', 'USD'), ('damage_crops', 'USD')):
        value = values[metric]
        if not value:
            continue
        base, _, scope = metric.rpartition('_') if metric.startswith(('deaths', 'injuries')) else (metric, '', None)
        name = 'storm_' + (base if scope else metric)
        dims = {'event_type': event_type, 'location': place, **({'attribution': scope} if scope else {})}
        yield {'kind': 'observation', 'id': f'stormev:{event_id}:{metric}', 'subject': subject, 'metric': name,
               'value': value, 'unit': unit, 'valid_from': begin, 'valid_to': end if end > begin else _plus_second(begin),
               'observed_at': observed, 'evidence': evidence, 'dimensions': dims,
               'attributes': {'nominal_usd': True} if unit == 'USD' else {}}


def _plus_second(stamp):
    moment = datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ') + timedelta(seconds=1)
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def _fatality(row, evidence, observed):
    fid = row['FATALITY_ID'].strip()
    yearmonth, day = row['FAT_YEARMONTH'].strip(), int(row['FAT_DAY'])
    hhmm = (row.get('FAT_TIME') or '0').strip().zfill(4)
    try:
        moment = datetime(int(yearmonth[:4]), int(yearmonth[4:6]), day, int(hhmm[:2]) % 24, int(hhmm[2:4]) % 60)
    except ValueError:  # a few records carry impossible days (e.g. 31 June); fall back to the first of the month
        moment = datetime(int(yearmonth[:4]), int(yearmonth[4:6]), 1)
    attrs = {'fatality_type': {'D': 'direct', 'I': 'indirect'}.get(row.get('FATALITY_TYPE', '').strip(), row.get('FATALITY_TYPE', '').strip()),
             'age': _int(row.get('FATALITY_AGE')), 'sex': row.get('FATALITY_SEX', '').strip() or None,
             'location': row.get('FATALITY_LOCATION', '').strip() or None, 'time_zone': 'local_unspecified'}
    # FATALITY_IDs are reused across yearly files; qualify by event and fatality month
    yield {'kind': 'event', 'id': f'stormev:fatality:{row["EVENT_ID"].strip()}:{yearmonth}:{fid}', 'event_type': 'storm_fatality',
           'occurred_at': moment.strftime('%Y-%m-%dT%H:%M:%SZ'), 'participants': ['noaa:storm_event:' + row['EVENT_ID'].strip()],
           'observed_at': observed, 'evidence': evidence, 'attributes': {k: v for k, v in attrs.items() if v is not None}}


def _location(row, evidence, observed):
    event_id, position = row['EVENT_ID'].strip(), row['LOCATION_INDEX'].strip()
    yearmonth = row['YEARMONTH'].strip()
    year, month = int(yearmonth[:4]), int(yearmonth[4:6])
    start = f'{year:04d}-{month:02d}-01'
    end = f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'
    dims = {'location_index': int(position), 'point': 'storm_event_location'}
    attrs = {'location': row.get('LOCATION', '').strip() or None, 'range_mi': _float(row.get('RANGE')),
             'azimuth': row.get('AZIMUTH', '').strip() or None, 'valid_time_precision': 'month_of_event'}
    attrs = {k: v for k, v in attrs.items() if v is not None}
    for field, metric in (('LATITUDE', 'latitude'), ('LONGITUDE', 'longitude')):
        value = _float(row.get(field))
        if value is None:
            continue
        yield {'kind': 'observation', 'id': f'stormev:{event_id}:loc{position}:{metric}',
               'subject': 'noaa:storm_event:' + event_id, 'metric': metric, 'value': value, 'unit': 'degrees',
               'valid_from': start, 'valid_to': end, 'observed_at': observed, 'evidence': evidence, 'dimensions': dims,
               'attributes': attrs if metric == 'latitude' else {}}
