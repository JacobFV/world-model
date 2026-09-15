import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def local(dataset):
    path = ROOT / 'data' / dataset / 'pipeline.py'
    spec = importlib.util.spec_from_file_location('local_test_' + dataset, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self, dataset, path, parameters=None):
        self.definition = {'id': dataset}
        self.parameters = parameters or {}
        self.raw_inputs = [{'dataset': dataset, 'artifact': '0' * 64}]
        self.path = path
        self.stages = {}

    def raw_path(self, index):
        return self.path

    def raw_evidence(self, locator, index):
        return [{'input': self.raw_inputs[index], 'locator': locator}]

    def stage_records(self, stage):
        return iter(self.stages[stage])


class LocalDatasetAdapters(unittest.TestCase):
    def test_original_sample_adapters_execute_from_local_pipelines(self):
        fixtures = json.loads((ROOT / 'tests/fixtures/normalizer_shapes.json').read_text())
        for dataset, source in fixtures.items():
            with self.subTest(dataset=dataset), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'payload'
                path.write_text(json.dumps(source) + '\n')
                path.with_name('receipt.json').write_text(json.dumps({'retrieved_at': '2026-01-01',
                    'source': {'sampling': {'config': {'body': {'seriesid': ['LNS14000000']}}}}}))
                records = list(local(dataset).run(Context(dataset, path)))
                self.assertTrue(records)
                self.assertTrue(all(record['attributes']['source_dataset'] == dataset for record in records))

    def test_country_parsed_stage_is_used_without_rereading_raw(self):
        module = local('demo_countries')
        context = Context('demo_countries', ROOT / 'tests/fixtures/countries.csv', {'format': 'csv'})
        parsed = list(module.parse(context))
        self.assertEqual(len(parsed), 2)
        context.stages['parsed'] = parsed
        context.path = Path('/missing/raw/must/not/be/reread')
        records = list(module.run(context))
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]['evidence'][0]['locator'], 'row:1')

    def test_unavailable_source_still_fails_without_fabricated_records(self):
        with self.assertRaisesRegex(ValueError, 'unavailable|No acquired'):
            list(local('bea_input_output').run(Context('bea_input_output', Path('/missing'))))

    def test_happiness_formula_is_local_and_keeps_record_lineage(self):
        context = Context('rando_joes_happiness_index', Path('/unused'))
        records = [dict(id='income:AA', metric='income', value=50000, unit='fictional_currency_per_person',
                        dimensions={'country': 'AA'}, valid_from='2024-01-01', valid_to='2025-01-01', observed_at='2025-01-01'),
                   dict(id='satisfaction:AA', metric='life_satisfaction', value=8, unit='points_0_10',
                        dimensions={'country': 'AA'}, valid_from='2024-01-01', valid_to='2025-01-01', observed_at='2025-01-01')]
        context.records = lambda dataset: iter(records)
        context.input_ref = lambda dataset: {'dataset': dataset, 'version': 'a' * 64}
        result = list(local('rando_joes_happiness_index').run(context))
        self.assertEqual(result[0]['value'], 65)
        self.assertEqual([e['record_id'] for e in result[0]['evidence']], ['income:AA', 'satisfaction:AA'])
