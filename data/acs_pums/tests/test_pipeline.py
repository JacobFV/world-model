"""Offline fixture test: tiny PUMS person/housing ZIPs -> weighted PUMA aggregates (no microdata rows)."""
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

PERSON = ('RT,SERIALNO,STATE,PUMA,PWGTP,ADJINC,AGEP,SEX,ESR,PINCP,WAGP,NAICSP,SOCP,JWTRNS,JWMNP,SCHL,POVPIP\n'
          'P,1,06,07501,10,1000000,40,1,1,60000,60000,5415,151252,11,,22,400\n'
          'P,1,06,07501,30,1000000,10,2,,,,,,,,03,50\n'
          'P,2,06,07501,20,1100000,30,2,1,20000,20000,722511,352014,01,25,19,150\n')
HOUSING = ('RT,SERIALNO,STATE,PUMA,WGTP,ADJHSG,ADJINC,NP,TYPEHUGQ,TEN,VACS,HINCP,GRNTP,VALP,SMOCP,GRPIP,OCPIP\n'
           'H,1,06,07501,15,1000000,1000000,2,1,1,,100000,,800000,3000,,36\n'
           'H,2,06,07501,25,1000000,1000000,1,1,3,,30000,1500,,,60,\n'
           'H,3,06,07501,5,1000000,1000000,0,1,,1,,,,,,\n'
           'H,4,06,07501,0,1000000,1000000,1,2,,,,,,,,\n')


class PumsTest(unittest.TestCase):
    def test_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            with zipfile.ZipFile(tmp / 'csv_pus.zip', 'w') as archive:
                archive.writestr('psam_pusa.csv', PERSON)
                archive.writestr('ACS_PUMS_README.pdf', b'%PDF')
            with zipfile.ZipFile(tmp / 'csv_hus.zip', 'w') as archive:
                archive.writestr('psam_husa.csv', HOUSING)
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'csv_pus.zip')}, {'path': str(tmp / 'csv_hus.zip')}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        prefix = 'pums24:0607501:'
        self.assertEqual(records['pums:within:20:0607501']['object'], 'geo:US:state:06')
        self.assertEqual(records[prefix + 'households_by_income:25000-49999']['dimensions']['household_income_bin_usd'], '25000-49999')
        self.assertEqual(records[prefix + 'mean_gross_rent']['unit'], 'USD_2024_per_month')
        self.assertEqual(records[prefix + 'population:all']['value'], 60)
        self.assertEqual(records[prefix + 'population_by_age:0-17']['dimensions']['age_group'], '0-17')
        self.assertEqual(records[prefix + 'population_16_plus_by_employment_status:employed']['value'], 30)
        self.assertEqual(records[prefix + 'employed_by_industry_sector:72']['value'], 20)
        self.assertEqual(records[prefix + 'workers_by_commute_mode:worked_from_home']['value'], 10)
        self.assertEqual(records[prefix + 'mean_personal_income']['value'], round((10 * 60000 + 20 * 22000) / 30, 2))
        self.assertEqual(records[prefix + 'mean_commute_time']['value'], 25)
        self.assertEqual(records[prefix + 'population_below_poverty:all']['value'], 30)
        self.assertEqual(records[prefix + 'households:all']['value'], 40)
        self.assertEqual(records[prefix + 'vacant_housing_units:all']['value'], 5)
        self.assertEqual(records[prefix + 'renter_households_by_rent_burden:50_plus']['value'], 25)
        self.assertEqual(records[prefix + 'median_household_income']['value'], 30500)
        self.assertEqual(records[prefix + 'mean_home_value']['unit'], 'USD_2024')
        self.assertFalse(any(r['kind'] == 'observation' and r['subject'] != 'geo:US:puma20:0607501' for r in records.values()))


if __name__ == '__main__':
    unittest.main()
