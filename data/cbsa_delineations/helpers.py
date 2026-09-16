"""Stdlib reader for legacy Excel 97-2003 workbooks (.xls: BIFF8 records inside an OLE2 compound file).

Only what delineation lists need: shared strings (SST with CONTINUE), LABELSST, LABEL, NUMBER, RK,
MULRK, FORMULA cached numbers/strings and BOOLERR. Encrypted and pre-BIFF8 files are refused.
"""
import struct
from pathlib import Path

_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_END = 0xFFFFFFFA
MAX_XLS_BYTES = 64 * 1024 * 1024


def _compound_stream(data, names=('Workbook', 'Book')):
    if data[:8] != _MAGIC:
        raise ValueError('Not an OLE2 compound file (.xls)')
    sector_shift, mini_shift = struct.unpack_from('<HH', data, 0x1E)
    num_fat, dir_start = struct.unpack_from('<II', data, 0x2C)[0], struct.unpack_from('<I', data, 0x30)[0]
    mini_cutoff, minifat_start, _, difat_start, num_difat = struct.unpack_from('<IIIII', data, 0x38)
    size = 1 << sector_shift
    per = size // 4

    def sector(number):
        offset = 512 + number * size
        if offset + size > len(data):
            raise ValueError('Truncated compound file')
        return data[offset:offset + size]

    difat = list(struct.unpack_from('<109I', data, 0x4C))
    following = difat_start
    for _ in range(num_difat):
        if following >= _END:
            break
        values = struct.unpack(f'<{per}I', sector(following))
        difat.extend(values[:-1])
        following = values[-1]
    fat = []
    for number in difat[:num_fat]:
        if number < _END:
            fat.extend(struct.unpack(f'<{per}I', sector(number)))

    def chain(start, table):
        out, seen = [], set()
        while start < _END:
            if start in seen or start >= len(table):
                raise ValueError('Corrupt compound-file sector chain')
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    directory = b''.join(sector(n) for n in chain(dir_start, fat))
    entries = []
    for offset in range(0, len(directory) - 127, 128):
        entry = directory[offset:offset + 128]
        name_length = struct.unpack_from('<H', entry, 64)[0]
        name = entry[:max(name_length - 2, 0)].decode('utf-16-le', 'replace')
        start, low, high = struct.unpack_from('<III', entry, 116)
        entries.append((name, entry[66], start, low if sector_shift == 9 else low | high << 32))
    if not entries:
        raise ValueError('Empty compound-file directory')
    root_start = entries[0][2]
    for name, kind, start, length in entries:
        if kind != 2 or name not in names:
            continue
        if length < mini_cutoff:
            mini = b''.join(sector(n) for n in chain(root_start, fat))
            minifat = []
            for number in chain(minifat_start, fat):
                minifat.extend(struct.unpack(f'<{per}I', sector(number)))
            mini_size = 1 << mini_shift
            content = b''.join(mini[n * mini_size:(n + 1) * mini_size] for n in chain(start, minifat))
        else:
            content = b''.join(sector(n) for n in chain(start, fat))
        if len(content) < length:
            raise ValueError('Truncated workbook stream')
        return content[:length]
    raise ValueError('No Workbook stream in .xls file')


def _records(stream, position=0):
    while position + 4 <= len(stream):
        record_id, length = struct.unpack_from('<HH', stream, position)
        yield record_id, stream[position + 4:position + 4 + length]
        position += 4 + length


