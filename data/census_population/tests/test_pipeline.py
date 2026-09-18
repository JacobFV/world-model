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
    # Vintage 2011: SUMLEV '10' and STATE '0' rather than '010'/'00'.
    'nst2011.csv': 'SUMLEV,REGION,DIVISION,STATE,NAME,CENSUS2010POP,ESTIMATESBASE2010,POPESTIMATE2010,POPESTIMATE2011,'
                   'BIRTHS2010,BIRTHS2011\n'
                   '10,0,0,0,United States,308745538,308745538,309330219,311591917,990000,4008000\n'
                   '40,4,9,6,California,37253956,37253956,37336011,37683933,100000,500000\n',
    # Vintage 2006: mixed-case field names ('Sumlev', 'births2006') and INTERNALMIG instead of DOMESTICMIG.
    'nst2006.csv': 'Sumlev,region,division,state,NAME,CENSUS2000POP,ESTIMATESBASE2000,POPESTIMATE2000,POPESTIMATE2006,'
                   'births2000,births2006,INTERNALMIG2006\n'
                   '010,0,0,00,United States,281421906,281424602,282216952,299398484,989020,4151889,0\n',
    # 2000-2010 national intercensal series: one row per (reference month, year, single year of age).
    'usint.csv': 'MONTH,YEAR,AGE,TOT_POP,TOT_MALE,TOT_FEMALE\n'
                 '4,2000,999,281424600,138056128,143368472\n'
                 '7,2000,999,282162411,138411644,143750767\n'
                 '7,2008,999,304093966,149925000,154168966\n'
                 '7,2008,30,4200000,2100000,2100000\n'
                 '4,2010,999,308745538,151781326,156964212\n',
}
LAST_MODIFIED = {'nst.csv': 'Thu, 19 Dec 2024 13:35:06 GMT', 'nst2023.csv': 'Tue, 19 Dec 2023 13:55:13 GMT',
                 'nst2011.csv': 'Tue, 19 Jul 2016 16:50:13 GMT', 'usint.csv': 'Wed, 24 Aug 2016 20:50:33 GMT'}


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
        # Vintage 2011: unpadded SUMLEV/STATE still resolve to the canonical geography ids.
        v2011 = records['pep11:US:population:2011']
        self.assertEqual((v2011['value'], v2011['dimensions']['vintage'], v2011['attributes']['released_at']),
                         (311591917, 2011, '2016-07-19T16:50:13Z'))
        self.assertEqual(records['pep11:state:06:population:2010']['value'], 37336011)
        self.assertEqual(records['pep11:US:population:decennial_census:2010']['value'], 308745538)
        # The base year comes from ESTIMATESBASE, so the first estimate year starts at its April 1 base.
        self.assertEqual(records['pep11:US:births:2010']['valid_from'], '2010-04-01')
        self.assertEqual(records['pep11:US:births:2011']['valid_from'], '2010-07-01')
        # Vintage 2006: mixed-case fields, a 2000 census base, and INTERNALMIG read as domestic migration.
        v2006 = records['pep06:US:population:2006']
        self.assertEqual((v2006['value'], v2006['dimensions']['vintage']), (299398484, 2006))
        self.assertEqual(records['pep06:US:population:estimates_base:2000']['value'], 281424602)
        self.assertEqual(records['pep06:US:births:2000']['valid_from'], '2000-04-01')
        self.assertEqual(records['pep06:US:net_domestic_migration:2006']['value'], 0)
        # Intercensal series: all-ages July-1 totals plus the April-1 census counts, under its own vintage label.
        intercensal = records['pepint:geo:US:population:2008']
        self.assertEqual((intercensal['value'], intercensal['valid_from'], intercensal['valid_to']),
                         (304093966, '2008-07-01', '2008-07-02'))
        self.assertEqual(intercensal['dimensions'], {'vintage': '2000_2010_intercensal'})
        self.assertEqual(intercensal['attributes']['released_at'], '2016-08-24T20:50:33Z')
        self.assertEqual(records['pepint:geo:US:population:2000']['value'], 282162411)
        self.assertEqual(records['pepint:geo:US:population:decennial_census:2010']['dimensions'],
                         {'vintage': '2000_2010_intercensal', 'basis': 'decennial_census'})
        # Age detail is not emitted: a national population row with an age or sex dimension would collide with
        # the annual national series that the estimation loader selects on metric and subject alone.
        self.assertFalse([k for k in records if k.startswith('pepint:') and ('male' in k or ':30' in k)])
        national = [r for r in records.values() if r.get('metric') == 'population' and r.get('subject') == 'geo:US'
                    and 'basis' not in (r.get('dimensions') or {})]
        keys = [(r['dimensions']['vintage'], r['valid_from']) for r in national]
        self.assertEqual(len(keys), len(set(keys)), 'one national population row per vintage and period')


if __name__ == '__main__':
    unittest.main()
