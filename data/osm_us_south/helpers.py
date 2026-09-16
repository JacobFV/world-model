"""Stdlib-only streaming reader for OpenStreetMap PBF files (``.osm.pbf``).

Format reference: https://wiki.openstreetmap.org/wiki/PBF_Format

A file is a sequence of ``[uint32 big-endian BlobHeader length][BlobHeader][Blob]``
frames. ``OSMHeader`` blobs carry a ``HeaderBlock``; ``OSMData`` blobs carry a
``PrimitiveBlock`` (string table, granularity/offsets and primitive groups with
plain nodes, dense nodes, ways or relations). Blobs are raw or zlib-compressed;
LZMA/LZ4/ZSTD blobs are rejected with a clear error.

Only one blob (at most 32 MiB decoded) is held in memory at a time, so callers
can index the frames once (:func:`blob_index`) and decode blocks independently,
e.g. in worker processes.
"""
import os
import struct
import zlib
from itertools import accumulate

MAX_HEADER_BYTES = 64 * 1024
MAX_BLOB_BYTES = 32 * 1024 * 1024
SUPPORTED_REQUIRED_FEATURES = {'OsmSchema-V0.6', 'DenseNodes'}
MEMBER_TYPES = ('node', 'way', 'relation')
GROUP_KINDS = {1: 'nodes', 2: 'dense', 3: 'ways', 4: 'relations', 5: 'changesets'}
_SIGN = 1 << 63
_WRAP = 1 << 64


def signed(value):
    """Interpret a decoded varint as a two's-complement int64."""
    return value - _WRAP if value >= _SIGN else value


def varint(buf, pos):
    result = shift = 0
    while True:
        try:
            byte = buf[pos]
        except IndexError:
            raise ValueError('Truncated protobuf varint') from None
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError('Protobuf varint is longer than 10 bytes')


def fields(buf, pos=0, end=None):
    """Yield ``(field_number, wire_type, value)`` for one protobuf message.

    Varint and fixed values are integers; length-delimited values are
    ``(start, end)`` offsets into ``buf``.
    """
    end = len(buf) if end is None else end
    while pos < end:
        key, pos = varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = varint(buf, pos)
        elif wire == 2:
            length, pos = varint(buf, pos)
            value = (pos, pos + length)
            pos += length
        elif wire == 5:
            if pos + 4 > end:
                raise ValueError('Truncated protobuf fixed32')
            value = struct.unpack_from('<I', buf, pos)[0]
            pos += 4
        elif wire == 1:
            if pos + 8 > end:
                raise ValueError('Truncated protobuf fixed64')
            value = struct.unpack_from('<Q', buf, pos)[0]
            pos += 8
        else:
            raise ValueError(f'Unsupported protobuf wire type {wire}')
        if pos > end:
            raise ValueError('Truncated protobuf message')
        yield number, wire, value


def packed(buf, start, end):
    """Decode a packed repeated varint field into a list of unsigned integers."""
    values = []
    append = values.append
    value = shift = 0
    for byte in buf[start:end]:
        if byte & 0x80:
            value |= (byte & 0x7F) << shift
            shift += 7
        else:
            append(value | (byte << shift))
            value = shift = 0
    if shift:
        raise ValueError('Truncated packed varint')
    return values


def zigzag(values):
    return [(v >> 1) ^ -(v & 1) for v in values]


def _extend(target, buf, wire, value):
    if wire == 2:
        target.extend(packed(buf, *value))
    elif wire == 0:
        target.append(value)
    else:
        raise ValueError('Expected a (packed) varint field')


