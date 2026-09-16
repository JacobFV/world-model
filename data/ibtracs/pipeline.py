"""IBTrACS v04r01 (since 1980) -> storm entities, genesis events and 3-hourly track observations.

The CSV has a header row followed by a units row (skipped). Rows are grouped by storm (SID),
so storm entities are emitted when the SID changes (no unbounded state). Positions use the
IBTrACS merged LAT/LON; intensity uses WMO-sanctioned agency values when present, with the
U.S. agency (JTWC/NHC) values as separate observations because WMO wind averaging periods differ
by agency (1-, 3- or 10-minute).
"""
from datetime import datetime, timedelta

READER = {'format': 'csv', 'strict': False}
BASINS = {'NA': 'North Atlantic', 'EP': 'Eastern North Pacific', 'WP': 'Western North Pacific', 'NI': 'North Indian',
          'SI': 'South Indian', 'SP': 'Southern Pacific', 'SA': 'South Atlantic', 'MM': 'Missing'}


def _num(text):
    text = (text or '').strip()
    if not text:
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def _time(text):
    moment = datetime.strptime(text.strip(), '%Y-%m-%d %H:%M:%S')
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ'), (moment + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ')


def run(context):
    if not context.raw_inputs:
        raise ValueError('ibtracs: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        storms, basins = set(), set()  # ~4k storm IDs since 1980 and 8 basins
        for locator, row in context.raw_rows(index, **READER):
            sid = (row.get('SID') or '').strip()
            if not sid or row.get('SEASON', '').strip() == 'Year':
                continue  # units row
            evidence = context.raw_evidence(locator, index)
            storm = 'ibtracs:storm:' + sid
            start, end = _time(row['ISO_TIME'])
            basin = row['BASIN'].strip() or 'MM'
            region = 'ibtracs:basin:' + basin
            if basin not in basins:
                basins.add(basin)
                yield {'kind': 'entity', 'id': 'ibtracs:entity:basin:' + basin, 'entity_id': region, 'entity_type': 'ocean',
                       'label': BASINS.get(basin, basin) + ' tropical cyclone basin', 'observed_at': observed,
                       'evidence': evidence, 'attributes': {'basin_code': basin}}
            if sid not in storms:
                storms.add(sid)
                name = row['NAME'].strip()
                label = f'{name.title() if name != "NOT_NAMED" else "Unnamed"} ({row["SEASON"].strip()}, {basin})'
                yield {'kind': 'entity', 'id': 'ibtracs:entity:' + sid, 'entity_id': storm, 'entity_type': 'entity',
                       'label': label, 'observed_at': observed, 'evidence': evidence,
                       'attributes': {'hazard_type': 'tropical_cyclone', 'sid': sid, 'name': name, 'season': int(row['SEASON']),
                                      'basin': basin, 'usa_atcf_id': row.get('USA_ATCF_ID', '').strip() or None}}
                yield {'kind': 'event', 'id': f'ibtracs:genesis:{sid}', 'event_type': 'tropical_cyclone_track_start',
                       'occurred_at': start, 'participants': [storm, region], 'observed_at': observed, 'evidence': evidence,
                       'attributes': {'nature': row['NATURE'].strip(), 'lat': _num(row['LAT']), 'lon': _num(row['LON'])}}
            stamp = start[:16].replace('-', '').replace('T', '').replace(':', '')
            track = row.get('TRACK_TYPE', '').strip()
            if track and track != 'main':
                stamp += ':' + track
            dims = {'track_type': row.get('TRACK_TYPE', '').strip(), 'nature': row.get('NATURE', '').strip(),
                    'interpolated': not row.get('IFLAG', 'O').strip().startswith('O'), 'basin': basin}
            values = [('LAT', 'latitude', 'degrees', {}), ('LON', 'longitude', 'degrees', {}),
                      ('DIST2LAND', 'distance_to_land', 'km', {}),
                      ('WMO_WIND', 'max_sustained_wind', 'kt', {'agency': row.get('WMO_AGENCY', '').strip() or None, 'source': 'wmo'}),
                      ('WMO_PRES', 'min_central_pressure', 'hPa', {'agency': row.get('WMO_AGENCY', '').strip() or None, 'source': 'wmo'}),
                      ('USA_WIND', 'max_sustained_wind', 'kt', {'agency': row.get('USA_AGENCY', '').strip() or None, 'source': 'usa', 'averaging': '1-minute'}),
                      ('USA_PRES', 'min_central_pressure', 'hPa', {'agency': row.get('USA_AGENCY', '').strip() or None, 'source': 'usa'}),
                      ('USA_SSHS', 'saffir_simpson_category', 'category', {'source': 'usa'}),
                      ('STORM_SPEED', 'storm_translation_speed', 'kt', {}),
                      ('STORM_DIR', 'storm_heading', 'degrees', {})]
            for field, metric, unit, extra in values:
                value = _num(row.get(field))
                if value is None:
                    continue
                suffix = extra.get('source', '')
                yield {'kind': 'observation', 'id': f'ibtracs:{sid}:{stamp}:{metric}{":" + suffix if suffix else ""}',
                       'subject': storm, 'metric': metric, 'value': value, 'unit': unit, 'valid_from': start, 'valid_to': end,
                       'observed_at': observed, 'evidence': evidence, 'dimensions': {**dims, **{k: v for k, v in extra.items() if v is not None}},
                       'attributes': {}}
