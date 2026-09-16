import copy
import unittest
from unittest.mock import patch
from worldmodel.coupled_economy import initialize_economy, step_economy, simulate_coupled_economy
from worldmodel.limits import LimitExceeded, use_limits


class CoupledEconomyTests(unittest.TestCase):
    def config(self):
        return {'banks': [{'id': 'a', 'reserves': 100, 'equity': 100, 'accounts': {'firm': 0}, 'loans': {}},
                          {'id': 'b', 'reserves': 100, 'equity': 50, 'accounts': {'worker': 50}, 'loans': {}}],
                'firms': [{'id': 'firm', 'bank': 'a', 'worker': 'worker', 'inventory': 0, 'capacity': 10, 'unit_cost': 5}],
                'households': [{'id': 'worker', 'bank': 'b'}]}

    def policy(self, production=4, purchase=3):
        return {'firms': {'firm': {'production': production, 'price': 10, 'credit_limit': 50}},
                'households': {'worker': {'purchases': {'firm': purchase}}}}

    def test_closed_credit_production_purchase_and_history(self):
        initial = initialize_economy(self.config())
        one = step_economy(initial, self.policy())
        a, b = one['banks']
        self.assertEqual((a['loans']['firm'], a['accounts']['firm'], b['accounts']['worker']), (20, 30, 40))
        self.assertEqual(one['firms'][0]['inventory'], 1)
        self.assertEqual(one['history'][0]['firms']['firm'], {'produced': 4, 'sold': 3, 'revenue': 30, 'cost': 20, 'profit': 10})
        two = step_economy(one, self.policy(0, 0))
        self.assertEqual(two['history'][0], one['history'][0])
        self.assertEqual(two['step'], 2)
        self.assertEqual(initial['step'], 0)
        self.assertTrue(two['history'][-1]['accounting']['balanced'])

    def test_failed_step_does_not_mutate_inputs(self):
        state = initialize_economy(self.config()); policy = self.policy()
        policy['firms']['firm']['production'] = -1
        before = copy.deepcopy((state, policy))
        with self.assertRaises(ValueError): step_economy(state, policy)
        self.assertEqual((state, policy), before)

    def test_infeasible_credit_and_reserves_reduce_output(self):
        p = self.policy(); p['firms']['firm']['credit_limit'] = 5
        out = step_economy(initialize_economy(self.config()), p)
        self.assertEqual(out['history'][0]['firms']['firm']['produced'], 1)
        self.assertTrue(any(x['kind'] == 'production_rationed' for x in out['history'][0]['events']))
        cfg = self.config(); cfg['banks'][0].update(reserves=0, equity=1, loans={'firm': 1})
        out = step_economy(initialize_economy(cfg), self.policy())
        self.assertEqual(out['history'][0]['firms']['firm']['produced'], 0)
        self.assertEqual(out['banks'][0]['loans']['firm'], 1)

    def test_bankruptcy_writes_off_loan_and_stops_firm(self):
        state = step_economy(initialize_economy(self.config()), self.policy(4, 0))
        p = self.policy(0, 0); p['firms']['firm']['bankrupt'] = True
        out = step_economy(state, p)
        self.assertEqual(out['banks'][0]['loans']['firm'], 0)
        self.assertEqual(out['banks'][0]['equity'], 80)
        self.assertEqual(out['firms'][0]['status'], 'bankrupt')
        self.assertEqual(step_economy(out, self.policy())['history'][-1]['firms']['firm']['produced'], 0)

    def test_shock_is_separate_and_inventory_conserved(self):
        state = step_economy(initialize_economy(self.config()), self.policy(4, 0))
        out = step_economy(state, self.policy(0, 0), {'inventory_loss': {'firm': 2}})
        self.assertEqual(out['firms'][0]['inventory'], 2)
        with self.assertRaises(ValueError): step_economy(state, {'shock': {}})

    def test_unknown_owners_and_corrupt_reserve_anchor_rejected(self):
        cfg = self.config(); cfg['banks'][0]['accounts']['ghost'] = 0
        with self.assertRaises(ValueError): initialize_economy(cfg)
        state = initialize_economy(self.config()); state['initial_reserves'] = 201
        with self.assertRaises(ValueError): step_economy(state, self.policy())

    def test_bounded_runner(self):
        result = simulate_coupled_economy({'initial_state': self.config(), 'policies': [self.policy(), self.policy(0, 0)]})
        self.assertEqual(result['step'], 2)

    def test_repayment_extinguishes_deposits_and_debt(self):
        p = self.policy(); p['firms']['firm']['repay'] = 15
        out = step_economy(initialize_economy(self.config()), p)
        self.assertEqual(out['banks'][0]['accounts']['firm'], 15)
        self.assertEqual(out['banks'][0]['loans']['firm'], 5)
        self.assertEqual(out['history'][0]['accounting']['loan_repaid'], 15)

    def test_default_can_make_bank_insolvent_without_erasing_household_deposit(self):
        cfg = self.config()
        cfg['banks'][0].update(reserves=5, equity=5)
        cfg['households'][0]['bank'] = 'a'
        cfg['banks'][0]['accounts']['worker'] = 0
        cfg['banks'][1]['accounts'] = {}; cfg['banks'][1]['equity'] = 100
        state = step_economy(initialize_economy(cfg), self.policy(4, 0))
        p = self.policy(0, 0); p['firms']['firm']['bankrupt'] = True
        out = step_economy(state, p)
        self.assertEqual(out['banks'][0]['status'], 'insolvent')
        self.assertEqual(out['banks'][0]['accounts']['worker'], 20)
        self.assertTrue(any(e['kind'] == 'bank_insolvent' for e in out['history'][-1]['events']))

    def test_process_consumes_new_policy_without_replaying_prior_steps(self):
        from worldmodel.coupled_economy import register_coupled_economy_processes
        from worldmodel.processes import ProcessRegistry
        registry = register_coupled_economy_processes(ProcessRegistry())
        inputs = {'economy_state': {'value': initialize_economy(self.config()), 'unit': None},
                  'economy_policy': {'value': self.policy(), 'unit': None}}
        result = registry.predict('coupled_economy.deterministic', inputs, {}, {'dt_seconds': 86400})
        one = result['pressures'][0]['value']
        inputs['economy_state']['value'] = one
        inputs['economy_policy']['value'] = self.policy(0, 0)
        two = registry.predict('coupled_economy.deterministic', inputs, {}, {'dt_seconds': 86400})['pressures'][0]['value']
        self.assertEqual(two['history'][0], one['history'][0])
        self.assertEqual(two['banks'], one['banks'])
        self.assertEqual(two['step'], 2)

    def test_late_error_rolls_back_already_executed_production(self):
        cfg = self.config()
        cfg['firms'].append({'id': 'zfirm', 'bank': 'a', 'worker': 'worker', 'inventory': 0, 'capacity': 1, 'unit_cost': 5})
        cfg['banks'][0]['accounts']['zfirm'] = 0
        state = initialize_economy(cfg); before = copy.deepcopy(state)
        with self.assertRaises(ValueError): step_economy(state, self.policy(), {'inventory_loss': {'zfirm': 1}})
        self.assertEqual(state, before)

    def cross_product(self, count, units=1):
        firms = [{'id': f'f{i}', 'bank': 'bank', 'worker': 'h0', 'inventory': 1000,
                  'capacity': 0, 'unit_cost': 1} for i in range(count)]
        households = [{'id': f'h{i}', 'bank': 'bank'} for i in range(count)]
        config = {'banks': [{'id': 'bank', 'reserves': count * 1000 + 1, 'equity': 1,
                            'accounts': {**{f'f{i}': 0 for i in range(count)}, **{f'h{i}': 1000 for i in range(count)}}, 'loans': {}}],
                  'firms': firms, 'households': households}
        policy = {'households': {h['id']: {'purchases': {f['id']: units for f in firms}} for h in households}}
        return config, policy

    def test_purchase_cross_product_rejected_before_any_transfer(self):
        # Fail fast if preflight is missing; never run the pathological workload.
        config, policy = self.cross_product(100)
        state = initialize_economy(config)
        with patch('worldmodel.coupled_economy.apply_transaction', side_effect=AssertionError('A transfer ran before the work-budget check')):
            with use_limits(coupled_max_step_transactions=5000):
                with self.assertRaisesRegex(LimitExceeded, 'work budget.*coupled_max_step_transactions=5000'):
                    step_economy(state, policy)
        self.assertEqual(state['step'], 0)
        self.assertEqual(state['banks'][0]['accounts']['h0'], 1000)
        self.assertEqual(step_economy(state, policy)['step'], 1)

    def test_cumulative_purchase_slots_cannot_evade_budget_with_empty_journals(self):
        config, policy = self.cross_product(5, units=0)
        one = step_economy(initialize_economy(config), policy)
        self.assertEqual(one['history'][0]['journal'], [])
        # Every historical step is this valid no-op policy, so balances stay fixed.
        state = copy.deepcopy(one)
        state['history'] = [{**copy.deepcopy(one['history'][0]), 'step': i + 1} for i in range(400)]
        state['step'] = 400
        with self.assertRaisesRegex(ValueError, 'work budget'):
            step_economy(state, policy, limits={'coupled_max_retained_postings': 40000})
        self.assertEqual(state['step'], 400)
        self.assertEqual(step_economy(state, policy, limits={'coupled_max_retained_postings': 40100})['step'], 401)

    def test_runner_preflights_whole_policy_sequence_before_transitions(self):
        config, policy = self.cross_product(20)
        with patch('worldmodel.coupled_economy.apply_transaction', side_effect=AssertionError('A transfer ran before sequence preflight')):
            with use_limits({'coupled_max_retained_postings': 50000}):
                with self.assertRaisesRegex(ValueError, 'work budget'):
                    simulate_coupled_economy({'initial_state': config, 'policies': [policy] * 50})
            # Summary history retains no journals, so only per-step limits apply.
            with use_limits({'coupled_max_retained_postings': 50000, 'coupled_max_step_transactions': 399}):
                with self.assertRaisesRegex(ValueError, 'coupled_max_step_transactions'):
                    simulate_coupled_economy({'initial_state': config, 'policies': [policy] * 50}, history='summary')

    def test_actor_horizon_and_quantity_limits_are_configurable(self):
        config = self.config()
        with self.assertRaisesRegex(LimitExceeded, 'capacity.*economy_max_quantity=9'):
            initialize_economy(config, limits={'economy_max_quantity': 9})
        state = initialize_economy(config)
        big = self.config(); big['firms'] = [dict(big['firms'][0], id=f'f{i}') for i in range(150)]
        big['banks'][0]['accounts'] = {f['id']: 0 for f in big['firms']}
        self.assertEqual(len(initialize_economy(big)['firms']), 150)
        with self.assertRaisesRegex(LimitExceeded, 'coupled_max_firms=100'):
            initialize_economy(big, limits={'coupled_max_firms': 100})
        with use_limits(coupled_max_steps=1):
            one = step_economy(state, self.policy())
            with self.assertRaisesRegex(LimitExceeded, 'coupled_max_steps=1'):
                step_economy(one, self.policy())
        with self.assertRaisesRegex(LimitExceeded, 'economy_max_quantity=3'):
            step_economy(state, self.policy(4, 0), limits={'economy_max_quantity': 3})
        self.assertEqual(step_economy(state, self.policy(2000000, 0))['history'][-1]['firms']['firm']['produced'], 10)

    def test_summary_and_every_n_history_are_exact_projections_of_full_history(self):
        from worldmodel.coupled_economy import summarize_record
        policies = [self.policy(4, 3), self.policy(0, 1), self.policy(2, 5), self.policy(0, 0), self.policy(3, 2)]
        full = simulate_coupled_economy({'initial_state': self.config(), 'policies': policies})
        summary = simulate_coupled_economy({'initial_state': self.config(), 'policies': policies}, history='summary')
        sampled = simulate_coupled_economy({'initial_state': self.config(), 'policies': policies}, history={'mode': 'every_n', 'every': 2})
        for other in (summary, sampled):
            self.assertEqual({k: v for k, v in other.items() if k not in ('history', 'history_policy')}, {k: v for k, v in full.items() if k != 'history'})
        strip = lambda records: [{k: v for k, v in r.items() if k != 'work'} for r in records]
        self.assertEqual(strip(summary['history']), strip(summarize_record(r) for r in full['history']))
        self.assertEqual(summary['history'][-1]['work']['reserved_postings'], 0)
        self.assertEqual([r.get('summary', False) for r in sampled['history']], [True, False, True, False, True])
        self.assertEqual({k: v for k, v in sampled['history'][1].items() if k != 'work'}, {k: v for k, v in full['history'][1].items() if k != 'work'})
        self.assertEqual(summary['history'][0]['totals'], {'cost': 20.0, 'produced': 4, 'profit': 10.0, 'revenue': 30.0, 'sold': 3})
        resumed = step_economy(summary, self.policy())
        self.assertEqual(resumed['step'], 6)
        with self.assertRaises(ValueError): initialize_economy(self.config(), history={'mode': 'every_n'})
