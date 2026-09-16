"""Offline acceptance tests for the CEPII BACI pipeline (tiny fixture ZIP, real Runner)."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]

COUNTRIES = 'country_code,country_name,country_iso2,country_iso3\n4,Afghanistan,AF,AFG\n12,Algeria,DZ,DZA\n384,CÃ´te d\'Ivoire,CI,CIV\n490,"Other Asia, nes",,S19\n'
PRODUCTS = 'code,description\n010121,"Horses: live, pure-bred"\n010129,Horses other\n020110,Beef carcasses\n'
Y2017 = 't,i,j,k,v,q\n2017,4,12,010121,5.5,1.25\n2017,4,12,010129,1.5,\n2017,4,12,020110,2,3\n2017,4,490,010121,0.25,0.5\n2017,384,4,020110,10,4\n'
Y2018 = 't,i,j,k,v,q\n2018,4,12,010121,7,2\n2018,999,12,010121,1,\n'


class BaciPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def build(self, y2017=Y2017, stage=None):
        archive = self.root / 'baci.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('BACI_HS17_Y2017_V202601.csv', y2017)
            zf.writestr('BACI_HS17_Y2018_V202601.csv', Y2018)
            zf.writestr('product_codes_HS17_V202601.csv', PRODUCTS)
            zf.writestr('country_codes_V202601.csv', COUNTRIES)
            zf.writestr('Readme.txt', 'fixture')
        self.store.import_shards('cepii_baci', [{'path': archive, 'retrieved_at': '2026-09-15T00:00:00+00:00'}],
                                 {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run('cepii_baci', stage=stage)
        return list(self.store.records(ref))

    def test_hs6_flows_entities_and_quantities(self):
        rows = self.build()
        entities = {r['entity_id']: r for r in rows if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:CIV']['label'], "Côte d'Ivoire")
        self.assertEqual(entities['baci:area:490']['entity_type'], 'jurisdiction')
        self.assertTrue(entities['baci:area:999']['attributes']['unmapped_code'])
        self.assertEqual(entities['hs17:010121']['attributes']['hs4'], 'hs17:0101')
        flows = [r for r in rows if r['kind'] == 'observation']
        self.assertEqual(len(flows), 7)
        first = next(r for r in flows if r['id'] == 'baci:v202601:2017:004:012:010121')
        self.assertEqual((first['subject'], first['value'], first['unit'], first['attributes']['quantity_t']),
                         ('iso3:AFG', 5.5, 'thousand_USD', 1.25))
        self.assertEqual(first['dimensions'], {'importer': 'iso3:DZA', 'product': 'hs17:010121', 'frequency': 'annual'})
        self.assertEqual((first['valid_from'], first['valid_to']), ('2017-01-01', '2018-01-01'))
        self.assertEqual(first['evidence'][0]['locator'], 'shard:0/member:BACI_HS17_Y2017_V202601.csv/line:2')
        self.assertIsNone(next(r for r in flows if r['id'].endswith('2017:004:012:010129'))['attributes']['quantity_t'])
        self.assertEqual(sum(r['kind'] == 'entity' and r['entity_id'] == 'iso3:AFG' for r in rows), 1)

    def test_hs4_aggregates_pair_flows_and_totals(self):
        rows = self.build(stage='hs4')
        agg = next(r for r in rows if r['id'] == 'baci:v202601:hs4:2017:004:012:0101')
        self.assertEqual((agg['value'], agg['attributes']['quantity_t'], agg['attributes']['hs6_lines'],
                          agg['attributes']['hs6_lines_without_quantity']), (7.0, 1.25, 2, 1))
        total = next(r for r in rows if r['id'] == 'baci:v202601:total:2017:004:012')
        self.assertEqual((total['subject'], total['value'], total['metric']), ('baci:flow:AFG:DZA', 9.0, 'bilateral_trade_total_value'))
        relations = {(r['subject'], r['predicate'], r['object']) for r in rows if r['kind'] == 'assertion'}
        self.assertIn(('baci:flow:AFG:DZA', 'flow_source', 'iso3:AFG'), relations)
        self.assertIn(('baci:flow:AFG:DZA', 'flow_destination', 'iso3:DZA'), relations)
        self.assertEqual(sum(r['kind'] == 'entity' and r['entity_id'] == 'baci:flow:AFG:DZA' for r in rows), 1)

    def test_unsorted_input_is_rejected_for_streaming_aggregation(self):
        unsorted = 't,i,j,k,v,q\n2017,4,12,010121,5.5,1\n2017,4,490,010121,1,1\n2017,4,12,020110,2,3\n'
        with self.assertRaisesRegex(ValueError, 'not grouped'):
            self.build(unsorted, stage='hs4')


if __name__ == '__main__':
    unittest.main()
