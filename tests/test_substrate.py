import json
import shutil
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.store import Store
from worldmodel.pipeline import Runner
from worldmodel.model import validate_record
from worldmodel.graph import Graph

PROJECT = Path(__file__).resolve().parents[1]


class SubstrateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = Catalog(PROJECT / 'data')
        self.store = Store(self.root / 'data')
        self.runner = Runner(self.catalog, self.store, PROJECT)

    def tearDown(self):
        self.temp.cleanup()

    def import_demo(self):
        return self.store.import_file('demo_countries', PROJECT / 'tests/fixtures/countries.csv',
                                      {'publisher': 'test fixture', 'release': 'fictional-v1'})

    def build(self):
        self.import_demo()
        return self.runner.run('rando_joes_happiness_index')

    def test_derived_output_has_exact_inputs_code_and_row_lineage(self):
        ref = self.build()
        manifest = self.store.manifest(ref)
        self.assertEqual(manifest['code']['entrypoint'], 'pipeline.py:run')
        self.assertEqual(ref['stage'], 'graph')
        self.assertIn('pipeline.py', manifest['code']['dataset_code']['files'])
        self.assertIn('worldmodel/transforms.py', manifest['code']['files'])
        self.assertEqual(len(manifest['inputs']), 1)
        rows = list(self.store.records(ref))
        self.assertEqual([r['value'] for r in rows], [65.0, 50.0])
        self.assertEqual(rows[0]['unit'], 'index_points')
        self.assertEqual(rows[0]['evidence'][0]['input'], manifest['inputs'][0])
        lineage = self.store.lineage(ref)
        self.assertEqual(len(lineage['versions']), 3)
        self.assertEqual(len(lineage['artifacts']), 1)
        self.assertEqual(lineage['artifacts'][0]['source']['release'], 'fictional-v1')

    def test_identical_run_reuses_version_but_changed_parameters_do_not(self):
        one = self.build()
        two = self.runner.run('rando_joes_happiness_index')
        self.assertEqual(one, two)
        three = self.runner.run('rando_joes_happiness_index', parameters={'income_weight': 0.25})
        self.assertNotEqual(one, three)
        self.assertTrue(self.store.verify(one))

    def test_tampered_final_output_is_rejected_before_downstream_use(self):
        ref = self.build()
        self.store.version_dir(ref).joinpath('records.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.verify(ref)

    def test_tampered_manifest_is_rejected(self):
        ref = self.build()
        path = self.store.version_dir(ref) / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['parameters']['income_weight'] = 999
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.store.manifest(ref)

    def test_failed_transform_publishes_nothing_and_records_failure(self):
        bad = self.root / 'bad.csv'
        bad.write_text('country,income,life_satisfaction\nZZ,not-a-number,8\n')
        self.store.import_file('demo_countries', bad, {'publisher': 'fixture'})
        with self.assertRaises(ValueError):
            self.runner.run('demo_countries')
        self.assertFalse(self.store.latest_path('demo_countries').exists())
        self.assertFalse(self.store.latest_path('demo_countries', 'normalized').exists())
        self.assertTrue(self.store.latest_path('demo_countries', 'parsed').exists())
        attempts = [json.loads(path.read_text()) for path in self.store.runs_dir('demo_countries').glob('*.json')]
        self.assertEqual({attempt['status'] for attempt in attempts}, {'succeeded', 'failed'})
        self.assertEqual(next(a for a in attempts if a['status'] == 'failed')['stage'], 'normalized')

    def test_raw_tampering_is_detected(self):
        ref = self.import_demo()
        self.store.artifact_dir(ref).joinpath('payload').write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.runner.run('demo_countries')

    def test_catalog_rejects_cycles_and_unconfigured_sources(self):
        defs = self.root / 'catalog'
        for name, dependency in [('a', 'b'), ('b', 'a')]:
            directory = defs / name
            directory.mkdir(parents=True)
            (directory / 'dataset.json').write_text(json.dumps({
                'id': name, 'kind': 'derived', 'schema_version': 1,
                'dependencies': [dependency], 'entrypoint': 'worldmodel.transforms:happiness'}))
        with self.assertRaisesRegex(ValueError, 'cycle'):
            Catalog(defs).plan('a')
        directory = defs / 'unconfigured'
        directory.mkdir()
        (directory / 'dataset.json').write_text(json.dumps({
            'id': 'unconfigured', 'schema_version': 1, 'kind': 'source', 'entrypoint': None}))
        with self.assertRaisesRegex(ValueError, 'configure'):
            Runner(Catalog(defs), self.store, PROJECT).run('unconfigured')

    def test_raw_acquisition_timestamp_is_integrity_protected(self):
        ref = self.import_demo()
        path = self.store.artifact_dir(ref) / 'receipt.json'
        receipt = json.loads(path.read_text())
        receipt['retrieved_at'] = '1900-01-01T00:00:00Z'
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.store.artifact(ref)

    def test_union_preserves_entity_evidence_from_multiple_datasets(self):
        defs = self.root / 'catalog'
        for name in ['one', 'two', 'combined']:
            path = defs / name
            path.mkdir(parents=True)
            definition = {'id': name, 'schema_version': 1,
                'kind': 'derived' if name == 'combined' else 'source',
                'entrypoint': 'worldmodel.transforms:union' if name == 'combined'
                              else 'worldmodel.transforms:evidence_jsonl',
                'dependencies': ['one', 'two'] if name == 'combined' else [],
                'parameters': {'format': 'jsonl'}}
            (path / 'dataset.json').write_text(json.dumps(definition))
        for name in ['one', 'two']:
            self.store.import_file(name, PROJECT / 'tests/fixtures/graph.jsonl', {'publisher': name})
        ref = Runner(Catalog(defs), self.store, PROJECT).run('combined')
        graph = Graph(self.root / 'graph.sqlite')
        graph.build(self.store, [ref])
        result = graph.neighbors('org:acme')
        self.assertEqual(len(result['entities']), 2)
        self.assertEqual(len(result['assertions']), 4)
        self.assertEqual({r['entity_id'] for r in result['entities']}, {'org:acme'})

    def test_relocation_preserves_lineage_and_versions(self):
        ref = self.build()
        destination = self.root / 'moved'
        shutil.copytree(self.store.root, destination)
        moved = Store(destination)
        self.assertTrue(moved.verify(ref))
        self.assertEqual(len(moved.lineage(ref)['versions']), 3)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.import_file('../escape', PROJECT / 'tests/fixtures/countries.csv', {})

    def test_invalid_evidence_times_and_nan_are_rejected(self):
        record = {'kind': 'observation', 'id': 'obs:1', 'metric': 'population',
                  'value': 217, 'unit': 'people', 'dimensions': {'geo': 'geo:US'},
                  'observed_at': '2025-01-01T00:00:00Z', 'valid_from': '2024-01-01',
                  'valid_to': '2025-01-01', 'evidence': []}
        with self.assertRaisesRegex(ValueError, 'evidence'):
            validate_record(record)
        record['evidence'] = [{'input': {'dataset': 'x', 'version': 'a' * 64}, 'record_id': 'x:1'}]
        validate_record(record)
        record['valid_to'] = '2023-01-01'
        with self.assertRaisesRegex(ValueError, 'time'):
            validate_record(record)
        record['valid_to'] = '2025-01-01'
        record['value'] = float('nan')
        with self.assertRaises(ValueError):
            validate_record(record)

    def test_graph_preserves_conflicting_claims_and_filters_both_times(self):
        raw = self.store.import_file('demo_graph', PROJECT / 'tests/fixtures/graph.jsonl',
                                     {'publisher': 'fictional graph'})
        ref = self.runner.run('demo_graph')
        graph = Graph(self.root / 'graph.sqlite')
        graph.build(self.store, [ref])
        claims = graph.neighbors('org:acme')['assertions']
        self.assertEqual(len(claims), 2)
        self.assertEqual({r['value'] for r in claims}, {0.6, 0.8})
        # The fixture publishes no availability date, so an as-of query excludes its claims by
        # default and says so; the named option restores the old ingestion-time reading.
        asof = graph.neighbors('org:acme', known_at='2024-06-01')
        self.assertEqual(asof['assertions'], [])
        self.assertEqual(asof['publication']['policy'], 'exclude_unknown_publication')
        self.assertTrue(asof['publication']['excluded_unknown_publication'])
        ingested = graph.neighbors('org:acme', known_at='2024-06-01', include_unknown_publication=True)
        self.assertEqual(len(ingested['assertions']), 1)
        self.assertIn('ingestion time', ingested['publication']['disclosure'])
        self.assertEqual(len(graph.neighbors('org:acme', valid_at='2026-01-01')['assertions']), 0)
        self.assertEqual(graph.neighbors('geo:US')['assertions'], [])
        self.assertEqual(len(graph.observations('establishment_count')['records']), 1)
        self.assertTrue(self.store.verify(ref))
        self.assertTrue(raw['artifact'])


if __name__ == '__main__':
    unittest.main()
