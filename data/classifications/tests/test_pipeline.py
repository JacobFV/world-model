"""Offline fixture test for full NAICS/trade/SOC files (legacy sample path is covered by tests/test_normalizer_contracts.py)."""
import shutil
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


def xlsx(path, rows):
    """Minimal inline-string workbook; rows = {row number: {column letter: text}}."""
    body = ''.join(f'<row r="{n}">' + ''.join(f'<c r="{c}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for c, v in cells.items())
                   + '</row>' for n, cells in sorted(rows.items()))
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{body}</sheetData></worksheet>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook/>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)


class ClassificationsFullTest(unittest.TestCase):
    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            xlsx(tmp / 'n22.xlsx', {1: {'A': 'Seq. No.', 'B': '2022 NAICS US   Code', 'C': '2022 NAICS US Title'},
                                    3: {'A': '1', 'B': '31-33', 'C': 'Manufacturing'}, 4: {'A': '2', 'B': '311', 'C': 'Food Manufacturing'},
                                    5: {'A': '3', 'B': '3111', 'C': 'Animal Food Manufacturing'}})
            xlsx(tmp / 'c.xlsx', {2: {'A': '(Note: bold codes...)'},
                                  3: {'A': '2017 NAICS Code', 'B': '2017 NAICS Title\n(and specific piece)', 'C': '2022 NAICS Code', 'D': '2022 NAICS Title'},
                                  4: {'A': '511110', 'B': 'Newspaper Publishers', 'C': '513110', 'D': 'Newspaper Publishers'}})
            xlsx(tmp / 'exp.xlsx', {1: {'A': 'commodity', 'B': 'descriptn', 'C': 'abbreviatn', 'D': 'unit_qy1', 'E': 'unit_qy2', 'F': 'sitc',
                                        'G': 'end_use', 'H': 'naics', 'I': 'usda', 'J': 'hitech'},
                                    2: {'A': '0101210000', 'B': 'HORSES, PUREBRED BREEDING, LIVE', 'C': 'HORSES', 'D': 'NO', 'F': '00150',
                                        'G': '10140', 'H': '112920', 'I': '0', 'J': '00'}})
            xlsx(tmp / 'soc.xlsx', {8: {'A': 'Major Group', 'B': 'Minor Group', 'C': 'Broad Group', 'D': 'Detailed Occupation'},
                                    9: {'A': '11-0000', 'E': 'Management Occupations'}, 10: {'B': '11-1000', 'E': 'Top Executives'},
                                    11: {'C': '11-1010', 'E': 'Chief Executives'}, 12: {'D': '11-1011', 'E': 'Chief Executives'}})
            line = '0101210000    ' + 'HORSES, LIVE'.ljust(55) + 'HORSES, PUREBRED BREEDING, LIVE'.ljust(155) + 'NO'.ljust(16) + '00150     10140     0    112920     00\n'
            (tmp / 'exp-code.txt').write_text(line)
            base = 'https://www.census.gov/'
            shards = [{'path': str(tmp / 'n22.xlsx'), 'request': {'url': base + 'naics/2022NAICS/2-6%20digit_2022_Codes.xlsx'}},
                      {'path': str(tmp / 'c.xlsx'), 'request': {'url': base + 'naics/concordances/2017_to_2022_NAICS.xlsx'}},
                      {'path': str(tmp / 'exp.xlsx'), 'request': {'url': base + 'foreign-trade/reference/codes/concordance/expconcord22.xlsx'}},
                      {'path': str(tmp / 'exp-code.txt'), 'request': {'url': base + 'foreign-trade/schedules/b/2025/exp-code.txt'}},
                      {'path': str(tmp / 'soc.xlsx'), 'request': {'url': 'https://www.bls.gov/soc/2018/soc_structure_2018.xlsx'}}]
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        self.assertEqual(records['class:within:naics2022:311']['object'], 'naics2022:31-33')
        self.assertEqual(records['class:within:naics2022:3111']['object'], 'naics2022:311')
        self.assertEqual(records['class:entity:naics2022:3111']['attributes']['level'], 4)
        concord = records['class:concord:2017-2022:511110:513110']
        self.assertEqual((concord['subject'], concord['predicate'], concord['object']), ('naics2017:511110', 'maps_to', 'naics2022:513110'))
        self.assertEqual(records['class:classified:scheduleb2022:0101210000:naics']['object'], 'naics2017:112920')
        self.assertEqual(records['class:classified:scheduleb2025:0101210000:naics']['object'], 'naics2022:112920')
        self.assertEqual(records['class:within:scheduleb2022:0101210000']['object'], 'hs:010121')
        self.assertEqual(records['class:classified:scheduleb2022:0101210000:sitc']['object'], 'sitc4:00150')
        self.assertEqual(records['class:within:soc2018:11-1011']['object'], 'soc2018:11-1010')
        self.assertEqual(records['class:within:soc2018:11-1000']['object'], 'soc2018:11-0000')
        self.assertEqual(records['class:entity:soc2018:11-1011']['label'], 'Chief Executives')


if __name__ == '__main__':
    unittest.main()
