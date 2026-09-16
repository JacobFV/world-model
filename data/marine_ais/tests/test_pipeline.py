"""Offline tests: a tiny MarineCadastre-shaped GeoParquet through the normalized stage."""
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]

try:
    import pyarrow
    import pyarrow.parquet as parquet
except ImportError:  # pragma: no cover - optional dependency
    pyarrow = None


def linestring(points):
    return struct.pack('<BII', 1, 2, len(points)) + b''.join(struct.pack('<2d', *p) for p in points)


@unittest.skipIf(pyarrow is None, 'pyarrow is not installed')
class MarineAisPipelineTests(unittest.TestCase):
    def test_tracks_vessels_and_stationary_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            table = pyarrow.table({
                'mmsi': pyarrow.array([255915811, 255915811, 303366000], pyarrow.int32()),
                'vessel_name': ['POLSTEAM INSKO', 'POLSTEAM INSKO', 'ISLAND EXPRESS II'],
                'imo': ['IMO9984833', 'IMO9984833', None],
                'call_sign': ['CQ2269', 'CQ2269', 'WDK6085'],
                'vessel_type': pyarrow.array([70, 70, 60], pyarrow.int32()),
                'vessel_type_name': ['Cargo', 'Cargo', 'Passenger'],
                'status': pyarrow.array([0, 5, None], pyarrow.int32()),
                'length': pyarrow.array([200.0, 200.0, 11.0], pyarrow.float32()),
                'width': pyarrow.array([24, 24, 6], pyarrow.int32()),
                'draft': pyarrow.array([9.3, 9.5, None], pyarrow.float32()),
                'cargo': pyarrow.array([70, 70, None], pyarrow.int32()),
                'transceiver': ['A', 'A', 'B'],
                'duration_minutes': pyarrow.array([600, 720, 90], pyarrow.int32()),
                'start_time': pyarrow.array([datetime(2025, 12, 1, 0, 0), datetime(2025, 12, 1, 12, 0), datetime(2025, 12, 2, 8, 0)], pyarrow.timestamp('ns')),
                'end_time': pyarrow.array([datetime(2025, 12, 1, 10, 0), datetime(2025, 12, 2, 0, 0), datetime(2025, 12, 2, 9, 30)], pyarrow.timestamp('ns')),
                'geometry': pyarrow.array([linestring([(-118.0, 33.0), (-118.0, 33.1)]), linestring([(-118.2, 33.7), (-118.2001, 33.7001)]),
                                           linestring([(-122.40, 37.80), (-122.41, 37.80)])], pyarrow.binary()),
            })
            path = root / 'ais-track-2025-12.parquet'
            parquet.write_table(table, path)
            february = root / 'ais-track-2026-02.parquet'
            parquet.write_table(table.slice(0, 1), february)
            store = Store(root / 'data')
            raw = store.import_shards('marine_ais', [{'path': path, 'name': path.name}, {'path': february, 'name': february.name}],
                                      {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('marine_ais', raw_refs={'marine_ais': [raw]})
            records = list(store.records(ref))
            by_id = {r['id']: r for r in records}
            self.assertEqual(len(by_id), len(records))
            first = by_id['marinecadastre:track:202512:0']
            self.assertEqual((first['event_type'], first['occurred_at'], first['participants']),
                             ('vessel_track_segment', '2025-12-01T00:00:00Z', ['mmsi:255915811']))
            self.assertAlmostEqual(first['attributes']['distance_km'], 11.12, delta=0.05)
            self.assertEqual(first['attributes']['bbox'], [-118.0, 33.0, -118.0, 33.1])
            self.assertNotIn('marinecadastre:track:202512:0:stationary', by_id)
            moored = by_id['marinecadastre:track:202512:1:stationary']
            self.assertEqual(moored['attributes']['basis'], 'ais_status_moored')
            vessel = by_id['mmsi:255915811']
            self.assertEqual((vessel['entity_type'], vessel['label'], vessel['attributes']['vessel_type_name']), ('vessel', 'POLSTEAM INSKO', 'Cargo'))
            self.assertEqual(by_id['mmsi:255915811:identified_by:imo']['value'], 'imo:9984833')
            self.assertEqual(by_id['mmsi:255915811:vessel_track_segments:202512']['value'], 2)
            self.assertEqual(by_id['mmsi:255915811:vessel_tracked_hours:202512']['value'], 22.0)
            self.assertAlmostEqual(by_id['mmsi:255915811:vessel_max_draft:202512']['value'], 9.5, places=2)
            hours = by_id['mmsi:303366000:vessel_tracked_hours:202512']
            self.assertEqual((hours['valid_from'], hours['valid_to']), ('2025-12-01', '2026-01-01'))
            self.assertNotIn('mmsi:303366000:identified_by:imo', by_id)
            self.assertTrue(all(r['evidence'][0]['locator'].split('/')[0] in ('shard:0', 'shard:1') for r in records))
            # Two monthly shards: one vessel entity, separate vessel-month activity observations.
            self.assertEqual(sum(1 for r in records if r.get('entity_id') == 'mmsi:255915811'), 1)
            self.assertEqual(by_id['mmsi:255915811']['attributes']['source_periods'], ['202512', '202602'])
            self.assertEqual(by_id['mmsi:255915811:vessel_track_segments:202602']['value'], 1)
            feb = by_id['mmsi:255915811:vessel_tracked_hours:202602']
            self.assertEqual((feb['value'], feb['valid_from'], feb['valid_to']), (10.0, '2026-02-01', '2026-03-01'))
            self.assertIn('marinecadastre:track:202602:0', by_id)
            self.assertEqual(by_id['mmsi:255915811:vessel_length']['valid_from'], '2025-12-01')


if __name__ == '__main__':
    unittest.main()