def blob_index(path):
    """Return frame descriptors ``{index, offset, type, data_offset, size}`` without decoding blobs."""
    blobs = []
    size = os.path.getsize(path)
    with open(path, 'rb') as stream:
        offset = 0
        while offset < size:
            stream.seek(offset)
            prefix = stream.read(4)
            if len(prefix) < 4:
                raise ValueError(f'Truncated PBF frame at byte {offset}')
            (length,) = struct.unpack('>I', prefix)
            if not 0 < length <= MAX_HEADER_BYTES:
                raise ValueError(f'Invalid PBF BlobHeader length {length} at byte {offset}')
            header = stream.read(length)
            if len(header) != length:
                raise ValueError(f'Truncated PBF BlobHeader at byte {offset}')
            kind = datasize = None
            for number, wire, value in fields(header):
                if number == 1 and wire == 2:
                    kind = header[value[0]:value[1]].decode('utf-8')
                elif number == 3 and wire == 0:
                    datasize = value
            if kind is None or datasize is None:
                raise ValueError(f'PBF BlobHeader at byte {offset} lacks type or datasize')
            if datasize > MAX_BLOB_BYTES:
                raise ValueError(f'PBF blob at byte {offset} exceeds 32 MiB')
            start = offset + 4 + length
            if start + datasize > size:
                raise ValueError(f'Truncated PBF blob at byte {offset}')
            blobs.append({'index': len(blobs), 'offset': offset, 'type': kind, 'data_offset': start, 'size': datasize})
            offset = start + datasize
    return blobs


def decode_blob(data):
    raw = compressed = raw_size = None
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            raw = data[value[0]:value[1]]
        elif number == 2 and wire == 0:
            raw_size = value
        elif number == 3 and wire == 2:
            compressed = data[value[0]:value[1]]
        elif number in (4, 5, 6, 7):
            name = {4: 'lzma', 5: 'bzip2', 6: 'lz4', 7: 'zstd'}[number]
            raise ValueError(f'Unsupported PBF blob compression {name}; the stdlib reader handles raw and zlib blobs')
    if raw is not None:
        payload = raw
    elif compressed is not None:
        inflater = zlib.decompressobj()
        payload = inflater.decompress(compressed, MAX_BLOB_BYTES + 1)
        if len(payload) > MAX_BLOB_BYTES or inflater.unconsumed_tail:
            raise ValueError('Decoded PBF blob exceeds 32 MiB')
    else:
        raise ValueError('PBF blob has no data')
    if raw_size is not None and len(payload) != raw_size:
        raise ValueError('PBF blob raw_size does not match decoded bytes')
    return payload


def read_blob(path, blob, stream=None):
    if stream is None:
        with open(path, 'rb') as handle:
            return read_blob(path, blob, handle)
    stream.seek(blob['data_offset'])
    data = stream.read(blob['size'])
    if len(data) != blob['size']:
        raise ValueError('Truncated PBF blob read')
    return decode_blob(data)


def parse_header(data):
    header = {'bbox': None, 'required_features': [], 'optional_features': [], 'writing_program': None,
              'source': None, 'replication_timestamp': None, 'replication_sequence': None,
              'replication_base_url': None}
    names = {4: 'required_features', 5: 'optional_features', 16: 'writing_program', 17: 'source',
             34: 'replication_base_url'}
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            box = {}
            for n, w, v in fields(data, *value):
                if n in (1, 2, 3, 4) and w == 0:
                    box[{1: 'left', 2: 'right', 3: 'top', 4: 'bottom'}[n]] = ((v >> 1) ^ -(v & 1)) * 1e-9
            header['bbox'] = box
        elif number in names and wire == 2:
            text = data[value[0]:value[1]].decode('utf-8')
            if number in (4, 5):
                header[names[number]].append(text)
            else:
                header[names[number]] = text
        elif number == 32 and wire == 0:
            header['replication_timestamp'] = signed(value)
        elif number == 33 and wire == 0:
            header['replication_sequence'] = signed(value)
    unsupported = sorted(set(header['required_features']) - SUPPORTED_REQUIRED_FEATURES)
    if unsupported:
        raise ValueError('Unsupported required PBF features: ' + ', '.join(unsupported))
    return header


