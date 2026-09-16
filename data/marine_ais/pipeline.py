"""MarineCadastre AIS vessel tracks (monthly GeoParquet files, one shard per month).

Each source row is a cleaned track segment (at most one UTC day) for one MMSI
with vessel static fields, navigation status and a WKB LineString. The
normalizer emits:

* one ``vessel_track_segment`` event per row (participants ``mmsi:NNNNNNNNN``)
  with start/end time and position, bounding box, vertex count and haversine
  distance; the full geometry stays in the raw artifact;
* ``vessel_stationary_period`` events for segments that are moored/anchored
  (AIS status 1 or 5) or move less than ``stationary_max_km`` over at least
  ``stationary_min_minutes``; these are port-call / anchorage candidates to be
  matched against port layers (e.g. the ``transport`` World Port Index);
* per-vessel entities (``vessel``, one per MMSI across all months) with IMO
  identifiers, and per vessel-month observations (segment count, tracked hours,
  distance, maximum draft) plus reported dimensions (length, width).

Reading GeoParquet requires the optional ``pyarrow`` package (imported lazily).
Memory holds one Arrow batch plus small per-vessel and per-vessel-month
summaries (bounded by the number of distinct MMSIs, not by rows).
"""
import math
import struct
from datetime import datetime, timezone

EARTH_RADIUS_KM = 6371.0088
STATIONARY_STATUS = {1: 'at_anchor', 5: 'moored'}
AIS_STATUS = {0: 'under_way_using_engine', 1: 'at_anchor', 2: 'not_under_command', 3: 'restricted_manoeuvrability',
              4: 'constrained_by_draught', 5: 'moored', 6: 'aground', 7: 'engaged_in_fishing', 8: 'under_way_sailing',
              15: 'undefined'}
COLUMNS = ['mmsi', 'vessel_name', 'imo', 'call_sign', 'vessel_type', 'vessel_type_name', 'status', 'length', 'width',
           'draft', 'cargo', 'transceiver', 'duration_minutes', 'start_time', 'end_time', 'geometry']
STATIC_FIELDS = ('imo', 'call_sign', 'vessel_type', 'vessel_type_name', 'length', 'width', 'transceiver')
COVERAGE = 'US coastal AIS receivers (MarineCadastre); vessels outside coverage are absent'


def _pyarrow():
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise ValueError('marine_ais needs the optional pyarrow package to read GeoParquet '
                         '(pip install --user pyarrow)') from error
    return parquet


def wkb_points(blob):
    """Return ``[(lon, lat), ...]`` from WKB Point/LineString/Multi* (ISO Z/M codes or EWKB flags)."""
    points = []

    def read(offset):
        order = '<' if blob[offset] == 1 else '>'
        (kind,) = struct.unpack_from(order + 'I', blob, offset + 1)
        offset += 5
        if kind & 0x20000000:  # EWKB SRID
            offset += 4
        iso = (kind & 0x0FFFFFFF) // 1000
        base = (kind & 0x0FFFFFFF) % 1000
        has_z = bool(kind & 0x80000000) or iso in (1, 3)
        has_m = bool(kind & 0x40000000) or iso in (2, 3)
        dims = 2 + has_z + has_m
        if base == 1:
            values = struct.unpack_from(order + f'{dims}d', blob, offset)
            points.append((values[0], values[1]))
            return offset + 8 * dims
        if base == 2:
            (count,) = struct.unpack_from(order + 'I', blob, offset)
            offset += 4
            values = struct.unpack_from(order + f'{dims * count}d', blob, offset)
            points.extend(zip(values[0::dims], values[1::dims]))
            return offset + 8 * dims * count
        if base in (4, 5):
            (count,) = struct.unpack_from(order + 'I', blob, offset)
            offset += 4
            for _ in range(count):
                offset = read(offset)
            return offset
        raise ValueError(f'Unsupported WKB geometry type {kind}')

    read(0)
    return points


