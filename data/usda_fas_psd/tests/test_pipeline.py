"""Offline acceptance test: PSD ZIP shard -> evidence records through the Runner."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
HEADER = 'Commodity_Code,Commodity_Description,Country_Code,Country_Name,Market_Year,Calendar_Year,Month,Attribute_ID,Attribute_Description,Unit_ID,Unit_Description,Value\n'
ROWS = [
    '0440000,Corn,US,United States,2024,2026,09,028,Production,08,(1000 MT),377633.0000',
    '0440000,Corn,US,United States,2024,2026,09,176,Ending Stocks,08,(1000 MT),',
    '0440000,Corn,E4,European Union,2024,2026,09,088,Exports,08,(1000 MT),4500.0000',
    '0410000,Wheat,UR,Union of Soviet Socialist Repu,1985,2018,00,028,Production,08,(1000 MT),78000.0000',
]


class PsdPipelineTest(unittest.TestCase):
    def test_zip_rows_become_country_commodity_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'psd.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('psd_alldata.csv', HEADER + '\n'.join(ROWS) + '\n')
            store = Store(Path(tmp) / 'data')
            store.import_shards('usda_fas_psd', [{'path': path, 'url': 'https://example.org/psd.zip'}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('usda_fas_psd')
            records = list(store.records(ref))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:USA']['entity_type'], 'country')
        self.assertTrue(entities['usda_psd:country:E4']['attributes']['aggregate'])
        self.assertIn('usda_psd:commodity:0440000', entities)
        observations = {r['metric']: r for r in records if r['kind'] == 'observation'}
        production = observations['production']
        self.assertEqual((production['subject'], production['value'], production['unit']), ('iso3:USA', 377633, '1000 t'))
        self.assertEqual((production['valid_from'], production['valid_to']), ('2024-01-01', '2025-01-01'))
        self.assertEqual(production['dimensions']['commodity'], 'usda_psd:commodity:0440000')
        self.assertEqual(production['evidence'][0]['locator'], 'shard:0/member:psd_alldata.csv/line:2')
        self.assertIsNone(observations['ending_stocks']['value'])
        self.assertTrue(observations['ending_stocks']['missing_reason'])
        self.assertTrue(observations['exports']['attributes']['aggregate'])
        # Market years before min_year (2000) are excluded.
        self.assertFalse(any(r.get('subject') == 'usda_psd:country:UR' for r in records))


if __name__ == '__main__':
    unittest.main()
