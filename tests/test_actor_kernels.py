import random
import unittest
from worldmodel.actor_kernels import register_actor_kernels, example_actors
from worldmodel.processes import ProcessRegistry


class ActorTests(unittest.TestCase):
    def setUp(self):
        self.registry = register_actor_kernels(ProcessRegistry())

    def predict(self, ident, state, seed=1, backend=None):
        port = ident.split('_behavior')[0] + '_state'
        return self.registry.predict(ident, {port: {'value': state, 'unit': None}}, {},
            {'dt_seconds': 86400, 'rng': random.Random(seed), 'agent_backend': backend})

    def test_expected_utility_and_loss_aversion_choose_differently(self):
        state = {'status': 'alive', 'wealth': 100, 'risk_aversion': 0, 'loss_aversion': 4, 'curvature': 1,
                 'choices': [{'id': 'safe', 'outcomes': [{'value': 0, 'probability': 1}]},
                             {'id': 'gamble', 'outcomes': [{'value': 20, 'probability': .5}, {'value': -10, 'probability': .5}]}]}
        self.assertEqual(self.predict('human_behavior.utility', state)['pressures'][0]['value']['decision'], 'gamble')
        self.assertEqual(self.predict('human_behavior.prospect', state)['pressures'][0]['value']['decision'], 'safe')

    def test_logit_is_seeded_and_probabilities_sum_to_one(self):
        state = example_actors()['human_state']
        first = self.predict('human_behavior.logit', state, 7)
        self.assertEqual(first, self.predict('human_behavior.logit', state, 7))
        self.assertAlmostEqual(sum(first['pressures'][0]['value']['choice_probabilities'].values()), 1)

    def test_learning_is_explicit_state_and_feedback_is_consumed_once(self):
        state = example_actors()['human_state']
        state.update(learning_rate=1, feedback={'choice_id': 'work', 'reward': 100})
        learned = self.predict('human_behavior.adaptive', state)['pressures'][0]['value']
        self.assertEqual(learned['decision'], 'work')
        self.assertEqual(learned['action_values']['work'], 100)
        self.assertIsNone(learned['feedback'])
        self.assertEqual(self.predict('human_behavior.adaptive', learned)['pressures'][0]['value']['action_values'], learned['action_values'])

    def test_agent_response_is_audited_and_cannot_change_lifecycle(self):
        class Backend:
            def predict(self, request):
                state = dict(request['state'], decision='work')
                return {'pressures': [{'port': 'human_state', 'mode': 'set', 'value': state}]}
        state = example_actors()['human_state']
        result = self.predict('human_behavior.agent', state, backend=Backend())
        self.assertIn('agent_audit', result['diagnostics'])
        class InvalidBackend:
            def predict(self, request):
                return {'pressures': [{'port': 'human_state', 'mode': 'set', 'value': {'status': 'dead'}}]}
        with self.assertRaises(ValueError):
            self.predict('human_behavior.agent', state, backend=InvalidBackend())

    def test_business_input_and_cash_constraints(self):
        state = example_actors()['business_state']
        state['inputs'][0]['inventory'] = 3
        state['cash'] = 2
        result = self.predict('business_behavior.cost_plus', state)['pressures'][0]['value']
        self.assertLessEqual(result['production'], 2 / state['labor_cost_per_unit'])
        self.assertGreaterEqual(result['inputs'][0]['inventory'], 0)
        self.assertGreaterEqual(result['cash'], 0)

    def test_demand_pricing_evaluates_numeric_alternatives(self):
        state = example_actors()['business_state']
        result = self.predict('business_behavior.demand_pricing', state)['pressures'][0]['value']
        self.assertEqual(len(result['price_evaluations']), len(state['candidate_prices']))
        self.assertEqual(result['price'], max(result['price_evaluations'], key=lambda x: x['profit'])['price'])

    def test_government_reaction_and_budget_limits(self):
        state = example_actors()['government_state']
        monetary = self.predict('government_behavior.monetary', state)['pressures'][0]['value']
        self.assertGreater(monetary['policy_rate'], state['neutral_rate'] + state['inflation'])
        fiscal = self.predict('government_behavior.fiscal', state)['pressures'][0]['value']
        self.assertLessEqual(sum(fiscal['allocations'].values()), state['budget'])
        self.assertGreaterEqual(fiscal['unallocated_budget'], 0)

    def test_dead_and_dissolved_actors_cannot_act(self):
        for ident, key, status in [('human_behavior.utility', 'human_state', 'dead'),
                                    ('business_behavior.cost_plus', 'business_state', 'dissolved')]:
            state = dict(example_actors()[key], status=status)
            result = self.predict(ident, state)
            self.assertEqual(result['pressures'][0]['value'], state)
            self.assertTrue(result['diagnostics']['inactive'])

    def test_deterministic_aging_reaches_death_boundary_at_end_of_day(self):
        state = {'status': 'alive', 'age_years': 80 - 1 / 365.25, 'max_age_years': 80}
        result = self.predict('human_behavior.aging', state)
        dead = result['pressures'][0]['value']
        self.assertEqual(dead['status'], 'dead')
        self.assertEqual(result['events'][0]['event_type'], 'death')
        self.assertEqual(result['events'][0]['offset_seconds'], 86400)
        self.assertEqual(self.predict('human_behavior.aging', dead)['events'], [])

    def test_mortality_zero_and_extreme_hazard_and_seed_repeat(self):
        state = {'status': 'alive', 'age_years': 20, 'mortality_hazard_per_year': 0}
        self.assertEqual(self.predict('human_behavior.mortality', state)['pressures'][0]['value']['status'], 'alive')
        state['mortality_hazard_per_year'] = 1e8
        self.assertEqual(self.predict('human_behavior.mortality', state)['pressures'][0]['value']['status'], 'dead')
        state['mortality_hazard_per_year'] = 100
        self.assertEqual(self.predict('human_behavior.mortality', state, 13), self.predict('human_behavior.mortality', state, 13))

    def test_invalid_probabilities_and_nonfinite_inputs_rejected(self):
        state = example_actors()['human_state']
        state['choices'][0]['outcomes'][0]['probability'] = .1
        with self.assertRaises(ValueError):
            self.predict('human_behavior.utility', state)
        state = example_actors()['business_state']
        state['cash'] = float('nan')
        with self.assertRaises(ValueError):
            self.predict('business_behavior.cost_plus', state)
