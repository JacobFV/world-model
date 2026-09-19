"""Compiling a contract into a materialization environment, and labelled rollouts."""
from copy import deepcopy
import json
import math
import unittest

from worldmodel.decision.compile import DecisionEvaluator, compile_contract, policies_from, recommend
from worldmodel.decision.contract import validate_contract
from worldmodel.environments import Environment
from test_decision_support import EXAMPLES, SyntheticStore, economy_contract, monetary_contract

FLAT = {'kind': 'target_path', 'action': 'reserve_margin', 'observation': 'policy_rate', 'path': [4.0]}
MARGIN = {'kind': 'constant', 'actions': {'reserve_margin': 0.5}}
NONE = {'kind': 'constant', 'actions': {'reserve_margin': 0.0}}


class CompilationTests(unittest.TestCase):
    def setUp(self):
        self.compiled = compile_contract(monetary_contract())
        self.policies = policies_from({'flat': FLAT, 'margin': MARGIN, 'bare': NONE}, self.compiled.contract)

    def test_the_compiled_environment_is_the_materialization_environment(self):
        environment = self.compiled.environment(self.compiled.scenario())
        self.assertIsInstance(environment, Environment)
        spec = self.compiled.environment_spec()
        self.assertEqual(spec['actions']['reserve_margin']['binding'], 'org:test-treasury')
        self.assertEqual(spec['max_steps'], 6)
        weights = {term['selector']['variable']: term['weight'] for term in spec['reward']['terms']}
        self.assertEqual(weights, {'metric.shortfall': -0.75, 'metric.excess': -0.25})
        observation, info = environment.reset(seed=0)
        self.assertEqual(sorted(observation), ['inflation', 'output_gap', 'policy_rate', 'quarter'])
        observation, reward, terminated, truncated, info = environment.step({'reserve_margin': 0.5})
        self.assertFalse(terminated)
        self.assertLessEqual(reward, 0.0)

    def test_an_objective_no_per_step_reward_can_express_is_reported_not_hidden(self):
        contract = monetary_contract()
        contract['objectives'][0]['aggregate'] = 'max'
        compiled = compile_contract(contract)
        self.assertTrue(any('cannot express' in note for note in compiled.environment_spec()['reward_approximations']))

    def test_rollouts_carry_the_evidence_of_every_mechanism_they_used_and_are_labelled(self):
        record = self.compiled.rollout(self.policies['margin'], self.compiled.scenario(), name='margin')
        used = record['mechanisms_used']['policy_rate_reaction']
        self.assertEqual(used['status'], 'assumed')
        self.assertEqual(used['steps_used'], 6)
        self.assertEqual(record['label']['label'], 'rests_on_unvalidated_mechanisms')
        self.assertIn('policy_rate_reaction', record['label']['unvalidated_mechanisms'])
        self.assertTrue(any('intervention response' in line for line in record['does_not_establish']))

    def test_scoring_covers_objectives_budget_and_success_criteria(self):
        record = self.compiled.rollout(self.policies['margin'], self.compiled.scenario(), name='margin', keep_trajectory=True)
        self.assertEqual(len(record['trajectory']), 7)
        shortfall = [step['metrics']['shortfall'] for step in record['trajectory'][1:]]
        objective = next(o for o in record['objectives'] if o['id'] == 'under')
        self.assertAlmostEqual(objective['value'], math.fsum(shortfall))
        budget = next(c for c in record['constraints'] if c['id'] == 'budget')
        self.assertAlmostEqual(budget['observed'], 0.5 * 6)          # 50 bp reserved for six quarters, limit 4
        self.assertTrue(budget['passed'] and record['feasible'])
        expensive = self.compiled.rollout(policies_from({'wide': {'kind': 'constant', 'actions': {'reserve_margin': 1.0}}},
                                                        self.compiled.contract)['wide'], self.compiled.scenario(), name='wide')
        self.assertFalse(expensive['feasible'])
        self.assertEqual([c['id'] for c in expensive['constraints'] if not c['passed']], ['budget'])

    def test_a_target_path_policy_reserves_at_the_planned_rate(self):
        record = self.compiled.rollout(self.policies['flat'], self.compiled.scenario(), name='flat', keep_trajectory=True)
        for step in record['trajectory'][1:]:
            self.assertAlmostEqual(step['metrics']['reserved_rate'], 4.0)

    def test_replay_is_deterministic_and_the_incremental_cache_matches_a_full_replay(self):
        scenario = self.compiled.scenario()
        first = self.compiled.rollout(self.policies['margin'], scenario, name='m', keep_trajectory=True)
        second = self.compiled.rollout(self.policies['margin'], scenario, name='m', keep_trajectory=True)
        self.assertEqual(json.dumps(first['trajectory'], sort_keys=True), json.dumps(second['trajectory'], sort_keys=True))
        evaluator = DecisionEvaluator(self.compiled, scenario)
        history = [{'inputs': [{'binding': 'org:test-treasury', 'port': 'reserve_margin', 'value': 0.5, 'unit': 'percentage_points'}]}
                   for _ in range(4)]
        for length in range(len(history) + 1):                        # incremental: one step at a time
            evaluator(history[:length], 0)
        incremental = deepcopy(evaluator.trajectory)
        fresh = DecisionEvaluator(self.compiled, scenario)
        fresh(history, 0)                                             # one full replay
        self.assertEqual(json.dumps(incremental, sort_keys=True), json.dumps(fresh.trajectory, sort_keys=True))

    def test_the_mechanism_reproduces_the_family_simulator(self):
        from worldmodel.models import monetary
        contract = monetary_contract()
        compiled = compile_contract(contract)
        scenario = compiled.scenario()
        record = compiled.rollout(self.policies['bare'], scenario, name='bare', keep_trajectory=True)
        config = {'horizon': 6, 'initial': {'policy_rate': 4.0},
                  'parameters': {k: v for k, v in compiled.bound.parameters.items()},
                  'paths': {'inflation': [d['inflation'] for d in scenario['drivers']],
                            'output_gap': [d['output_gap'] for d in scenario['drivers']]}}
        family = monetary.simulate(config, 'deterministic')['path']
        for step, row in zip(record['trajectory'][1:], family):
            self.assertAlmostEqual(step['metrics']['policy_rate'], row['policy_rate'], places=9)

    def test_recommendation_ranks_supplied_candidates_and_says_that_is_all_it_does(self):
        result = recommend(self.compiled, self.policies, [self.compiled.scenario()])
        self.assertIn(result['recommended'], self.policies)
        self.assertIn('not_an_optimality_claim', result)
        self.assertEqual(result['label']['label'], 'rests_on_unvalidated_mechanisms')

    def test_policy_declarations_are_checked(self):
        with self.assertRaisesRegex(ValueError, 'unknown kind'):
            policies_from({'p': {'kind': 'neural'}}, self.compiled.contract)
        with self.assertRaisesRegex(ValueError, 'constant actions must name'):
            policies_from({'p': {'kind': 'constant', 'actions': {}}}, self.compiled.contract)


