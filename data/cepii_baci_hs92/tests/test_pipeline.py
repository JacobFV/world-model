"""Offline acceptance tests for the CEPII BACI HS92 pipeline (tiny fixture ZIP, real Runner)."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]

COUNTRIES = 'country_code,country_name,country_iso2,country_iso3\n4,Afghanistan,AF,AFG\n12,Algeria,DZ,DZA\n384,CÃ´te d\'Ivoire,CI,CIV\n490,"Other Asia, nes",,S19\n'
PRODUCTS = ('code,description\n010111,"Horses: live, pure-bred"\n010119,Horses other\n020110,Beef carcasses\n'
            '9999AA,Commodities not specified according to kind **LEGACY NON-WCO CODE**\n')
Y1995 = ('t,i,j,k,v,q\n1995,4,12,010111,5.5,1.25\n1995,4,12,010119,1.5,\n1995,4,12,020110,2,3\n1995,4,12,9999AA,4,\n'
         '1995,4,490,010111,0.25,0.5\n1995,384,4,020110,10,4\n')
Y2024 = 't,i,j,k,v,q\n2024,4,12,010111,7,2\n2024,999,12,010111,1,\n'


class BaciHs92PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def build(self, y1995=Y1995, stage=None, member='BACI_HS92_Y1995_V202601.csv'):
        archive = self.root / 'baci.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(member, y1995)
            zf.writestr('BACI_HS92_Y2024_V202601.csv', Y2024)
            zf.writestr('product_codes_HS92_V202601.csv', PRODUCTS)
            zf.writestr('country_codes_V202601.csv', COUNTRIES)
            zf.writestr('Readme.txt', 'fixture')
        self.store.import_shards('cepii_baci_hs92', [{'path': archive, 'retrieved_at': '2026-09-15T00:00:00+00:00'}],
                                 {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run('cepii_baci_hs92', stage=stage)
        return list(self.store.records(ref))

    def test_hs6_flows_entities_and_optional_quantity(self):
        rows = self.build()
        entities = {r['entity_id']: r for r in rows if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:CIV']['label'], "Côte d'Ivoire")
        self.assertEqual(entities['baci:area:490']['entity_type'], 'jurisdiction')
        self.assertTrue(entities['baci:area:999']['attributes']['unmapped_code'])
        self.assertEqual(entities['hs92:010111']['attributes']['nomenclature'], 'HS1992')
        self.assertIsNone(entities['hs92:010111']['attributes']['special_code'])
        self.assertEqual(entities['hs92:9999AA']['attributes']['special_code'], 'not_a_wco_hs_code')
        flows = [r for r in rows if r['kind'] == 'observation']
        self.assertEqual(len(flows), 8)
        self.assertEqual(next(r for r in flows if r['id'] == 'baci92:1995:004:012:9999AA')['dimensions']['product'], 'hs92:9999AA')
        first = next(r for r in flows if r['id'] == 'baci92:1995:004:012:010111')
        self.assertEqual((first['subject'], first['value'], first['unit'], first['attributes']['quantity_t']),
                         ('iso3:AFG', 5.5, 'thousand_USD', 1.25))
        self.assertEqual(first['dimensions'], {'importer': 'iso3:DZA', 'product': 'hs92:010111', 'frequency': 'annual'})
        self.assertEqual((first['valid_from'], first['valid_to']), ('1995-01-01', '1996-01-01'))
        self.assertEqual(first['evidence'][0]['locator'], 'shard:0/member:BACI_HS92_Y1995_V202601.csv/line:2')
        self.assertNotIn('attributes', next(r for r in flows if r['id'] == 'baci92:1995:004:012:010119'))

    def test_hs4_aggregates_pair_flows_and_totals(self):
        rows = self.build(stage='hs4')
        agg = next(r for r in rows if r['id'] == 'baci92:hs4:1995:004:012:0101')
        self.assertEqual((agg['value'], agg['attributes']['quantity_t'], agg['attributes']['hs6_lines'],
                          agg['attributes']['hs6_lines_without_quantity']), (7.0, 1.25, 2, 1))
        unclassified = next(r for r in rows if r['id'] == 'baci92:hs4:1995:004:012:9999')
        self.assertEqual((unclassified['value'], unclassified['dimensions']['product']), (4.0, 'hs92:9999'))
        total = next(r for r in rows if r['id'] == 'baci92:total:1995:004:012')
        self.assertEqual((total['subject'], total['value']), ('baci92:flow:AFG:DZA', 13.0))
        relations = {(r['subject'], r['predicate'], r['object']) for r in rows if r['kind'] == 'assertion'}
        self.assertIn(('baci92:flow:AFG:DZA', 'flow_source', 'iso3:AFG'), relations)
        self.assertIn(('baci92:flow:AFG:DZA', 'flow_destination', 'iso3:DZA'), relations)

    def test_unsorted_input_and_wrong_nomenclature_are_rejected(self):
        unsorted = 't,i,j,k,v,q\n1995,4,12,010111,5.5,1\n1995,4,490,010111,1,1\n1995,4,12,020110,2,3\n'
        with self.assertRaisesRegex(ValueError, 'not grouped'):
            self.build(unsorted, stage='hs4')


if __name__ == '__main__':
    unittest.main()
