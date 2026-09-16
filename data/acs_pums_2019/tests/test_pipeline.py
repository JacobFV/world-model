"""Offline fixture test: 2015-2019 PUMS layout (ST, TYPE columns) -> 2010-PUMA aggregates in 2019 dollars."""
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

PERSON = ('RT,SERIALNO,DIVISION,SPORDER,PUMA,REGION,ST,ADJINC,PWGTP,AGEP,SEX,ESR,PINCP,WAGP,NAICSP,SOCP,JWTRNS,JWMNP,SCHL,POVPIP\n'
          'P,2015000000067,6,01,02701,3,01,1080470,9,61,1,1,14000,14000,4MS,4930XX,01,20,19,150\n'
          'P,2015000000068,6,01,02701,3,01,1080470,11,30,2,3,,,,,,,21,80\n')
HOUSING = ('RT,SERIALNO,DIVISION,PUMA,REGION,ST,ADJHSG,ADJINC,WGTP,NP,TYPE,TEN,VACS,HINCP,GRNTP,VALP,SMOCP,GRPIP,OCPIP\n'
           'H,2015000000067,6,02701,3,01,1079106,1080470,9,4,1,3,,17450,250,,,17,\n'
           'H,2015000000069,6,02701,3,01,1079106,1080470,4,1,2,,,,,,,,\n')


class Pums2019Test(unittest.TestCase):
    def test_2019_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            with zipfile.ZipFile(tmp / 'csv_pus.zip', 'w') as archive:
                archive.writestr('psam_pusa.csv', PERSON)
            with zipfile.ZipFile(tmp / 'csv_hus.zip', 'w') as archive:
                archive.writestr('psam_husa.csv', HOUSING)
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'csv_pus.zip')}, {'path': str(tmp / 'csv_hus.zip')}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        prefix = 'pums19:0102701:'
        population = records[prefix + 'population:all']
        self.assertEqual((population['subject'], population['value']), ('geo:US:puma10:0102701', 20))
        self.assertEqual((population['valid_from'], population['valid_to'], population['dimensions']['period']),
                         ('2015-01-01', '2020-01-01', '2015-2019'))
        self.assertEqual(records['pums:within:10:0102701']['object'], 'geo:US:state:01')
        income = records[prefix + 'mean_personal_income']
        self.assertEqual((income['value'], income['unit']), (round(14000 * 1.08047, 2), 'USD_2019'))
        self.assertEqual(records[prefix + 'households:all']['value'], 9)  # group quarters (TYPE 2) excluded
        self.assertEqual(records[prefix + 'mean_gross_rent']['unit'], 'USD_2019_per_month')
        self.assertEqual(records[prefix + 'population_16_plus_by_employment_status:unemployed']['value'], 11)
        self.assertEqual(records[prefix + 'employed_by_occupation_group:49']['value'], 9)


if __name__ == '__main__':
    unittest.main()
