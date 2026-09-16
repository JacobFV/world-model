"""The numpy coupled-economy backend reproduces the pure-Python reference exactly."""
import copy
import random
import unittest

from worldmodel.backends import numpy_available
from worldmodel.coupled_economy import initialize_economy, step_economy, simulate_coupled_economy
from tests.test_coupled_economy_scale import random_scenario


def outcome(function, *args, **kwargs):
    try:
        return 'ok', function(*args, **kwargs)
    except ValueError:
        return 'error', None


def stressed_scenario(rng, steps=5):
    """Many buyers per firm, shared workers and scarce reserves/deposits/inventory."""
    banks = ['a', 'b', 'c']
    households = [{'id': 'h%02d' % i, 'bank': rng.choice(banks)} for i in range(rng.randint(8, 30))]
    if rng.random() < .5:
        for h in households: h['labor_capacity'] = rng.randint(0, 20)
    firms = [{'id': 'f%02d' % i, 'bank': rng.choice(banks), 'worker': rng.choice(households[:4])['id'],
              'inventory': rng.randint(0, 30), 'capacity': rng.randint(0, 25), 'unit_cost': rng.choice([1, 2, 3.33])}
             for i in range(rng.randint(2, 8))]
    if rng.random() < .5:
        for f in firms: f['labor_per_unit'] = rng.randint(1, 4)
    mechanisms = {}
    if rng.random() < .6: mechanisms['inventory_valuation'] = True
    if rng.random() < .5:
        mechanisms['interest'] = {'policy_rate': rng.choice([.05, .9]), 'spread': .03, 'day_count': 360, 'insufficient': rng.choice(['defer', 'bankrupt'])}
    if rng.random() < .4:
        mechanisms['price_feedback'] = {'initial_price': 7, 'minimum_price': 2, 'maximum_price': 30, 'target_inventory': 10,
                                        'adjustment': .3, 'unmet_demand_response': .07}
    if rng.random() < .4:
        mechanisms['demand_feedback'] = {'reference_price': 7, 'elasticity': rng.choice([.5, 1.5]), 'energy_response': 1, 'rate_response': .5}
    bank_rows = []
    for bid in banks:
        accounts = {x['id']: rng.choice([0, 5, 12.5, 40, 90]) for x in households + firms if x['bank'] == bid}
        loans = {f['id']: rng.choice([0, 3, 30]) for f in firms if f['bank'] == bid and rng.random() < .5}
        reserves = rng.choice([0, 10, 25, 60, 1000])
        bank_rows.append({'id': bid, 'reserves': reserves, 'accounts': accounts, 'loans': loans,
                          'equity': round(reserves + sum(loans.values()) - sum(accounts.values()), 2)})
    config = {'banks': bank_rows, 'firms': firms, 'households': households}
    if mechanisms: config['mechanisms'] = mechanisms
    policies, shocks = [], []
    for _ in range(steps):
        firm_actions = {}
        for f in firms:
            action = {'production': rng.randint(0, 20), 'credit_limit': rng.choice([0, 20, 200]), 'repay': rng.choice([0, 1, 25])}
            if 'price_feedback' not in mechanisms or rng.random() < .2: action['price'] = rng.choice([2, 5, 7.5, 11])
            firm_actions[f['id']] = action
        household_actions = {h['id']: {'purchases': {f['id']: rng.randint(0, 9) for f in rng.sample(firms, rng.randint(0, len(firms)))}}
                             for h in households if rng.random() < .95}
        shock = {'inventory_loss': {firms[0]['id']: 1}} if rng.random() < .2 else {}
        if 'demand_feedback' in mechanisms and rng.random() < .4: shock['expected_rate_change'] = rng.choice([-.5, .5])
        if 'interest' in mechanisms and rng.random() < .2: shock['unit_cost'] = {firms[-1]['id']: 4}
        policies.append({'firms': firm_actions, 'households': household_actions}); shocks.append(shock)
    return config, policies, shocks


