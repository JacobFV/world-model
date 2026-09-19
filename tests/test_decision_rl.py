"""Policy optimization gated on validated dynamics, and the refusals."""
import unittest

from worldmodel.decision.compile import compile_contract
from worldmodel.decision.gated_rl import RefusedToOptimize, bin_encoder, optimization_gate, optimize
from test_decision_support import SyntheticStore, economy_contract, monetary_contract

ACTIONS = [{'reserve_margin': 0.0}, {'reserve_margin': 0.25}, {'reserve_margin': 0.5}, {'reserve_margin': 1.0}]
BINS = {'policy_rate': [1.0, 3.0, 5.0], 'inflation': [1.5, 2.5], 'output_gap': [-1.0, 1.0]}
BASELINES = {'no_margin': {'kind': 'constant', 'actions': {'reserve_margin': 0.0}},
             'fixed_50bp': {'kind': 'constant', 'actions': {'reserve_margin': 0.5}},
             'fitted_rule': {'kind': 'fitted_rule_quantile', 'quantile': 0.75}}
RESAMPLE = {'source': 'historical_resample'}


class RefusalTests(unittest.TestCase):
    def test_an_assumed_mechanism_is_refused_and_the_refusal_says_why(self):
        compiled = compile_contract(monetary_contract())
        gate = optimization_gate(compiled)
        self.assertFalse(gate['allowed'])
        self.assertTrue(any('policy_rate_reaction is assumed' in reason for reason in gate['reasons']))
        with self.assertRaises(RefusedToOptimize) as raised:
            optimize(compiled, actions=ACTIONS, encoder_bins=BINS, training_seeds=[1], evaluation_seeds=[2])
        self.assertEqual(raised.exception.report['allowed'], False)

    def test_an_environment_with_one_assumed_mechanism_among_several_is_refused(self):
        compiled = compile_contract(economy_contract())
        gate = optimization_gate(compiled)
        self.assertFalse(gate['allowed'])
        self.assertEqual(sorted(name for name, m in gate['mechanisms'].items() if m['status'] != 'validated'),
                         ['demand_response', 'loan_rate_pass_through', 'policy_rate_rule', 'price_adjustment'])

    def test_evidence_that_did_not_come_from_the_store_is_refused(self):
        compiled = compile_contract(monetary_contract())
        self.assertTrue(any('not read from the calibration_reports store' in reason
                            for reason in optimization_gate(compiled)['reasons']))


class GatedOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = SyntheticStore()
        cls.index = cls.fixture.index()
        cls.contract = monetary_contract('validated', cls.fixture.good['report_id'], drivers=RESAMPLE)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def compiled(self, **overrides):
        contract = dict(self.contract)
        contract.update(overrides)
        return compile_contract(contract, self.index, history=self.fixture.history())

    def test_declared_driver_paths_are_refused_because_an_invented_distribution_is_an_assumption(self):
        compiled = compile_contract(monetary_contract('validated', self.fixture.good['report_id']), self.index,
                                    history=self.fixture.history())
        reasons = optimization_gate(compiled)['reasons']
        self.assertTrue(any('not drawn from observed data' in reason for reason in reasons))

    def test_history_without_provenance_is_not_observed(self):
        compiled = compile_contract(self.contract, self.index, history={'rows': self.fixture.rows, 'evidence': None})
        self.assertFalse(optimization_gate(compiled)['training_source']['observed'])

    def test_a_validated_environment_passes_the_gate_and_trains(self):
        compiled = self.compiled()
        gate = optimization_gate(compiled)
        self.assertTrue(gate['allowed'], gate['reasons'])
        result = optimize(compiled, actions=ACTIONS, encoder_bins=BINS, training_seeds=list(range(120)),
                          evaluation_seeds=list(range(5000, 5060)), baselines=BASELINES, seed=3)
        self.assertEqual(result['gate']['allowed'], True)
        self.assertGreater(result['algorithm']['states_visited'], 1)
        comparison = result['evaluations']['held_out_seeds']['comparison']
        self.assertEqual(sorted(comparison), ['fitted_rule', 'fixed_50bp', 'learned', 'no_margin'])
        for name, row in comparison.items():
            self.assertEqual(row['episodes'], 60)
            self.assertLessEqual(row['success_rate'], 1.0)
        paired = result['evaluations']['held_out_seeds']['learned_minus_baseline']['no_margin']
        self.assertIn('interval_95', paired)
        self.assertEqual(result['label']['label'], 'conditional_on_validated_fitted_dynamics')
        self.assertIn('not validated counterfactual response', result['conditional_on_fitted_dynamics'])
        self.assertTrue(any('conditional on' in line for line in result['does_not_establish']))

    def test_training_and_evaluation_seeds_must_be_disjoint(self):
        with self.assertRaises(ValueError):
            optimize(self.compiled(), actions=ACTIONS, encoder_bins=BINS, training_seeds=[1, 2], evaluation_seeds=[2, 3])

    def test_actions_outside_the_contract_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside the contract bounds'):
            optimize(self.compiled(), actions=[{'reserve_margin': 9.0}], encoder_bins=BINS, training_seeds=[1],
                     evaluation_seeds=[2])

    def test_a_history_split_reports_both_evaluations(self):
        compiled = compile_contract(monetary_contract('validated', self.fixture.good['report_id'],
                                                      drivers={'source': 'historical_resample', 'evaluation_from': '2005-01-01'}),
                                    self.index, history=self.fixture.history())
        source = compiled.bound.training_source()
        self.assertEqual(sorted(source['split']), ['evaluate', 'train'])
        result = optimize(compiled, actions=ACTIONS, encoder_bins=BINS, training_seeds=list(range(60)),
                          evaluation_seeds=list(range(9000, 9030)), baselines=BASELINES, seed=1)
        self.assertEqual(sorted(result['evaluations']), ['held_out_history', 'training_history'])

    def test_the_encoder_requires_ascending_edges(self):
        with self.assertRaisesRegex(ValueError, 'ascending'):
            bin_encoder({'policy_rate': [3.0, 1.0]})
        self.assertEqual(bin_encoder({'x': [1.0, 2.0]})({'x': 1.5}), [1])


if __name__ == '__main__':
    unittest.main()
