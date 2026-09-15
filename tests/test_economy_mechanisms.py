import copy
import json
import unittest
from unittest.mock import patch
from worldmodel.banking import simulate_banking
from worldmodel.coupled_economy import initialize_economy, step_economy, simulate_coupled_economy


class MechanismTests(unittest.TestCase):
    def config(self):
        return {'banks': [{'id': 'a', 'reserves': 100, 'equity': 100, 'accounts': {'firm': 0}, 'loans': {}},
                          {'id': 'b', 'reserves': 100, 'equity': 50, 'accounts': {'worker': 50}, 'loans': {}}],
                'firms': [{'id': 'firm', 'bank': 'a', 'worker': 'worker', 'inventory': 0, 'capacity': 10, 'unit_cost': 5}],
                'households': [{'id': 'worker', 'bank': 'b'}]}

    def policy(self, production=4, purchases=3, **fields):
        return {'firms': {'firm': {'production': production, 'price': 10, 'credit_limit': 50, **fields}},
                'households': {'worker': {'purchases': {'firm': purchases}}}}

    def interest(self, cfg, **fields):
        cfg['mechanisms'] = {'interest': {'policy_rate': .1, 'spread': 0, 'day_count': 365,
                                         'duration_days': 1, 'insufficient': 'defer', **fields}}
        return cfg

    def test_bank_interest_reduces_deposit_raises_equity_leaves_principal_and_reserves(self):
        cfg = {'banks': [{'id': 'bank', 'reserves': 20, 'equity': 20,
                         'accounts': {'firm': 10}, 'loans': {'firm': 10}}],
               'transactions': [{'kind': 'interest', 'bank': 'bank', 'borrower': 'firm', 'amount': 1.23}]}
        result = simulate_banking(cfg); bank = result['banks'][0]
        self.assertEqual((bank['accounts']['firm'], bank['equity'], bank['loans']['firm'], bank['reserves']), (8.77, 21.23, 10, 20))
        self.assertTrue(result['accounting']['balanced'])
        cfg['transactions'][0]['amount'] = 11
        with self.assertRaises(ValueError): simulate_banking(cfg)

    def test_shared_labor_rationing_and_prospective_capacity(self):
        cfg = self.config(); cfg['households'][0]['labor_capacity'] = 5
        cfg['firms'][0]['labor_per_unit'] = 2
        cfg['firms'].append({**cfg['firms'][0], 'id': 'zfirm', 'labor_per_unit': 1})
        cfg['banks'][0]['accounts']['zfirm'] = 0
        p = self.policy(4, 0); p['firms']['zfirm'] = {'production': 4, 'price': 10, 'credit_limit': 50}
        one = step_economy(initialize_economy(cfg), p)
        m = one['history'][-1]['firms']
        self.assertEqual((m['firm']['produced'], m['zfirm']['produced']), (2, 1))
        self.assertEqual(one['history'][-1]['labor']['worker'], {'capacity': 5, 'requested': 12, 'allocated': 5, 'unmet': 7})
        p['households']['worker']['labor_capacity'] = 0
        two = step_economy(one, p)
        self.assertEqual(two['history'][-1]['labor']['worker']['allocated'], 0)
        self.assertEqual(two['history'][0], one['history'][0])
        cfg['firms'][0]['labor_per_unit'] = .5
        with self.assertRaises(ValueError): initialize_economy(cfg)

    def test_daily_interest_rounding_override_spread_and_payment_order(self):
        cfg = self.interest(self.config(), policy_rate=.1825)
        one = step_economy(initialize_economy(cfg), self.policy(4, 3))
        two = step_economy(one, self.policy(0, 0, repay=30))
        # Opening debt $20 * .1825 / 365 = exactly one cent.
        self.assertEqual(two['history'][-1]['accounting']['interest_paid'], .01)
        self.assertEqual(two['banks'][0]['loans']['firm'], 0)
        self.assertEqual(two['banks'][0]['accounts']['firm'], 9.99)
        self.assertEqual(two['banks'][0]['equity'], 100.01)
        p = self.policy(0, 0); p['policy_rate'] = .365
        changed = step_economy(one, p)
        self.assertEqual(changed['history'][-1]['accounting']['interest_paid'], .02)
        self.assertEqual(changed['history'][0], one['history'][0])
        cfg['mechanisms']['interest']['policy_rate'] = 0; cfg['mechanisms']['interest']['spread'] = .09125
        state = step_economy(initialize_economy(cfg), self.policy(4, 3))
        self.assertEqual(step_economy(state, self.policy(0, 0))['history'][-1]['accounting']['interest_paid'], .01)

    def test_arrears_persist_and_can_trigger_explicit_bankruptcy(self):
        one = step_economy(initialize_economy(self.interest(self.config())), self.policy(4, 0))
        two = step_economy(one, self.policy(0, 0))
        self.assertEqual(two['firms'][0]['interest_arrears'], .01)
        three = step_economy(two, self.policy(0, 1))
        self.assertEqual(three['history'][-1]['accounting']['interest_paid'], .02)
        self.assertEqual(three['firms'][0]['interest_arrears'], 0)
        cfg = self.interest(self.config(), insufficient='bankrupt')
        state = step_economy(initialize_economy(cfg), self.policy(4, 0))
        out = step_economy(state, self.policy(0, 0))
        self.assertEqual(out['firms'][0]['status'], 'bankrupt')
        self.assertEqual(out['firms'][0]['interest_arrears'], .01)
        self.assertEqual(out['banks'][0]['loans']['firm'], 0)

    def test_policy_rule_is_bounded_and_direct_override_wins(self):
        cfg = self.interest(self.config(), policy_rule={'reference_rate': .02, 'inflation_target': .02,
            'inflation_response': 1.5, 'output_response': .5, 'minimum_rate': 0, 'maximum_rate': .2})
        state = initialize_economy(cfg)
        out = step_economy(state, self.policy(0, 0), {'inflation': .5, 'output_gap': .1})
        self.assertEqual(out['mechanisms']['interest']['policy_rate'], .2)
        p = self.policy(0, 0); p['policy_rate'] = .03
        self.assertEqual(step_economy(state, p, {'inflation': .5, 'output_gap': .1})['mechanisms']['interest']['policy_rate'], .03)
        with self.assertRaises(ValueError): step_economy(state, self.policy())

    def test_inventory_weighted_cost_sales_and_write_down(self):
        cfg = self.config(); cfg['mechanisms'] = {'inventory_valuation': True}
        cfg['firms'][0].update(inventory=2, inventory_value=6)
        one = step_economy(initialize_economy(cfg), self.policy(2, 1))
        self.assertEqual(one['firms'][0]['inventory'], 3)
        self.assertEqual(one['firms'][0]['inventory_value'], 12)
        m = one['history'][-1]['firms']['firm']
        self.assertEqual((m['cost_of_goods_sold'], m['operating_profit'], m['profit']), (4, 6, 0))
        two = step_economy(one, self.policy(0, 0), {'inventory_loss': {'firm': 2}})
        self.assertEqual(two['firms'][0]['inventory_value'], 4)
        self.assertEqual(two['history'][-1]['firms']['firm']['inventory_write_down'], 8)

    def test_lagged_price_demand_expectations_and_cash_constraints(self):
        cfg = self.config(); cfg['mechanisms'] = {
            'price_feedback': {'initial_price': 10, 'minimum_price': 5, 'maximum_price': 12,
                               'target_inventory': 2, 'adjustment': .5, 'unmet_demand_response': .1},
            'demand_feedback': {'reference_price': 10, 'elasticity': 1, 'energy_response': 1, 'rate_response': 1}}
        p = self.policy(4, 4); p['firms']['firm'].pop('price')
        one = step_economy(initialize_economy(cfg), p, {'expected_energy_change': 1})
        # Current demand uses previous expectations; the new expectation affects the next day.
        self.assertEqual(one['history'][-1]['prices']['firm'], 12)
        self.assertEqual(one['history'][-1]['firms']['firm']['sold'], 3)
        two = step_economy(one, p)
        self.assertEqual(two['history'][-1]['firms']['firm']['sold'], 0)
        self.assertEqual(two['history'][0], one['history'][0])

    def test_funded_collateral_partial_recovery_and_goods_cost(self):
        cfg = self.config(); cfg['mechanisms'] = {'inventory_valuation': True}
        one = step_economy(initialize_economy(cfg), self.policy(4, 0))
        p = self.policy(0, 0, bankrupt=True, collateral_sale={'buyer': 'worker', 'units': 3, 'unit_price': 4})
        out = step_economy(one, p); m = out['history'][-1]['firms']['firm']
        self.assertEqual(out['banks'][0]['loans']['firm'], 0)
        self.assertEqual(out['banks'][0]['equity'], 92)
        self.assertEqual((out['firms'][0]['inventory'], out['firms'][0]['inventory_value']), (1, 5))
        self.assertEqual(m['collateral_recovered'], 12)
        self.assertEqual(out['history'][-1]['accounting']['loan_defaulted'], 8)
        again = step_economy(out, self.policy(0, 0, bankrupt=True))
        self.assertEqual(again['banks'], out['banks'])
        before = copy.deepcopy(one); p['firms']['firm']['collateral_sale']['units'] = 5
        with self.assertRaises(ValueError): step_economy(one, p)
        self.assertEqual(one, before)

    def test_malformed_options_and_shocks_reject_atomically(self):
        for fields in ({'policy_rate': float('nan')}, {'day_count': 0}, {'day_count': 365.5},
                       {'duration_days': 2}, {'spread': -1}, {'insufficient': 'ignore'}):
            with self.assertRaises(ValueError): initialize_economy(self.interest(self.config(), **fields))
        state = initialize_economy(self.config()); before = copy.deepcopy(state)
        with self.assertRaises(ValueError): step_economy(state, self.policy(), {'unit_cost': {'firm': .001}})
        self.assertEqual(state, before)

    def test_labor_and_finance_constraints_do_not_create_unfunded_goods(self):
        cfg = self.config(); cfg['households'][0]['labor_capacity'] = 100
        p = self.policy(4, 0, credit_limit=5)
        out = step_economy(initialize_economy(cfg), p)
        self.assertEqual(out['firms'][0]['inventory'], 1)
        self.assertEqual(out['history'][-1]['labor']['worker']['allocated'], 1)
        self.assertEqual(out['history'][-1]['labor']['worker']['unmet'], 3)
        self.assertEqual(out['banks'][0]['loans']['firm'], 5)

    def test_zero_interest_and_weighted_cost_pennies_exhaust_exactly(self):
        cfg = self.interest(self.config(), policy_rate=0)
        cfg['mechanisms']['inventory_valuation'] = True
        cfg['firms'][0].update(inventory=3, inventory_value=.01)
        state = initialize_economy(cfg)
        allocated = 0
        for _ in range(3):
            state = step_economy(state, self.policy(0, 1))
            allocated += round(state['history'][-1]['firms']['firm']['cost_of_goods_sold'] * 100)
            self.assertEqual(state['history'][-1]['accounting']['interest_paid'], 0)
        self.assertEqual((state['firms'][0]['inventory'], state['firms'][0]['inventory_value'], allocated), (0, 0, 1))
        cfg['firms'][0].update(inventory=1, inventory_value=20)
        out = step_economy(initialize_economy(cfg), self.policy(0, 1))
        self.assertEqual(out['history'][-1]['firms']['firm']['operating_profit'], -10)

    def test_collateral_full_zero_and_cash_rationed_recovery(self):
        state = step_economy(initialize_economy(self.config()), self.policy(4, 0))
        full = step_economy(state, self.policy(0, 0, bankrupt=True,
                              collateral_sale={'buyer': 'worker', 'units': 2, 'unit_price': 10}))
        self.assertEqual(full['history'][-1]['accounting']['loan_defaulted'], 0)
        self.assertEqual(full['banks'][0]['reserves'], 100)
        self.assertEqual(full['banks'][1]['reserves'], 100)
        zero = step_economy(state, self.policy(0, 0, bankrupt=True,
                              collateral_sale={'buyer': 'worker', 'units': 0, 'unit_price': 10}))
        self.assertEqual(zero['history'][-1]['accounting']['loan_defaulted'], 20)
        # $70 household balance funds one $50 unit, with no loan to finance recovery.
        rationed = step_economy(state, self.policy(0, 0, bankrupt=True,
                              collateral_sale={'buyer': 'worker', 'units': 4, 'unit_price': 50}))
        event = next(e for e in rationed['history'][-1]['events'] if e['kind'] == 'collateral_sale')
        self.assertEqual((event['sold_units'], event['principal_recovered'], event['proceeds']), (1, 20, 50))
        self.assertEqual(rationed['banks'][0]['accounts']['firm'], 30)
        with self.assertRaises(ValueError):
            step_economy(state, self.policy(0, 0, bankrupt=True,
                         collateral_sale={'buyer': 'missing', 'units': 1, 'unit_price': 1}))

    def test_collateral_reserve_constraint_is_explicit_and_atomic(self):
        cfg = self.config()
        cfg['banks'][1].update(reserves=0, equity=0, loans={'firm': 0})
        # Cross-bank actor loan is invalid; an otherwise balanced explicit deposit bank is required.
        with self.assertRaises(ValueError): initialize_economy(cfg)
        state = step_economy(initialize_economy(self.config()), self.policy(4, 0))
        state['banks'][1]['reserves'] = 0; state['banks'][1]['equity'] = -70; state['banks'][1]['status'] = 'insolvent'
        state['banks'][0]['reserves'] += 120; state['banks'][0]['equity'] += 120
        out = step_economy(state, self.policy(0, 0, bankrupt=True,
                          collateral_sale={'buyer': 'worker', 'units': 2, 'unit_price': 10}))
        self.assertEqual(out['history'][-1]['firms']['firm']['collateral_recovered'], 0)
        self.assertEqual(out['firms'][0]['inventory'], 4)

    def test_interest_transactions_reserved_even_for_missing_firm_actions(self):
        cfg = self.interest(self.config(), policy_rate=0)
        state = initialize_economy(cfg)
        first = step_economy(state, {})
        self.assertEqual(first['history'][-1]['work']['reserved_transactions'], 2)
        # Enough loan actors make interest/default reservation exceed ledger work before callbacks.
        cfg['firms'] = [{**cfg['firms'][0], 'id': 'f' + str(i)} for i in range(100)]
        cfg['banks'][0]['accounts'] = {f['id']: 0 for f in cfg['firms']}
        with patch('worldmodel.coupled_economy.step_economy', side_effect=AssertionError('transition called')):
            with self.assertRaisesRegex(ValueError, 'work budget'):
                simulate_coupled_economy({'initial_state': cfg, 'policies': [{}] * 30})

    def test_seedless_replay_and_serialized_incremental_state_agree(self):
        cfg = self.interest(self.config()); cfg['mechanisms']['inventory_valuation'] = True
        cfg['households'][0]['labor_capacity'] = 3
        policies = [self.policy(4, 1), self.policy(2, 1), self.policy(0, 0, bankrupt=True,
                    collateral_sale={'buyer': 'worker', 'units': 1, 'unit_price': 2})]
        shocks = [{}, {'unit_cost': {'firm': 6}}, {}]
        expected = simulate_coupled_economy({'initial_state': cfg, 'policies': policies, 'shocks': shocks})
        state = initialize_economy(cfg)
        for policy, shock in zip(policies, shocks):
            before = copy.deepcopy(state)
            state = step_economy(json.loads(json.dumps(state)), policy, shock)
            self.assertEqual(state['history'][:-1], before['history'])
            self.assertTrue(state['history'][-1]['accounting']['balanced'])
        self.assertEqual(state, expected)

    def test_price_zero_target_and_demand_do_not_create_purchasing_power(self):
        cfg = self.config(); cfg['mechanisms'] = {'price_feedback': {'initial_price': 10, 'minimum_price': 5,
            'maximum_price': 12, 'target_inventory': 0, 'adjustment': 1, 'unmet_demand_response': 1},
            'demand_feedback': {'reference_price': 100, 'elasticity': 4}}
        cfg['firms'][0]['inventory'] = 100
        p = self.policy(0, 1000000); p['firms']['firm'].pop('price')
        out = step_economy(initialize_economy(cfg), p)
        self.assertEqual(out['history'][-1]['prices']['firm'], 5)
        self.assertEqual(out['history'][-1]['firms']['firm']['sold'], 10)
        self.assertEqual(out['banks'][1]['accounts']['worker'], 0)
        self.assertEqual(out['firms'][0]['inventory'], 90)

    def test_malformed_counter_and_collateral_buyer_raise_value_error(self):
        state = initialize_economy(self.config()); state['step'] = False
        with self.assertRaises(ValueError): step_economy(state, {})
        state = step_economy(initialize_economy(self.config()), self.policy(4, 0))
        with self.assertRaises(ValueError):
            step_economy(state, self.policy(0, 0, bankrupt=True,
                         collateral_sale={'buyer': [], 'units': 1, 'unit_price': 1}))

    def test_integrated_example_balances_and_retains_expected_outcomes(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / 'examples/economy-policy-feedback.json').read_text())
        result = simulate_coupled_economy(config)
        self.assertEqual(result, simulate_coupled_economy(config))
        self.assertEqual(result['step'], 4)
        self.assertEqual(result['firms'][0]['inventory'], 2)
        self.assertEqual(result['firms'][0]['inventory_value'], 13.6)
        self.assertEqual(result['banks'][0]['equity'], 81.03)
        self.assertEqual(result['banks'][0]['loans']['firm'], 0)
        self.assertEqual(sum(b['reserves'] for b in result['banks']), 200)
        self.assertEqual(result['history'][-1]['accounting']['loan_defaulted'], 19)
        self.assertTrue(all(r['accounting']['balanced'] for r in result['history']))
        self.assertEqual(result['history'][1]['monetary_policy']['policy_rate'], .09)
        self.assertEqual(result['history'][2]['monetary_policy']['source'], 'direct_policy')
