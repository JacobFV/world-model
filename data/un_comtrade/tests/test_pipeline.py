"""Offline acceptance tests for the UN Comtrade pipeline (fixture JSON pages, real Runner)."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


def record(**overrides):
    row = {'typeCode': 'C', 'freqCode': 'M', 'refYear': 2025, 'refMonth': 3, 'period': '202503', 'reporterCode': 842,
           'reporterISO': 'USA', 'reporterDesc': 'USA', 'flowCode': 'M', 'partnerCode': 0, 'partnerISO': 'W00',
           'partnerDesc': 'World', 'partner2Code': 0, 'classificationCode': 'H6', 'cmdCode': '87',
           'cmdDesc': 'Vehicles other than railway', 'customsCode': 'C00', 'motCode': 0, 'mosCode': '0',
           'qtyUnitCode': -1, 'qtyUnitAbbr': 'N/A', 'qty': 0.0, 'isQtyEstimated': False, 'netWgt': 1500.5,
           'isNetWgtEstimated': False, 'cifvalue': 1000.0, 'fobvalue': 950.0, 'primaryValue': 1000.0,
           'isReported': True, 'isAggregate': True}
    row.update(overrides)
    return row


class ComtradePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def build(self, pages):
        shards = []
        for number, page in enumerate(pages):
            path = self.root / f'page{number}.json'
            path.write_text(json.dumps(page))
            shards.append({'path': path, 'retrieved_at': '2026-09-15T00:00:00+00:00'})
        self.store.import_shards('un_comtrade', shards, {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run('un_comtrade')
        return list(self.store.records(ref))

    def test_monthly_values_weights_quantities_and_areas(self):
        pages = [{'count': 2, 'error': '', 'data': [
                     record(),
                     record(flowCode='X', cmdCode='TOTAL', cmdDesc='All Commodities', partnerCode=490, partnerISO='S19',
                            partnerDesc='Other Asia, nes', netWgt=None, primaryValue=None, qtyUnitCode=8, qtyUnitAbbr='kg', qty=12.0)]},
                 {'count': 0, 'error': '', 'data': []}]
        rows = self.build(pages)
        entities = {r['entity_id']: r for r in rows if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:USA']['entity_type'], 'country')
        self.assertEqual(entities['comtrade:area:0']['entity_type'], 'jurisdiction')
        self.assertIn('comtrade:area:490', entities)
        self.assertIn('hs:TOTAL', entities)
        obs = [r for r in rows if r['kind'] == 'observation']
        value = next(r for r in obs if r['metric'] == 'trade_value' and r['dimensions']['flow'] == 'import')
        self.assertEqual((value['subject'], value['value'], value['unit'], value['valid_from'], value['valid_to']),
                         ('iso3:USA', 1000, 'USD', '2025-03-01', '2025-04-01'))
        self.assertEqual((value['dimensions']['partner'], value['dimensions']['product'], value['attributes']['valuation']),
                         ('comtrade:area:0', 'hs:87', 'CIF'))
        self.assertEqual(next(r for r in obs if r['metric'] == 'trade_net_weight')['value'], 1500.5)
        missing = next(r for r in obs if r['metric'] == 'trade_value' and r['dimensions']['flow'] == 'export')
        self.assertIsNone(missing['value'])
        self.assertEqual(missing['missing_reason'], 'not_reported_by_comtrade')
        quantity = next(r for r in obs if r['metric'] == 'trade_quantity')
        self.assertEqual((quantity['value'], quantity['unit']), (12, 'kg'))
        self.assertEqual(value['evidence'][0]['locator'], 'shard:0/record:0')

    def test_batched_reporters_in_one_page_share_entities(self):
        page = {'count': 3, 'error': '', 'data': [
            record(),
            record(reporterCode=699, reporterISO='IND', reporterDesc='India', refYear=2024, refMonth=12, period='202412'),
            record(reporterCode=699, reporterISO='IND', reporterDesc='India', refYear=2024, refMonth=12, period='202412',
                   flowCode='X', cmdCode='TOTAL', partnerCode=842, partnerISO='USA', partnerDesc='USA')]}
        rows = self.build([page])
        entities = [r['entity_id'] for r in rows if r['kind'] == 'entity']
        self.assertEqual(len(entities), len(set(entities)))
        self.assertIn('iso3:IND', entities)
        india = [r for r in rows if r['kind'] == 'observation' and r['subject'] == 'iso3:IND' and r['metric'] == 'trade_value']
        self.assertEqual(sorted((r['dimensions']['partner'], r['valid_from']) for r in india),
                         [('comtrade:area:0', '2024-12-01'), ('iso3:USA', '2024-12-01')])
        self.assertEqual({r['evidence'][0]['locator'] for r in india}, {'shard:0/record:1', 'shard:0/record:2'})

    def test_error_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'error'):
            self.build([{'error': 'Rate limit', 'data': None}])


if __name__ == '__main__':
    unittest.main()
