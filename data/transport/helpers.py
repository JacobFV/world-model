"""Stdlib-only readers for the transport raw shards.

* :func:`shapefile_records` streams ESRI shapefile geometry (``.shp``) and
  attributes (``.dbf``) in lockstep from a ZIP archive such as a Census TIGER
  download, one record at a time.
* :func:`geojson_features` reads one ArcGIS GeoJSON query page.
* :func:`line_summary` reduces a polyline to length, bounding box and endpoints
  so normalized evidence does not copy full geometry.
"""
import json
import math
import struct
import zipfile

EARTH_RADIUS_KM = 6371.0088
SHAPE_NULL, SHAPE_POINT, SHAPE_POLYLINE, SHAPE_POLYGON = 0, 1, 3, 5
MAX_GEOJSON_BYTES = 128 * 1024 * 1024


def _exact(stream, size, what):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f'Truncated {what}')
    return data


def dbf_header(stream):
    head = _exact(stream, 32, 'DBF header')
    count, header_size, record_size = struct.unpack('<IHH', head[4:12])
    fields = []
    remaining = header_size - 32
    while remaining > 0:
        marker = stream.read(1)
        remaining -= 1
        if marker == b'\r':
            break
        descriptor = marker + _exact(stream, 31, 'DBF field descriptor')
        remaining -= 31
        name = descriptor[:11].split(b'\x00', 1)[0].decode('ascii')
        fields.append((name, chr(descriptor[11]), descriptor[16], descriptor[17]))
    if remaining > 0:
        _exact(stream, remaining, 'DBF header padding')
    if sum(f[2] for f in fields) + 1 != record_size:
        raise ValueError('DBF record size does not match field descriptors')
    return count, fields, record_size


def _dbf_value(raw, kind, encoding):
    text = raw.decode(encoding, 'replace').strip()
    if kind in ('N', 'F'):
        if not text or set(text) <= {'*'}:
            return None
        number = float(text)
        return int(number) if number.is_integer() and '.' not in text else number
    if kind == 'L':
        return {'T': True, 'Y': True, 'F': False, 'N': False}.get(text.upper()[:1])
    return text or None


def _shape(content):
    (shape_type,) = struct.unpack_from('<i', content, 0)
    if shape_type == SHAPE_NULL:
        return {'type': 'null', 'parts': []}
    if shape_type == SHAPE_POINT:
        x, y = struct.unpack_from('<2d', content, 4)
        return {'type': 'point', 'parts': [[(x, y)]]}
    if shape_type in (SHAPE_POLYLINE, SHAPE_POLYGON):
        parts_count, points_count = struct.unpack_from('<2i', content, 36)
        offsets = list(struct.unpack_from(f'<{parts_count}i', content, 44)) + [points_count]
        start = 44 + 4 * parts_count
        if len(content) < start + 16 * points_count:
            raise ValueError('Truncated shapefile polyline record')
        flat = struct.unpack_from(f'<{2 * points_count}d', content, start)
        points = list(zip(flat[0::2], flat[1::2]))
        parts = [points[offsets[i]:offsets[i + 1]] for i in range(parts_count)]
        return {'type': 'polyline' if shape_type == SHAPE_POLYLINE else 'polygon', 'parts': parts}
    raise ValueError(f'Unsupported shapefile shape type {shape_type}')


