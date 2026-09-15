import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store
from worldmodel.util import atomic_json

PROJECT = Path(__file__).resolve().parents[1]


class StagePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / 'catalog' / 'staged'
        self.dataset.mkdir(parents=True)
        self.store = Store(self.root / 'runtime')
        self.catalog = Catalog(self.root / 'catalog')
        self.definition = {'schema_version': 2, 'id': 'staged', 'kind': 'derived', 'dependencies': [],
            'parameters': {'offset': 1}, 'output_stage': 'joined', 'stages': [
            self.stage('parsed', 'parse'), self.stage('left', 'left', ['parsed']),
            self.stage('right', 'right', ['parsed']), self.stage('joined', 'join', ['left', 'right'])]}
        self.write_definition()
        self.write_code()

    def stage(self, name, function, parents=None):
        return {'id': name, 'entrypoint': 'pipeline.py:' + function, 'depends_on': parents or [],
                'schema': {'format': 'jsonl', 'required': {'value': 'number'}},
                'validation': {'allow_empty': False, 'max_rows': 10}, 'cache': 'content', 'retention': 'retain'}

    def write_definition(self):
        atomic_json(self.dataset / 'dataset.json', self.definition)

    def write_code(self, extra=''):
        (self.dataset / 'helpers.py').write_text('BASE = 2\n')
        (self.dataset / 'pipeline.py').write_text('''from pathlib import Path
from .helpers import BASE

def mark(name):
    with Path(__file__).with_name('calls.txt').open('a') as stream:
        stream.write(name + '\\n')

def parse(context):
    mark('parsed')
    yield {'value': BASE + context.parameters['offset']}

def left(context):
    mark('left')
    for row in context.stage_records('parsed'):
        yield {'value': row['value'] * 2}

def right(context):
    mark('right')
    for row in context.stage_records('parsed'):
        yield {'value': row['value'] * 3}

def join(context):
    mark('joined')
    assert context.stage_ref('left')['stage'] == 'left'
    yield {'value': sum(row['value'] for name in ('left', 'right') for row in context.stage_records(name))}
''' + extra)

    def runner(self): return Runner(self.catalog, self.store, PROJECT)

    def test_branching_named_refs_provenance_and_content_cache(self):
        ref = self.runner().run('staged')
        self.assertEqual(ref['stage'], 'joined')
        self.assertEqual(list(self.store.records(ref)), [{'value': 15}])
        manifest = self.store.manifest(ref)
        self.assertEqual({r['stage'] for r in manifest['inputs']}, {'left', 'right'})
        self.assertIn('helpers.py', manifest['code']['dataset_code']['files'])
        self.assertIn('pipeline.py', manifest['code']['dataset_code']['sources'])
        self.assertEqual(self.store.latest('staged'), ref)
        self.assertEqual(self.runner().run('staged'), ref)
        self.assertEqual((self.dataset / 'calls.txt').read_text().splitlines(), ['parsed', 'left', 'right', 'joined'])
        changed = self.runner().run('staged', parameters={'offset': 2})
        self.assertEqual(list(self.store.records(changed)), [{'value': 20}])
        self.assertEqual(self.runner().run('staged'), ref)

    def test_select_stage_builds_ancestors_only_and_cache_off_executes(self):
        self.definition['stages'][0]['cache'] = 'off'; self.write_definition()
        first = self.runner().run('staged', stage='parsed')
        second = self.runner().run('staged', stage='parsed')
        self.assertEqual(first, second)
        self.assertEqual((self.dataset / 'calls.txt').read_text().splitlines(), ['parsed', 'parsed'])
        self.assertFalse(self.store.latest_path('staged').exists())

    def test_local_helper_edit_invalidates_cache_and_module_import(self):
        first = self.runner().run('staged', stage='parsed')
        (self.dataset / 'helpers.py').write_text('BASE = 9\n')
        second = self.runner().run('staged', stage='parsed')
        self.assertNotEqual(first, second)
        self.assertEqual(list(self.store.records(second)), [{'value': 10}])

    def test_source_mutation_rejects_publication(self):
        self.write_code("\ndef mutate(context):\n    Path(__file__).with_name('helpers.py').write_text('BASE = 5\\n')\n    yield {'value': 1}\n")
        self.definition['stages'][0]['entrypoint'] = 'pipeline.py:mutate'; self.write_definition()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.runner().run('staged', stage='parsed')
        self.assertFalse(self.store.latest_path('staged', 'parsed').exists())

    def test_schema_row_limit_and_undeclared_stage_access(self):
        for body, pattern in [("yield {'value': 'bad'}", 'type'),
                              ("yield from ({'value': 1} for _ in range(11))", 'row'),
                              ("yield from context.stage_records('right')", 'Undeclared')]:
            self.write_code('\ndef invalid(context):\n    ' + body + '\n')
            self.definition['stages'][0]['entrypoint'] = 'pipeline.py:invalid'; self.write_definition()
            with self.assertRaisesRegex(ValueError, pattern): self.runner().run('staged', stage='parsed')

    def test_declarations_preflight_cycles_paths_and_options(self):
        from copy import deepcopy
        original = deepcopy(self.definition)
        changes = [('depends_on', ['joined']), ('entrypoint', '../pipeline.py:parse'),
                   ('cache', 'sometimes'), ('retention', 'delete'), ('schema', {'format': 'unknown'}),
                   ('validation', {'max_rows': True})]
        for key, value in changes:
            self.definition = deepcopy(original); self.definition['stages'][0][key] = value; self.write_definition()
            with self.assertRaises(ValueError): self.runner().run('staged')
        self.assertFalse((self.dataset / 'calls.txt').exists())

    def test_generic_source_stage_to_evidence_preserves_raw_lineage(self):
        self.definition.update(kind='source', output_stage='normalized', stages=[
            self.stage('parsed', 'parse'), self.stage('normalized', 'normalize', ['parsed'])])
        self.definition['stages'][1]['schema'] = {'format': 'evidence_jsonl'}
        self.write_definition()
        (self.dataset / 'pipeline.py').write_text('''import json

def parse(context):
    with context.raw_path().open() as stream:
        for row in json.load(stream): yield row

def normalize(context):
    for row in context.stage_records('parsed'):
        yield {'kind':'observation','id':'obs:one','subject':'entity:one',
               'metric':'score','value':row['value'],'unit':'unit','dimensions':{},
               'observed_at':'2025-01-01','evidence':context.raw_evidence('row:1')}
''')
        raw_path = self.root / 'raw.json'; raw_path.write_text('[{"value": 3}]')
        raw = self.store.import_file('staged', raw_path, {'publisher': 'fictional', 'license': 'MIT'})
        ref = self.runner().run('staged')
        row = list(self.store.records(ref))[0]
        self.assertEqual(row['evidence'][0]['input'], raw)
        self.assertTrue(self.store.verify(ref))
        manifest = self.store.manifest(ref)
        self.assertEqual(manifest['inputs'][0]['stage'], 'parsed')
        self.assertEqual(manifest['raw_inputs'], [raw])

    def test_external_dataset_dependency_uses_output_and_can_be_pinned(self):
        other = self.catalog.root / 'consumer'; other.mkdir()
        atomic_json(other / 'dataset.json', {'schema_version': 2, 'id': 'consumer', 'kind': 'derived',
                    'dependencies': ['staged'], 'output_stage': 'result', 'stages': [self.stage('result', 'run')]})
        (other / 'pipeline.py').write_text("def run(context):\n    yield {'value': sum(r['value'] for r in context.records('staged'))}\n")
        original = self.runner().run('staged')
        ref = self.runner().run('consumer', input_refs={'staged': original})
        self.assertEqual(list(self.store.records(ref)), [{'value': 15}])
        self.assertEqual(self.store.manifest(ref)['inputs'], [original])

    def test_corrupt_cached_output_rejected_without_handler_call(self):
        ref = self.runner().run('staged', stage='parsed')
        (self.store.version_dir(ref) / 'records.jsonl').write_text('{"value":999}\n')
        with self.assertRaisesRegex(ValueError, 'checksum'): self.runner().run('staged', stage='parsed')
        self.assertEqual((self.dataset / 'calls.txt').read_text().splitlines(), ['parsed'])

    def test_missing_rebuildable_artifact_is_recomputed(self):
        import shutil
        self.definition['stages'][0]['retention'] = 'rebuildable'; self.write_definition()
        ref = self.runner().run('staged', stage='parsed')
        shutil.rmtree(self.store.version_dir(ref))
        self.assertEqual(self.runner().run('staged', stage='parsed'), ref)
        self.assertEqual((self.dataset / 'calls.txt').read_text().splitlines(), ['parsed', 'parsed'])

    def test_required_field_malformed_type_is_rejected_as_declaration(self):
        self.definition['stages'][0]['schema']['required']['value'] = []
        self.write_definition()
        with self.assertRaises(ValueError): self.runner().run('staged')

    def test_local_json_resource_changes_invalidate_cache(self):
        (self.dataset / 'settings.json').write_text('{"value": 3}')
        (self.dataset / 'pipeline.py').write_text("import json\nfrom pathlib import Path\ndef parse(context):\n    yield json.loads(Path(__file__).with_name('settings.json').read_text())\n")
        first = self.runner().run('staged', stage='parsed')
        (self.dataset / 'settings.json').write_text('{"value": 9}')
        second = self.runner().run('staged', stage='parsed')
        self.assertNotEqual(first, second)
        self.assertEqual(list(self.store.records(second)), [{'value': 9}])
        self.assertIn('settings.json', self.store.manifest(second)['code']['dataset_code']['files'])

    def test_symlink_declaration_and_local_resources_are_rejected(self):
        outside = self.root / 'external.json'; outside.write_text('{}')
        (self.dataset / 'settings.json').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'): self.runner().run('staged', stage='parsed')
        (self.dataset / 'settings.json').unlink()
        declaration = self.dataset / 'dataset.json'
        outside.write_text(declaration.read_text()); declaration.unlink(); declaration.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'): self.runner().run('staged', stage='parsed')
