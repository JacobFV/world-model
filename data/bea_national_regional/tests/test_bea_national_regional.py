"""Offline test: NIPA registers/data, a state regional ZIP and a county API page through the Runner."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
DATASET = 'bea_national_regional'
TABLES = 'TableId,TableTitle\nT10105,"Table 1.1.5. Gross Domestic Product"\n'
SERIES = ('%SeriesCode,SeriesLabel,MetricName,CalculationType,DefaultScale,TableId:LineNo,SeriesCodeParents\n'
          'A191RC,"Gross domestic product","Current Dollars","Level",-6,T10105:1,NONE\n'
          'A191RL,"Gross domestic product","Fisher Quantity Index","Percent change, annual rate",0,T10101:1,NONE\n')
NIPA = '%SeriesCode,Period,Value\nA191RC,2024Q4,"29,723,864.1"\nA191RL,2024Q4,2.4\n'
STATE = ('GeoFIPS,GeoName,Region,TableName,LineCode,IndustryClassification,Description,Unit,2023,2024\n'
         ' "00000","United States *", ,SAGDP2,1,"...","All industry total ","Millions of current dollars",27720708.0,29298013.0\n'
         ' "06000","California", 8,SAGDP2,3,"11","  Agriculture, forestry, fishing and hunting 2/","Millions of current dollars",(D),50000.5\n'
         ' "91000","New England", 1,SAGDP2,1,"...","All industry total ","Millions of current dollars",(NA),1300.1\n'
         '"Note: See the included footnote file."\n'
         '"SAGDP2: Gross domestic product (GDP) by state 1."\n')
COUNTY = {'BEAAPI': {'Request': {'RequestParam': [{'ParameterName': 'USERID', 'ParameterValue': 'secret-user-id'}]},
                     'Results': {'Statistic': 'Personal income', 'UnitOfMeasure': 'Thousands of dollars', 'Data': [
                         {'Code': 'CAINC1-1', 'GeoFips': '06037', 'GeoName': 'Los Angeles, CA', 'TimePeriod': '2023',
                          'CL_UNIT': 'Thousands of dollars', 'UNIT_MULT': '3', 'DataValue': '1,000,000'},
                         {'Code': 'CAINC1-1', 'GeoFips': '51901', 'GeoName': 'Albemarle + Charlottesville, VA*',
                          'TimePeriod': '2023', 'CL_UNIT': 'Thousands of dollars', 'UNIT_MULT': '3', 'DataValue': '(D)'},
                         {'Code': 'CAINC1-1', 'GeoFips': '06037', 'GeoName': 'Los Angeles, CA', 'TimePeriod': '2022',
                          'CL_UNIT': 'Thousands of dollars', 'UNIT_MULT': '3', 'DataValue': '(NA)'}]}}}
EARNINGS = {'BEAAPI': {'Results': {'Statistic': 'Private nonfarm earnings: Manufacturing',
                                   'UnitOfMeasure': 'Thousands of dollars', 'Data': [
                                       {'Code': 'CAINC5N-500', 'GeoFips': '06037', 'GeoName': 'Los Angeles, CA',
                                        'TimePeriod': '2023', 'CL_UNIT': 'Thousands of dollars', 'UNIT_MULT': '3',
                                        'DataValue': '2,500'}]}}}
FARM = {'BEAAPI': {'Results': {'Statistic': 'Farm earnings', 'UnitOfMeasure': 'Thousands of dollars', 'Data': [
    {'Code': 'CAINC5N-81', 'GeoFips': '06037', 'GeoName': 'Los Angeles, CA', 'TimePeriod': '2023',
     'CL_UNIT': 'Thousands of dollars', 'UNIT_MULT': '3', 'DataValue': '7'}]}}}
REAL_GDP = {'BEAAPI': {'Results': {'Statistic': 'Real GDP: Utilities', 'UnitOfMeasure': 'Thousands of chained 2017 dollars',
                                   'Data': [{'Code': 'CAGDP9-10', 'GeoFips': '06037', 'GeoName': 'Los Angeles, CA',
                                             'TimePeriod': '2023', 'CL_UNIT': 'Thousands of chained 2017 dollars',
                                             'UNIT_MULT': '3', 'DataValue': '40'}]}}}


class BeaNationalRegionalPipelineTest(unittest.TestCase):
    def test_nipa_state_and_county(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            files = {'TablesRegister.txt': TABLES, 'SeriesRegister.txt': SERIES, 'NipaDataQ.txt': NIPA,
                     'county.json': json.dumps(COUNTY), 'earnings.json': json.dumps(EARNINGS),
                     'farm.json': json.dumps(FARM), 'real_gdp.json': json.dumps(REAL_GDP)}
            for name, text in files.items():
                (temp / name).write_text(text)
            with zipfile.ZipFile(temp / 'SAGDP.zip', 'w') as archive:
                archive.writestr('SAGDP2__ALL_AREAS_1997_2024.csv', STATE)
                archive.writestr('SAGDP2_CA_1997_2024.csv', STATE)
                archive.writestr('SAGDP3__ALL_AREAS_1997_2024.csv', STATE.replace('SAGDP2', 'SAGDP3'))
            base = 'https://apps.bea.gov/'
            shards = [{'path': temp / 'TablesRegister.txt', 'request': {'url': base + 'national/Release/TXT/TablesRegister.txt'}},
                      {'path': temp / 'SeriesRegister.txt', 'request': {'url': base + 'national/Release/TXT/SeriesRegister.txt'}},
                      {'path': temp / 'NipaDataQ.txt', 'request': {'url': base + 'national/Release/TXT/NipaDataQ.txt'}},
                      {'path': temp / 'SAGDP.zip', 'request': {'url': base + 'regional/zip/SAGDP.zip'}},
                      {'path': temp / 'county.json', 'request': {
                          'url': base + 'api/data/?method=GetData&datasetname=Regional&TableName=CAINC1&LineCode=1'}},
                      {'path': temp / 'earnings.json', 'request': {
                          'url': base + 'api/data/?method=GetData&datasetname=Regional&TableName=CAINC5N&LineCode=500'}},
                      {'path': temp / 'farm.json', 'request': {
                          'url': base + 'api/data/?method=GetData&datasetname=Regional&TableName=CAINC5N&LineCode=81'}},
                      {'path': temp / 'real_gdp.json', 'request': {
                          'url': base + 'api/data/?method=GetData&datasetname=Regional&TableName=CAGDP9&LineCode=10'}}]
            store = Store(temp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run(DATASET)
            text = gzip.open(store.version_dir(ref) / 'records.jsonl.gz', 'rt').read()
        self.assertNotIn('secret-user-id', text)
        records = [json.loads(line) for line in text.splitlines()]
        obs = [r for r in records if r['kind'] == 'observation']
        gdp = next(r for r in obs if r['dimensions'].get('series_code') == 'A191RC')
        self.assertEqual((gdp['subject'], gdp['metric'], gdp['value'], gdp['unit'], gdp['valid_from'], gdp['valid_to']),
                         ('geo:US', 'gross_domestic_product', 29_723_864_100_000, 'USD', '2024-10-01', '2025-01-01'))
        self.assertEqual(gdp['dimensions']['frequency'], 'Q')
        growth = next(r for r in obs if r['dimensions'].get('series_code') == 'A191RL')
        self.assertEqual((growth['value'], growth['unit']), (2.4, 'percent'))
        state = [r for r in obs if r['dimensions'].get('table', '').startswith('SAGDP')]
        self.assertEqual(len(state), 5)  # SAGDP3 filtered out, per-state duplicate file ignored, (NA) skipped, footer skipped
        ca = {r['valid_from']: r for r in state if r['subject'] == 'geo:US:state:06'}
        self.assertIsNone(ca['2023-01-01']['value'])
        self.assertEqual(ca['2023-01-01']['missing_reason'], 'suppressed_to_avoid_disclosure')
        self.assertEqual((ca['2024-01-01']['value'], ca['2024-01-01']['metric'], ca['2024-01-01']['dimensions']['industry'],
                          ca['2024-01-01']['dimensions']['naics']),
                         (50_000_500_000, 'gdp', 'agriculture_forestry_fishing_and_hunting', '11'))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['geo:US:bea_region:91']['entity_type'], 'aggregate_cohort')
        self.assertTrue(entities['geo:US:county:51901']['attributes']['bea_combined_area'])
        self.assertEqual(entities['bea:nipa:A191RC']['entity_type'], 'economic_series')
        county = sorted((r['subject'], r['valid_from'], r['value'], r['metric']) for r in obs
                        if r['dimensions'].get('table') == 'CAINC1')
        self.assertEqual(county, [('geo:US:county:06037', '2023-01-01', 1_000_000_000, 'personal_income'),
                                  ('geo:US:county:51901', '2023-01-01', None, 'personal_income')])
        detail = {(r['metric'], r['dimensions'].get('industry'), r['unit'], r['value']) for r in obs
                  if r['dimensions'].get('table') in ('CAINC5N', 'CAGDP9')}
        self.assertEqual(detail, {('earnings_by_place_of_work', 'manufacturing', 'USD', 2_500_000),
                                  ('farm_earnings', None, 'USD', 7_000),
                                  ('real_gdp', 'utilities', 'USD_chained_2017', 40_000)})
        within = {(r['subject'], r['object']) for r in records if r['kind'] == 'assertion'}
        self.assertIn(('geo:US:county:06037', 'geo:US:state:06'), within)


if __name__ == '__main__':
    unittest.main()
