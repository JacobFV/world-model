"""Offline fixture test: tiny relationship files -> crosswalk assertions -> worldmodel.crosswalks."""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.crosswalks import load_concordance_csv
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name
BASE = 'https://www2.census.gov/geo/docs/maps-data/data/'

ZC20 = ('﻿OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|AREALAND_ZCTA5_20|AREAWATER_ZCTA5_20|MTFCC_ZCTA5_20|CLASSFP_ZCTA5_20|'
        'FUNCSTAT_ZCTA5_20|OID_COUNTY_20|GEOID_COUNTY_20|NAMELSAD_COUNTY_20|AREALAND_COUNTY_20|AREAWATER_COUNTY_20|MTFCC_COUNTY_20|'
        'CLASSFP_COUNTY_20|FUNCSTAT_COUNTY_20|AREALAND_PART|AREAWATER_PART\n'
        '||||||||1|01003|Baldwin County|4000|100|G4020|H1|A|40|0\n'
        '2|00601|ZCTA5 00601|1000|0|G6350|B5|S|3|72001|Adjuntas Municipio|5000|0|G4020|H1|A|750|0\n'
        '2|00601|ZCTA5 00601|1000|0|G6350|B5|S|4|72141|Utuado Municipio|3000|0|G4020|H1|A|250|0\n')
Z1020 = ('OID_ZCTA5_10|GEOID_ZCTA5_10|NAMELSAD_ZCTA5_10|AREALAND_ZCTA5_10|AREAWATER_ZCTA5_10|MTFCC_ZCTA5_10|CLASSFP_ZCTA5_10|FUNCSTAT_ZCTA5_10|'
         'OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|AREALAND_ZCTA5_20|AREAWATER_ZCTA5_20|MTFCC_ZCTA5_20|CLASSFP_ZCTA5_20|FUNCSTAT_ZCTA5_20|'
         'AREALAND_PART|AREAWATER_PART\n'
         '1|00601|ZCTA5 00601|900|0|G6350|B5|S|2|00601|ZCTA5 00601|1000|0|G6350|B5|S|900|0\n')
COUSUB = ('OID_COUSUB_20|GEOID_COUSUB_20|NAMELSAD_COUSUB_20|AREALAND_COUSUB_20|AREAWATER_COUSUB_20|MTFCC_COUSUB_20|CLASSFP_COUSUB_20|'
          'FUNCSTAT_COUSUB_20|OID_COUSUB_10|GEOID_COUSUB_10|NAMELSAD_COUSUB_10|AREALAND_COUSUB_10|AREAWATER_COUSUB_10|MTFCC_COUSUB_10|'
          'CLASSFP_COUSUB_10|FUNCSTAT_COUSUB_10|AREALAND_PART|AREAWATER_PART\n'
          '1|0100190171|Autaugaville CCD|478|13|G4040|Z5|S|2|0100190171|Autaugaville CCD|500|13|G4040|Z5|S|478|13\n'
          '3|0100190315|Billingsley CCD|386|1|G4040|Z5|S|2|0100190171|Autaugaville CCD|500|13|G4040|Z5|S|22|0\n')
TRACT = ('OID_TRACT_20|GEOID_TRACT_20|NAMELSAD_TRACT_20|AREALAND_TRACT_20|AREAWATER_TRACT_20|MTFCC_TRACT_20|FUNCSTAT_TRACT_20|'
         'OID_TRACT_10|GEOID_TRACT_10|NAMELSAD_TRACT_10|AREALAND_TRACT_10|AREAWATER_TRACT_10|MTFCC_TRACT_10|FUNCSTAT_TRACT_10|'
         'AREALAND_PART|AREAWATER_PART\n'
         '1|46102940500|Tract 9405|600|0|G5020|S|2|46113940500|Tract 9405|600|0|G5020|S|600|0\n'
         '3|46102940800|Tract 9408|400|0|G5020|S|4|46113940800|Tract 9408|400|0|G5020|S|400|0\n'
         '5|46007940100|Tract 9401|100|0|G5020|S|6|46007940100|Tract 9401|100|0|G5020|S|99|0\n'
         '5|46007940100|Tract 9401|100|0|G5020|S|4|46113940800|Tract 9408|400|0|G5020|S|1|0\n')