def track_distance_km(points):
    total = 0.0
    radians = math.radians
    for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
        p1, p2 = radians(lat1), radians(lat2)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(radians(lon2 - lon1) / 2) ** 2
        total += 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))
    return total


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        stamp = datetime.fromtimestamp(int(value) / 1e9, timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _month_window(period, fallback):
    if len(period) == 6 and period.isdigit():
        year, month = int(period[:4]), int(period[4:])
    else:
        year, month = int(fallback[:4]), int(fallback[5:7])
    return f'{year:04d}-{month:02d}-01', f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'


def run(context):
    if not context.raw_inputs or context.raw_coverage()['layout'] != 'shards':
        raise ValueError('No acquired source adapter: marine_ais (expects a sharded MarineCadastre GeoParquet acquisition)')
    yield from _normalize_full(context)


def _normalize_full(context):
    parquet = _pyarrow()
    parameters = context.parameters
    stationary_km = float(parameters.get('stationary_max_km', 1.0))
    stationary_minutes = float(parameters.get('stationary_min_minutes', 60))
    batch_size = int(parameters.get('batch_size', 50000))
    raw = context.raw_inputs[0]
    receipt = context.raw_receipt()
    complete = context.raw_coverage()['complete']
    profiles = {}  # mmsi -> identity profile across months
    months = {}    # (mmsi, period) -> activity summary
    periods_seen = set()
    for shard in context.raw_shards():
        name = shard.get('name') or f"shard{shard['index']}"
        period = ''.join(ch for ch in name if ch.isdigit())[:6] or f"shard{shard['index']}"
        if period in periods_seen:
            raise ValueError(f'Duplicate AIS source period {period} ({name})')
        periods_seen.add(period)
        observed = shard.get('retrieved_at') or receipt['retrieved_at']
        source = parquet.ParquetFile(shard['path'])
        available = set(source.schema_arrow.names)
        missing = [c for c in ('mmsi', 'start_time', 'end_time', 'geometry') if c not in available]
        if missing:
            raise ValueError(f'{name}: GeoParquet lacks columns {missing}')
        columns = [c for c in COLUMNS if c in available]
        row_number = -1
        for batch in source.iter_batches(batch_size=batch_size, columns=columns):
            data = {column: batch.column(column).to_pylist() for column in columns if column != 'geometry'}
            geometries = batch.column('geometry')
            for position in range(batch.num_rows):
                row_number += 1
                row = {column: _clean(values[position]) for column, values in data.items()}
                mmsi = row.get('mmsi')
                start, end = _iso(row.get('start_time')), _iso(row.get('end_time'))
                if mmsi is None or start is None:
                    continue
                blob = geometries[position].as_py()
                points = wkb_points(blob) if blob else []
                locator = f"shard:{shard['index']}/row:{row_number}"
                evidence = [{'input': raw, 'locator': locator}]
                vessel = f'mmsi:{mmsi}'
                distance = track_distance_km(points) if len(points) > 1 else 0.0
                attributes = {'end_time': end, 'duration_minutes': row.get('duration_minutes'), 'vertices': len(points),
                              'distance_km': round(distance, 3), 'source_period': period, 'complete_source': complete}
                bbox = None
                if points:
                    lons = [p[0] for p in points]
                    lats = [p[1] for p in points]
                    bbox = [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)]
                    attributes.update(bbox=bbox, start_position=[round(points[0][0], 5), round(points[0][1], 5)],
                                      end_position=[round(points[-1][0], 5), round(points[-1][1], 5)])
                status = row.get('status')
                for field in ('vessel_type', 'cargo', 'transceiver', 'draft'):
                    if row.get(field) is not None:
                        attributes[field] = round(row[field], 2) if isinstance(row[field], float) else row[field]
                if status is not None:
                    attributes['nav_status'] = status
                    attributes['nav_status_name'] = AIS_STATUS.get(status, 'reserved')
                identity = f'marinecadastre:track:{period}:{row_number}'
                yield {'kind': 'event', 'id': identity, 'event_type': 'vessel_track_segment', 'occurred_at': start,
                       'participants': [vessel], 'observed_at': observed, 'evidence': evidence, 'attributes': attributes}
                duration = row.get('duration_minutes') or 0
                diagonal = track_distance_km([(bbox[0], bbox[1]), (bbox[2], bbox[3])]) if bbox else None
                basis = None
                if status in STATIONARY_STATUS:
                    basis = 'ais_status_' + STATIONARY_STATUS[status]
                elif bbox and duration >= stationary_minutes and diagonal <= stationary_km:
                    basis = f'movement_within_{stationary_km:g}km_for_{stationary_minutes:g}min'
                if basis:
                    center = [round((bbox[0] + bbox[2]) / 2, 5), round((bbox[1] + bbox[3]) / 2, 5)] if bbox else None
                    yield {'kind': 'event', 'id': identity + ':stationary', 'event_type': 'vessel_stationary_period',
                           'occurred_at': start, 'participants': [vessel], 'observed_at': observed, 'evidence': evidence,
                           'attributes': {'end_time': end, 'duration_minutes': duration, 'basis': basis, 'center': center,
                                          'bbox_diagonal_km': None if diagonal is None else round(diagonal, 3),
                                          'port_match': 'not_performed; join center to port layers',
                                          'derived_from': identity}}
                profile = profiles.get(mmsi)
                if profile is None:
                    profile = profiles[mmsi] = {'first': start, 'last': end or start, 'locator': locator, 'observed': observed,
                                                'period': period, 'names': set(), 'static': {}}
                profile['first'] = min(profile['first'], start)
                profile['last'] = max(profile['last'], end or start)
                if row.get('vessel_name') and len(profile['names']) < 5:
                    profile['names'].add(row['vessel_name'])
                for field in STATIC_FIELDS:
                    if row.get(field) is not None and field not in profile['static']:
                        profile['static'][field] = row[field]
                summary = months.get((mmsi, period))
                if summary is None:
                    summary = months[(mmsi, period)] = {'segments': 0, 'minutes': 0, 'km': 0.0, 'draft': None,
                                                        'locator': locator, 'observed': observed, 'first': start}
                summary['segments'] += 1
                summary['minutes'] += duration
                summary['km'] += distance
                if row.get('draft') is not None:
                    summary['draft'] = max(summary['draft'] or 0, row['draft'])
    by_vessel = {}
    for mmsi, period in months:
        by_vessel.setdefault(mmsi, []).append(period)
    for mmsi in sorted(profiles):
        profile = profiles[mmsi]
        key = f'mmsi:{mmsi}'
        evidence = [{'input': raw, 'locator': profile['locator']}]
        static = profile['static']
        names = sorted(profile['names'])
        vessel_periods = sorted(by_vessel.get(mmsi, []))
        attributes = {'mmsi': mmsi, 'mmsi_plausible': 100000000 <= mmsi <= 999999999, 'vessel_names': names,
                      'first_seen': profile['first'], 'last_seen': profile['last'], 'first_row_locator': profile['locator'],
                      'source_periods': vessel_periods,
                      **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in static.items()}}
        yield {'kind': 'entity', 'id': key, 'entity_id': key, 'entity_type': 'vessel', 'label': names[0] if names else f'MMSI {mmsi}',
               'observed_at': profile['observed'], 'evidence': evidence, 'attributes': attributes}
        imo = str(static.get('imo') or '').upper().replace('IMO', '').strip()
        if imo.isdigit() and len(imo) == 7 and imo != '0000000':
            yield {'kind': 'assertion', 'id': key + ':identified_by:imo', 'observed_at': profile['observed'], 'evidence': evidence,
                   'subject': key, 'predicate': 'identified_by', 'value': 'imo:' + imo, 'attributes': {'scheme': 'IMO ship number'}}
        first_start, _ = _month_window(profile['period'], profile['first'])
        for field, metric in (('length', 'vessel_length'), ('width', 'vessel_width')):
            if static.get(field):
                yield {'kind': 'observation', 'id': f'{key}:{metric}', 'observed_at': profile['observed'], 'evidence': evidence,
                       'subject': key, 'metric': metric, 'value': round(float(static[field]), 2), 'unit': 'm',
                       'valid_from': first_start, 'dimensions': {},
                       'attributes': {'basis': 'first reported AIS static value', 'coverage': COVERAGE}}
        for period in vessel_periods:
            summary = months[(mmsi, period)]
            month_start, month_end = _month_window(period, summary['first'])
            month_evidence = [{'input': raw, 'locator': summary['locator']}]
            measures = [('vessel_track_segments', summary['segments'], 'segments'),
                        ('vessel_tracked_hours', round(summary['minutes'] / 60, 3), 'hours'),
                        ('vessel_track_distance', round(summary['km'], 3), 'km')]
            if summary['draft']:
                measures.append(('vessel_max_draft', round(float(summary['draft']), 2), 'm'))
            for metric, value, unit in measures:
                yield {'kind': 'observation', 'id': f'{key}:{metric}:{period}', 'observed_at': summary['observed'],
                       'evidence': month_evidence, 'subject': key, 'metric': metric, 'value': value, 'unit': unit,
                       'valid_from': month_start, 'valid_to': month_end, 'dimensions': {'source_period': period},
                       'attributes': {'coverage': COVERAGE, 'aggregate': True, 'frequency': 'monthly'}}
