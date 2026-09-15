import copy
import unittest
from worldmodel.scenario_benchmark import benchmark_scenarios, parameter_ensemble, economy_scenario_example


class ScenarioBenchmarkTests(unittest.TestCase):
    def test_economy_smoke_disjoint_scenarios_baseline_and_objectives(self):
        request = economy_scenario_example()
        result = benchmark_scenarios(**request)
        self.assertLessEqual(result['transitions'], 100)
        self.assertTrue(result['splits_disjoint'])
        self.assertIn('baseline', result['test']['policies'])
        self.assertIn('pareto_front', result['test'])
        self.assertIn('worst_case', result['test']['policies'][result['selected_policy']])
        self.assertIn('inventory', result['test']['policies']['baseline']['weighted'])
        self.assertFalse(result['causally_validated'])
        changed = copy.deepcopy(request)
        for scenario in changed['scenarios']:
            if scenario['split'] == 'test': scenario['config']['initial_state']['households'][0]['labor_capacity'] = 0
        self.assertEqual(benchmark_scenarios(**changed)['selected_policy'], result['selected_policy'])

    def test_budget_and_duplicate_scenario_preflight(self):
        from unittest.mock import patch
        request = economy_scenario_example(); request['max_transitions'] = 1
        with patch('worldmodel.scenario_benchmark.step_economy', side_effect=AssertionError('executed')):
            with self.assertRaisesRegex(ValueError, 'budget'): benchmark_scenarios(**request)
        request = economy_scenario_example()
        request['scenarios'][-1]['config'] = copy.deepcopy(request['scenarios'][0]['config'])
        with self.assertRaisesRegex(ValueError, 'disjoint'): benchmark_scenarios(**request)

    def test_parameter_ambiguity_and_withheld_disagreement_are_explicit(self):
        def evaluate(parameters, config):
            return {'interest': config.get('policy_rate', parameters['policy_rate']) + parameters['spread']}
        params = [{'id': 'a', 'parameters': {'policy_rate': .04, 'spread': .06}},
                  {'id': 'b', 'parameters': {'policy_rate': .08, 'spread': .02}},
                  {'id': 'baseline', 'parameters': {'policy_rate': 0, 'spread': 0}}]
        observations = [{'input': {}, 'path': ['interest'], 'value': .1, 'unit': 'annual_fraction', 'epistemic_status': 'synthetic_scenario'}]
        result = parameter_ensemble(evaluate, params, observations, probes=[{'id': 'higher_rate', 'input': {'policy_rate': .2}}],
                                    outputs={'interest': {'path': ['interest'], 'unit': 'annual_fraction'}}, baseline_id='baseline', tolerance=1e-12)
        self.assertEqual(result['accepted_parameter_ids'], ['a', 'b'])
        self.assertTrue(result['observational_ambiguity'])
        self.assertGreater(result['probes'][0]['outputs']['interest']['maximum'], result['probes'][0]['outputs']['interest']['minimum'])
        self.assertFalse(result['causally_validated'])
        self.assertEqual(result['evaluations'], 5)

    def test_weighted_worst_case_constraints_and_pareto(self):
        request = economy_scenario_example()
        tests = [s for s in request['scenarios'] if s['split'] == 'test']
        tests[0]['weight'], tests[1]['weight'] = 1, 3
        report = benchmark_scenarios(**request)['test']
        for pid, summary in report['policies'].items():
            values = [r['objectives']['inventory'] for r in report['per_scenario'] if r['policy'] == pid]
            self.assertEqual(summary['weighted']['inventory'], .25 * values[0] + .75 * values[1])
            self.assertEqual(summary['worst_case']['inventory'], min(values))
        from worldmodel.scenario_benchmark import _pareto
        self.assertEqual(_pareto({'a': {'x': 1e308, 'y': 1}, 'b': {'x': -1e308, 'y': 0},
                                 'c': {'x': -1e308, 'y': 2}},
                                {'x': {'direction': 'maximize'}, 'y': {'direction': 'minimize'}}), ['a', 'b'])

    def test_parameter_preflight_never_invokes_evaluator(self):
        def forbidden(*args): raise AssertionError('evaluator called')
        params = [{'id': 'a', 'parameters': {'p': 0}}, {'id': 'b', 'parameters': {'p': 1}}]
        obs = [{'input': {}, 'path': [], 'value': 0, 'unit': 'USD', 'epistemic_status': 'synthetic_scenario'}]
        kwargs = {'probes': [], 'outputs': {'x': {'path': ['x'], 'unit': 'USD'}}, 'baseline_id': 'a'}
        with self.assertRaises(ValueError): parameter_ensemble(forbidden, params, obs, **kwargs)
        obs[0]['path'] = ['x']
        with self.assertRaisesRegex(ValueError, 'budget'): parameter_ensemble(forbidden, params, obs, max_evaluations=1, **kwargs)
        obs[0]['epistemic_status'] = 'observed'
        with self.assertRaisesRegex(ValueError, 'synthetic'): parameter_ensemble(forbidden, params, obs, **kwargs)