class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = SyntheticStore()
        cls.index = cls.fixture.index()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def test_a_validated_mechanism_takes_its_parameters_from_the_report(self):
        compiled = compile_contract(monetary_contract('validated', self.fixture.good['report_id']), self.index)
        resolution = compiled.resolutions['policy_rate_reaction']
        self.assertEqual(resolution['status'], 'validated')
        self.assertEqual(resolution['binding']['assumed'], [])
        reported = self.fixture.good['final_estimate']['parameters']
        for name, value in compiled.bound.parameters.items():
            self.assertAlmostEqual(value, reported[name], places=12)

    def test_estimated_uncertainty_uses_the_refit_covariance_and_stays_in_the_wald_ellipsoid(self):
        compiled = compile_contract(monetary_contract('validated', self.fixture.good['report_id']), self.index,
                                    history=self.fixture.history())
        uncertainty = compiled.bound.uncertainty
        self.assertEqual(uncertainty['method'], 'joint_wald_ellipsoid')
        self.assertTrue(uncertainty['refit_check']['reproduced'])
        self.assertLess(uncertainty['refit_check']['max_relative_difference'], 1e-6)
        corner = {d['name']: d['high'] for d in compiled.bound.dimensions if d['name'].startswith('coef.')}
        projected = compiled.bound.project({**compiled.bound.nominal_point(), **corner})
        estimate, inverse = compiled.bound.estimate, compiled.bound.inverse_covariance
        d = [projected[f'coef.{n}'] - v for n, v in zip(('const', 'rho', 'b_pi', 'b_y'), estimate)]
        distance = math.sqrt(sum(d[i] * inverse[i][j] * d[j] for i in range(4) for j in range(4)))
        self.assertLessEqual(distance, uncertainty['radius'] + 1e-9)

    def test_a_report_that_sets_only_some_parameters_lowers_the_mechanism(self):
        from worldmodel.decision.evidence import EvidenceIndex
        report_id = self.fixture.good['report_id']
        entry = deepcopy(self.index.get(report_id))
        del entry['record']['parameters']['policy_shock_sd']
        partial = EvidenceIndex({report_id: entry}, validated_processes=['monetary_model'], source='store')
        compiled = compile_contract(monetary_contract('validated', report_id), partial)
        resolution = compiled.resolutions['policy_rate_reaction']
        self.assertEqual(resolution['status'], 'assumed')
        self.assertEqual(resolution['binding']['assumed'], ['policy_shock_sd'])
        self.assertEqual(compiled.bound.parameters['policy_shock_sd'], 0.25)      # the family default stands in

    def test_scope_caveats_state_what_the_holdout_did_and_did_not_cover(self):
        compiled = compile_contract(monetary_contract('validated', self.fixture.good['report_id']), self.index)
        caveats = ' | '.join(compiled.resolutions['policy_rate_reaction']['caveats'])
        self.assertIn('multi-step skill was never tested', caveats)
        self.assertIn('scored conditional on realized inflation, output_gap', caveats)

    def test_a_parameter_the_report_estimates_cannot_also_be_declared_assumed(self):
        contract = monetary_contract('validated', self.fixture.good['report_id'])
        contract['assumed_parameters'] = {'rho': {'mechanism': 'policy_rate_reaction', 'unit': 'per_quarter',
                                                  'minimum': 0.5, 'maximum': 0.95, 'nominal': 0.8}}
        with self.assertRaisesRegex(ValueError, 'estimated by report'):
            compile_contract(contract, self.index)

    def test_binding_coverage_lowers_a_validated_claim_whose_report_sets_only_some_parameters(self):
        from worldmodel.decision.evidence import EvidenceIndex
        from worldmodel.util import digest
        report_id = 'b' * 64
        record = {'schema': 'worldmodel.calibration/1', 'process_id': 'coupled_economy', 'component': 'interest_pass_through',
                  'estimate_id': 'c' * 64, 'report_id': report_id, 'validated': True,
                  'parameters': {'pass_through': 0.9, 'spread': 0.02}, 'standard_errors': {'pass_through': 0.05, 'spread': 0.002},
                  'process_parameters': {'mechanisms.interest.pass_through': 0.9, 'mechanisms.interest.spread': 0.02}}
        record['record_id'] = digest(record)
        entry = {'report_id': report_id, 'label': 'ipt.partial', 'attempt': 'ipt.partial', 'in_plan': True, 'current': True,
                 'superseded_by': None, 'counted': True, 'verified': True, 'passed': True, 'failing': [], 'record': record,
                 'component': 'interest_pass_through', 'process_id': 'coupled_economy', 'protocol': {'horizon': 1},
                 'data_inputs': [], 'version': 'v', 'published': 0.0}
        index = EvidenceIndex({report_id: entry}, validated_processes=[], source='store')
        contract = economy_contract()
        contract['mechanisms']['loan_rate_pass_through']['evidence'] = {
            'status': 'validated', 'report_id': report_id, 'component': 'interest_pass_through'}
        compiled = compile_contract(contract, index)
        resolution = compiled.resolutions['loan_rate_pass_through']
        self.assertEqual(resolution['declared_status'], 'validated')
        self.assertEqual(resolution['status'], 'assumed')
        self.assertEqual(resolution['binding']['bound_from_report'], ['spread', 'pass_through'])
        self.assertTrue(any('binding coverage' in reason for reason in resolution['reasons']))


