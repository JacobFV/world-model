"""Offline tests for the Census international trade pipeline."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.acquisition import normalize_config
from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
DATASET = 'census_intl_trade'

IMPORTS = [["I_COMMODITY", "GEN_VAL_MO", "CON_VAL_MO", "CAL_DUT_MO", "DUT_VAL_MO", "time", "COMM_LVL", "CTY_CODE"],
           ["570331", "409808", "380908", "95227", "380908", "2025-06", "HS6", "5700"],
           ["830910", "0", "0", "0", "0", "2025-06", "HS6", "5700"]]
# Duplicate CTY_CODE header (field requested and used as predicate) must not break parsing.
EXPORTS = [["E_COMMODITY", "CTY_CODE", "ALL_VAL_MO", "time", "COMM_LVL", "CTY_CODE"],
           ["841790", "-", "100705", "2026-07", "HS6", "-"],
           ["711510", "0003", "2500", "2026-07", "HS6", "0003"]]


class CensusTradeTests(unittest.TestCase):
    def test_config_is_valid(self):
        definition = json.loads((ROOT / 'data' / DATASET / 'dataset.json').read_text())
        config = normalize_config(definition['acquisition'])
        self.assertEqual(config['credentials'], [{'env': 'CENSUS_API_KEY', 'query': 'key'}])

    def test_imports_exports_partners_and_zero_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shards = []
            for name, payload in (('imports.json', IMPORTS), ('exports.json', EXPORTS)):
                (root / name).write_text(json.dumps(payload))
                shards.append({'path': root / name})
            (root / 'empty').write_bytes(b'')
            shards.append({'path': root / 'empty'})
            store = Store(root / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run(DATASET)
            records = list(store.records(ref))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:CHN']['entity_type'], 'country')
        self.assertEqual(sum(r['kind'] == 'entity' and r['entity_id'] == 'iso3:USA' for r in records), 1)
        self.assertTrue(entities['census:partner:all']['attributes']['aggregate'])
        self.assertTrue(entities['census:country:0003']['attributes']['aggregate'])
        self.assertNotIn('hs:830910', entities)  # All-zero month is skipped, not emitted as trade.
        obs = {(r['metric'], r['dimensions']['partner'], r['dimensions']['product']): r for r in records if r['kind'] == 'observation'}
        duty = obs[('import_calculated_duty', 'iso3:CHN', 'hs:570331')]
        self.assertEqual((duty['value'], duty['unit'], duty['valid_from'], duty['valid_to']), (95227, 'USD', '2025-06-01', '2025-07-01'))
        self.assertAlmostEqual(duty['attributes']['effective_duty_rate_fraction'], 95227 / 380908, places=6)
        self.assertEqual(obs[('import_general_value', 'iso3:CHN', 'hs:570331')]['value'], 409808)
        exports = obs[('export_value', 'census:partner:all', 'hs:841790')]
        self.assertEqual((exports['subject'], exports['value'], exports['dimensions']['flow'], exports['evidence'][0]['locator']),
                         ('iso3:USA', 100705, 'exports', 'shard:1/record:1'))


if __name__ == '__main__':
    unittest.main()
