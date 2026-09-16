"""Offline fixture test: Census concordance xlsx, WITS zip (cp1252) and UNSD two-sheet workbook."""
import importlib.util
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from worldmodel.catalog import Catalog
from worldmodel.crosswalks import load_concordance_csv
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name


def _sheet(rows):
    body = ''.join(f'<row r="{n}">' + ''.join(f'<c r="{c}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for c, v in cells.items())
                   + '</row>' for n, cells in sorted(rows.items()))
    return ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{body}</sheetData></worksheet>')


def xlsx(path, *sheets):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook/>')
        for number, rows in enumerate(sheets, 1):
            archive.writestr(f'xl/worksheets/sheet{number}.xml', _sheet(rows))


def adapter():
    spec = importlib.util.spec_from_file_location('crosswalk_export', HERE / 'crosswalk_export.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TradeConcordancesTest(unittest.TestCase):
    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'crosswalk_export.py'):
                shutil.copy2(HERE / name, catalog / name)
            head = dict(zip('ABCDEFGHIJ', ['commodity', 'descriptn', 'abbreviatn', 'unit_qy1', 'unit_qy2', 'sitc', 'end_use', 'naics', 'usda', 'hitech']))
            xlsx(tmp / 'imp.xlsx', {1: head,
                                    2: dict(zip('ABCDFGHIJ', ['0101210010', 'HORSES MALE', 'HORSES', 'NO', '00150', '12060', '112920', '0', '00'])),
                                    3: dict(zip('ABCDFGHIJ', ['0101210020', 'HORSES FEMALE', 'HORSES', 'NO', '00150', '12060', '112920', '0', '00'])),
                                    4: dict(zip('ABCDFGHIJ', ['0302110000', 'TROUT', 'TROUT', 'KG', '03411', '00010', '1141XX', '1', '00'])),
                                    5: dict(zip('ABCDFGHIJ', ['0302119000', 'TROUT OTHER', 'TROUT', 'KG', '03411', '00010', '112511', '1', '00']))})
            with zipfile.ZipFile(tmp / 'wits.zip', 'w') as archive:
                archive.writestr('JobID-81_Concordance_H4_to_I3.CSV', ('HS 2012 Product Code,HS 2012 Product Description,ISIC Revision 3 Product Code,'
                                                                       'ISIC Revision 3 Product Description\r\n090300,Maté.,0113,"Growing of crops"\r\n').encode('cp1252'))
                archive.writestr('[Content_Types].xml', '<Types/>')
            xlsx(tmp / 'un.xlsx', {1: {'A': 'From HS2022', 'B': 'To HS2017'}, 2: {'A': '010121', 'B': '010121'}, 3: {'A': '970690', 'B': '970600'}},
                 {1: {'A': 'Between'}, 2: {'A': 'HS2022', 'B': ' HS2017', 'C': 'Relationship'},
                  3: {'A': '010121', 'B': '010121', 'C': '1:1'}, 4: {'A': '970610', 'B': '970600', 'C': 'n:1'},
                  5: {'A': '970690', 'B': '970600', 'C': 'n:1'}, 6: {'A': '970690', 'B': '970500', 'C': '1:n'}})
            shards = [{'path': str(tmp / 'imp.xlsx'), 'request': {'url': 'https://www.census.gov/foreign-trade/reference/codes/concordance/impconcord26.xlsx'}},
                      {'path': str(tmp / 'wits.zip'), 'request': {'url': 'https://wits.worldbank.org/data/public/concordance/Concordance_H4_to_I3.zip'}},
                      {'path': str(tmp / 'un.xlsx'), 'request': {'url': 'https://unstats.un.org/unsd/classifications/Econ/tables/HS2022toHS2017ConversionAndCorrelationTables.xlsx'}}]
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            records = list(store.records(Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)))
            by_id = {r['id']: r for r in records}
            xw = adapter()
            self.assertEqual(xw.crosswalk_ids(records), {
                'census_hs22_naics2022_imports2026': 3, 'census_hts2026_enduse': 4, 'census_hts2026_hs22': 4,
                'census_hts2026_naics2022': 4, 'census_hts2026_sitc4': 4, 'un_hs22_hs17_conversion': 2,
                'un_hs22_hs17_correlation': 4, 'wits_hs12_isic3': 1})
            naics = by_id['xw:census_hts2026_naics2022:0101210010:112920']
            self.assertEqual((naics['subject'], naics['object'], naics['valid_to']), ('hts2026:0101210010', 'naics2022:112920', '2027-01-01'))
            self.assertTrue(by_id['xw:census_hts2026_naics2022:0302110000:1141XX']['attributes']['trade_aggregate_code'])
            split = by_id['xw:census_hs22_naics2022_imports2026:030211:112511']
            self.assertEqual((split['attributes']['weight'], split['attributes']['tendigit_line_share']), (None, 0.5))
            self.assertEqual(by_id['xwe:hs12:090300']['label'], 'Maté.')
            self.assertEqual(by_id['xw:wits_hs12_isic3:090300:0113']['object'], 'isic3:0113')
            corr = by_id['xw:un_hs22_hs17_correlation:970690:970500']
            self.assertEqual((corr['attributes']['relationship'], corr['attributes']['weight']), ('1:n', None))
            walk = xw.build_crosswalk(records, 'census_hts2026_naics2022')
            self.assertEqual(walk.apportion({'0101210010': 3, '0101210020': 4}, at='2026-06-01')['values'], {'112920': 7.0})
            path = tmp / 'hs.csv'
            loaded = load_concordance_csv(path, **xw.write_concordance_csv(records, 'un_hs22_hs17_correlation', path))
            self.assertEqual(loaded.check_partition()[0]['issue'], 'split_without_weights')
            self.assertEqual(loaded.apportion({'970610': 2.0})['values'], {'970600': 2.0})


if __name__ == '__main__':
    unittest.main()
