"""Offline fixture tests: NRI ArcGIS JSONL pages and a manually imported NRI table ZIP."""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name


def run(tmp, path, **metadata):
    catalog = tmp / 'catalog' / DATASET
    catalog.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
    shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
    store = Store(tmp / 'data')
    if metadata.get('single'):
        store.import_file(DATASET, path, {'publisher': 'fixture'})
    else:
        store.import_shards(DATASET, [{'path': str(path)}], {'publisher': 'fixture'}, complete=True)
    ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
    return {r['id']: r for r in store.records(ref)}


class NriTest(unittest.TestCase):
    def test_feature_pages(self):
        county = {'attributes': {'NRI_ID': 'C06075', 'STATEFIPS': '06', 'COUNTYFIPS': '075', 'STCOFIPS': '06075',
                                 'COUNTY': 'San Francisco', 'COUNTYTYPE': 'County', 'STATEABBRV': 'CA', 'POPULATION': 873965,
                                 'EAL_VALT': 123456789.5, 'RISK_SCORE': 98.1, 'RISK_RATNG': 'Very High', 'ERQK_EALT': 1.0e8,
                                 'ERQK_AFREQ': 0.02, 'HRCN_EALT': None, 'NRI_VER': 'December 2025'}}
        tract = {'attributes': {'NRI_ID': 'T06075010100', 'STCOFIPS': '06075', 'TRACTFIPS': '06075010100', 'COUNTY': 'San Francisco',
                                'STATEABBRV': 'CA', 'SOVI_SCORE': 40.2, 'IFLD_RISKS': 12.5, 'NRI_VER': 'December 2025'}}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'pages.jsonl').write_text(json.dumps(county) + '\n' + json.dumps(tract) + '\n')
            records = run(tmp, tmp / 'pages.jsonl')
        eal = records['nri:county:06075:eal_valt']
        self.assertEqual((eal['subject'], eal['metric'], eal['value'], eal['unit'], eal['dimensions']['hazard']),
                         ('geo:US:county:06075', 'expected_annual_loss', 123456789.5, 'USD/year', 'all'))
        quake = records['nri:county:06075:erqk_afreq']
        self.assertEqual((quake['metric'], quake['dimensions']['hazard']), ('hazard_annualized_frequency', 'earthquake'))
        self.assertNotIn('nri:county:06075:hrcn_ealt', records)
        self.assertEqual(records['nri:within:tract:06075010100']['object'], 'geo:US:county:06075')
        self.assertEqual(records['nri:tract:06075010100:ifld_risks']['dimensions']['hazard'], 'inland_flooding')

    def test_imported_table_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with zipfile.ZipFile(tmp / 'NRI_Table_Counties.zip', 'w') as archive:
                archive.writestr('NRI_Table_Counties.csv', 'NRI_ID,STCOFIPS,COUNTY,STATEABBRV,RISK_SCORE,TRND_EALT\nC01001,01001,Autauga,AL,45.5,1200.25\n')
                archive.writestr('NRIDataDictionary.csv', 'Field Name,Field Alias\nRISK_SCORE,National Risk Index - Score\n')
            records = run(tmp, tmp / 'NRI_Table_Counties.zip', single=True)
        self.assertEqual(records['nri:county:01001:risk_score']['value'], 45.5)
        self.assertEqual(records['nri:county:01001:trnd_ealt']['dimensions']['hazard'], 'tornado')


if __name__ == '__main__':
    unittest.main()
