"""Stdlib streaming helpers for BLS flat files and XLSX workbooks (dataset-local).

BLS ``time.series`` files are tab separated with space-padded headers and values
and CRLF line endings. XLSX worksheets are streamed with ``iterparse`` so a
400k-row OEWS workbook never becomes an in-memory table.
"""
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MISSING = {'', '-', '.', '(NA)', 'NA', 'N/A'}


def shard_name(shard):
    """Published file name of a ``files`` shard (explicit name, else URL basename)."""
    request = shard.get('request') or {}
    name = shard.get('name') or request.get('name') or posixpath.basename((request.get('url') or shard.get('url') or '').split('?')[0])
    if not name:
        raise ValueError(f'shard {shard.get("index")}: cannot determine source file name')
    return name


def tab_rows(path, encoding='utf-8'):
    """Yield ``(physical line number, row dict)`` from a padded BLS tab file."""
    with open(path, 'rb') as raw:
        text = io.TextIOWrapper(raw, encoding=encoding, errors='replace', newline='')
        header = None
        for number, line in enumerate(text, 1):
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            values = [v.strip() for v in line.split('\t')]
            if header is None:
                header = values
                if len(set(header)) != len(header):
                    raise ValueError(f'{path}: duplicate headers')
                continue
            if len(values) < len(header):
                values += [''] * (len(header) - len(values))
            elif len(values) > len(header):
                if any(values[len(header):]):
                    raise ValueError(f'line {number}: {len(values)} fields, header has {len(header)}')
                values = values[:len(header)]
            yield number, dict(zip(header, values))


def mapping(path, key, value=None):
    """Load a small BLS mapping file keyed by ``key`` (tuple of column names allowed)."""
    result = {}
    for _, row in tab_rows(path):
        k = tuple(row[c] for c in key) if isinstance(key, tuple) else row[key]
        result[k] = row[value] if value else row
    return result


def number(text):
    text = (text or '').strip().replace(',', '')
    if text in MISSING:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float('inf'), float('-inf')):
        return None
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


def _month(year, month):
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1).isoformat()


def period_bounds(year, period):
    """Half-open validity interval for a BLS period code.

    Returns ``(valid_from, valid_to, frequency, period_type)`` or ``None`` for codes
    that are not calendar periods.
    """
    year = int(year)
    kind, n = period[:1], int(period[1:]) if period[1:].isdigit() else -1
    if kind == 'M' and 1 <= n <= 12:
        return _month(year, n), _month(year, n + 1), 'monthly', 'month'
    if kind == 'M' and n == 13:
        return f'{year:04d}-01-01', f'{year + 1:04d}-01-01', 'annual', 'annual_average'
    if kind == 'Q' and 1 <= n <= 4:
        return _month(year, 3 * n - 2), _month(year, 3 * n + 1), 'quarterly', 'quarter'
    if kind == 'Q' and n == 5:
        return f'{year:04d}-01-01', f'{year + 1:04d}-01-01', 'annual', 'annual_average'
    if kind == 'S' and n in (1, 2):
        return _month(year, 6 * n - 5), _month(year, 6 * n + 1), 'semiannual', 'half_year'
    if kind == 'S' and n == 3:
        return f'{year:04d}-01-01', f'{year + 1:04d}-01-01', 'annual', 'annual_average'
    if kind == 'A' and n == 1:
        return f'{year:04d}-01-01', f'{year + 1:04d}-01-01', 'annual', 'annual'
    return None


def footnotes(text):
    codes = [c for c in re.split(r'[\s,]+', text or '') if c]
    return codes