class EconomyKernelTests(unittest.TestCase):
    def setUp(self):
        self.compiled = compile_contract(economy_contract())
        self.policies = policies_from(json.loads((EXAMPLES / 'economy-firm-policies.json').read_text()), self.compiled.contract)

    def test_the_firm_rollout_reports_the_economy_and_its_enforced_identities(self):
        record = self.compiled.rollout(self.policies['steady'], self.compiled.scenario(), name='steady', keep_trajectory=True)
        self.assertEqual(record['steps'], 30)
        self.assertEqual(sorted(record['mechanisms_used']), ['demand_response', 'loan_rate_pass_through', 'policy_rate_rule',
                                                            'price_adjustment'])
        self.assertTrue(any('reserves conserved' in line for line in record['identities_enforced']))
        self.assertTrue(record['success'])

    def test_an_aggressive_plan_fails_its_own_success_criteria(self):
        record = self.compiled.rollout(self.policies['aggressive'], self.compiled.scenario(), name='aggressive')
        self.assertFalse(record['success'])
        self.assertIn('solvent_at_end', record['failed'])

    def test_uncertain_inputs_and_assumed_parameters_move_the_outcome(self):
        point = dict(self.compiled.bound.nominal_point())
        point['input.unit_cost_multiplier'] = 2.0
        point['input.household_demand'] = 1
        stressed = self.compiled.rollout(self.policies['steady'], self.compiled.scenario(point), name='steady')
        nominal = self.compiled.rollout(self.policies['steady'], self.compiled.scenario(), name='steady')
        self.assertLess(stressed['weighted_score'], nominal['weighted_score'])


if __name__ == '__main__':
    unittest.main()
