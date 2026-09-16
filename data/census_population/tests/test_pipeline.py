"""Offline fixture test for full Population Estimates files (the legacy sample path is covered by tests/test_normalizer_contracts.py)."""
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

FILES = {
    'nst.csv': 'SUMLEV,REGION,DIVISION,STATE,NAME,ESTIMATESBASE2020,POPESTIMATE2023,POPESTIMATE2024,BIRTHS2024,NPOPCHG_2020,RBIRTH2024\n'
               '010,0,0,00,United States,331515736,336806231,340110988,3605563,61984,10.6\n'
               '040,4,9,06,California,39538245,38965193,39431263,400000,-500,10.1\n',
    'co.csv': 'SUMLEV,REGION,DIVISION,STATE,COUNTY,STNAME,CTYNAME,POPESTIMATE2024,DOMESTICMIG2024,GQESTIMATES2024\n'
              '040,4,9,06,000,California,California,39431263,-200000,800000\n'
              '050,4,9,06,075,California,San Francisco County,827526,-9000,20000\n',
    'cc.csv': 'SUMLEV,STATE,COUNTY,STNAME,CTYNAME,YEAR,AGEGRP,TOT_POP,TOT_MALE,TOT_FEMALE,WA_MALE,WA_FEMALE,BA_MALE,BA_FEMALE,IA_MALE,IA_FEMALE,'
              'AA_MALE,AA_FEMALE,NA_MALE,NA_FEMALE,TOM_MALE,TOM_FEMALE,H_MALE,H_FEMALE,NHWA_MALE,NHWA_FEMALE\n'
              '050,06,075,California,San Francisco County,1,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n'
              '050,06,075,California,San Francisco County,6,0,827526,420000,407526,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16\n'
              '050,06,075,California,San Francisco County,6,1,30000,15500,14500,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0\n',
    'cbsa.csv': 'CBSA,MDIV,STCOU,NAME,LSAD,POPESTIMATE2024\n'
                '41860,,,"San Francisco-Oakland-Fremont, CA",Metropolitan Statistical Area,4648486\n'
                '41860,41884,,"San Francisco-San Mateo-Redwood City, CA",Metropolitan Division,1500000\n'
                '41860,41884,06075,"San Francisco County, CA",County or equivalent,827526\n',
    'co2020.csv': 'SUMLEV,REGION,DIVISION,STATE,COUNTY,STNAME,CTYNAME,CENSUS2010POP,POPESTIMATE2010,POPESTIMATE2020,BIRTHS2010,BIRTHS2011\n'
                  '050,4,9,06,075,California,San Francisco County,805235,805505,870000,2000,8900\n',
    'nst2023.csv': 'SUMLEV,REGION,DIVISION,STATE,NAME,ESTIMATESBASE2020,POPESTIMATE2020,POPESTIMATE2023\n'
                   '010,0,0,00,United States,331464948,331526933,334914895\n',
}
LAST_MODIFIED = {'nst.csv': 'Thu, 19 Dec 2024 13:35:06 GMT', 'nst2023.csv': 'Tue, 19 Dec 2023 13:55:13 GMT'}


class CensusPopulationFullTest(unittest.TestCase):
    def test_full_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            shards = []
            for name, text in FILES.items():
                (tmp / name).write_text(text, encoding='latin-1')
                shards.append({'path': str(tmp / name), **({'last_modified': LAST_MODIFIED[name]} if name in LAST_MODIFIED else {})})
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        us = records['pep24:US:population:2024']
        self.assertEqual((us['subject'], us['value'], us['valid_from'], us['valid_to']), ('geo:US', 340110988, '2024-07-01', '2024-07-02'))
        births = records['pep24:state:06:births:2024']
        self.assertEqual((births['valid_from'], births['valid_to']), ('2023-07-01', '2024-07-01'))
        self.assertEqual(records['pep24:US:population_change:2020']['valid_from'], '2020-04-01')
        self.assertEqual(records['pep24:US:births_rate:2024']['unit'], 'per_1000_population')
        self.assertIn('pep24:county:06075:net_domestic_migration:2024', records)
        self.assertNotIn('pep24:state:06:net_domestic_migration:2024', records)  # state rows of county file skipped
        self.assertEqual(records['pep24:county:06075:pop:all:nhwa_female:2024']['dimensions'],
                         {'vintage': 2024, 'age_group': 'all', 'sex': 'female', 'hispanic_origin': 'not_hispanic', 'race': 'white_alone'})
        self.assertEqual(records['pep24:county:06075:pop:0-4:male:2024']['value'], 15500)
        self.assertFalse(any(':2020' in k and ':pop:' in k for k in records))  # YEAR=1 estimates base skipped
        self.assertEqual(records['pep:within:geo:US:county:06075:geo:US:metdiv:41884']['object'], 'geo:US:metdiv:41884')
        self.assertEqual(records['pep24:cbsa:41860:population:2024']['value'], 4648486)
        census = records['pep20:county:06075:population:decennial_census:2010']
        self.assertEqual((census['valid_from'], census['dimensions']['vintage']), ('2010-04-01', 2020))
        self.assertEqual(records['pep20:county:06075:births:2011']['valid_from'], '2010-07-01')
        # every vintage is its own series with its release (knowledge) time
        v2023, v2024 = records['pep23:US:population:2023'], records['pep24:US:population:2023']
        self.assertEqual((v2023['value'], v2023['dimensions']['vintage'], v2023['attributes']['released_at']),
                         (334914895, 2023, '2023-12-19T13:55:13Z'))
        self.assertEqual((v2024['value'], v2024['attributes']['released_at']), (336806231, '2024-12-19T13:35:06Z'))
        self.assertEqual(records['pep23:US:population:estimates_base:2020']['value'], 331464948)


if __name__ == '__main__':
    unittest.main()