class PrimitiveBlock:
    """One decoded ``OSMData`` block. Coordinates are returned in degrees."""

    def __init__(self, data):
        self.data = data
        self.strings = []
        self.granularity = 100
        self.date_granularity = 1000
        self.lat_offset = self.lon_offset = 0
        self.groups = []
        for number, wire, value in fields(data):
            if number == 1 and wire == 2:
                strings = []
                for n, w, v in fields(data, *value):
                    if n == 1 and w == 2:
                        strings.append(data[v[0]:v[1]].decode('utf-8', 'replace'))
                self.strings = strings
            elif number == 2 and wire == 2:
                self.groups.append(value)
            elif number == 17 and wire == 0:
                self.granularity = signed(value)
            elif number == 18 and wire == 0:
                self.date_granularity = signed(value)
            elif number == 19 and wire == 0:
                self.lat_offset = signed(value)
            elif number == 20 and wire == 0:
                self.lon_offset = signed(value)
        if self.granularity <= 0:
            raise ValueError('Invalid PBF granularity')

    def group_kind(self, group):
        for number, _, _ in fields(self.data, *group):
            return GROUP_KINDS.get(number, 'unknown')
        return 'empty'

    def kinds(self):
        return {self.group_kind(group) for group in self.groups}

    def lat(self, raw):
        return (self.lat_offset + self.granularity * raw) * 1e-9

    def lon(self, raw):
        return (self.lon_offset + self.granularity * raw) * 1e-9

    def e7(self, raw, offset):
        """Coordinate as integer 1e-7 degrees (exact for the default granularity of 100)."""
        if offset == 0 and self.granularity == 100:
            return raw
        return round((offset + self.granularity * raw) / 100)

    def tags(self, keys, vals):
        if len(keys) != len(vals):
            raise ValueError('OSM tag keys and values differ in length')
        strings = self.strings
        try:
            return {strings[k]: strings[v] for k, v in zip(keys, vals)}
        except IndexError:
            raise ValueError('OSM tag string index outside string table') from None

    def dense_raw(self, group):
        """Return ``(ids, lat_raw, lon_raw, keys_vals)`` for a dense-node group (deltas resolved)."""
        data = self.data
        raw = {1: [], 8: [], 9: [], 10: []}
        for number, wire, value in fields(data, *group):
            if number != 2 or wire != 2:
                continue
            for n, w, v in fields(data, *value):
                if n in raw:
                    _extend(raw[n], data, w, v)
        ids = list(accumulate(zigzag(raw[1])))
        lats = list(accumulate(zigzag(raw[8])))
        lons = list(accumulate(zigzag(raw[9])))
        if not len(ids) == len(lats) == len(lons):
            raise ValueError('DenseNodes id/lat/lon arrays differ in length')
        return ids, lats, lons, raw[10]

    def dense_tags(self, keys_vals, count):
        """Yield ``(node_position, tags)`` for tagged nodes of a dense group."""
        if not keys_vals:
            return
        if len(keys_vals) == count:  # Only delimiters: no node carries tags.
            return
        strings = self.strings
        node = index = 0
        size = len(keys_vals)
        tags = {}
        try:
            while index < size:
                key = keys_vals[index]
                if key == 0:
                    if tags:
                        yield node, tags
                        tags = {}
                    node += 1
                    index += 1
                else:
                    if index + 1 >= size:
                        raise ValueError('Truncated DenseNodes keys_vals')
                    tags[strings[key]] = strings[keys_vals[index + 1]]
                    index += 2
        except IndexError:
            raise ValueError('DenseNodes tag string index outside string table') from None
        if tags or node != count:
            raise ValueError('DenseNodes keys_vals does not match the node count')

    def dense(self, group):
        """Yield ``(id, lat, lon, tags)`` for every node in a dense group."""
        ids, lats, lons, keys_vals = self.dense_raw(group)
        tagged = dict(self.dense_tags(keys_vals, len(ids)))
        for position, node_id in enumerate(ids):
            yield node_id, self.lat(lats[position]), self.lon(lons[position]), tagged.get(position, {})

    def nodes(self, group):
        """Yield ``(id, lat, lon, tags)`` for plain (non-dense) nodes."""
        data = self.data
        for number, wire, value in fields(data, *group):
            if number != 1 or wire != 2:
                continue
            node_id = lat = lon = None
            keys, vals = [], []
            for n, w, v in fields(data, *value):
                if n == 1 and w == 0:
                    node_id = (v >> 1) ^ -(v & 1)
                elif n == 2:
                    _extend(keys, data, w, v)
                elif n == 3:
                    _extend(vals, data, w, v)
                elif n == 8 and w == 0:
                    lat = (v >> 1) ^ -(v & 1)
                elif n == 9 and w == 0:
                    lon = (v >> 1) ^ -(v & 1)
            if node_id is None or lat is None or lon is None:
                raise ValueError('PBF node lacks id or coordinates')
            yield node_id, self.lat(lat), self.lon(lon), self.tags(keys, vals)

    def ways(self, group, want=None):
        """Yield ``(id, tags, refs)``; ``want(tags)`` skips decoding refs of unwanted ways."""
        data = self.data
        for number, wire, value in fields(data, *group):
            if number != 3 or wire != 2:
                continue
            way_id = None
            keys, vals, spans = [], [], []
            for n, w, v in fields(data, *value):
                if n == 1 and w == 0:
                    way_id = signed(v)
                elif n == 2:
                    _extend(keys, data, w, v)
                elif n == 3:
                    _extend(vals, data, w, v)
                elif n == 8:
                    spans.append((w, v))
            if way_id is None:
                raise ValueError('PBF way lacks id')
            tags = self.tags(keys, vals)
            if want is not None and not want(tags):
                continue
            refs = []
            for w, v in spans:
                _extend(refs, data, w, v)
            yield way_id, tags, list(accumulate(zigzag(refs)))

    def relations(self, group, want=None):
        """Yield ``(id, tags, members)`` with members as ``(type, ref, role)``."""
        data = self.data
        strings = self.strings
        for number, wire, value in fields(data, *group):
            if number != 4 or wire != 2:
                continue
            relation_id = None
            keys, vals, roles, memids, types = [], [], [], [], []
            for n, w, v in fields(data, *value):
                if n == 1 and w == 0:
                    relation_id = signed(v)
                elif n == 2:
                    _extend(keys, data, w, v)
                elif n == 3:
                    _extend(vals, data, w, v)
                elif n == 8:
                    _extend(roles, data, w, v)
                elif n == 9:
                    _extend(memids, data, w, v)
                elif n == 10:
                    _extend(types, data, w, v)
            if relation_id is None:
                raise ValueError('PBF relation lacks id')
            tags = self.tags(keys, vals)
            if want is not None and not want(tags):
                continue
            if not len(roles) == len(memids) == len(types):
                raise ValueError('PBF relation member arrays differ in length')
            try:
                members = [(MEMBER_TYPES[t], ref, strings[signed(r)])
                           for t, ref, r in zip(types, accumulate(zigzag(memids)), roles)]
            except IndexError:
                raise ValueError('PBF relation member type or role outside range') from None
            yield relation_id, tags, members


