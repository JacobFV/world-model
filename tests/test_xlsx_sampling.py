import io
import unittest
import zipfile
from worldmodel.sampling import xlsx_rows

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
def archive(rows,strings=None):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{NS}"><sheetData>{rows}</sheetData></worksheet>')
        if strings is not None:z.writestr('xl/sharedStrings.xml',strings)
    stream.seek(0)
    return zipfile.ZipFile(stream)
def row(n,values):
    return f'<row r="{n}">'+''.join(f'<c r="{k}{n}" t="inlineStr"><is><t>{v}</t></is></c>' for k,v in values.items())+'</row>'

class WorkbookSamplingTests(unittest.TestCase):
    def config(self,**kw):return {'max_uncompressed_bytes':10000,'header_row':3,'context_cells':{'date':'B1'},'stop_when_blank':'Ticker',**kw}
    def test_metadata_sparse_rows_and_footer_are_explicit(self):
        with archive(row(1,{'B':'2026-01-01'})+row(3,{'A':'Name','C':'Ticker'})+row(4,{'A':'Alpha','C':'AAA'})+row(5,{'A':'Disclaimer'})) as z:
            values=list(xlsx_rows(z,self.config()))
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]['_context'],{'date':'2026-01-01'})
        self.assertEqual(values[0]['_source']['row'],4)
        self.assertEqual(values[0]['Ticker'],'AAA')
    def test_duplicate_headers_context_collisions_and_missing_cells_fail(self):
        for rows,cfg in [(row(1,{'B':'date'})+row(3,{'A':'x','B':'x'}),self.config()),
                         (row(1,{'B':'date'})+row(3,{'A':'_context'}),self.config()),
                         (row(3,{'A':'x'}),self.config())]:
            with self.subTest(rows=rows),archive(rows) as z:
                with self.assertRaises(ValueError):list(xlsx_rows(z,cfg))
    def test_shared_strings_xml_budget_and_entities_fail(self):
        with archive('<row r="1"><c r="A1" t="s"><v>-1</v></c></row>','<sst/>') as z:
            with self.assertRaises(ValueError):list(xlsx_rows(z,{'max_uncompressed_bytes':1000}))
        with archive(row(1,{'A':'x'})) as z:
            with self.assertRaises(ValueError):list(xlsx_rows(z,{'max_uncompressed_bytes':1}))
        with archive(row(1,{'A':'x'}),'<!DOCTYPE x [<!ENTITY y "bad">]><sst/>') as z:
            with self.assertRaises(ValueError):list(xlsx_rows(z,{'max_uncompressed_bytes':1000}))
    def test_selected_columns_and_row_range(self):
        with archive(row(1,{'A':'x','B':'y'})+row(2,{'A':'1','B':'2'})+row(3,{'A':'3','B':'4'})) as z:
            values=list(xlsx_rows(z,{'max_uncompressed_bytes':10000,'columns':{'B':'value'},'start_row':3,'end_row':3}))
        self.assertEqual(values[0]['value'],'4')
        self.assertNotIn('x',values[0])
    def test_missing_sheet_duplicate_context_and_utf16_dtd_reject(self):
        with archive(row(1,{'A':'x'})) as z:
            with self.assertRaises(KeyError):list(xlsx_rows(z,{'max_uncompressed_bytes':1000,'archive_member':'missing'}))
            with self.assertRaises(ValueError):list(xlsx_rows(z,{'max_uncompressed_bytes':1000,'context_cells':{'a':'A1','b':'A1'}}))
        xml=('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE sst [<!ENTITY x "bad">]><sst/>').encode('utf-16')
        with archive(row(1,{'A':'x'}),xml) as z:
            with self.assertRaises(ValueError):list(xlsx_rows(z,{'max_uncompressed_bytes':2000}))
