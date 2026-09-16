"""Stdlib streaming readers for ESRI shapefile (.shp polygons) and dBASE (.dbf) members inside ZIP archives.

Only the pieces needed for Census cartographic boundary files: shape types 0 (null),
5 (Polygon) and 15 (PolygonZ, Z/M ignored); DBF character/numeric fields.
"""
import struct

SCALE = 100000  # 1e-5 degree (~1.1 m) quantization for compact geometry


def dbf_records(stream, encoding='utf-8'):
    """Yield dict rows from a DBF stream, in record order."""
    header = stream.read(32)
    if len(header) < 32:
        raise ValueError('Truncated DBF header')
    count, header_length, record_length = struct.unpack('<IHH', header[4:12])
    fields = []
    consumed = 32
    while True:
        first = stream.read(1)
        consumed += 1
        if first in (b'\r', b''):
            break
        descriptor = first + stream.read(31)
        consumed += 31
        name = descriptor[:11].split(b'\x00', 1)[0].decode('ascii')
        fields.append((name, chr(descriptor[11]), descriptor[16]))
    stream.read(header_length - consumed)
    if sum(size for _, _, size in fields) + 1 != record_length:
        raise ValueError('DBF field sizes do not match record length')
    for _ in range(count):
        record = stream.read(record_length)
        if len(record) < record_length:
            raise ValueError('Truncated DBF record')
        if record[:1] == b'*':
            yield None  # deleted record keeps its position aligned with the .shp
            continue
        offset, row = 1, {}
        for name, kind, size in fields:
            raw = record[offset:offset + size]
            offset += size
            text = raw.decode(encoding, errors='replace').strip()
            if kind in ('N', 'F') and text:
                number = float(text)
                row[name] = int(number) if number.is_integer() and '.' not in text else number
            else:
                row[name] = text
        yield row


def _read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Truncated shapefile')
    return data


def shp_records(stream):
    """Yield (record number, bbox, rings) where rings are lists of (lon, lat) float tuples."""
    header = _read(stream, 100)
    if struct.unpack('>i', header[:4])[0] != 9994:
        raise ValueError('Not an ESRI shapefile')
    while True:
        head = stream.read(8)
        if not head:
            return
        if len(head) < 8:
            raise ValueError('Truncated shapefile record header')
        number, words = struct.unpack('>ii', head)
        content = _read(stream, words * 2)
        shape_type = struct.unpack('<i', content[:4])[0]
        if shape_type == 0:
            yield number, None, []
            continue
        if shape_type not in (5, 15):
            raise ValueError(f'Unsupported shape type {shape_type}')
        bbox = struct.unpack('<4d', content[4:36])
        parts, points = struct.unpack('<ii', content[36:44])
        starts = struct.unpack(f'<{parts}i', content[44:44 + 4 * parts])
        base = 44 + 4 * parts
        coordinates = struct.unpack(f'<{2 * points}d', content[base:base + 16 * points])
        rings = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < parts else points
            rings.append([(coordinates[2 * k], coordinates[2 * k + 1]) for k in range(start, end)])
        yield number, bbox, rings


def encode_ring(ring, scale=SCALE):
    """Quantize to 1/scale degrees, drop repeated quantized vertices, delta-encode: [x0, y0, dx1, dy1, ...]."""
    out, last = [], None
    for lon, lat in ring:
        point = (round(lon * scale), round(lat * scale))
        if point == last:
            continue
        if last is None:
            out.extend(point)
        else:
            out.extend((point[0] - last[0], point[1] - last[1]))
        last = point
    return out


def decode_ring(values, scale=SCALE):
    x, y, points = 0, 0, []
    for i in range(0, len(values), 2):
        if i == 0:
            x, y = values[0], values[1]
        else:
            x, y = x + values[i], y + values[i + 1]
        points.append((x / scale, y / scale))
    return points
