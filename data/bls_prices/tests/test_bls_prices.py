"""Offline acceptance test: tiny price flat-file shards and a Pink Sheet workbook through the Runner."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
HEAD = ['series_id', 'year', 'period', 'value', 'footnote_codes']


def tab(header, rows):
    lines = ['\t'.join([header[0].ljust(20)] + header[1:])]
    lines += ['\t'.join([str(row[0]).ljust(20)] + [str(v) for v in row[1:]]) for row in rows]
    return ('\r\n'.join(lines) + '\r\n').encode()


def workbook(rows):
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    body = ''.join('<row r="%d">%s</row>' % (n, ''.join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (c, n, v)
                                                        for c, v in row.items())) for n, row in rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as book:
        book.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                      f'<sheet name="AFOSHEET" sheetId="1" r:id="rId1"/><sheet name="Monthly Prices" sheetId="2" r:id="rId2"/>'
                      '</sheets></workbook>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                      'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/>'
                      '<Relationship Id="rId2" Target="worksheets/sheet2.xml" Type="x"/></Relationships>')
        book.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{main}"><sheetData/></worksheet>')
        book.writestr('xl/worksheets/sheet2.xml', f'<worksheet xmlns="{main}"><sheetData>{body}</sheetData></worksheet>')
    return buffer.getvalue()


class BlsPricesTest(unittest.TestCase):
    def records(self, directory):
        files = {
            'cu.data.0.Current': tab(HEAD, [['CUSR0000SA0', 1997, 'M01', '159.40', ''], ['CUUR0000SA0', 1997, 'S01', '160.0', '']]),
            'cu.data.1.AllItems': tab(HEAD, [['CUSR0000SA0', 1996, 'M12', '158.60', ''], ['CUSR0000SA0', 1997, 'M01', '159.40', '']]),
            'cu.series': tab(['series_id', 'area_code', 'item_code', 'seasonal', 'periodicity_code', 'base_code', 'base_period'],
                             [['CUSR0000SA0', '0000', 'SA0', 'S', 'R', 'S', '1982-84=100'],
                              ['CUUR0000SA0', '0000', 'SA0', 'U', 'S', 'S', '1982-84=100']]),
            'cu.item': tab(['item_code', 'item_name'], [['SA0', 'All items']]),
            'cu.area': tab(['area_code', 'area_name'], [['0000', 'U.S. city average']]),
            'wp.data.0.Current': tab(HEAD, [['WPU011', 2021, 'M01', '197.6', 'P']]),
            'wp.series': tab(['series_id', 'group_code', 'item_code', 'seasonal', 'base_date'], [['WPU011', '01', '1', 'U', '198200']]),
            'wp.item': tab(['group_code', 'item_code', 'item_name'], [['01', '1', 'Fruits & melons']]),
            'pc.data.0.Current': tab(HEAD, [['PCU1133--1133--', 1998, 'M01', '192.3', '']]),
            'pc.series': tab(['series_id', 'industry_code', 'product_code', 'seasonal', 'base_date'],
                             [['PCU1133--1133--', '1133--', '1133--', 'U', '198112']]),
            'pc.product': tab(['industry_code', 'product_code', 'product_name'], [['1133--', '1133--', 'Logging']]),
            'ap.data.0.Current': tab(HEAD, [['APU0000709111', 1995, 'M01', '-', '']]),
            'ap.series': tab(['series_id', 'area_code', 'item_code'], [['APU0000709111', '0000', '709111']]),
            'ap.item': tab(['item_code', 'item_name'], [['709111', 'Milk, fresh, whole, fortified, per 1/2 gal. (1.9 lit)']]),
            'ap.area': tab(['area_code', 'area_name'], [['0000', 'U.S. city average']]),
            'ei.data.0.Current': tab(HEAD, [['EIUIR', 2020, 'M12', '100.0', '']]),
            'ei.series': tab(['series_id', 'seasonal', 'index_code', 'series_name', 'base_period'],
                             [['EIUIR', 'U', 'IR', 'All imports', 'December 2020=100']]),
            'ei.index': tab(['index_code', 'index_name'], [['IR', 'BEA End Use Import Price Indexes']]),
            'CMO-Historical-Data-Monthly.xlsx': workbook([
                (4, {'A': 'World Bank Commodity Price Data'}),
                (5, {'B': 'Crude oil, WTI', 'C': 'Natural gas index', 'D': 'Beef **'}),
                (6, {'B': '($/bbl)', 'C': '(2010=100)', 'D': '($/kg)'}),
                (7, {'A': '2025M01', 'B': '75.1', 'C': '110.2', 'D': '…'})]),
        }
        shards = []
        for name, content in files.items():
            path = Path(directory) / name
            path.write_bytes(content)
            shards.append({'path': path, 'request': {'method': 'GET', 'url': 'https://download.bls.gov/pub/' + name},
                           'retrieved_at': '2026-09-15T00:00:00+00:00', 'complete': True})
        store = Store(Path(directory) / 'root')
        definition = json.loads((PROJECT / 'data/bls_prices/dataset.json').read_text())
        store.import_shards('bls_prices', shards, {'publisher': 'fixture', 'acquisition': definition['acquisition']},
                            complete=True)
        ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('bls_prices')
        return list(store.records(ref))

    def test_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            records = self.records(directory)
        obs = [r for r in records if r['kind'] == 'observation']
        cpi = [r for r in obs if r['dimensions'].get('series_id') == 'CUSR0000SA0']
        self.assertEqual(sorted(r['valid_from'] for r in cpi), ['1996-12-01', '1997-01-01'])  # no duplicate from history file
        self.assertTrue(all(r['unit'] == 'index_1982_1984_100' and r['subject'] == 'bls:cpi_item:SA0' for r in cpi))
        self.assertTrue(all(r['attributes']['series_id'] == 'CUSR0000SA0' for r in cpi))
        self.assertTrue(all(r['attributes']['vintage'] == 'current_at_retrieval' and r['attributes']['realtime_end'] is None
                            and r['attributes']['realtime_start'] == '2026-09-15' for r in obs))
        semi = next(r for r in obs if r['dimensions'].get('series_id') == 'CUUR0000SA0')
        self.assertEqual((semi['valid_from'], semi['valid_to']), ('1997-01-01', '1997-07-01'))
        wp = next(r for r in obs if r['metric'] == 'producer_price_index' and r['dimensions']['ppi_type'] == 'commodity')
        self.assertEqual((wp['subject'], wp['unit'], wp['attributes']['preliminary']), ('bls:ppi:wp:011', 'index_1982_100', True))
        pc = next(r for r in obs if r['dimensions'].get('ppi_type') == 'industry')
        self.assertEqual((pc['unit'], pc['dimensions']['naics_industry_code']), ('index_1981_12_100', '1133'))
        milk = next(r for r in obs if r['metric'] == 'average_price')
        self.assertEqual((milk['unit'], milk['value'], milk['dimensions']['area']), ('USD_per_half_gallon', None, 'geo:US'))
        self.assertEqual(next(r for r in obs if r['metric'] == 'import_price_index')['unit'], 'index_2020_12_100')
        pink = {r['subject']: r for r in obs if r['id'].startswith('bls_prices:pink:')}
        self.assertEqual((pink['worldbank:cmo:crude_oil_wti']['value'], pink['worldbank:cmo:crude_oil_wti']['unit']),
                         (75.1, 'USD_per_barrel'))
        self.assertEqual(pink['worldbank:cmo:natural_gas_index']['metric'], 'commodity_price_index')
        self.assertIsNone(pink['worldbank:cmo:beef']['value'])
        self.assertEqual(pink['worldbank:cmo:beef']['valid_to'], '2025-02-01')


if __name__ == '__main__':
    unittest.main()
