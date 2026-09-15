import math
import random
import unittest

from worldmodel.processes import ProcessRegistry, combine_pressures
from worldmodel.process_library import default_registry


def pressure(value, mode='rate', strength=1, confidence=1, unit='kg'):
    return dict(port='stock', value=value, mode=mode, strength=strength,
                confidence=confidence, unit=unit)


class PressureTests(unittest.TestCase):
    def test_exact_competing_rates_and_targets_are_step_invariant(self):
        ps = [pressure(3), pressure(10, 'target', 2), pressure(-1)]
        whole = combine_pressures(0, ps, 2)
        split = combine_pressures(combine_pressures(0, ps, 1), ps, 1)
        self.assertAlmostEqual(whole, 11 * (1 - math.exp(-4)))
        self.assertAlmostEqual(whole, split)
        self.assertAlmostEqual(combine_pressures(1, [pressure(2, 'target', 1e20)], 1), 2)

    def test_competing_process_ports_can_bind_to_same_state_variable(self):
        self.assertEqual(combine_pressures(0, [pressure(2), dict(pressure(3), port='other')], 1), 5)

    def test_vector_dimensions_units_and_finite_strengths(self):
        self.assertEqual(combine_pressures([1, 2], [pressure([3, 4])], 2, 'vector'), [7, 10])
        for ps in ([pressure([3])], [pressure([3, 4], strength=-1)],
                   [pressure([3, 4]), pressure([1, 1], unit='m')]):
            with self.assertRaises(ValueError):
                combine_pressures([1, 2], ps, 1, 'vector')
        for dt in (-1, float('nan'), True):
            with self.assertRaises(ValueError):
                combine_pressures(0, [], dt)

    def test_categorical_sets_are_exclusive_and_ties_fail(self):
        self.assertEqual(combine_pressures('a', [pressure('b', 'set')], 1, 'string'), 'b')
        with self.assertRaises(ValueError):
            combine_pressures('a', [pressure('b', 'set'), pressure('c', 'set')], 1, 'string')
        with self.assertRaises(ValueError):
            combine_pressures(0, [pressure(1, 'set'), pressure(2)], 1)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProcessRegistry()
        self.registry.register_process(dict(id='flow', inputs={'stock': {'type': 'number', 'unit': 'kg'}},
            outputs={'stock': {'type': 'number', 'unit': 'kg'}}, topology='self', description='test'))
        self.registry.register_implementation(dict(id='flow.d', process_id='flow', fidelity='deterministic',
            max_step_seconds=10, cost_per_call=2, description='test'),
            lambda inputs, parameters, context: {'pressures': [pressure(1)], 'events': [], 'memory': {}, 'diagnostics': {}})

    def test_handler_provenance_and_physical_minimum_are_declared(self):
        descriptor = self.registry.describe()['implementations'][0]
        self.assertIn('source_sha256', descriptor['handler_identity'])
        self.assertIn('module', descriptor['handler_identity'])
        population = next(p for p in default_registry().describe()['processes'] if p['id'] == 'population_growth')
        self.assertEqual(population['outputs']['population']['minimum'], 0)

    def test_selection_is_strict_and_descriptors_are_defensive(self):
        self.assertEqual(self.registry.select('flow', 'deterministic', 10, 2)['id'], 'flow.d')
        for fidelity, step, budget in [('agent', 1, 2), ('deterministic', 11, 2), ('deterministic', 1, 1)]:
            with self.assertRaises(ValueError):
                self.registry.select('flow', fidelity, step, budget)
        self.registry.describe()['processes'][0]['inputs'].clear()
        self.assertIn('stock', self.registry.describe()['processes'][0]['inputs'])

    def test_predict_rejects_bad_input_and_output_units_and_types(self):
        self.assertEqual(self.registry.predict('flow.d', {'stock': {'value': 0, 'unit': 'kg'}}, {}, {'dt_seconds': 1})['pressures'][0]['value'], 1)
        for value, unit in [(True, 'kg'), (float('inf'), 'kg'), (0, 'm')]:
            with self.assertRaises(ValueError):
                self.registry.predict('flow.d', {'stock': {'value': value, 'unit': unit}}, {}, {'dt_seconds': 1})
        with self.assertRaises(ValueError):
            self.registry.predict('flow.d', {}, {}, {'dt_seconds': 1})
        self.registry.register_implementation(dict(id='bad', process_id='flow', fidelity='deterministic',
            max_step_seconds=10, cost_per_call=1, description='invalid unit'),
            lambda i, p, c: {'pressures': [pressure(1, unit='m')]})
        with self.assertRaises(ValueError):
            self.registry.predict('bad', {'stock': {'value': 1, 'unit': 'kg'}}, {}, {'dt_seconds': 1})

    def test_unitless_ports_still_require_explicit_unit_declaration(self):
        reg = default_registry()
        inputs = {'context': {'value': {}}, 'goals': {'value': [], 'unit': None},
                  'enabled': {'value': True, 'unit': None}, 'current_action': {'value': 'wait', 'unit': None}}
        with self.assertRaisesRegex(ValueError, 'unit'):
            reg.predict('human_decision.deterministic', inputs, {}, {'dt_seconds': 1})

    def test_default_stochastic_repeats_with_seed_and_agent_requires_backend(self):
        reg = default_registry()
        inputs = {'population': {'value': 100, 'unit': 'people'}}
        def run(seed):
            return reg.predict('population_growth.stochastic', inputs, {'growth_rate': 0.01}, {'dt_seconds': 1, 'rng': random.Random(seed)})
        self.assertEqual(run(5), run(5))
        self.assertNotEqual(run(5), run(6))
        with self.assertRaises(ValueError):
            reg.select('population_growth', 'agent', 1, 100)

    def test_agent_memory_goals_and_response_are_audited(self):
        class Backend:
            def predict(self, request):
                return {'pressures': [dict(port='action', mode='set', value=request['goals'][0], strength=1, confidence=.8, unit=None)],
                        'memory': {'count': request['memory'].get('count', 0) + 1}}
        reg = default_registry()
        inputs = {'context': {'value': {}, 'unit': None}, 'goals': {'value': ['save'], 'unit': None},
                  'enabled': {'value': True, 'unit': None}, 'current_action': {'value': 'wait', 'unit': None}}
        result = reg.predict('human_decision.agent', inputs, {}, {'dt_seconds': 1, 'entity_id': 'alice',
            'memory': {'count': 3}, 'agent_backend': Backend(), 'budget': 9})
        self.assertEqual(result['memory']['count'], 4)
        audit = result['diagnostics']['agent_audit']
        self.assertEqual(audit['request']['entity_id'], 'alice')
        self.assertEqual(audit['request']['budget'], 9)
        self.assertEqual(audit['response']['memory']['count'], 4)

    def test_offline_library_flows_and_movement(self):
        reg = default_registry()
        cases = [('resource_inventory', {'inventory': (10, 'barrel'), 'inflow': (3, 'barrel/second'), 'outflow': (1, 'barrel/second')}, 2),
                 ('investment_cash_flow', {'cash': (10, 'USD'), 'revenue': (3, 'USD/second'), 'expenditure': (1, 'USD/second')}, 2),
                 ('movement', {'position': ([1, 2], 'km'), 'velocity': ([3, 4], 'km/second')}, [3, 4])]
        for name, inputs, expected in cases:
            result = reg.predict(name + '.deterministic', {k: {'value': v, 'unit': u} for k, (v, u) in inputs.items()}, {}, {'dt_seconds': 1})
            self.assertEqual(result['pressures'][0]['value'], expected)
            self.assertTrue(result['diagnostics']['illustrative'])


if __name__ == '__main__':
    unittest.main()
