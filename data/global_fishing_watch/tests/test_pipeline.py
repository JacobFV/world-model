"""Offline test: fixture GFW v3 event pages stored as JSONL shards."""
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
EVENTS = [
    {'id': 'pv1', 'type': 'port_visit', 'start': '2025-01-03T04:00:00.000Z', 'end': '2025-01-04T01:00:00.000Z',
     'position': {'lat': 33.74, 'lon': -118.26}, 'vessel': {'id': 'v-abc', 'ssvid': '255915811', 'name': 'POLSTEAM INSKO', 'flag': 'PRT'},
     'port_visit': {'visitId': 'x', 'confidence': '4', 'durationHrs': 21.0,
                    'startAnchorage': {'anchorageId': 'a-1', 'name': 'LOS ANGELES', 'flag': 'USA', 'lat': 33.74, 'lon': -118.26, 'atDock': True}}},
    {'id': 'en1', 'type': 'encounter', 'start': '2025-02-01T00:00:00.000Z', 'end': '2025-02-01T03:00:00.000Z',
     'position': {'lat': 1.0, 'lon': 2.0}, 'vessel': {'id': 'v-1', 'ssvid': '412000001', 'flag': 'CHN'},
     'encounter': {'vessel': {'id': 'v-2', 'ssvid': 'X12', 'flag': 'PAN'}, 'medianDistanceKilometers': 0.03, 'medianSpeedKnots': 1.2}},
]


class GlobalFishingWatchPipelineTests(unittest.TestCase):
    def test_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'page.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in EVENTS))
            store = Store(root / 'data')
            raw = store.import_shards('global_fishing_watch', [{'path': root / 'page.jsonl', 'name': 'events-0'}],
                                      {'publisher': 'fixture', 'acquisition': {'reader': {'format': 'jsonl'}}}, complete=True)
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('global_fishing_watch', raw_refs={'global_fishing_watch': [raw]})
            records = {r['id']: r for r in store.records(ref)}
            visit = records['gfw:event:pv1']
            self.assertEqual((visit['event_type'], visit['participants']), ('vessel_port_visit', ['mmsi:255915811', 'gfw:anchorage:a-1']))
            self.assertEqual((visit['attributes']['port_name'], visit['attributes']['duration_hours']), ('LOS ANGELES', 21.0))
            encounter = records['gfw:event:en1']
            self.assertEqual(encounter['participants'], ['mmsi:412000001', 'gfw:vessel:v-2'])
            self.assertEqual(visit['evidence'][0]['locator'], 'shard:0/line:1')


if __name__ == '__main__':
    unittest.main()