def base_unit(base):
    """'1982-84=100' -> index_1982_1984_100; '198200' -> index_1982_100; 'December 2020=100' -> index_2020_12_100."""
    base = (base or '').strip()
    months = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                          'september', 'october', 'november', 'december'], 1)}
    if re.fullmatch(r'\d{6}', base):
        return f'index_{base[:4]}_100' if base[4:] == '00' else f'index_{base[:4]}_{base[4:]}_100'
    match = re.fullmatch(r'(\d{4})-(\d{2})\s*=\s*100', base)
    if match:
        return f'index_{match[1]}_{match[1][:2]}{match[2]}_100'
    match = re.fullmatch(r'([A-Za-z]+)\s+(\d{4})\s*=\s*100', base)
    if match and match[1].lower() in months:
        return f'index_{match[2]}_{months[match[1].lower()]:02d}_100'
    match = re.fullmatch(r'(\d{4})\s*=\s*100', base)
    if match:
        return f'index_{match[1]}_100'
    slug = re.sub(r'[^a-z0-9]+', '_', base.lower()).strip('_')
    return 'index_' + (slug or 'unspecified_base')


# ----- XLSX streaming -------------------------------------------------------------------------

def _shared_strings(archive):
    if 'xl/sharedStrings.xml' not in archive.namelist():
        return []
    strings = []
    with archive.open('xl/sharedStrings.xml') as stream:
        for _, element in ET.iterparse(stream, events=('end',)):
            if element.tag == MAIN + 'si':
                strings.append(''.join(t.text or '' for t in element.iter(MAIN + 't')))
                element.clear()
    return strings


def sheet_members(archive):
    """Map worksheet names to archive members."""
    workbook = ET.fromstring(archive.read('xl/workbook.xml'))
    rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    targets = {r.get('Id'): r.get('Target') for r in rels}
    result = {}
    for sheet in workbook.iter(MAIN + 'sheet'):
        target = targets.get(sheet.get(REL + 'id'), '')
        target = target.lstrip('/')
        result[sheet.get('name')] = target if target.startswith('xl/') else 'xl/' + target
    return result


def xlsx_stream(archive, member, max_uncompressed_bytes=1 << 30):
    """Yield ``(row number, {column letter: text})`` for one worksheet, streaming."""
    info = archive.getinfo(member)
    if info.file_size > max_uncompressed_bytes:
        raise ValueError(f'{member}: worksheet exceeds {max_uncompressed_bytes} bytes')
    strings = _shared_strings(archive)
    with archive.open(member) as stream:
        head = stream.read(4096)
        if re.search(br'<!\s*(DOCTYPE|ENTITY)', head, re.I):
            raise ValueError('XLSX DTD/entities are forbidden')
    with archive.open(member) as stream:
        for _, element in ET.iterparse(stream, events=('end',)):
            if element.tag != MAIN + 'row':
                continue
            number = int(element.get('r'))
            values = {}
            for cell in element.iter(MAIN + 'c'):
                ref = cell.get('r', '')
                column = re.match(r'[A-Z]+', ref)
                if not column:
                    continue
                kind = cell.get('t')
                if kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.iter(MAIN + 't'))
                else:
                    v = cell.find(MAIN + 'v')
                    text = v.text if v is not None and v.text is not None else ''
                    if kind == 's' and text:
                        text = strings[int(text)]
                values[column[0]] = text
            element.clear()
            yield number, values


def xlsx_table(archive, member, header_row=1):
    """Yield ``(row number, {header: text})`` below ``header_row``."""
    header = None
    for number, values in xlsx_stream(archive, member):
        if number < header_row:
            continue
        if number == header_row:
            header = {k: v.strip() for k, v in values.items() if v and v.strip()}
            continue
        if header is None:
            raise ValueError(f'{member}: header row {header_row} missing')
        yield number, {name: values.get(column, '') for column, name in header.items()}


def open_zip_member(path, pattern):
    archive = zipfile.ZipFile(path)
    names = [n for n in archive.namelist() if re.search(pattern, n, re.I) and not n.startswith('__MACOSX/')]
    if len(names) != 1:
        archive.close()
        raise ValueError(f'{path}: expected exactly one member matching {pattern}, found {names}')
    return archive, names[0]
