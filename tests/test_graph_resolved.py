import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from worldmodel.graph import Graph

REF = {'dataset': 'fixture', 'version': 'b' * 64}
EVIDENCE = [{'input': {'dataset': 'fixture', 'version': 'a' * 64}, 'record_id': 'fixture:1'}]


def entity(key, label, typ='organization'):
    return {'kind': 'entity', 'id': 'e:' + key, 'entity_id': key, 'entity_type': typ, 'label': label,
            'observed_at': '2020-01-01', 'evidence': EVIDENCE}


def edge(key, subject, predicate, obj, weight=1.0, **extra):
    return {'kind': 'assertion', 'id': 'a:' + key, 'subject': subject, 'predicate': predicate, 'object': obj,
            'observed_at': extra.pop('observed_at', '2020-01-01'), 'evidence': EVIDENCE, 'attributes': {'weight': weight}, **extra}


class ResolvedGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = Graph(Path(self.tmp.name) / 'graph.sqlite')
        records = [entity('org:a', 'A'), entity('org:b', 'B'), entity('org:c', 'C'), entity('org:a2', 'A duplicate'),
                   entity('org:d', 'D'),
                   edge('1', 'org:a', 'owns', 'org:b', 0.6),
                   edge('2', 'org:b', 'owns', 'org:c', 0.9),
                   edge('3', 'org:a2', 'supplied_by', 'org:d', 5.0, valid_from='2021-01-01'),
                   edge('4', 'org:d', 'owns', 'org:c', 0.1, observed_at='2023-01-01'),
                   edge('5', 'org:c', 'supplied_by', 'org:b', 2.0)]
        self.built = self.graph.build_from_records([(REF, records)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_counts_and_original_queries(self):
        self.assertEqual((self.built['records'], self.built['edges']), (10, 5))
        self.assertEqual(len(self.graph.neighbors('org:b')['assertions']), 3)

    def test_neighborhood_filters(self):
        result = self.graph.neighborhood('org:a', hops=2)
        self.assertEqual({n['id'] for n in result['nodes']}, {'org:a', 'org:b', 'org:c'})
        self.assertEqual(len(self.graph.neighborhood('org:a', hops=3, predicates=['owns'], min_weight=0.5)['edges']), 2)
        self.assertEqual(self.graph.neighborhood('org:a', hops=1, direction='in')['edges'], [])
        self.assertTrue(self.graph.neighborhood('org:c', hops=2, limit=1)['truncated'])
        old = self.graph.neighborhood('org:c', hops=1, known_at='2022-01-01')
        self.assertNotIn('org:d', {n['id'] for n in old['nodes']})
        records = self.graph.edge_records(result['edges'])
        self.assertEqual(records[0]['_provenance']['input'], REF)

    def test_paths_centrality_flow(self):
        paths = self.graph.paths('org:a', 'org:c', max_hops=3)
        self.assertEqual(paths['length'], 2)
        # Two distinct supporting edge sequences: b-owns->c and c-supplied_by->b traversed in reverse.
        self.assertEqual(sorted((p[1]['predicate'], p[1]['direction']) for p in paths['paths']),
                         [('owns', 'forward'), ('supplied_by', 'reverse')])
        owned = self.graph.paths('org:a', 'org:c', max_hops=3, predicates=['owns'], direction='out')
        self.assertEqual([[step['to'] for step in path] for path in owned['paths']], [['org:b', 'org:c']])
        self.assertEqual(self.graph.paths('org:a', 'org:d', max_hops=3, direction='out')['paths'], [])
        self.assertEqual(self.graph.paths('org:a', 'org:d', max_hops=3)['length'], 3)
        degree = self.graph.degree_centrality(limit=2)
        self.assertEqual(degree[0]['node'], 'org:b')
        weighted = self.graph.degree_centrality(weighted=True, predicates=['supplied_by'], direction='in', limit=5)
        self.assertEqual(weighted[0], {'node': 'org:d', 'score': 5.0, 'degree': 1})
        ranks = self.graph.pagerank(predicates=['owns'])
        self.assertTrue(ranks['converged'])
        self.assertEqual(ranks['ranks'][0]['node'], 'org:c')
        with self.assertRaises(ValueError):
            self.graph.pagerank(max_edges=1)
        flow = self.graph.flow_aggregate('supplied_by', group_by='object')
        self.assertEqual(flow['rows'][0], {'object': 'org:d', 'total': 5.0, 'edges': 1})

    def test_resolution_attach_and_resolved_queries(self):
        with self.assertRaises(ValueError):
            self.graph.attach_resolution([{'canonical_id': 'org:a', 'members': ['org:a', 'org:a2']}], view={})
        attached = self.graph.attach_resolution([{'canonical_id': 'org:a', 'members': ['org:a', 'org:a2']}],
                                                view={'view_digest': 'd' * 64, 'policy': {'threshold': 0.95}})
        self.assertEqual(attached['resolved_entities'], 2)
        resolved = self.graph.resolved_entity('org:a2')
        self.assertEqual((resolved['canonical_id'], resolved['members']), ('org:a', ['org:a', 'org:a2']))
        self.assertEqual(len(resolved['entities']), 2)
        plain = self.graph.neighborhood('org:a', hops=1)
        merged = self.graph.neighborhood('org:a', hops=1, resolved=True)
        self.assertNotIn('org:d', {n['id'] for n in plain['nodes']})
        self.assertIn('org:d', {n['id'] for n in merged['nodes']})
        self.assertEqual(self.graph.paths('org:a2', 'org:b', resolved=True)['length'], 1)
        self.assertEqual(self.graph.flow_aggregate('supplied_by', resolved=True)['rows'][0]['subject'], 'org:a')
        self.assertEqual(self.graph.resolution()['clusters'], 1)

    def test_schema_two_index_keeps_original_queries(self):
        path = Path(self.tmp.name) / 'legacy.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''CREATE TABLE records (dataset TEXT, stage TEXT, version TEXT, input_ref TEXT, id TEXT, entity_id TEXT,
                kind TEXT, metric TEXT, subject TEXT, object TEXT, observed_at TEXT, valid_from TEXT, valid_to TEXT, body TEXT);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO metadata VALUES ('schema_version', '2');''')
        legacy = Graph(path)
        self.assertEqual(legacy.observations('count'), [])
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            legacy.neighborhood('org:a')


if __name__ == '__main__':
    unittest.main()
