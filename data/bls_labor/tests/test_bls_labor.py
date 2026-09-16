"""Offline acceptance test: tiny BLS flat-file shards through the Runner."""
import csv
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


def tab(header, rows):
    widths = [30] + [0] * (len(header) - 1)
    lines = ['\t'.join(h.ljust(w) for h, w in zip(header, widths))]
    lines += ['\t'.join(str(v).ljust(w) for v, w in zip(row, widths)) for row in rows]
    return ('\r\n'.join(lines) + '\r\n').encode()


def xlsx(rows):
    """Minimal inline-string workbook with one sheet."""
    def col(i):
        return chr(ord('A') + i)
    body = ''.join('<row r="%d">%s</row>' % (n, ''.join(
        '<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (col(i), n, v) for i, v in enumerate(row)))
        for n, row in enumerate(rows, 1))
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as book:
        book.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                      f'<sheet name="All May 2025 data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                      'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        book.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{main}"><sheetData>{body}</sheetData></worksheet>')
    return buffer.getvalue()


class BlsLaborFullTest(unittest.TestCase):
    def build(self, directory):
        data = Path(directory)
        files = {}
        head = ['series_id', 'year', 'period', 'value', 'footnote_codes']
        files['sm.data.0.Current'] = tab(head, [['SMS06310800000000001', 2025, 'M01', '4567.8', 'P'],
                                                ['SMS06000000000000001', 2025, 'M13', '18000.1', '']])
        files['sm.area'] = tab(['area_code', 'area_name'], [['00000', 'Statewide'], ['31080', 'Los Angeles-Long Beach-Anaheim, CA']])
        files['sm.industry'] = tab(['industry_code', 'industry_name'], [['00000000', 'Total Nonfarm']])
        files['sm.state'] = tab(['state_code', 'state_name'], [['06', 'California']])
        files['la.data.64.County'] = tab(head, [['LAUCN060370000000003', 2025, 'M02', '5.9', ''],
                                                ['LAUCN060370000000006', 2025, 'M02', '-', 'N']])
        files['la.area'] = tab(['area_type_code', 'area_code', 'area_text'], [['F', 'CN0603700000000', 'Los Angeles County, CA']])
        files['jt.data.1.AllItems'] = tab(head, [['JTS000000000000000JOL', 2025, 'M03', '7500', '']])
        files['jt.industry'] = tab(['industry_code', 'industry_text'], [['000000', 'Total nonfarm']])
        files['jt.sizeclass'] = tab(['sizeclass_code', 'sizeclass_text'], [['00', 'All size classes']])
        files['ce.data.01a.CurrentSeasAE'] = tab(head, [['CES0000000001', 2025, 'M04', '159000', '']])
        files['ce.series'] = tab(['series_id', 'supersector_code', 'industry_code', 'data_type_code', 'seasonal'],
                                 [['CES0000000001', '00', '00000000', '01', 'S']])
        files['ce.industry'] = tab(['industry_code', 'naics_code', 'industry_name'], [['00000000', '-', 'Total nonfarm']])
        out = io.StringIO()
        writer = csv.writer(out, quoting=csv.QUOTE_NONNUMERIC)
        cols = ['area_fips', 'own_code', 'industry_code', 'agglvl_code', 'size_code', 'year', 'qtr', 'disclosure_code',
                'annual_avg_estabs', 'annual_avg_emplvl', 'total_annual_wages', 'annual_avg_wkly_wage', 'avg_annual_pay']
        writer.writerow(cols)
        writer.writerow(['06037', '5', '52', '74', '0', '2025', 'A', '', 30000, 220000, 30000000000, 2600, 136000])
        writer.writerow(['06037', '5', '5221', '76', '0', '2025', 'A', '', 1, 2, 3, 4, 5])  # filtered agglvl
        writer.writerow(['06037', '1', '52', '74', '0', '2025', 'A', 'N', 3, 0, 0, 0, 0])  # suppressed
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('2025.annual.singlefile.csv', out.getvalue())
        files['2025_annual_singlefile.zip'] = buffer.getvalue()
        files['ln.data.1.AllData'] = tab(head, [['LNS14000000', 2026, 'M08', '4.1', ''],
                                                ['LNS99999999', 2026, 'M08', '1.0', ''],  # not a listed headline series
                                                ['LNU04000000', 2025, 'M13', '4.3', '']])
        out = io.StringIO()
        writer = csv.writer(out, quoting=csv.QUOTE_NONNUMERIC)
        writer.writerow(['area_fips', 'own_code', 'industry_code', 'agglvl_code', 'size_code', 'year', 'qtr', 'disclosure_code',
                         'qtrly_estabs', 'month1_emplvl', 'month2_emplvl', 'month3_emplvl', 'total_qtrly_wages', 'avg_wkly_wage'])
        writer.writerow(['06037', '5', '52', '74', '0', '2025', '2', '', 30100, 219000, 220000, 221000, 7600000000, 2650])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('2025.q1-q4.singlefile.csv', out.getvalue())
        files['2025_qtrly_singlefile.zip'] = buffer.getvalue()
        header = ['AREA', 'AREA_TITLE', 'AREA_TYPE', 'NAICS', 'I_GROUP', 'OWN_CODE', 'OCC_CODE', 'OCC_TITLE', 'O_GROUP',
                  'TOT_EMP', 'EMP_PRSE', 'H_MEAN', 'A_MEAN', 'H_MEDIAN', 'A_MEDIAN']
        book = xlsx([header, ['06', 'California', '2', '000000', 'cross-industry', '1235', '15-1252', 'Software Developers',
                             'detailed', '150000', '1.2', '90.1', '187400', '#', '#'],
                     # Same NAICS published at two industry aggregation levels with identical values.
                     ['99', 'U.S.', '1', '327000', '3-digit', '5', '00-0000', 'All Occupations', 'total',
                      '414630', '0.5', '29.9', '62190', '26.8', '55740'],
                     ['99', 'U.S.', '1', '327000', '4-digit', '5', '00-0000', 'All Occupations', 'total',
                      '414630', '0.5', '29.9', '62190', '26.8', '55740']])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('oesm25all/all_data_M_2025.xlsx', book)
        files['oesm25all.zip'] = buffer.getvalue()
        # Pre-2019 state/MSA files: no AREA_TYPE/NAICS columns, OCC_GROUP instead of O_GROUP, AREA_NAME; aMSA is skipped.
        old = ['PRIM_STATE', 'AREA', 'AREA_NAME', 'OCC_CODE', 'OCC_TITLE', 'OCC_GROUP', 'TOT_EMP', 'EMP_PRSE', 'H_MEAN',
               'A_MEAN', 'H_MEDIAN', 'A_MEDIAN']
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('oesm15ma/MSA_M2015_dl.xlsx', xlsx([old, ['CA', '31080', 'Los Angeles-Long Beach-Anaheim, CA',
                                                                       '15-1133', 'Software Developers, Systems Software',
                                                                       'detailed', '30000', '3.1', '60.0', '124800', '58.2', '121000']]))
            archive.writestr('oesm15ma/BOS_M2015_dl.xlsx', xlsx([old, ['AK', '0200001', 'Southeast Alaska nonmetropolitan area',
                                                                       '00-0000', 'All Occupations', 'total', '36100', '2.4', '25',
                                                                       '52010', '**', '*']]))
            archive.writestr('oesm15ma/aMSA_M2015_dl.xlsx', xlsx([old, ['CA', '31080', 'x', '00-0000', 'All', 'total', '1', '1',
                                                                        '1', '1', '1', '1']]))
        files['oesm15ma.zip'] = buffer.getvalue()
        out = io.StringIO()
        writer = csv.writer(out, quoting=csv.QUOTE_NONNUMERIC)
        writer.writerow(cols)
        writer.writerow(['06037', '5', '52', '74', '0', '1995', 'A', '', 20000, 200000, 9000000000, 900, 45000])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('1995.annual.singlefile.csv', out.getvalue())
        files['1995_annual_singlefile.zip'] = buffer.getvalue()
        shards = []
        for name, content in files.items():
            path = data / ('src-' + name)
            path.write_bytes(content)
            shards.append({'path': path, 'request': {'method': 'GET', 'url': 'https://download.bls.gov/x/' + name},
                           'retrieved_at': '2026-09-15T00:00:00+00:00', 'complete': True, 'role': 'data'})
        store = Store(data / 'root')
        definition = json.loads((PROJECT / 'data/bls_labor/dataset.json').read_text())
        store.import_shards('bls_labor', shards, {'publisher': 'fixture', 'acquisition': definition['acquisition']},
                            complete=True)
        ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('bls_labor')
        return list(store.records(ref))

    def test_full_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            records = self.build(directory)
        obs = [r for r in records if r['kind'] == 'observation']
        by = {(r['metric'], r['subject'], r['dimensions'].get('series_id'), r['dimensions'].get('ownership')): r for r in obs}
        sm = by[('employment', 'geo:US:msa:31080', 'SMS06310800000000001', None)]
        self.assertEqual((sm['value'], sm['unit'], sm['valid_from'], sm['valid_to']), (4567800, 'persons', '2025-01-01', '2025-02-01'))
        self.assertTrue(sm['attributes']['preliminary'])
        annual = by[('employment', 'geo:US:state:06', 'SMS06000000000000001', None)]
        self.assertEqual((annual['valid_from'], annual['dimensions']['period_type']), ('2025-01-01', 'annual_average'))
        rate = by[('unemployment_rate', 'geo:US:county:06037', 'LAUCN060370000000003', None)]
        self.assertEqual(rate['value'], 5.9)
        lf = by[('labor_force', 'geo:US:county:06037', 'LAUCN060370000000006', None)]
        self.assertIsNone(lf['value'])
        self.assertTrue(lf['missing_reason'])
        self.assertEqual(by[('job_openings', 'geo:US', 'JTS000000000000000JOL', None)]['value'], 7500000)
        payrolls = by[('nonfarm_payroll_employment', 'geo:US', 'CES0000000001', None)]
        self.assertEqual((payrolls['value'], payrolls['unit'], payrolls['dimensions']['frequency'],
                          payrolls['dimensions']['seasonal_adjustment']), (159000, 'thousand_persons', 'monthly', 'SA'))
        self.assertEqual((payrolls['attributes']['series_id'], payrolls['attributes']['source_series'],
                          payrolls['attributes']['vintage'], payrolls['attributes']['realtime_start'],
                          payrolls['attributes']['realtime_end']),
                         ('CES0000000001', 'CES0000000001', 'current_at_retrieval', '2026-09-15', None))
        self.assertTrue(all(r['attributes']['vintage'] == 'current_at_retrieval' for r in obs))
        self.assertEqual((rate['dimensions']['geography'], rate['attributes']['source_series']),
                         ('geo:US:county:06037', 'LAUCN060370000000003'))
        cps = by[('unemployment_rate', 'geo:US', 'LNS14000000', None)]
        self.assertEqual((cps['value'], cps['unit'], cps['dimensions']['survey'], cps['valid_from'],
                          cps['attributes']['source_series']), (4.1, 'percent', 'CPS', '2026-08-01', 'LNS14000000'))
        nsa = by[('unemployment_rate', 'geo:US', 'LNU04000000', None)]
        self.assertEqual((nsa['dimensions']['seasonal_adjustment'], nsa['dimensions']['period_type']), ('NSA', 'annual_average'))
        self.assertFalse(any(r['dimensions'].get('series_id') == 'LNS99999999' for r in obs))
        quarterly = [r for r in obs if r['attributes'].get('survey') == 'QCEW' and r['dimensions']['frequency'] != 'annual']
        self.assertEqual(len(quarterly), 6)
        may = next(r for r in quarterly if r['attributes']['source_field'] == 'month2_emplvl')
        self.assertEqual((may['metric'], may['value'], may['valid_from'], may['valid_to'], may['attributes']['area_fips'],
                          may['attributes']['industry_code'], may['dimensions']['industry']),
                         ('employment', 220000, '2025-05-01', '2025-06-01', '06037', '52', 'naics2022:52'))
        wages = next(r for r in quarterly if r['metric'] == 'total_quarterly_wages')
        self.assertEqual((wages['valid_from'], wages['valid_to'], wages['dimensions']['period_type']),
                         ('2025-04-01', '2025-07-01', 'quarter'))
        qcew = [r for r in obs if r['attributes'].get('survey') == 'QCEW' and r['dimensions']['frequency'] == 'annual'
                and r['valid_from'] == '2025-01-01']
        self.assertEqual(len(qcew), 10)
        private = next(r for r in qcew if r['metric'] == 'employment' and r['dimensions']['ownership'] == 'private')
        self.assertEqual((private['value'], private['dimensions']['industry']), (220000, 'naics2022:52'))
        suppressed = next(r for r in qcew if r['metric'] == 'total_annual_wages' and r['dimensions']['ownership'] == 'federal_government')
        self.assertEqual((suppressed['value'], suppressed['missing_reason']), (None, 'suppressed_confidentiality'))
        old_msa = {r['metric']: r for r in obs if r['dimensions'].get('occupation') == 'soc2010:15-1133'}
        self.assertEqual((old_msa['employment']['subject'], old_msa['employment']['value'], old_msa['employment']['valid_from'],
                          old_msa['employment']['dimensions']['occupation_group'], old_msa['median_annual_wage']['value']),
                         ('geo:US:msa:31080', 30000, '2015-05-01', 'detailed', 121000))
        bos = {r['metric']: r for r in obs if r['subject'] == 'bls:oews_area:0200001'}
        self.assertEqual((bos['employment']['value'], bos['mean_hourly_wage']['value'], bos['median_annual_wage']['missing_reason']),
                         (36100, 25, 'not_available'))
        self.assertEqual(sum(1 for r in obs if r['dimensions'].get('occupation', '').startswith('soc2010:')
                             and r['metric'] == 'employment'), 2)  # aMSA member skipped
        old_qcew = next(r for r in obs if r['attributes'].get('survey') == 'QCEW' and r['valid_from'] == '1995-01-01'
                        and r['metric'] == 'employment')
        self.assertEqual((old_qcew['value'], old_qcew['dimensions']['industry']), (200000, 'naics2002:52'))
        oews = {r['metric']: r for r in obs if r['attributes'].get('survey') == 'OEWS'
                and r['dimensions']['occupation'] == 'soc2018:15-1252'}
        levels = sorted(r['dimensions']['industry_group'] for r in obs if r['attributes'].get('survey') == 'OEWS'
                        and r['metric'] == 'employment' and r['dimensions']['naics_code'] == '327000')
        self.assertEqual(levels, ['3-digit', '4-digit'])
        self.assertEqual(oews['employment']['value'], 150000)
        self.assertEqual(oews['employment']['dimensions']['occupation'], 'soc2018:15-1252')
        self.assertEqual(oews['median_annual_wage']['missing_reason'], 'top_coded_at_or_above_published_maximum')
        entities = {r['entity_id'] for r in records if r['kind'] == 'entity'}
        self.assertTrue({'geo:US', 'geo:US:state:06', 'geo:US:county:06037', 'soc2018:15-1252'} <= entities)
        self.assertTrue(any(r['kind'] == 'assertion' and r['subject'] == 'geo:US:county:06037' for r in records))
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records))


if __name__ == '__main__':
    unittest.main()
