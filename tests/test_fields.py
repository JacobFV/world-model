import unittest
from worldmodel.fields import FieldWorld, simulate_fields, register_field_processes, estimate_process_work
from worldmodel.processes import ProcessRegistry


class FieldTests(unittest.TestCase):
    def config(self):
        return {'cells': [{'id': 'cell:a', 'measure': 1, 'coordinates': [0, 0], 'tags': ['land']},
                          {'id': 'cell:b', 'measure': 3, 'coordinates': [2, 0], 'tags': ['water']}],
                'edges': [{'id': 'edge:ab', 'source': 'cell:a', 'target': 'cell:b', 'conductance': 1}],
                'fields': {'mass': {'kind': 'extensive', 'unit': 'kg', 'values': {'cell:a': 4, 'cell:b': 0}},
                           'density': {'kind': 'intensive', 'unit': 'kg/m2', 'values': {'cell:a': 4, 'cell:b': 0}}},
                'claims': [{'id': 'claim:one', 'claimant': 'actor:one', 'cells': ['cell:a']},
                           {'id': 'claim:two', 'claimant': 'actor:two', 'cells': ['cell:a', 'cell:b']}]}

    def projection(self, **kw):
        return {'cells': ['cell:a'], 'fields': ['mass'], 'limit': 100, 'observed_at': '2026-01-01',
                'evidence': [{'input': {'dataset': 'scenario', 'artifact': 'a' * 64}, 'locator': 'config'}], **kw}

    def test_conservation_intensive_and_extensive(self):
        world = FieldWorld(self.config())
        result = world.evolve({'duration_seconds': 20, 'step_seconds': 10})
        for field in result['conservation'].values():
            self.assertAlmostEqual(field['initial_integral'], field['final_integral'])
        self.assertTrue(all(v >= 0 for f in result['state']['fields'].values() for v in f['values'].values()))
        self.assertGreater(result['execution']['substeps'], 2)
        self.assertAlmostEqual(result['state']['fields']['mass']['values']['cell:a'], 1, places=5)
        self.assertAlmostEqual(result['state']['fields']['density']['values']['cell:b'], 1, places=5)
        self.assertEqual(world.config['fields']['mass']['values']['cell:a'], 4)

    def test_directed_transport_and_stability_budget(self):
        config = self.config(); config['edges'][0].update(conductance=0, transport_rate=10)
        result = FieldWorld(config).evolve({'duration_seconds': 1, 'step_seconds': 1})
        self.assertAlmostEqual(result['state']['fields']['mass']['values']['cell:b'], 4)
        with self.assertRaises(ValueError):
            FieldWorld(config).evolve({'duration_seconds': 100, 'step_seconds': 1, 'max_substeps': 2})
        with self.assertRaises(ValueError):
            FieldWorld(config).evolve({'duration_seconds': 1, 'step_seconds': 1, 'max_work': 1})

    def test_lazy_subset_and_overlapping_claims(self):
        iterator = FieldWorld(self.config()).project(self.projection())
        self.assertIs(iter(iterator), iterator)
        records = list(iterator)
        cells = [r for r in records if r['kind'] == 'entity' and r['entity_type'] == 'field_cell']
        self.assertEqual([r['entity_id'] for r in cells], ['cell:a'])
        claims = [r for r in records if r.get('predicate') == 'claims_field_cell']
        self.assertEqual(len(claims), 2)
        observations = [r for r in records if r['kind'] == 'observation']
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]['value']['field'], 'mass')
        with self.assertRaises(ValueError): list(FieldWorld(self.config()).project(self.projection(limit=1)))

    def test_bounds_and_no_invented_refinement(self):
        req = self.projection(); req.pop('cells'); req['bounds'] = [-1, -1, 1, 1]
        self.assertEqual(len([r for r in FieldWorld(self.config()).project(req) if r.get('entity_type') == 'field_cell']), 1)
        with self.assertRaises(ValueError): list(FieldWorld(self.config()).project(self.projection(resolution=.01)))

    def test_invalid_state_and_projection_evidence(self):
        cfg = self.config(); cfg['cells'][0]['measure'] = 0
        with self.assertRaises(ValueError): FieldWorld(cfg)
        cfg = self.config(); cfg['fields']['mass']['values']['cell:a'] = -1
        with self.assertRaises(ValueError): FieldWorld(cfg)
        with self.assertRaises(ValueError): list(FieldWorld(self.config()).project(self.projection(evidence=[])))

    def test_cumulative_work_and_claim_evidence(self):
        config = self.config(); config['edges'] = []
        self.assertEqual(estimate_process_work(config, 2)['cell_edge_updates'], 8)
        with self.assertRaises(ValueError): estimate_process_work(config, 100000)
        config['claims'][0]['evidence'] = [{'input': {'dataset': 'claim_source', 'artifact': 'b' * 64}, 'locator': 'claim:1'}]
        records = list(FieldWorld(config).project(self.projection()))
        assertion = next(r for r in records if r.get('subject') == 'claim:one' and r.get('predicate') == 'claims_field_cell')
        self.assertEqual(len(assertion['evidence']), 2)
        self.assertEqual(assertion['epistemic_status'], 'synthetic_scenario')

    def test_nonfinite_integral_is_rejected_explicitly(self):
        config = self.config()
        config['fields'] = {'mass': {'kind': 'extensive', 'unit': 'kg',
                            'values': {'cell:a': 1e308, 'cell:b': 1e308}}}
        with self.assertRaisesRegex(ValueError, 'finite integral'):
            FieldWorld(config).evolve({'duration_seconds': 0})

    def test_adapter_end_step_and_budget(self):
        registry = register_field_processes(ProcessRegistry())
        config = self.config(); config['edges'] = []
        result = registry.predict('field_dynamics.deterministic', {'fields_state': {'value': config, 'unit': None}}, {}, {'dt_seconds': 86400})
        self.assertEqual(result['pressures'][0]['value']['fields']['mass']['values']['cell:a'], 4)
        self.assertEqual(result['diagnostics']['execution']['substeps'], 1)
