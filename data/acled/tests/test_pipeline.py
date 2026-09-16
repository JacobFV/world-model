"""Offline acceptance test: ACLED API JSONL page shard -> events and monthly aggregates."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.acquisition import normalize_config
from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


class AcledPipelineTest(unittest.TestCase):
    def test_declaration_is_valid_and_budget_zero(self):
        definition = json.loads((ROOT / 'data/acled/dataset.json').read_text())
        config = normalize_config(definition['acquisition'])
        self.assertEqual(config['desired_bytes'], 0)
        self.assertEqual(definition['status'], 'awaiting_credentials')

    def test_events_and_aggregates(self):
        rows = [{'event_id_cnty': 'FIC1', 'event_date': '2026-01-03', 'event_type': 'Battles', 'sub_event_type': 'Armed clash',
                 'actor1': 'Military Forces of Fictionland', 'inter1': '1', 'actor2': 'Fictional Rebels', 'inter2': '2',
                 'iso': '999', 'country': 'Fictionland', 'admin1': 'North', 'latitude': '1.5', 'longitude': '2.5',
                 'fatalities': '3', 'notes': 'must not be copied'},
                {'event_id_cnty': 'FIC2', 'event_date': '2026-01-20', 'event_type': 'Battles', 'actor1': 'Fictional Rebels',
                 'iso': '999', 'iso3': 'FIC', 'country': 'Fictionland', 'fatalities': '0'}]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shard = tmp / 'page.jsonl'
            shard.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            store = Store(tmp / 'data')
            store.import_shards('acled', [{'path': shard}], {'publisher': 'fixture'}, complete=True)
            records = list(store.records(Runner(Catalog(ROOT / 'data'), store, ROOT).run('acled')))
            events = [r for r in records if r['kind'] == 'event']
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]['event_type'], 'acled:battles')
            self.assertNotIn('notes', json.dumps(records))
            self.assertIn('acled:actor:fictional_rebels', events[0]['participants'])
            fatalities = {r['subject']: r['value'] for r in records if r.get('metric') == 'acled_fatalities'}
            self.assertEqual(fatalities, {'iso3166num:999': 3, 'iso3:FIC': 0})


if __name__ == '__main__':
    unittest.main()