class _Segments:
    """Reads SST data that Excel splits across CONTINUE records."""

    def __init__(self, parts):
        self.parts, self.index, self.position = parts, 0, 0

    def take(self, count):
        out = bytearray()
        while count:
            while self.position >= len(self.parts[self.index]):
                self.index += 1
                self.position = 0
                if self.index >= len(self.parts):
                    raise ValueError('SST overrun')
            part = self.parts[self.index]
            step = min(count, len(part) - self.position)
            out += part[self.position:self.position + step]
            self.position += step
            count -= step
        return bytes(out)

    def chars(self, count, wide):
        out = []
        while count:
            if self.position >= len(self.parts[self.index]):
                self.index += 1
                if self.index >= len(self.parts):
                    raise ValueError('SST overrun')
                wide = self.parts[self.index][0] & 1  # a continued string restates its width flag
                self.position = 1
            part = self.parts[self.index]
            width = 2 if wide else 1
            step = min(count, (len(part) - self.position) // width)
            if step <= 0:
                raise ValueError('Corrupt SST string')
            raw = part[self.position:self.position + step * width]
            self.position += step * width
            count -= step
            out.append(raw.decode('utf-16-le' if wide else 'latin-1'))
        return ''.join(out)


def _shared_strings(parts):
    segments = _Segments(parts)
    _, unique = struct.unpack('<II', segments.take(8))
    strings = []
    for _ in range(unique):
        count, flags = struct.unpack('<HB', segments.take(3))
        runs = struct.unpack('<H', segments.take(2))[0] if flags & 0x08 else 0
        extra = struct.unpack('<I', segments.take(4))[0] if flags & 0x04 else 0
        strings.append(segments.chars(count, flags & 0x01))
        if runs:
            segments.take(4 * runs)
        if extra:
            segments.take(extra)
    return strings


def _unicode(body, offset):
    count, flags = struct.unpack_from('<HB', body, offset)
    offset += 3
    if flags & 0x08:
        offset += 2
    if flags & 0x04:
        offset += 4
    if flags & 0x01:
        return body[offset:offset + 2 * count].decode('utf-16-le')
    return body[offset:offset + count].decode('latin-1')


def _rk(value):
    if value & 0x02:
        number = struct.unpack('<i', struct.pack('<I', value))[0] >> 2
    else:
        number = struct.unpack('<d', b'\x00\x00\x00\x00' + struct.pack('<I', value & 0xFFFFFFFC))[0]
    return number / 100 if value & 0x01 else number


def xls_rows(path, sheet=0):
    """Yield (1-based row number, {0-based column: str|float|bool}) for one worksheet of a BIFF8 .xls."""
    path = Path(path)
    if path.stat().st_size > MAX_XLS_BYTES:
        raise ValueError('.xls file exceeds MAX_XLS_BYTES')
    stream = _compound_stream(path.read_bytes())
    sheets, sst, collecting, first = [], None, False, True
    for record_id, body in _records(stream):
        if first:
            if record_id != 0x0809 or struct.unpack_from('<H', body, 0)[0] != 0x0600:
                raise ValueError('Only BIFF8 (Excel 97-2003) .xls workbooks are supported')
            first = False
            continue
        if record_id == 0x002F:
            raise ValueError('Encrypted .xls workbooks are not supported')
        if record_id == 0x003C and collecting:
            sst.append(body)
            continue
        collecting = False
        if record_id == 0x0085:
            offset, kind = struct.unpack_from('<I', body, 0)[0], body[5]
            count, flags = body[6], body[7]
            raw = body[8:8 + count * (2 if flags & 1 else 1)]
            sheets.append((raw.decode('utf-16-le' if flags & 1 else 'latin-1'), kind, offset))
        elif record_id == 0x00FC:
            sst, collecting = [body], True
        elif record_id == 0x000A:
            break
    strings = _shared_strings(sst) if sst else []
    worksheets = [s for s in sheets if s[1] == 0]
    if sheet >= len(worksheets):
        raise ValueError('Worksheet index out of range')
    rows, pending = {}, None
    records = _records(stream, worksheets[sheet][2])
    next(records)  # worksheet BOF
    for record_id, body in records:
        if record_id == 0x000A:
            break
        if record_id == 0x00BD:
            row, column = struct.unpack_from('<HH', body, 0)
            last = struct.unpack_from('<H', body, len(body) - 2)[0]
            for step in range(last - column + 1):
                rows.setdefault(row, {})[column + step] = _rk(struct.unpack_from('<I', body, 4 + step * 6 + 2)[0])
            continue
        if record_id == 0x0207:
            if pending is not None:
                row, column = pending
                rows.setdefault(row, {})[column] = _unicode(body, 0)
                pending = None
            continue
        if record_id not in (0x00FD, 0x0204, 0x0203, 0x027E, 0x0006, 0x0205):
            continue
        row, column = struct.unpack_from('<HH', body, 0)
        if record_id == 0x00FD:
            value = strings[struct.unpack_from('<I', body, 6)[0]]
        elif record_id == 0x0204:
            value = _unicode(body, 6)
        elif record_id == 0x0203:
            value = struct.unpack_from('<d', body, 6)[0]
        elif record_id == 0x027E:
            value = _rk(struct.unpack_from('<I', body, 6)[0])
        elif record_id == 0x0205:
            if body[7]:
                continue  # error value
            value = bool(body[6])
        else:
            result = body[6:14]
            if result[6:8] == b'\xff\xff':
                if result[0] == 0:
                    pending = (row, column)
                elif result[0] == 1:
                    rows.setdefault(row, {})[column] = bool(result[2])
                continue
            value = struct.unpack('<d', result)[0]
        rows.setdefault(row, {})[column] = value
    for row in sorted(rows):
        yield row + 1, rows[row]
