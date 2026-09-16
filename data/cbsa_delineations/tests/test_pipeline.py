"""Offline fixture test: a generated BIFF8 .xls and an .xlsx delineation list -> dated membership crosswalks."""
import importlib.util
import shutil
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name
BASE = 'https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/'
HEADER = ['CBSA Code', 'Metropolitan Division Code', 'CSA Code', 'CBSA Title', 'Metropolitan/Micropolitan Statistical Area',
          'Metropolitan Division Title', 'CSA Title', 'County/County Equivalent', 'State Name', 'FIPS State Code',
          'FIPS County Code', 'Central/Outlying County']
ROWS = [['35620', '35614', '408', 'New York-Newark-Jersey City, NY-NJ-PA', 'Metropolitan Statistical Area',
         'New York-Jersey City-White Plains, NY-NJ', 'New York-Newark, NY-NJ-CT-PA', 'Bronx County', 'New York', '36', '005', 'Central'],
        ['10100', '', '', 'Aberdeen, SD', 'Micropolitan Statistical Area', '', '', 'Edmunds County', 'South Dakota', '46', '045', 'Outlying']]


def _record(record_id, body):
    return struct.pack('<HH', record_id, len(body)) + body


def _string(text):
    return struct.pack('<HB', len(text), 0) + text.encode('latin-1')


