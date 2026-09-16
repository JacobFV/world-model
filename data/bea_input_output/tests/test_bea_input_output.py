"""Offline test: tiny BEA IO/SUP workbook archives and a GDPbyIndustry page through the Runner."""
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
DATASET = 'bea_input_output'


def column(position):
    name = ''
    while position:
        position, rest = divmod(position - 1, 26)
        name = chr(65 + rest) + name
    return name


def xlsx(sheets):
    """Minimal workbook with inline strings; sheets is {name: [row values]}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as book:
        entries, rels = [], []
        for position, (name, rows) in enumerate(sheets.items(), 1):
            entries.append(f'<sheet name="{name}" sheetId="{position}" r:id="rId{position}"/>')
            rels.append(f'<Relationship Id="rId{position}" Type="worksheet" Target="worksheets/sheet{position}.xml"/>')
            xml_rows = []
            for number, values in enumerate(rows, 1):
                cells = []
                for index, value in enumerate(values, 1):
                    if value is None:
                        continue
                    ref = f'{column(index)}{number}'
                    if isinstance(value, (int, float)):
                        cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
                xml_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
            book.writestr(f'xl/worksheets/sheet{position}.xml',
                          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                          f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        book.writestr('xl/workbook.xml',
                      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                      f'<sheets>{"".join(entries)}</sheets></workbook>')
        book.writestr('xl/_rels/workbook.xml.rels',
                      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      f'{"".join(rels)}</Relationships>')
    return buffer.getvalue()


USE_2023 = [
    ['The Use of Commodities by Industries - Summary'], ['(Millions of dollars)'], [], ['2023'],
    [None, 'Commodities/Industries', '111CA', '211', 'F010', None],
    ['IOCode', 'Name', 'Farms', 'Oil and gas extraction', 'Personal consumption expenditures', 'Total Commodity Output'],
    ['111CA', 'Farms', '100', '...', '50', '400'],
    ['211', 'Oil and gas extraction', '0', '25.5', None, '300'],
    [None, 'Total Intermediate', '100', '25.5', None, None],
    ['1. Consists of noncomparable imports and the rest-of-the-world adjustment.'],
]
USE_2022 = USE_2023[:6] + [['111CA', 'Farms', '90', None, None, None]]
DR_2017 = [
    ['Commodity-by-Industry Direct Requirements Table, 2017'], ['Bureau of Economic Analysis'], [],
    ['Commodity / Industry', None, 'Oilseed farming'], ['Code', 'Commodity Description', '1111A0'],
    ['1111A0', 'Oilseed farming', '4.6608799999999999E-2'],
]
GDP = {'BEAAPI': {'Request': {'RequestParam': [{'ParameterName': 'USERID', 'ParameterValue': 'secret-user-id'}]},
                  'Results': [{'Statistic': 'GDP by Industry Table',
                               'Data': [{'TableID': '1', 'Frequency': 'A', 'Year': '2023', 'Quarter': '2023',
                                         'Industry': '11', 'IndustrYDescription': 'Agriculture', 'DataValue': '108.6',
                                         'NoteRef': '1'},
                                        {'TableID': '1', 'Frequency': 'Q', 'Year': '2023', 'Quarter': 'IV',
                                         'Industry': '11', 'IndustrYDescription': 'Agriculture', 'DataValue': '1,110.25',
                                         'NoteRef': '1'}],
                               'Notes': [{'NoteRef': '1', 'NoteText': 'Value Added by Industry [Billions of dollars]'}]}]}}


class BeaInputOutputPipelineTest(unittest.TestCase):
    def test_workbooks_and_gdp_by_industry(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with zipfile.ZipFile(temp / 'io.zip', 'w') as archive:
                archive.writestr('IOUse_Before_Redefinitions_PRO_1997-2023_Summary.xlsx',
                                 xlsx({'2022': USE_2022, '2023': USE_2023}))
                archive.writestr('CxI_DR_2017_Detail.xlsx', xlsx({'NAICS Codes': [['x']], '2017': DR_2017}))
                archive.writestr('IOUse_After_Redefinitions_PUR_2017_Detail.xlsx', xlsx({'2017': USE_2023}))
            (temp / 'gdp.json').write_text(json.dumps(GDP))
            store = Store(temp / 'data')
            base = 'https://apps.bea.gov/'
            store.import_shards(DATASET, [
                {'path': temp / 'io.zip', 'request': {'url': base + 'industry/iTables%20Static%20Files/AllTablesIO.zip'}},
                {'path': temp / 'gdp.json',
                 'request': {'url': base + 'api/data/?method=GetData&datasetname=GDPbyIndustry&TableID=1'}}],
                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run(DATASET)
            text = gzip.open(store.version_dir(ref) / 'records.jsonl.gz', 'rt').read()
        self.assertNotIn('secret-user-id', text)
        records = [json.loads(line) for line in text.splitlines()]
        obs = [r for r in records if r['kind'] == 'observation']
        use = {(r['dimensions']['year'], r['dimensions']['row_code'], r['dimensions']['column_code']): r
               for r in obs if r['metric'] == 'io_use'}
        self.assertEqual(sorted(k for k in use if k[0] == 2023),
                         [(2023, '111CA', '111CA'), (2023, '111CA', 'F010'), (2023, '111CA', 'T:total_commodity_output'),
                          (2023, '211', '211'), (2023, '211', 'T:total_commodity_output'),
                          (2023, 'T:total_intermediate', '111CA'), (2023, 'T:total_intermediate', '211')])
        farms = use[(2023, '111CA', '111CA')]
        self.assertEqual((farms['value'], farms['unit'], farms['subject'], farms['valid_from'], farms['valid_to']),
                         (100_000_000, 'USD', 'bea_io:summary:commodity:111CA', '2023-01-01', '2024-01-01'))
        self.assertEqual(use[(2023, '211', '211')]['value'], 25_500_000)
        self.assertEqual(use[(2023, '111CA', 'F010')]['dimensions']['counterpart'], 'bea_io:summary:final_use:F010')
        self.assertTrue(farms['evidence'][0]['locator'].startswith('shard:0/member:IOUse_Before'))
        relations = sorted((r['subject'], r['predicate'], r['object']) for r in records if r['kind'] == 'assertion')
        self.assertEqual(relations, [('bea_io:summary:industry:111CA', 'consumes', 'bea_io:summary:commodity:111CA'),
                                     ('bea_io:summary:industry:211', 'consumes', 'bea_io:summary:commodity:211')])
        requirement = next(r for r in obs if r['metric'] == 'io_direct_requirement')
        self.assertEqual((requirement['value'], requirement['unit'], requirement['subject']),
                         (0.0466088, 'USD_per_USD', 'bea_io:detail:commodity:1111A0'))
        self.assertFalse(any(r['dimensions'].get('price_basis') == 'purchasers' for r in obs))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['bea_io:summary:industry:211']['entity_type'], 'industry')
        self.assertEqual(entities['bea_io:summary:commodity:211']['entity_type'], 'product')
        self.assertTrue(entities['bea_io:summary:total:T:total_intermediate']['attributes']['aggregate'])
        gdp = sorted((r['valid_from'], r['value'], r['unit']) for r in obs if r['metric'] == 'value_added')
        self.assertEqual(gdp, [('2023-01-01', 108_600_000_000, 'USD'), ('2023-10-01', 1_110_250_000_000, 'USD')])


if __name__ == '__main__':
    unittest.main()
