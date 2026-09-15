import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store
from worldmodel.util import atomic_json
from worldmodel.graph import Graph
from worldmodel.pipeline import Context
from worldmodel.transforms import happiness

PROJECT = Path(__file__).resolve().parents[1]


class IntegrityEdgeTests(unittest.TestCase):
    def test_loaded_code_cannot_be_attributed_to_edited_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(PROJECT / 'worldmodel', root / 'worldmodel', ignore=shutil.ignore_patterns('__pycache__'))
            script = '''
from pathlib import Path
import worldmodel.transforms
from worldmodel.provenance import capture_code
p = Path('worldmodel/transforms.py')
p.write_text(p.read_text().replace("float(row[metric])", "float(row[metric]) + 1000"))
try:
    capture_code(Path.cwd(), 'worldmodel.transforms:countries')
except ValueError as error:
    assert 'restart' in str(error)
else:
    raise AssertionError('Accepted stale loaded implementation')
'''
            result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_pointer_failure_does_not_mislabel_published_output_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.import_file('demo_countries', PROJECT / 'tests/fixtures/countries.csv', {'publisher':'fixture'})
            def fail_pointer(path, value):
                if Path(path).name == 'latest.json':
                    raise OSError('simulated pointer write failure')
                return atomic_json(path, value)
            with patch('worldmodel.pipeline.atomic_json', side_effect=fail_pointer):
                with self.assertWarnsRegex(RuntimeWarning, 'published'):
                    ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('demo_countries')
            self.assertTrue(store.verify(ref))
            attempt = json.loads(next((Path(tmp) / 'demo_countries/runs').glob('*.json')).read_text())
            self.assertEqual(attempt['status'], 'succeeded')
            self.assertIn('bookkeeping_error', attempt)

    def test_dataset_lock_rejects_second_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            with store.lock('demo_countries'):
                with self.assertRaisesRegex(RuntimeError, 'active writer'):
                    store.import_file('demo_countries', PROJECT / 'tests/fixtures/countries.csv', {})

    def test_exact_pin_ignores_newer_raw_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            runner = Runner(Catalog(PROJECT / 'data'), store, PROJECT)
            store.import_file('demo_countries', PROJECT / 'tests/fixtures/countries.csv', {})
            source = runner.run('demo_countries')
            bad = Path(tmp) / 'bad.csv'
            bad.write_text('country,income,life_satisfaction\nAA,no,8\n')
            store.import_file('demo_countries', bad, {})
            ref = runner.run('rando_joes_happiness_index', input_refs={'demo_countries':source})
            self.assertEqual([r['value'] for r in store.records(ref)], [65.0, 50.0])
            self.assertEqual(store.manifest(ref)['inputs'], [source])

    def test_multihop_edges_are_bounded_and_respect_valid_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = []
            for name in ['a', 'b', 'c']:
                records.append({'kind':'entity','id':f'org:{name}',
                                'entity_type':'organization','label':name,
                                'observed_at':'2024-01-01T00:00:00Z'})
            for subject, obj in [('a','b'), ('b','c')]:
                records.append({'kind':'assertion','id':f'claim:{subject}{obj}',
                                'subject':f'org:{subject}','object':f'org:{obj}',
                                'predicate':'controls','observed_at':'2024-01-01T00:00:00Z',
                                'valid_from':'2024-01-01','valid_to':'2025-01-01'})
            path = root / 'graph.jsonl'
            path.write_text(''.join(json.dumps(r) + '\n' for r in records))
            store = Store(root / 'data')
            store.import_file('demo_graph', path, {})
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('demo_graph')
            graph = Graph(root / 'graph.sqlite')
            graph.build(store, [ref])
            self.assertEqual(len(graph.neighbors('org:a', hops=1)['assertions']), 1)
            self.assertEqual(len(graph.neighbors('org:a', hops=2)['assertions']), 2)
            limited = graph.neighbors('org:a', hops=2, limit=1)
            self.assertEqual(len(limited['assertions']), 1)
            self.assertTrue(limited['truncated'])
            self.assertEqual(graph.neighbors('org:a', valid_at='2025-01-01')['assertions'], [])

    def test_derived_observed_time_compares_instants_not_timestamp_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_records = []
            for metric, value, unit, observed in [
                    ('income', 50000, 'fictional_currency_per_person', '2025-01-01T00:00:00Z'),
                    ('life_satisfaction', 8, 'points_0_10', '2025-01-01T01:00:00+05:00')]:
                source_records.append({'kind':'observation','id':f'fixture:AA:{metric}',
                    'metric':metric,'value':value,'unit':unit,'dimensions':{'country':'AA'},
                    'valid_from':'2024-01-01','valid_to':'2025-01-01','observed_at':observed})
            raw = root / 'input.jsonl'
            raw.write_text(''.join(json.dumps(r) + '\n' for r in source_records))
            store = Store(root / 'data')
            store.import_file('demo_graph', raw, {})
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('demo_graph')
            context = Context(store, {}, {'input_dataset':'demo_graph'}, [ref], [])
            result = list(happiness(context))
            self.assertEqual(result[0]['observed_at'], '2025-01-01T00:00:00Z')