def iter_elements(path):
    """Yield ``('header', dict)`` then ``(kind, id, payload)`` for every element (small files and tests).

    Nodes: ``('node', id, (lat, lon, tags))``; ways: ``('way', id, (tags, refs))``;
    relations: ``('relation', id, (tags, members))``.
    """
    with open(path, 'rb') as stream:
        for blob in blob_index(path):
            data = read_blob(path, blob, stream)
            if blob['type'] == 'OSMHeader':
                yield 'header', None, parse_header(data)
                continue
            if blob['type'] != 'OSMData':
                continue
            block = PrimitiveBlock(data)
            for group in block.groups:
                kind = block.group_kind(group)
                if kind == 'dense':
                    for node_id, lat, lon, tags in block.dense(group):
                        yield 'node', node_id, (lat, lon, tags)
                elif kind == 'nodes':
                    for node_id, lat, lon, tags in block.nodes(group):
                        yield 'node', node_id, (lat, lon, tags)
                elif kind == 'ways':
                    for way_id, tags, refs in block.ways(group):
                        yield 'way', way_id, (tags, refs)
                elif kind == 'relations':
                    for relation_id, tags, members in block.relations(group):
                        yield 'relation', relation_id, (tags, members)


# --- minimal encoder used by offline tests to build fixture PBF files ---------

