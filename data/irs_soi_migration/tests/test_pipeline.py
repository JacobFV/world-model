"""Offline fixture test: IRS SOI county/state inflow and outflow CSVs -> flows and observations."""
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
BASE = 'https://www.irs.gov/pub/irs-soi/'

FILES = {
    'countyinflow2122.csv': 'y2_statefips,y2_countyfips,y1_statefips,y1_countyfips,y1_state,y1_countyname,n1,n2,agi\n'
                            '1,1,96,0,AL,Autauga County Total Migration-US and Foreign,2105,4580,133082\n'
                            '1,1,1,51,AL,Elmore County,500,1100,30000\n'
                            '1,1,1,1,AL,Autauga County Non-migrants,20000,45000,1500000\n'
                            '1,1,13,121,GA,Fulton County,-1,-1,-1\n',
    'countyoutflow2122.csv': 'y1_statefips,y1_countyfips,y2_statefips,y2_countyfips,y2_state,y2_countyname,n1,n2,agi\n'
                             '1,1,97,3,AL,Autauga County Total Migration-Different State,900,1800,50000\n'
                             '1,1,1,1,AL,Autauga County Non-migrants,20000,45000,1500000\n'
                             '1,1,1,51,AL,Elmore County,450,900,25000\n',
    'stateinflow2122.csv': 'y2_statefips,y1_statefips,y1_state,y1_state_name,n1,n2,AGI\n'
                           '01,97,AL,AL Total Migration-US,55653,107306,3981924\n'
                           '01,97,AL,AL Total Migration-Same State,40000,80000,2000000\n'
                           '01,13,GA,Georgia,7000,14000,500000\n',
}


class IrsMigrationTest(unittest.TestCase):
    def test_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            shards = []
            for name, text in FILES.items():
                (tmp / name).write_text(text, encoding='latin-1')
                shards.append({'path': str(tmp / name), 'request': {'method': 'GET', 'url': BASE + name}})
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        flow = 'irs:migration_flow:county:01051-01001'
        self.assertEqual(records['irsmig:flow:county:01051-01001:source']['object'], 'geo:US:county:01051')
        self.assertEqual(records['irsmig:flow:county:01051-01001:destination']['object'], 'geo:US:county:01001')
        agi = records['irsmig:county:202122:01051-01001:migration_agi']
        self.assertEqual((agi['subject'], agi['value'], agi['unit'], agi['valid_from'], agi['valid_to']),
                         (flow, 30000000, 'USD', '2021-01-01', '2023-01-01'))
        suppressed = records['irsmig:county:202122:13121-01001:migration_returns']
        self.assertIsNone(suppressed['value'])
        self.assertIn('suppressed', suppressed['missing_reason'])
        # outflow duplicates of inflow cells are skipped; outflow-only aggregates are kept
        self.assertEqual(records['irsmig:county:202122:01001-01001:migration_returns']['dimensions']['perspective'], 'inflow')
        self.assertEqual(records['irsmig:county:202122:01001-97003:migration_returns']['value'], 900)
        self.assertNotIn('irsmig:county:202122:01001-01051:migration_returns', records)
        self.assertEqual(records['irsmig:entity:irs:migration_aggregate:96000']['entity_type'], 'aggregate_cohort')
        self.assertEqual(records['irsmig:entity:geo:US:county:01001']['attributes'], {})
        # state files reuse code 97 for US and same-state totals
        self.assertEqual(records['irsmig:state:202122:97000-01:migration_returns']['value'], 55653)
        self.assertEqual(records['irsmig:state:202122:97001-01:migration_returns']['value'], 40000)
        self.assertEqual(records['irsmig:state:202122:13-01:migration_individuals']['value'], 14000)


if __name__ == '__main__':
    unittest.main()
