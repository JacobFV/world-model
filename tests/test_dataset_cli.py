import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.catalog import Catalog
from worldmodel.store import Store
from worldmodel.sampling import sample_dataset, explore

PROJECT = Path(__file__).resolve().parents[1]


class DatasetLayoutIntegrationTests(unittest.TestCase):
    def test_sample_payloads_and_profiles_have_separate_tracked_indexes(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            result = sample_dataset(store, Catalog(PROJECT/'data').get('demo_countries'))
            self.assertEqual(result['status'], 'sampled')
            self.assertTrue((Path(root)/'demo_countries/artifacts/raw'/result['artifact']['artifact']/'payload').is_file())
            self.assertTrue((Path(root)/'demo_countries/manifests/samples/latest.json').is_file())
            index = Path(root)/'demo_countries/manifests/samples'/f"{result['sample_id']}.json"
            metadata = json.loads(index.read_text())
            self.assertNotIn('sources',metadata['code'])
            self.assertTrue(metadata['code']['files'])
            self.assertNotIn('examples',metadata.get('profile',{}))
            self.assertEqual(explore(store,'demo_countries')['rows'],result['rows'])
            self.assertFalse(any(store.scratch_dir('demo_countries').glob('sample-*')))

    def test_cli_pins_named_stage_without_guessing_directory(self):
        from worldmodel.cli import reference
        result=reference('demo_countries/parsed@'+'a'*64,None)
        self.assertEqual(result,{'dataset':'demo_countries','stage':'parsed','version':'a'*64})
        with self.assertRaises(ValueError):reference('demo_countries/../parsed@'+'a'*64,None)

    def test_new_dataset_scaffolds_code_contract_and_local_ignore(self):
        from worldmodel.dataset_scaffold import create_dataset
        with tempfile.TemporaryDirectory() as root:
            catalog=Catalog(Path(root))
            definition=create_dataset(catalog,'example',kind='source',description='Example source')
            self.assertEqual(definition['schema_version'],2)
            self.assertEqual(catalog.get('example')['output_stage'],'normalized')
            base=Path(root)/'example'
            self.assertIn('def run(context)',(base/'pipeline.py').read_text())
            self.assertIn('/artifacts/',(base/'.gitignore').read_text())
            self.assertTrue((base/'tests/test_pipeline.py').is_file())
            with self.assertRaises(ValueError):create_dataset(catalog,'example',kind='source',description='Again')