def _enc_varint(value):
    if value < 0:
        value += _WRAP
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _enc_zigzag(value):
    return (value << 1) ^ (value >> 63)


def _field(number, wire, payload):
    key = _enc_varint((number << 3) | wire)
    if wire == 0:
        return key + _enc_varint(payload)
    return key + _enc_varint(len(payload)) + payload


def _packed(values):
    return b''.join(_enc_varint(v) for v in values)


def _deltas(values):
    previous = 0
    out = []
    for value in values:
        out.append(_enc_zigzag(value - previous))
        previous = value
    return out


def encode_pbf(nodes=(), ways=(), relations=(), compress=True, bbox=None):
    """Encode a tiny PBF: nodes ``(id, lat, lon, tags)``, ways ``(id, tags, refs)``,
    relations ``(id, tags, [(type, ref, role)])``. One block per element kind."""
    strings = ['']
    index = {'': 0}

    def sid(text):
        if text not in index:
            index[text] = len(strings)
            strings.append(text)
        return index[text]

    def frame(kind, payload):
        blob = _field(2, 0, len(payload)) + (_field(3, 2, zlib.compress(payload)) if compress else _field(1, 2, payload))
        header = _field(1, 2, kind.encode()) + _field(3, 0, len(blob))
        return struct.pack('>I', len(header)) + header + blob

    header_block = _field(4, 2, b'OsmSchema-V0.6') + _field(4, 2, b'DenseNodes') + _field(16, 2, b'worldmodel-test')
    if bbox:
        left, bottom, right, top = bbox
        header_block = _field(1, 2, b''.join(_field(n, 0, _enc_zigzag(round(v * 1e9)))
                                             for n, v in ((1, left), (2, right), (3, top), (4, bottom)))) + header_block
    out = [frame('OSMHeader', header_block)]
    groups = []
    if nodes:
        kv = []
        for _, _, _, tags in nodes:
            for key, value in tags.items():
                kv += [sid(key), sid(value)]
            kv.append(0)
        dense = (_field(1, 2, _packed(_deltas([n[0] for n in nodes])))
                 + _field(8, 2, _packed(_deltas([round(n[1] * 1e7) for n in nodes])))
                 + _field(9, 2, _packed(_deltas([round(n[2] * 1e7) for n in nodes])))
                 + _field(10, 2, _packed(kv)))
        groups.append(_field(2, 2, dense))
    if ways:
        body = b''
        for way_id, tags, refs in ways:
            body += _field(3, 2, _field(1, 0, way_id) + _field(2, 2, _packed([sid(k) for k in tags]))
                           + _field(3, 2, _packed([sid(v) for v in tags.values()]))
                           + _field(8, 2, _packed(_deltas(refs))))
        groups.append(body)
    if relations:
        body = b''
        for relation_id, tags, members in relations:
            body += _field(4, 2, _field(1, 0, relation_id) + _field(2, 2, _packed([sid(k) for k in tags]))
                           + _field(3, 2, _packed([sid(v) for v in tags.values()]))
                           + _field(8, 2, _packed([sid(m[2]) for m in members]))
                           + _field(9, 2, _packed(_deltas([m[1] for m in members])))
                           + _field(10, 2, _packed([MEMBER_TYPES.index(m[0]) for m in members])))
        groups.append(body)
    for group in groups:
        table = _field(1, 2, b''.join(_field(1, 2, s.encode()) for s in strings))
        out.append(frame('OSMData', table + _field(2, 2, group)))
    return b''.join(out)