def write_xls(path, rows):
    """Minimal BIFF8 workbook in an OLE2 container; the SST is split by a CONTINUE record."""
    strings = sorted({c for row in rows for c in row if c and not c.isdigit()})
    index = {s: i for i, s in enumerate(strings)}
    sst = struct.pack('<II', len(strings), len(strings)) + b''.join(_string(s) for s in strings)
    cut = 8 + 3 + 4  # split inside the first string's characters
    sheet = [_record(0x0809, struct.pack('<HH', 0x0600, 0x10) + b'\x00' * 12)]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if not value:
                continue
            if value.isdigit() and c == 0:  # CBSA codes as RK integers
                sheet.append(_record(0x027E, struct.pack('<HHHI', r, c, 0, (int(value) << 2) | 2)))
            elif value.isdigit() and c == 2:
                sheet.append(_record(0x0203, struct.pack('<HHHd', r, c, 0, float(value))))
            elif c == 7:
                sheet.append(_record(0x0204, struct.pack('<HHH', r, c, 0) + _string(value)))
            elif value.isdigit():
                sheet.append(_record(0x0204, struct.pack('<HHH', r, c, 0) + _string(value)))
            else:
                sheet.append(_record(0x00FD, struct.pack('<HHHI', r, c, 0, index[value])))
    sheet.append(_record(0x000A, b''))
    glob_head = _record(0x0809, struct.pack('<HH', 0x0600, 0x05) + b'\x00' * 12)
    name = b'Sheet1'
    boundsheet_len = 4 + 8 + len(name)
    sst_records = _record(0x00FC, sst[:cut]) + _record(0x003C, b'\x00' + sst[cut:])
    offset = len(glob_head) + boundsheet_len + len(sst_records) + 4
    globals_ = glob_head + _record(0x0085, struct.pack('<IBBBB', offset, 0, 0, len(name), 0) + name) + sst_records + _record(0x000A, b'')
    stream = globals_ + b''.join(sheet)
    stream += b'\x00' * (-len(stream) % 512 or 0)
    if len(stream) < 4096:
        stream += b'\x00' * (4096 - len(stream))
    sectors = len(stream) // 512
    fat = [0xFFFFFFFD, 0xFFFFFFFE] + [3 + i if i < sectors - 1 else 0xFFFFFFFE for i in range(sectors)]
    fat += [0xFFFFFFFF] * (128 - len(fat))
    header = bytearray(512)
    header[:8] = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    struct.pack_into('<HHHH', header, 0x18, 0x3E, 3, 0xFFFE, 9)
    struct.pack_into('<H', header, 0x20, 6)
    struct.pack_into('<IIIIIIIII', header, 0x28, 0, 1, 1, 0, 4096, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    struct.pack_into('<109I', header, 0x4C, 0, *([0xFFFFFFFF] * 108))

    def entry(name, kind, start, size):
        encoded = (name + '\x00').encode('utf-16-le')
        raw = bytearray(128)
        raw[:len(encoded)] = encoded
        struct.pack_into('<HBB', raw, 64, len(encoded), kind, 1)
        struct.pack_into('<III', raw, 68, 0xFFFFFFFF, 0xFFFFFFFF, 1 if kind == 5 else 0xFFFFFFFF)
        struct.pack_into('<III', raw, 116, start, size, 0)
        return bytes(raw)

    directory = entry('Root Entry', 5, 0xFFFFFFFE, 0) + entry('Workbook', 2, 2, len(stream)) + b'\x00' * 256
    Path(path).write_bytes(bytes(header) + struct.pack('<128I', *fat[:128]) + directory + stream)


def write_xlsx(path, rows, header_row=3):
    table = {1: ['Table with row headers in column A and column headers in row 3'], 2: ['List 1. CBSAs, JULY 2023'],
             header_row: rows[0], **{header_row + 1 + i: row for i, row in enumerate(rows[1:])}}
    body = ''
    for n, row in sorted(table.items()):
        cells = ''.join(f'<c r="{chr(65 + c)}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for c, v in enumerate(row) if v)
        body += f'<row r="{n}">{cells}</row>'
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{body}</sheetData></worksheet>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook/>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)


class CbsaDelineationsTest(unittest.TestCase):
    def test_xls_reader(self):
        spec = importlib.util.spec_from_file_location('helpers', HERE / 'helpers.py')
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.xls'
            write_xls(path, [['Title'], [], HEADER] + ROWS)
            rows = dict(helpers.xls_rows(path))
        self.assertEqual(rows[1][0], 'Title')
        self.assertEqual(rows[3][11], 'Central/Outlying County')
        self.assertEqual((rows[4][0], rows[4][2], rows[4][10], rows[5][7]), (35620, 408.0, '005', 'Edmunds County'))

    def test_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'helpers.py', 'crosswalk_export.py'):
                shutil.copy2(HERE / name, catalog / name)
            write_xls(tmp / 'a.xls', [['Table'], ['List 1'], HEADER] + ROWS + [[], ['Note: footnote']])
            write_xlsx(tmp / 'b.xlsx', [HEADER, ROWS[1]] + [['Source: File prepared by U.S. Census Bureau']])
            shards = [{'path': str(tmp / 'a.xls'), 'request': {'url': BASE + '2020/delineation-files/list1_2020.xls'}},
                      {'path': str(tmp / 'b.xlsx'), 'request': {'url': BASE + '2023/delineation-files/list1_2023.xlsx'}}]
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            records = list(store.records(Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)))
        by_id = {r['id']: r for r in records}
        bronx = by_id['omb2020:omb_county_cbsa:36005:35620']
        self.assertEqual((bronx['subject'], bronx['object'], bronx['valid_from'], bronx['valid_to']),
                         ('geo:US:county:36005', 'geo:US:cbsa:35620', '2020-03-06', '2023-07-21'))
        self.assertEqual((bronx['attributes']['central_outlying'], bronx['attributes']['cbsa_type']), ('central', 'metropolitan'))
        self.assertEqual(by_id['omb2020:omb_county_csa:36005:408']['object'], 'geo:US:csa:408')
        self.assertEqual(by_id['omb2020:omb_metdiv_cbsa:35614:35620']['object'], 'geo:US:cbsa:35620')
        self.assertEqual(by_id['omb2020:type:10100']['value'], 'micropolitan_statistical_area')
        edmunds = by_id['omb2023:omb_county_cbsa:46045:10100']
        self.assertNotIn('valid_to', edmunds)
        self.assertEqual(by_id['omb2023:status:46045']['value'], 'outlying')
        spec = importlib.util.spec_from_file_location('crosswalk_export', HERE / 'crosswalk_export.py')
        xw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(xw)
        walk = xw.build_crosswalk(records, 'omb_county_cbsa_2020')
        self.assertEqual(walk.apportion({'36005': 5.0}, at='2021-01-01')['values'], {'35620': 5.0})
        self.assertEqual(xw.build_crosswalk(records, 'omb_county_cbsa_2023').targets('46045', at='2024-01-01')[0]['target'], '10100')


if __name__ == '__main__':
    unittest.main()
