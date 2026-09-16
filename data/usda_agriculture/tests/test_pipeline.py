"""Offline acceptance test: Quick Stats bulk gzip shard -> evidence records through the Runner."""
import gzip
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
COLUMNS = ('SOURCE_DESC SECTOR_DESC GROUP_DESC COMMODITY_DESC CLASS_DESC PRODN_PRACTICE_DESC UTIL_PRACTICE_DESC '
           'STATISTICCAT_DESC UNIT_DESC SHORT_DESC DOMAIN_DESC DOMAINCAT_DESC AGG_LEVEL_DESC STATE_ANSI STATE_FIPS_CODE '
           'STATE_ALPHA STATE_NAME ASD_CODE ASD_DESC COUNTY_ANSI COUNTY_CODE COUNTY_NAME REGION_DESC ZIP_5 WATERSHED_CODE '
           'WATERSHED_DESC CONGR_DISTRICT_CODE COUNTRY_CODE COUNTRY_NAME LOCATION_DESC YEAR FREQ_DESC BEGIN_CODE END_CODE '
           'REFERENCE_PERIOD_DESC WEEK_ENDING LOAD_TIME VALUE CV_%').split()


def row(**values):
    base = dict.fromkeys(COLUMNS, '')
    base.update(SECTOR_DESC='CROPS', GROUP_DESC='FIELD CROPS', COMMODITY_DESC='CORN', CLASS_DESC='ALL CLASSES',
                PRODN_PRACTICE_DESC='ALL PRODUCTION PRACTICES', UTIL_PRACTICE_DESC='GRAIN', DOMAIN_DESC='TOTAL',
                DOMAINCAT_DESC='NOT SPECIFIED', STATE_FIPS_CODE='19', STATE_ALPHA='IA', STATE_NAME='IOWA',
                COUNTRY_CODE='9000', COUNTRY_NAME='UNITED STATES', FREQ_DESC='ANNUAL', REFERENCE_PERIOD_DESC='YEAR',
                LOAD_TIME='2024-01-01 00:00:00')
    base.update(values)
    return '\t'.join(base[c] for c in COLUMNS)


class QuickStatsBulkTest(unittest.TestCase):
    def test_filtered_bulk_rows_become_geo_observations(self):
        lines = ['\t'.join(COLUMNS),
                 row(SOURCE_DESC='SURVEY', STATISTICCAT_DESC='YIELD', UNIT_DESC='BU / ACRE',
                     SHORT_DESC='CORN, GRAIN - YIELD, MEASURED IN BU / ACRE', AGG_LEVEL_DESC='COUNTY',
                     COUNTY_ANSI='153', COUNTY_CODE='153', COUNTY_NAME='POLK', ASD_CODE='50', YEAR='2023', VALUE='201.5', **{'CV_%': '2.1'}),
                 row(SOURCE_DESC='CENSUS', STATISTICCAT_DESC='AREA HARVESTED', UNIT_DESC='ACRES',
                     SHORT_DESC='CORN, GRAIN - ACRES HARVESTED', AGG_LEVEL_DESC='STATE', YEAR='2022', VALUE='(D)'),
                 row(SOURCE_DESC='SURVEY', STATISTICCAT_DESC='STOCKS', UNIT_DESC='BU', UTIL_PRACTICE_DESC='ON FARM',
                     SHORT_DESC='CORN, ON FARM - STOCKS, MEASURED IN BU', AGG_LEVEL_DESC='NATIONAL', STATE_FIPS_CODE='99',
                     YEAR='2024', FREQ_DESC='POINT IN TIME', REFERENCE_PERIOD_DESC='FIRST OF DEC', VALUE='1,234,000'),
                 row(SOURCE_DESC='SURVEY', STATISTICCAT_DESC='YIELD', UNIT_DESC='BU / ACRE', SHORT_DESC='OLD',
                     AGG_LEVEL_DESC='STATE', YEAR='1995', VALUE='100'),
                 row(SOURCE_DESC='SURVEY', STATISTICCAT_DESC='PROGRESS', UNIT_DESC='PCT PLANTED', SHORT_DESC='WEEKLY',
                     AGG_LEVEL_DESC='STATE', YEAR='2024', FREQ_DESC='WEEKLY', REFERENCE_PERIOD_DESC='WEEK #20', VALUE='50')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'qs.crops.txt.gz'
            path.write_bytes(gzip.compress(('\n'.join(lines) + '\n').encode()))
            store = Store(Path(tmp) / 'data')
            store.import_shards('usda_agriculture', [{'path': path, 'url': 'https://example.org/qs.crops.txt.gz'}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('usda_agriculture')
            records = list(store.records(ref))
        entities = {r['entity_id']: r['entity_type'] for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['geo:US:county:19153'], 'county')
        self.assertEqual(entities['geo:US:state:19'], 'state')
        self.assertIn('nass:commodity:corn', entities)
        self.assertIn(('geo:US:county:19153', 'geo:US:state:19'),
                      {(r['subject'], r['object']) for r in records if r['kind'] == 'assertion'})
        observations = {r['metric']: r for r in records if r['kind'] == 'observation'}
        self.assertEqual(set(observations), {'yield', 'area_harvested', 'stocks'})
        yield_ = observations['yield']
        self.assertEqual((yield_['subject'], yield_['value'], yield_['unit']), ('geo:US:county:19153', 201.5, 'BU / ACRE'))
        self.assertEqual((yield_['valid_from'], yield_['valid_to']), ('2023-01-01', '2024-01-01'))
        self.assertEqual(yield_['attributes']['cv_percent'], 2.1)
        suppressed = observations['area_harvested']
        self.assertIsNone(suppressed['value'])
        self.assertIn('withheld', suppressed['missing_reason'])
        stocks = observations['stocks']
        self.assertEqual((stocks['subject'], stocks['value'], stocks['valid_from'], stocks['valid_to']),
                         ('geo:US', 1234000, '2024-12-01', '2024-12-02'))
        self.assertEqual(stocks['dimensions']['utilization_practice'], 'on farm')


if __name__ == '__main__':
    unittest.main()
