"""Offline acceptance tests: tiny encoded PBF through the parsed and normalized stages."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]


def _helpers():
    spec = importlib.util.spec_from_file_location('osm_topology_helpers_test', HERE / 'helpers.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NODES = [
    (1, 37.0000, -122.0000, {}),
    (2, 37.0010, -122.0000, {}),
    (3, 37.0020, -122.0000, {}),
    (4, 37.0010, -122.0010, {}),
    (5, 37.0010, -121.9990, {'amenity': 'fuel', 'name': 'Fixture Fuel'}),
    (6, 37.0100, -122.0100, {}),
    (7, 37.0100, -122.0000, {}),
    (8, 37.0000, -122.0100, {}),
    (9, 37.0200, -122.0200, {'railway': 'station', 'name': 'Fixture Station'}),
]
WAYS = [
    (100, {'highway': 'primary', 'name': 'Main St', 'maxspeed': '35 mph', 'lanes': '2'}, [1, 2, 3]),
    (101, {'highway': 'residential', 'oneway': '-1'}, [4, 2, 5]),
    (102, {'highway': 'footway'}, [1, 4]),
    (103, {'railway': 'rail', 'usage': 'main'}, [3, 9]),
    (104, {'aeroway': 'aerodrome', 'name': 'Fixture Field', 'iata': 'FXF'}, [6, 7, 8, 6]),
]
RELATIONS = [(200, {'type': 'multipolygon', 'landuse': 'port', 'name': 'Fixture Port'}, [('way', 104, 'outer')])]


class OsmTopologyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.helpers = _helpers()

    def tearDown(self):
        self.tmp.cleanup()

    def pbf(self):
        path = self.root / 'fixture.osm.pbf'
        path.write_bytes(self.helpers.encode_pbf(NODES, WAYS, RELATIONS, bbox=(-122.1, 36.9, -121.9, 37.1)))
        return path

    def test_decoder_roundtrip(self):
        elements = list(self.helpers.iter_elements(self.pbf()))
        self.assertEqual(elements[0][0], 'header')
        nodes = {e[1]: e[2] for e in elements if e[0] == 'node'}
        self.assertAlmostEqual(nodes[5][0], 37.001, places=7)
        self.assertEqual(nodes[5][2], {'amenity': 'fuel', 'name': 'Fixture Fuel'})
        ways = {e[1]: e[2] for e in elements if e[0] == 'way'}
        self.assertEqual(ways[101], ({'highway': 'residential', 'oneway': '-1'}, [4, 2, 5]))
        relation = next(e for e in elements if e[0] == 'relation')
        self.assertEqual(relation[2][1], [('way', 104, 'outer')])

    def test_decoder_rejects_truncation_and_unknown_compression(self):
        data = self.pbf().read_bytes()
        broken = self.root / 'broken.osm.pbf'
        broken.write_bytes(data[:-5])
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            list(self.helpers.iter_elements(broken))
        with self.assertRaisesRegex(ValueError, 'zstd'):
            self.helpers.decode_blob(self.helpers._field(7, 2, b'abc'))

    def run_full(self, workers):
        shard = {'path': self.pbf(), 'name': 'fixture.osm.pbf', 'url': 'https://example.invalid/fixture.osm.pbf'}
        raw = self.store.import_shards('osm_topology', [shard], {'publisher': 'fixture', 'acquisition': {'reader': None}},
                                       complete=True, method='link')
        runner = Runner(Catalog(PROJECT / 'data'), self.store, PROJECT)
        ref = runner.run('osm_topology', parameters={'workers': workers}, raw_refs={'osm_topology': [raw]})
        return raw, ref

    def test_full_pbf_graph_and_evidence(self):
        for workers in (1, 2):
            with self.subTest(workers=workers):
                raw, ref = self.run_full(workers)
                records = list(self.store.records(ref))
                edges = [r for r in records if r['kind'] == 'assertion' and r['predicate'].endswith('connects_to')]
                entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
                roads = {(e['subject'], e['object'], e['attributes']['way']) for e in edges if e['predicate'] == 'road_connects_to'}
                # Way 100 splits at shared node 2; way 101 is reversed (oneway=-1); the footway is excluded.
                self.assertEqual(roads, {('osm:node:1', 'osm:node:2', 'osm:way:100'), ('osm:node:2', 'osm:node:3', 'osm:way:100'),
                                         ('osm:node:2', 'osm:node:4', 'osm:way:101'), ('osm:node:5', 'osm:node:2', 'osm:way:101')})
                main = next(e for e in edges if e['id'] == 'osm:way:100:segment:0:road')
                self.assertAlmostEqual(main['attributes']['length_m'], 111.19, delta=0.2)
                self.assertEqual(main['attributes']['maxspeed_kph'], 56.3)
                self.assertTrue(main['attributes']['bidirectional'])
                self.assertFalse(next(e for e in edges if e['attributes']['way'] == 'osm:way:101')['attributes']['bidirectional'])
                self.assertTrue(any(e['predicate'] == 'rail_connects_to' for e in edges))
                for edge in edges:
                    self.assertIn(edge['subject'], entities)
                    self.assertIn(edge['object'], entities)
                self.assertEqual(entities['osm:node:3']['attributes']['networks'], ['road', 'rail'])
                self.assertEqual(entities['osm:feature:node:5']['entity_type'], 'facility')
                airport = entities['osm:feature:way:104']
                self.assertEqual((airport['entity_type'], airport['label']), ('airport', 'Fixture Field'))
                self.assertAlmostEqual(airport['attributes']['lat'], (37.01 + 37.01 + 37.0) / 3, places=6)
                self.assertEqual(entities['osm:feature:relation:200']['entity_type'], 'port')
                self.assertIn('osm:feature:node:9', entities)
                self.assertTrue(all(r['evidence'][0]['input'] == raw for r in records))
                self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:0/') for r in records))
                self.assertEqual(len(records), len({r['id'] for r in records}))

    def test_sample_payload_still_normalizes(self):
        path = self.root / 'sample.jsonl'
        path.write_text(json.dumps({'type': 'way', 'id': 1, 'nodes': [1, 2], 'tags': {'highway': 'residential'},
                                    'geometry': [{'lat': 37.0, 'lon': -122.0}, {'lat': 37.001, 'lon': -122.0}]}) + '\n')
        raw = self.store.import_file('osm_topology', path, source={'publisher': 'fixture'})
        ref = Runner(Catalog(PROJECT / 'data'), self.store, PROJECT).run('osm_topology', raw_refs={'osm_topology': [raw]})
        records = list(self.store.records(ref))
        self.assertEqual(sorted((r['subject'], r['object']) for r in records if r.get('predicate') == 'road_connects_to'),
                         [('osm:node:1', 'osm:node:2'), ('osm:node:2', 'osm:node:1')])


if __name__ == '__main__':
    sys.exit(unittest.main())