def shapefile_records(path, encoding=None):
    """Yield ``(record_number, attributes, shape)`` from the single ``.shp``/``.dbf`` pair in a ZIP."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shp = [n for n in names if n.lower().endswith('.shp')]
        dbf = [n for n in names if n.lower().endswith('.dbf')]
        if len(shp) != 1 or len(dbf) != 1:
            raise ValueError('Expected exactly one .shp and one .dbf member')
        cpg = [n for n in names if n.lower().endswith('.cpg')]
        if encoding is None:
            encoding = archive.read(cpg[0]).decode('ascii').strip() if cpg else 'latin-1'
            encoding = 'utf-8' if encoding.upper().replace('-', '') == 'UTF8' else encoding
        with archive.open(shp[0]) as shapes, archive.open(dbf[0]) as table:
            header = _exact(shapes, 100, 'shapefile header')
            if struct.unpack('>i', header[:4])[0] != 9994:
                raise ValueError('Not a shapefile')
            count, fields, record_size = dbf_header(table)
            for number in range(1, count + 1):
                record = _exact(table, record_size, 'DBF record')
                position = 1
                attributes = {}
                for name, kind, size, _ in fields:
                    attributes[name] = _dbf_value(record[position:position + size], kind, encoding)
                    position += size
                head = _exact(shapes, 8, 'shapefile record header')
                _, words = struct.unpack('>2i', head)
                shape = _shape(_exact(shapes, 2 * words, 'shapefile record'))
                if record[:1] == b'*':
                    continue
                yield number, attributes, shape


def geojson_features(path):
    with open(path, 'rb') as stream:
        content = stream.read(MAX_GEOJSON_BYTES + 1)
    if len(content) > MAX_GEOJSON_BYTES:
        raise ValueError('GeoJSON page exceeds the reader cap')
    payload = json.loads(content)
    if 'error' in payload:
        raise ValueError(f'ArcGIS query page returned an error: {payload["error"]}')
    if payload.get('type') != 'FeatureCollection':
        raise ValueError('Expected a GeoJSON FeatureCollection')
    return payload.get('features') or [], bool((payload.get('properties') or {}).get('exceededTransferLimit'))


def haversine_km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def geometry_parts(geometry):
    """Coordinate parts ``[[(lon, lat), ...], ...]`` from GeoJSON (Point/LineString/Polygon and Multi*)."""
    if not geometry:
        return []
    kind, coordinates = geometry.get('type'), geometry.get('coordinates')
    if kind == 'Point':
        return [[tuple(coordinates[:2])]]
    if kind in ('LineString', 'MultiPoint'):
        return [[tuple(c[:2]) for c in coordinates]]
    if kind in ('MultiLineString', 'Polygon'):
        return [[tuple(c[:2]) for c in part] for part in coordinates]
    if kind == 'MultiPolygon':
        return [[tuple(c[:2]) for c in ring] for polygon in coordinates for ring in polygon]
    raise ValueError(f'Unsupported GeoJSON geometry type {kind}')


def line_summary(parts, area=False):
    """Length (km, lines only), bbox, vertex count, endpoints and vertex-mean centroid."""
    points = [p for part in parts for p in part]
    if not points:
        return None
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    summary = {'bbox': [round(min(lons), 6), round(min(lats), 6), round(max(lons), 6), round(max(lats), 6)],
               'vertices': len(points), 'centroid': [round(sum(lons) / len(lons), 6), round(sum(lats) / len(lats), 6)]}
    if len(points) == 1:
        summary['point'] = [round(points[0][0], 6), round(points[0][1], 6)]
    elif not area:
        summary['length_km'] = round(sum(haversine_km(a[0], a[1], b[0], b[1]) for part in parts for a, b in zip(part, part[1:])), 4)
        summary['start'] = [round(parts[0][0][0], 6), round(parts[0][0][1], 6)]
        summary['end'] = [round(parts[-1][-1][0], 6), round(parts[-1][-1][1], 6)]
    return summary


# --- encoders for offline tests ----------------------------------------------

def write_shapefile_zip(path, fields, rows, encoding='utf-8'):
    """Write a tiny polyline/point shapefile ZIP: fields ``[(name, 'C'|'N', size, decimals)]``,
    rows ``[(attributes, [[(x, y), ...], ...])]``; single-vertex single-part rows become points."""
    shp_records = []
    for number, (_, parts) in enumerate(rows, 1):
        points = [p for part in parts for p in part]
        if len(parts) == 1 and len(points) == 1:
            content = struct.pack('<i2d', SHAPE_POINT, *points[0])
        else:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            offsets, total = [], 0
            for part in parts:
                offsets.append(total)
                total += len(part)
            content = (struct.pack('<i4d2i', SHAPE_POLYLINE, min(xs), min(ys), max(xs), max(ys), len(parts), len(points))
                       + struct.pack(f'<{len(parts)}i', *offsets) + struct.pack(f'<{2 * len(points)}d', *[c for p in points for c in p]))
        shp_records.append(struct.pack('>2i', number, len(content) // 2) + content)
    body = b''.join(shp_records)
    shp = struct.pack('>7i', 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack('<2i', 1000, SHAPE_POLYLINE) + bytes(64) + body
    record_size = 1 + sum(f[2] for f in fields)
    header = struct.pack('<B3BIHH20x', 3, 124, 1, 1, len(rows), 32 + 32 * len(fields) + 1, record_size)
    descriptors = b''.join(name.encode('ascii').ljust(11, b'\x00') + kind.encode() + bytes(4) + bytes([size, decimals]) + bytes(14)
                           for name, kind, size, decimals in fields)
    records = b''
    for attributes, _ in rows:
        records += b' '
        for name, kind, size, _ in fields:
            value = attributes.get(name)
            text = '' if value is None else str(value)
            encoded = text.encode(encoding)[:size]
            records += encoded.rjust(size) if kind == 'N' else encoded.ljust(size)
    dbf = header + descriptors + b'\r' + records + b'\x1a'
    with zipfile.ZipFile(path, 'w') as archive:
        stem = 'fixture'
        archive.writestr(stem + '.shp', shp)
        archive.writestr(stem + '.dbf', dbf)
        archive.writestr(stem + '.cpg', 'UTF-8')