@unittest.skipUnless(numpy_available(), 'numpy backend requires the optional fast extra')
class NumpyEconomyTests(unittest.TestCase):
    def compare(self, config, policies, shocks, history='summary'):
        from worldmodel.coupled_economy_numpy import ArrayEconomy
        status, state = outcome(initialize_economy, copy.deepcopy(config), history=history)
        if status != 'ok':
            return 0
        economy = ArrayEconomy.from_state(state)
        self.assertEqual(economy.to_state(), state)
        steps = 0
        for policy, shock in zip(policies, shocks):
            expected_status, expected = outcome(step_economy, state, policy, shock)
            before = economy.to_state()
            got_status, _ = outcome(economy.step, policy, shock)
            self.assertEqual(expected_status, got_status, (policy, shock))
            if expected_status != 'ok':
                self.assertEqual(economy.to_state(), before)
                break
            self.assertEqual(economy.to_state(), expected)
            state = expected; steps += 1
        return steps

    def test_random_scenarios_equal_reference(self):
        rng = random.Random(1234)
        steps = 0
        for _ in range(150):
            steps += self.compare(*random_scenario(rng))
        self.assertGreater(steps, 500)

    def test_stressed_couplings_equal_reference_and_are_vectorized(self):
        from worldmodel.coupled_economy_numpy import ArrayEconomy
        rng = random.Random(77)
        steps = 0
        for _ in range(150):
            steps += self.compare(*stressed_scenario(rng))
        self.assertGreater(steps, 400)
        vectorized = reference = 0
        for seed in range(40):
            config, policies, shocks = stressed_scenario(random.Random(seed), steps=8)
            status, state = outcome(initialize_economy, config, history='summary')
            if status != 'ok': continue
            economy = ArrayEconomy.from_state(state)
            for policy, shock in zip(policies, shocks):
                if outcome(economy.step, policy, shock)[0] != 'ok': break
            vectorized += economy.diagnostics['vectorized_steps']; reference += economy.diagnostics['reference_steps']
        self.assertGreater(vectorized, 100)
        self.assertEqual(reference, 0)

    def test_every_n_uses_reference_records_and_columnar_policy_matches(self):
        from worldmodel.coupled_economy_numpy import ArrayEconomy, ColumnarPolicy
        rng = random.Random(8)
        for _ in range(20):
            config, policies, shocks = stressed_scenario(rng, steps=6)
            request = {'initial_state': config, 'policies': policies, 'shocks': shocks}
            status, expected = outcome(simulate_coupled_economy, copy.deepcopy(request), history={'mode': 'every_n', 'every': 3})
            got_status, got = outcome(simulate_coupled_economy, copy.deepcopy(request), history={'mode': 'every_n', 'every': 3}, backend='numpy')
            self.assertEqual(status, got_status)
            if status == 'ok':
                self.assertEqual(expected, got)
        config, policies, shocks = stressed_scenario(random.Random(11), steps=4)
        state = initialize_economy(config, history='summary')
        economy = ArrayEconomy.from_state(state)
        np = economy.np
        for policy, shock in zip(policies, shocks):
            columns = ColumnarPolicy.from_dict(economy, policy)
            arrays = ColumnarPolicy.from_arrays(
                economy, production=columns.production, price=columns.price, credit_limit=columns.credit_limit, repay=columns.repay,
                purchases=(columns.purchase_household[::-1], columns.purchase_firm[::-1], columns.purchase_units[::-1]))
            status, state = outcome(step_economy, state, policy, shock)
            got_status, _ = outcome(economy.step, arrays, shock)
            self.assertEqual(status, got_status)
            if status != 'ok': break
            self.assertEqual(economy.to_state(), state)
        with self.assertRaises(ValueError):
            ColumnarPolicy.from_arrays(economy, purchases=(np.array([0, 0]), np.array([1, 1]), np.array([1, 2])))

    def test_limits_and_array_checkpoint_roundtrip(self):
        import tempfile
        from pathlib import Path
        from worldmodel.coupled_economy_numpy import ArrayEconomy
        from worldmodel.limits import LimitExceeded
        config, policies, shocks = stressed_scenario(random.Random(21), steps=3)
        state = initialize_economy(config, history='summary')
        economy = ArrayEconomy.from_state(state, limits={'coupled_max_steps': 2})
        for policy, shock in zip(policies[:2], shocks[:2]):
            outcome(economy.step, policy, shock)
        if economy.step_count == 2:
            with self.assertRaisesRegex(LimitExceeded, 'coupled_max_steps=2'):
                economy.step(policies[2], shocks[2])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'economy'
            economy.checkpoint(target)
            restored = ArrayEconomy.restore(target)
            self.assertEqual(restored.to_state(), economy.to_state())
            column = next(target.glob('*.bin'))
            data = bytearray(column.read_bytes()); data[0] ^= 1; column.write_bytes(bytes(data))
            with self.assertRaises(ValueError):
                ArrayEconomy.restore(target)


if __name__ == '__main__':
    unittest.main()
