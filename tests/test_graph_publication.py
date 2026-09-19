"""The publication date: how it is populated, what an as-of query does with it, and what
an index built before it says when asked a question it cannot answer.

See ``docs/point-in-time-graph.md``.
"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from worldmodel.graph import (PUBLICATION_RULES, PUBLICATION_SCHEMA, SCHEMA_VERSION, AsOf, Graph, publication,
                              rule_publication, time_key)

EVIDENCE = [{'input': {'dataset': 'fixture', 'version': 'a' * 64}, 'record_id': 'fixture:1'}]
INGESTED = '2026-09-15T00:00:00+00:00'


def ref(dataset, version='b'):
    return {'dataset': dataset, 'stage': 'normalized', 'version': version * 64}


def observation(key, subject, value, *, dataset='panel', **extra):
    return {'kind': 'observation', 'id': dataset + ':' + key, 'subject': subject, 'metric': 'value', 'value': value,
            'observed_at': INGESTED, 'evidence': EVIDENCE, **extra}


def assertion(key, subject, obj, *, dataset='panel', **extra):
    return {'kind': 'assertion', 'id': dataset + ':' + key, 'subject': subject, 'predicate': 'owns', 'object': obj,
            'observed_at': INGESTED, 'evidence': EVIDENCE, **extra}


class PopulationTests(unittest.TestCase):
    """Priority order: dimensions.available_at, attributes.realtime_start, declared rule, else null."""

    def test_priority_order_and_null_for_unknown(self):
        available = observation('1', 'geo:US', 1.0, dimensions={'available_at': '1991-09-30'},
                                attributes={'realtime_start': '2001-01-01'})
        self.assertEqual(publication(available), (time_key('1991-09-30'), 'dimensions.available_at'))

        vintaged = observation('2', 'geo:US', 1.0, attributes={'realtime_start': '2001-01-01'})
        self.assertEqual(publication(vintaged), (time_key('2001-01-01'), 'attributes.realtime_start'))

        rule = PUBLICATION_RULES['noaa_climdiv']
        dated = observation('3', 'geo:US', 1.0, valid_from='1990-01-01', valid_to='1991-01-01')
        self.assertEqual(publication(dated, rule), (time_key('1991-02-01'), 'rule'))
        # The rule alone is not enough: a record with no reference period cannot be dated by a lag.
        self.assertEqual(publication(observation('4', 'geo:US', 1.0), rule), (None, None))
        # And with no rule and nothing published, unknown stays unknown - never the ingest time.
        self.assertEqual(publication(observation('5', 'geo:US', 1.0)), (None, None))

    def test_a_retrieval_vintage_is_refused_not_taken_as_a_publication_date(self):
        """bls_labor fills realtime_start with the retrieval date and says so in attributes.vintage."""
        retrieved = observation('1', 'geo:US', 1.0, attributes={'vintage': 'current_at_retrieval',
                                                                'realtime_start': '2026-09-15'})
        self.assertEqual(publication(retrieved), (None, 'refused:current_at_retrieval'))
        # A declared rule still applies to such a record; only the fake vintage is refused.
        dated = observation('2', 'geo:US', 1.0, valid_from='1990-01-01', valid_to='1991-01-01',
                            attributes={'vintage': 'current_at_retrieval', 'realtime_start': '2026-09-15'})
        self.assertEqual(publication(dated, PUBLICATION_RULES['bls_labor']), (time_key('1991-10-01'), 'rule'))
        # And a real ALFRED vintage, which marks the vintage with the date itself, is not refused.
        archived = observation('3', 'geo:US', 1.0, attributes={'realtime_start': '2001-01-01'},
                               dimensions={'vintage': '2001-01-01'})
        self.assertEqual(publication(archived), (time_key('2001-01-01'), 'attributes.realtime_start'))

    def test_rule_anchors_on_the_end_of_the_reference_period(self):
        rule = {'lag_months': 9}
        self.assertEqual(rule_publication({'valid_from': '1990-01-01', 'valid_to': '1991-01-01'}, rule),
                         time_key('1991-10-01'))
        # valid_from alone is the only anchor a point-in-time record offers.
        self.assertEqual(rule_publication({'valid_from': '1990-01-01'}, rule), time_key('1990-10-01'))
        self.assertIsNone(rule_publication({}, rule))
        self.assertIsNone(rule_publication({'valid_from': '1990-01-01'}, {'lag_months': None}))
        # Month arithmetic clamps to the shorter month and crosses the year end.
        self.assertEqual(rule_publication({'valid_from': '2019-01-31'}, {'lag_months': 1}), time_key('2019-02-28'))
        self.assertEqual(rule_publication({'valid_from': '2020-01-31'}, {'lag_months': 1}), time_key('2020-02-29'))

    def test_declared_rules_still_match_the_adapter_that_declared_them(self):
        """PUBLICATION_RULES restates lags county_panel declared; it must not drift from them."""
        from worldmodel.embedding.county_panel import SOURCES
        for dataset, rule in PUBLICATION_RULES.items():
            key = rule['declared_by'].split('"')[1]
            self.assertEqual(SOURCES[key]['dataset'], dataset, dataset)
            self.assertEqual(SOURCES[key]['lag_months'], rule['lag_months'], dataset)

    def test_build_populates_and_censuses_every_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Graph(Path(tmp) / 'index.sqlite')
            built = graph.build_from_records([
                (ref('panel'), [observation('1', 'geo:US', 1.0, dimensions={'available_at': '1991-09-30'}),
                                observation('2', 'geo:US', 2.0, attributes={'realtime_start': '2001-01-01'}),
                                observation('3', 'geo:US', 3.0)]),
                (ref('noaa_climdiv', 'c'), [observation('4', 'geo:US', 4.0, dataset='noaa_climdiv',
                                                        valid_from='1990-01-01', valid_to='1991-01-01')]),
            ], validate=False)
            coverage = built['publication_coverage']
            self.assertEqual(coverage['totals']['records'], 4)
            self.assertEqual(coverage['totals']['records_published'], 3)
            self.assertEqual(coverage['totals']['sources'],
                             {'dimensions.available_at': 1, 'attributes.realtime_start': 1, 'rule': 1})
            self.assertEqual(coverage['totals']['records_share'], 0.75)
            by_dataset = {row['dataset']: row for row in coverage['datasets']}
            self.assertEqual(by_dataset['panel']['records_published'], 2)
            self.assertEqual(by_dataset['noaa_climdiv']['records_share'], 1.0)
            # The same census is readable from the index itself, without a scan.
            stored = graph.publication_coverage()
            self.assertEqual(stored['graph_schema'], SCHEMA_VERSION)
            self.assertEqual(stored['coverage'], coverage)
            self.assertIn('noaa_climdiv', stored['rules_applied'])

    def test_publication_rules_can_be_disabled_at_build_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = Graph(Path(tmp) / 'index.sqlite').build_from_records([
                (ref('noaa_climdiv'), [observation('1', 'geo:US', 1.0, valid_from='1990-01-01',
                                                   valid_to='1991-01-01')])], validate=False, publication_rules={})
            self.assertEqual(built['publication_coverage']['totals']['records_published'], 0)


class AsOfQueryTests(unittest.TestCase):
    """``known_at`` filters on the publication date; unknown is excluded and disclosed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = Graph(Path(self.tmp.name) / 'index.sqlite')
        self.graph.build_from_records([
            (ref('dated'), [
                observation('1', 'geo:US', 1.0, dimensions={'available_at': '2020-01-01'}),
                observation('2', 'geo:US', 2.0, dimensions={'available_at': '2024-01-01'}),
                assertion('3', 'org:a', 'org:b', dimensions={'available_at': '2020-01-01'}),
            ]),
            (ref('undated', 'c'), [
                observation('4', 'geo:US', 4.0, dataset='undated'),
                assertion('5', 'org:a', 'org:c', dataset='undated'),
                assertion('6', 'org:a', 'org:d', dataset='undated'),
            ]),
        ], validate=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_without_a_publication_date_are_excluded_and_counted(self):
        result = self.graph.observations('value', known_at='2022-01-01')
        self.assertEqual([r['value'] for r in result['records']], [1.0])
        note = result['publication']
        self.assertEqual(note['policy'], 'exclude_unknown_publication')
        self.assertEqual(note['filtered_on'], 'published_at')
        self.assertEqual(note['excluded_unknown_publication'], 1)
        self.assertEqual(note['excluded_by_dataset'], {'undated': 1})
        self.assertEqual(note['counted_over'], 'the candidate rows of this query, before its limit')
        self.assertIn('no publication date', note['disclosure'])
        # Nothing published after the horizon leaks in, and nothing is dated by its ingest time
        # even though every record here was ingested in 2026.
        self.assertNotIn(2.0, [r['value'] for r in result['records']])

    def test_named_option_restores_ingestion_time_and_says_so(self):
        result = self.graph.observations('value', known_at='2022-01-01', include_unknown_publication=True)
        self.assertEqual(sorted(r['value'] for r in result['records']), [1.0])
        note = result['publication']
        self.assertEqual(note['policy'], 'include_unknown_publication_as_ingested')
        self.assertIn('ingestion time', note['disclosure'])
        self.assertEqual(note['excluded_unknown_publication'], 0)
        # Nothing undated was ingested by 2022 either, so this horizon let nothing through on it.
        self.assertEqual(note['included_unknown_publication'], 0)
        kept = self.graph.observations('value', known_at='2026-10-01', include_unknown_publication=True)
        self.assertEqual(kept['publication']['included_unknown_publication'], 1)
        self.assertEqual(kept['publication']['unknown_publication_by_dataset'], {'undated': 1})
        # The undated row was ingested in 2026, so a 2026 horizon returns it under this option
        # and only under it.
        late = self.graph.observations('value', known_at='2026-10-01', include_unknown_publication=True)
        self.assertEqual(sorted(r['value'] for r in late['records']), [1.0, 2.0, 4.0])
        self.assertEqual(sorted(r['value'] for r in
                                self.graph.observations('value', known_at='2026-10-01')['records']), [1.0, 2.0])

    def test_edge_queries_exclude_and_attribute_by_dataset(self):
        hood = self.graph.neighborhood('org:a', hops=1, known_at='2022-01-01')
        self.assertEqual([e['object'] for e in hood['edges']], ['org:b'])
        self.assertEqual(hood['publication']['excluded_by_dataset'], {'undated': 2})
        # A traversal's count is a floor over the nodes it reached, and the result says which it is.
        self.assertIn('floor', hood['publication']['counted_over'])
        flow = self.graph.flow_aggregate('owns', known_at='2022-01-01')
        self.assertEqual(flow['rows'], [{'subject': 'org:a', 'total': 1.0, 'edges': 1}])
        self.assertEqual(flow['publication']['excluded_by_dataset'], {'undated': 2})
        self.assertEqual(flow['publication']['counted_over'], 'every edge this query scans')
        degree = self.graph.degree_centrality(known_at='2022-01-01')
        self.assertEqual({row['node'] for row in degree['rows']}, {'org:a', 'org:b'})
        self.assertEqual(degree['publication']['excluded_unknown_publication'], 2)
        ranks = self.graph.pagerank(known_at='2022-01-01')
        self.assertEqual(ranks['publication']['excluded_by_dataset'], {'undated': 2})
        paths = self.graph.paths('org:a', 'org:c', known_at='2022-01-01')
        self.assertEqual(paths['paths'], [])
        self.assertEqual(paths['publication']['policy'], 'exclude_unknown_publication')
        # The undated edge was ingested in 2026, so the ingestion-time fallback reaches org:c
        # only at a 2026 horizon - and never under the default policy.
        self.assertIsNone(self.graph.paths('org:a', 'org:c', known_at='2022-01-01',
                                           include_unknown_publication=True)['length'])
        opened = self.graph.paths('org:a', 'org:c', known_at='2026-10-01', include_unknown_publication=True)
        self.assertEqual(opened['length'], 1)
        self.assertEqual(opened['publication']['included_unknown_publication'], 2)
        self.assertIsNone(self.graph.paths('org:a', 'org:c', known_at='2026-10-01')['length'])

    def test_a_query_with_no_horizon_is_unchanged_and_still_labelled(self):
        result = self.graph.observations('value')
        self.assertEqual(len(result['records']), 3)
        self.assertEqual(result['publication'],
                         {'known_at': None, 'policy': 'no as-of filter', 'graph_schema': SCHEMA_VERSION,
                          'publication_dates_available': True})

    def test_valid_at_is_untouched_by_the_publication_policy(self):
        as_of = AsOf(SCHEMA_VERSION, '2021-01-01', None)
        suffix, args = as_of.filters()
        self.assertNotIn('published_at', suffix)
        self.assertEqual(args, [time_key('2021-01-01')] * 2)


class OlderSchemaTests(unittest.TestCase):
    """A schema-2/3 index stays readable, and reports what it cannot answer rather than guessing."""

    def _legacy(self, path, version):
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''
                CREATE TABLE records (dataset TEXT, stage TEXT, version TEXT, input_ref TEXT, id TEXT, entity_id TEXT,
                    kind TEXT, metric TEXT, subject TEXT, object TEXT, observed_at TEXT, valid_from TEXT,
                    valid_to TEXT, body TEXT);
                CREATE TABLE edges (subject TEXT, predicate TEXT, object TEXT, weight REAL, valid_from TEXT,
                    valid_to TEXT, observed_at TEXT, record_rowid INTEGER);
                CREATE TABLE resolved (entity_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, cluster_size INTEGER);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);''')
            db.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', version))
            db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                'legacy', 'normalized', 'v', '{"dataset":"legacy"}', 'legacy:1', None, 'observation', 'value',
                'geo:US', None, INGESTED, None, None, '{"id":"legacy:1","value":7.0}'))
            db.execute('INSERT INTO edges VALUES (?,?,?,?,?,?,?,?)',
                       ('org:a', 'owns', 'org:b', 1.0, None, None, INGESTED, 1))
        return Graph(path)

    def test_queries_without_a_horizon_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ('2', '3'):
                graph = self._legacy(Path(tmp) / ('legacy%s.sqlite' % version), version)
                result = graph.observations('value')
                self.assertEqual([r['value'] for r in result['records']], [7.0])
                self.assertFalse(result['publication']['publication_dates_available'])
                self.assertEqual(graph.publication_coverage()['coverage'], None)
                self.assertIn('before graph schema ' + PUBLICATION_SCHEMA,
                              graph.publication_coverage()['reason'])

    def test_an_as_of_query_is_refused_rather_than_answered_from_ingest_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = self._legacy(Path(tmp) / 'legacy.sqlite', '3')
            for query in (lambda: graph.observations('value', known_at='2026-10-01'),
                          lambda: graph.neighborhood('org:a', known_at='2026-10-01'),
                          lambda: graph.degree_centrality(known_at='2026-10-01'),
                          lambda: graph.flow_aggregate('owns', known_at='2026-10-01')):
                with self.assertRaisesRegex(ValueError, 'no publication dates'):
                    query()

    def test_the_old_behaviour_remains_available_on_an_old_index_with_the_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = self._legacy(Path(tmp) / 'legacy.sqlite', '3')
            result = graph.observations('value', known_at='2026-10-01', include_unknown_publication=True)
            self.assertEqual([r['value'] for r in result['records']], [7.0])
            note = result['publication']
            self.assertEqual(note['filtered_on'], 'observed_at')
            self.assertFalse(note['publication_dates_available'])
            self.assertIn('not a point-in-time view', note['disclosure'])
            hood = graph.neighborhood('org:a', known_at='2026-10-01', include_unknown_publication=True)
            self.assertEqual(len(hood['edges']), 1)
            self.assertIn('not a point-in-time view', hood['publication']['disclosure'])


if __name__ == '__main__':
    unittest.main()
