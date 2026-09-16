"""GHCN-Daily by-station files -> station entities, daily observations (recent period) and monthly aggregates.

Inputs: `ghcnd-stations.txt` (fixed width, parsed by helpers.py) and `by_station/<ID>.csv.gz` files
(headerless: ID, YYYYMMDD, ELEMENT, VALUE, MFLAG, QFLAG, SFLAG, OBS-TIME). Only stations that have a data shard
are emitted from the metadata file. Units: TMAX/TMIN/TAVG tenths of degC -> degC, PRCP tenths of mm -> mm,
SNOW/SNWD mm. Values with a non-blank quality flag (QFLAG) failed NCEI QC and are excluded.
Daily values are emitted from `parameters.daily_from` (default 1991); monthly aggregates cover the full record:
temperature means need >= 25 valid days, precipitation/snowfall totals need at most 2 missing days.
"""
import calendar
import re

ELEMENTS = {'TMAX': ('maximum_temperature', 'degC', 0.1), 'TMIN': ('minimum_temperature', 'degC', 0.1),
            'TAVG': ('average_temperature', 'degC', 0.1), 'PRCP': ('precipitation', 'mm', 0.1),
            'SNOW': ('snowfall', 'mm', 1), 'SNWD': ('snow_depth', 'mm', 1)}
SUMMED = {'PRCP', 'SNOW'}
FIELDS = ['station', 'date', 'element', 'value', 'mflag', 'qflag', 'sflag', 'obs_time']
STATION_FILE = re.compile(r'by_station/([A-Z0-9]{11})\.csv(\.gz)?')


def _next_day(date):
    year, month, day = int(date[:4]), int(date[4:6]), int(date[6:8])
    if day < calendar.monthrange(year, month)[1]:
        return f'{year:04d}-{month:02d}-{day + 1:02d}'
    return f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'


def _shard_kind(shard, receipt):
    name = str((shard.get('request') or {}).get('url') or receipt.get('original_name') or '')
    match = STATION_FILE.search(name)
    if match:
        return 'data', match.group(1)
    if name.endswith('ghcnd-stations.txt'):
        return 'stations', None
    with open(shard['path'], 'rb') as stream:
        head = stream.read(2)
    return ('data', None) if head == b'\x1f\x8b' else ('stations', None)


def run(context):
    from worldmodel.raw_readers import iter_rows
    from worldmodel.source_helpers import STATE_FIPS
    from .helpers import station_records
    if not context.raw_inputs:
        raise ValueError('ghcn_daily: raw acquisition required')
    daily_from = str(context.parameters.get('daily_from', 1991))
    daily_scope = context.parameters.get('daily_stations', 'all')
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        shards = [(shard, *_shard_kind(shard, receipt)) for shard in context.raw_shards(index)]
        wanted = {station for _, kind, station in shards if kind == 'data' and station}
        daily = set()  # stations whose daily values are emitted (others get monthly aggregates only)
        for shard, kind, _ in shards:
            if kind != 'stations':
                continue
            with open(shard['path'], encoding='ascii', errors='replace') as stream:
                for number, station in station_records(stream):
                    if wanted and station['id'] not in wanted:
                        continue
                    reference = ((station['gsn'] and station['id'].startswith('US'))
                                 or (station['network'] == 'HCN' and station['id'].startswith('USW')))
                    if daily_scope == 'all' or reference:
                        daily.add(station['id'])
                    station['reference_station'] = reference
                    subject = 'ghcn:station:' + station['id']
                    evidence = context.raw_evidence(f'shard:{shard["index"]}/line:{number}', index)
                    yield {'kind': 'entity', 'id': 'ghcn:entity:' + station['id'], 'entity_id': subject,
                           'entity_type': 'location', 'label': f'{station["name"]} ({station["id"]})', 'observed_at': observed,
                           'evidence': evidence, 'attributes': {k: v for k, v in station.items() if k not in ('id', 'name')}}
                    fips = STATE_FIPS.get(station['state'] or '') if station['id'].startswith('US') else None
                    if fips:
                        yield {'kind': 'assertion', 'id': f'ghcn:within:{station["id"]}', 'subject': subject,
                               'predicate': 'within', 'object': 'geo:US:state:' + fips, 'observed_at': observed,
                               'evidence': evidence, 'attributes': {}}
                    for field, metric, unit in (('latitude', 'latitude', 'degrees'), ('longitude', 'longitude', 'degrees'),
                                                ('elevation_m', 'elevation', 'm')):
                        if station[field] is None:
                            continue
                        yield {'kind': 'observation', 'id': f'ghcn:{station["id"]}:{metric}', 'subject': subject,
                               'metric': metric, 'value': station[field], 'unit': unit, 'observed_at': observed,
                               'evidence': evidence, 'dimensions': {'point': 'station'}, 'attributes': {'valid_time_unknown': True}}
        for shard, kind, _ in shards:
            if kind == 'data':
                yield from _station_data(context, index, shard, observed, daily_from, iter_rows, daily)


def _station_data(context, index, shard, observed, daily_from, iter_rows, daily=()):
    months = {}  # (element, yyyymm) -> [sum, count, first locator]; <= ~10k keys per station
    config = {'format': 'csv', 'fieldnames': FIELDS, 'strict': False}
    for locator, row in iter_rows([shard], config):
        element = row['element']
        if element not in ELEMENTS or (row.get('qflag') or '').strip():
            continue
        metric, unit, scale = ELEMENTS[element]
        value = round(int(row['value']) * scale, 1)
        date = row['date']
        station = row['station']
        if date[:4] >= daily_from and (not daily or station in daily):
            yield {'kind': 'observation', 'id': f'ghcn:{station}:{element}:{date}', 'subject': 'ghcn:station:' + station,
                   'metric': metric, 'value': value, 'unit': unit, 'valid_from': f'{date[:4]}-{date[4:6]}-{date[6:8]}',
                   'valid_to': _next_day(date), 'observed_at': observed,
                   'evidence': context.raw_evidence(locator, index), 'dimensions': {'frequency': 'daily'},
                   'attributes': {k: v for k, v in (('mflag', (row.get('mflag') or '').strip()),
                                                   ('sflag', (row.get('sflag') or '').strip())) if v}}
        slot = months.setdefault((station, element, date[:6]), [0.0, 0, locator])
        slot[0] += value
        slot[1] += 1
    for (station, element, month), (total, count, locator) in sorted(months.items()):
        year, number = int(month[:4]), int(month[4:])
        days = calendar.monthrange(year, number)[1]
        metric, unit, _ = ELEMENTS[element]
        if element in SUMMED:
            if count < days - 2:
                continue
            value, aggregation = round(total, 1), 'sum'
        else:
            if count < 25:
                continue
            value, aggregation = round(total / count, 2), 'mean'
        yield {'kind': 'observation', 'id': f'ghcn:{station}:{element}:{month}:monthly', 'subject': 'ghcn:station:' + station,
               'metric': metric, 'value': value, 'unit': unit, 'valid_from': f'{year:04d}-{number:02d}-01',
               'valid_to': f'{year + (number == 12):04d}-{number % 12 + 1:02d}-01', 'observed_at': observed,
               'evidence': context.raw_evidence(locator, index),
               'dimensions': {'frequency': 'monthly', 'aggregation': aggregation},
               'attributes': {'valid_days': count, 'days_in_month': days}}
