import copy
import random
import unittest

from worldmodel.economy import simulate_economy, example_economy, validate_accounting, value_bond
from worldmodel.economy_processes import register_economy_processes, estimate_process_work
from worldmodel.processes import ProcessRegistry


class EconomyTests(unittest.TestCase):
    def test_conservation_and_balanced_journal(self):
        result = simulate_economy(example_economy())
        self.assertTrue(validate_accounting(result)['balanced'])
        initial = result['snapshots'][0]['metrics']['total_cash']
        for snap in result['snapshots']:
            self.assertAlmostEqual(initial, snap['metrics']['total_cash'])
            self.assertAlmostEqual(snap['bank']['loans'], sum(b['loan'] for b in snap['businesses']))
        bad = copy.deepcopy(result)
        bad['journal'][-1]['postings'][0]['debit'] += 1
        with self.assertRaises(ValueError):
            validate_accounting(bad)

    def test_accounting_rejects_tampered_equity_profit_and_metrics(self):
        result = simulate_economy(example_economy())
        for location, key in [('bank', 'equity'), ('business', 'equity'), ('business', 'profit'), ('metrics', 'total_profit')]:
            bad = copy.deepcopy(result)
            target = bad['snapshots'][-1]
            obj = target['businesses'][0] if location == 'business' else target[location]
            obj[key] += 5
            with self.assertRaises(ValueError):
                validate_accounting(bad)
        bad = copy.deepcopy(result)
        bad['metrics']['total_profit'] += 5
        with self.assertRaises(ValueError):
            validate_accounting(bad)

    def test_replay_work_is_preflighted_and_bounded(self):
        state = {'config': example_economy(), 'step': 2}
        self.assertEqual(estimate_process_work(state, 3)['firm_days'], 12)
        self.assertEqual(estimate_process_work(state, 3)['cumulative_firm_days'], 15)
        with self.assertRaises(ValueError):
            estimate_process_work(state, 1000)

    def test_credit_storage_and_cash_limits(self):
        config = example_economy()
        config['bank']['cash'] = 3
        firm = config['businesses'][0]
        firm.update(cash=0, loan=0, credit_limit=2, inventory=0, storage_capacity=1)
        result = simulate_economy(config)
        for snap in result['snapshots']:
            for business in snap['businesses']:
                self.assertGreaterEqual(business['cash'], -1e-9)
                self.assertGreaterEqual(business['inventory'], -1e-9)
                self.assertLessEqual(business['inventory'], business['storage_capacity'])
                self.assertLessEqual(business['loan'], business['credit_limit'])
            self.assertGreaterEqual(snap['bank']['cash'], -1e-9)
        self.assertGreater(result['metrics']['stockouts'], 0)

    def test_multifirm_stresses_conserve_cash_and_bank_equity(self):
        rng = random.Random(120)
        for _ in range(20):
            config = example_economy()
            config['days'] = 12
            config['bank'].update(cash=rng.uniform(1, 1000), credit_limit=500, annual_rate=rng.random())
            config['customers']['cash'] = rng.uniform(1, 10000)
            original = config['businesses'][0]
            config['businesses'] = []
            for n in range(3):
                firm = copy.deepcopy(original)
                firm.update(id=f'firm{n}', cash=rng.uniform(0, 200), loan=0, inventory=rng.uniform(0, 100),
                            daily_energy_need=rng.uniform(0, 100), revenue_per_unit=rng.uniform(0, 30))
                config['businesses'].append(firm)
            result = simulate_economy(config)
            self.assertTrue(result['accounting']['balanced'])
            bank = result['snapshots'][-1]['bank']
            self.assertAlmostEqual(bank['equity'], config['bank']['cash'] + bank['interest_income'] - bank['credit_losses'])

    def test_negative_book_equity_is_distinct_from_liquidity_default(self):
        config = example_economy()
        config['days'] = 0
        config['businesses'][0].update(cash=1, loan=100, inventory=0)
        result = simulate_economy(config)
        self.assertEqual(result['metrics']['insolvent_businesses'], 1)
        self.assertEqual(result['metrics']['defaults'], 0)
        self.assertEqual(result['events'][0]['kind'], 'insolvency')

    def test_anticipation_and_financing_cost_change_purchases(self):
        base = example_economy()
        base['days'] = 1
        firm = base['businesses'][0]
        firm.update(cash=100000, storage_capacity=10000)
        firm['policy'].update(anticipation=4, expected_price_change=0)
        low = simulate_economy(base)['metrics']['total_energy_spend']
        firm['policy']['expected_price_change'] = .5
        anticipating = simulate_economy(base)['metrics']['total_energy_spend']
        base['bank']['annual_rate'] = 10
        costly = simulate_economy(base)['metrics']['total_energy_spend']
        self.assertGreater(anticipating, low)
        self.assertLess(costly, anticipating)

    def test_default_writes_off_both_sides_without_creating_cash(self):
        config = example_economy()
        config['businesses'][0].update(cash=0, loan=100, credit_limit=100)
        result = simulate_economy(config)
        self.assertEqual(result['metrics']['defaults'], 1)
        self.assertEqual(result['metrics']['total_loans'], 0)
        self.assertTrue(validate_accounting(result)['balanced'])
        self.assertTrue(any(e['kind'] == 'default' for e in result['events']))

    def test_shocks_are_applied_on_declared_day_without_future_peeking(self):
        config = example_economy()
        config['shocks'] = [{'step': 2, 'energy_price': 99, 'annual_rate': .2}]
        result = simulate_economy(config)
        self.assertEqual(result['snapshots'][1]['energy_price'], config['energy_price'])
        self.assertEqual(result['snapshots'][2]['energy_price'], 99)

    def test_invalid_inputs_and_bond_duration(self):
        for change in ({'days': 100001}, {'energy_price': 0}, {'days': True}):
            config = dict(example_economy(), **change)
            with self.assertRaises(ValueError):
                simulate_economy(config)
        bond = value_bond(100, .05, .05, 10)
        self.assertAlmostEqual(bond['price'], 100)
        self.assertGreater(bond['modified_duration'], 0)
        self.assertLess(value_bond(100, .05, .06, 10)['price'], 100)

    def test_registry_daily_steps_match_standalone_and_reject_partial_day(self):
        registry = ProcessRegistry()
        register_economy_processes(registry)
        config = example_economy()
        state = {'config': config, 'step': 0}
        for step in range(1, 3):
            result = registry.predict('bank_energy_business.deterministic',
                {'economy_state': {'value': state, 'unit': None}}, {}, {'dt_seconds': 86400})
            state = result['pressures'][0]['value']
            self.assertEqual(state['step'], step)
        direct = simulate_economy(dict(config, days=2))
        self.assertEqual(state['snapshot'], direct['snapshots'][-1])
        with self.assertRaises(ValueError):
            registry.predict('bank_energy_business.deterministic',
                {'economy_state': {'value': state, 'unit': None}}, {}, {'dt_seconds': 1})