ZC10 = ('ZCTA5,STATE,COUNTY,GEOID,POPPT,HUPT,AREAPT,AREALANDPT,ZPOP,ZHU,ZAREA,ZAREALAND,COPOP,COHU,COAREA,COAREALAND,'
        'ZPOPPCT,ZHUPCT,ZAREAPCT,ZAREALANDPCT,COPOPPCT,COHUPCT,COAREAPCT,COAREALANDPCT\n'
        '00601,72,001,72001,18465,7695,165132671,164333375,18570,7744,167459085,166659789,19483,8125,173777444,172725651,99.43,99.37,98.61,98.6,94.77,94.71,95.03,95.14\n'
        '00601,72,141,72141,105,49,2326414,2326414,18570,7744,167459085,166659789,33149,14192,298027589,294039825,0.57,0.63,1.39,1.4,0.32,0.35,0.78,0.79\n')


def adapter():
    spec = importlib.util.spec_from_file_location('crosswalk_export', HERE / 'crosswalk_export.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CensusRelationshipFilesTest(unittest.TestCase):
    def test_pipeline_and_crosswalk_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'crosswalk_export.py'):
                shutil.copy2(HERE / name, catalog / name)
            files = {'rel2020/zcta520/tab20_zcta520_county20_natl.txt': ZC20, 'rel2020/zcta520/tab20_zcta510_zcta520_natl.txt': Z1020,
                     'rel2020/cousub/tab20_cousub20_cousub10_natl.txt': COUSUB, 'rel2020/tract/tab20_tract20_tract10_natl.txt': TRACT,
                     'rel/zcta_county_rel_10.txt': ZC10}
            shards = []
            for number, (path, text) in enumerate(files.items()):
                (tmp / f'{number}.txt').write_text(text, encoding='utf-8')
                shards.append({'path': str(tmp / f'{number}.txt'), 'request': {'url': BASE + path}})
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = list(store.records(ref))
            by_id = {r['id']: r for r in records}
            xw = adapter()
            self.assertEqual(xw.crosswalk_ids(records), {
                'census_county10_county20_via_tract': 3, 'census_cousub10_cousub20': 2, 'census_zcta510_county10': 2,
                'census_zcta510_zcta520': 1, 'census_zcta520_county20': 2})
            split = by_id['xw:census_zcta520_county20:00601:72001']
            self.assertEqual((split['subject'], split['object']), ('geo:US:zcta:00601', 'geo:US:county:72001'))
            self.assertEqual(split['attributes']['weight'], 0.75)
            self.assertEqual(split['attributes']['reverse_weights']['land_area'], 0.15)
            self.assertEqual(by_id['xw:census_zcta520_county20:unmatched:2020:01003:2']['value'], 40)
            recode = by_id['xw:census_county10_county20_via_tract:46113:46102']
            self.assertAlmostEqual(recode['attributes']['weight'], 1000 / 1001, places=11)
            self.assertEqual(recode['attributes']['aggregated_tract_rows'], 2)
            walk = xw.build_crosswalk(records, 'census_zcta520_county20')
            result = walk.apportion({'00601': 100.0})
            self.assertEqual(result['values'], {'72001': 75.0, '72141': 25.0})
            reverse = xw.build_crosswalk(records, 'census_zcta520_county20', reverse=True)
            self.assertAlmostEqual(reverse.targets('72141')[0]['weight'], 250 / 3000, places=11)
            county = xw.build_crosswalk(records, 'census_county10_county20_via_tract')
            self.assertAlmostEqual(county.apportion({'46113': 1001.0})['values']['46102'], 1000.0)
            pop = xw.build_crosswalk(records, 'census_zcta510_county10', basis='housing_units')
            self.assertTrue(pop.temporal)
            self.assertAlmostEqual(pop.targets('00601', at='2015-01-01')[0]['weight'], 7695 / 7744)
            path = tmp / 'zc.csv'
            kwargs = xw.write_concordance_csv(records, 'census_zcta510_county10', path)
            loaded = load_concordance_csv(path, **kwargs)
            self.assertAlmostEqual(loaded.apportion({'00601': 18570})['values']['72141'], 105)


if __name__ == '__main__':
    unittest.main()
