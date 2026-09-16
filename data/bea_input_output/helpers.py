"""Stdlib streaming reader for XLSX workbooks nested inside BEA ZIP archives.

The shared raw readers cannot open a workbook stored inside a ZIP archive, so this
module reads the inner workbook bytes (bounded) and streams worksheet rows with
``xml.etree.ElementTree.iterparse``. DTD/entity declarations are rejected.
"""
import io
import posixpath
import re
import zipfile
from xml.etree.ElementTree import fromstring, iterparse

MIB = 1024 * 1024
MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'
CELL = re.compile(r'([A-Z]+)([0-9]+)')


def column_name(position):
    name = ''
    while position:
        position, rest = divmod(position - 1, 26)
        name = chr(65 + rest) + name
    return name


def column_key(name):
    return (len(name), name)


def _guard(book, member, limit):
    info = book.getinfo(member)
    if info.file_size > limit:
        raise ValueError(f'XLSX member {member} exceeds {limit} bytes')
    with book.open(member) as stream:
        head = stream.read(4096)
    if re.search(rb'<!\s*(DOCTYPE|ENTITY)', head.replace(b'\x00', b''), re.I):
        raise ValueError('XLSX DTD/entities are forbidden')


def open_workbook(archive, member, limit=64 * MIB):
    """Return a ZipFile over a workbook stored as ``member`` of ``archive``."""
    if archive.getinfo(member).file_size > limit:
        raise ValueError(f'Workbook {member} exceeds {limit} bytes')
    return zipfile.ZipFile(io.BytesIO(archive.read(member)))


def sheets(book, limit=8 * MIB):
    """List ``(sheet name, worksheet XML path)`` in workbook order."""
    _guard(book, 'xl/workbook.xml', limit)
    workbook = fromstring(book.read('xl/workbook.xml'))
    targets = {}
    if 'xl/_rels/workbook.xml.rels' in book.namelist():
        _guard(book, 'xl/_rels/workbook.xml.rels', limit)
        for rel in fromstring(book.read('xl/_rels/workbook.xml.rels')).iter(PKG + 'Relationship'):
            target = rel.get('Target') or ''
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            targets[rel.get('Id')] = path
    result = []
    for position, sheet in enumerate(workbook.iter(MAIN + 'sheet'), 1):
        result.append((sheet.get('name'), targets.get(sheet.get(REL + 'id'), f'xl/worksheets/sheet{position}.xml')))
    return result


def shared_strings(book, limit=64 * MIB):
    if 'xl/sharedStrings.xml' not in book.namelist():
        return []
    _guard(book, 'xl/sharedStrings.xml', limit)
    strings = []
    with book.open('xl/sharedStrings.xml') as stream:
        for _, element in iterparse(stream, events=('end',)):
            if element.tag == MAIN + 'si':
                strings.append(''.join(t.text or '' for t in element.iter(MAIN + 't')))
                element.clear()
    return strings


def sheet_rows(book, path, strings, limit=256 * MIB):
    """Yield ``(row number, {column letter: text})`` for non-empty cells, streaming."""
    _guard(book, path, limit)
    with book.open(path) as stream:
        previous = 0
        for _, element in iterparse(stream, events=('end',)):
            if element.tag != MAIN + 'row':
                continue
            number = int(element.get('r') or previous + 1)
            previous = number
            values = {}
            for position, cell in enumerate(element.iter(MAIN + 'c'), 1):
                match = CELL.fullmatch(cell.get('r') or '')
                column = match.group(1) if match else column_name(position)
                kind = cell.get('t')
                if kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.iter(MAIN + 't'))
                else:
                    node = cell.find(MAIN + 'v')
                    text = node.text if node is not None else None
                    if kind == 's' and text is not None:
                        index = int(text)
                        if index >= len(strings):
                            raise ValueError('Invalid shared string reference')
                        text = strings[index]
                if text is not None and text != '':
                    values[column] = text
            element.clear()
            yield number, values
