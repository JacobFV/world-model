"""Offline fixture test: Census API JSON table responses (county group and tract variable list)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name

COUNTY = [['B19013_001E', 'B19013_001EA', 'B19013_001M', 'B19013_001MA', 'GEO_ID', 'NAME', 'state', 'county'],
          ['-666666666', '-', '-222222222', '**', '0500000US35011', 'De Baca County, New Mexico', '35', '011'],
          ['55625', None, '13820', None, '0500000US30019', 'Daniels County, Montana', '30', '019']]
TRACT = [['GEO_ID', 'B01003_001E', 'B01003_001M', 'B25077_001E', 'B25077_001M', 'state', 'county', 'tract'],
         ['1400000US10001040100', '7465', '869', '250000', '-555555555', '10', '001', '040100']]


class AcsTest(unittest.TestCase):
    def test_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            (tmp / 'county.json').write_text(json.dumps(COUNTY))
            (tmp / 'tract.json').write_text(json.dumps(TRACT))
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'county.json')}, {'path': str(tmp / 'tract.json')}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        income = records['acs5y24:county:30019:B19013_001']
        self.assertEqual((income['metric'], income['value'], income['unit'], income['attributes']['moe90']),
                         ('median_household_income', 55625, 'USD_2024', 13820))
        self.assertEqual((income['valid_from'], income['valid_to']), ('2020-01-01', '2025-01-01'))
        missing = records['acs5y24:county:35011:B19013_001']
        self.assertIsNone(missing['value'])
        self.assertEqual((missing['missing_reason'], missing['attributes']['moe_status'], missing['attributes']['moe_annotation']),
                         ('estimate_not_computable', 'moe_not_computable', '**'))
        population = records['acs5y24:tract:10001040100:B01003_001']
        self.assertEqual((population['subject'], population['value']), ('geo:US:tract:10001040100', 7465))
        self.assertEqual(records['acs5y24:tract:10001040100:B25077_001']['attributes']['moe_status'], 'estimate_controlled_no_moe')
        self.assertEqual(records['acs:within:geo:US:tract:10001040100']['object'], 'geo:US:county:10001')
        self.assertEqual(records['acs:entity:geo:US:county:30019']['label'], 'Daniels County, Montana')


if __name__ == '__main__':
    unittest.main()
