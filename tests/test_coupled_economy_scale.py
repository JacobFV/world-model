"""Scale rewrite of the coupled economy preserves the frozen reference semantics."""
import copy
import json
import random
import unittest

from worldmodel.coupled_economy import initialize_economy, step_economy, simulate_coupled_economy, summarize_record
from tests import legacy_coupled_economy as legacy


def random_scenario(rng, steps=6):
    bank_ids = ['b%d' % i for i in range(rng.randint(1, 3))]
    households = [{'id': 'h%d' % i, 'bank': rng.choice(bank_ids)} for i in range(rng.randint(1, 7))]
    firms = [{'id': 'f%d' % i, 'bank': rng.choice(bank_ids), 'worker': rng.choice(households)['id'],
              'inventory': rng.randint(0, 6), 'capacity': rng.randint(0, 9), 'unit_cost': rng.choice([1, 2.5, 5, 0.37])}
             for i in range(rng.randint(1, 6))]
    mechanisms = {}
    if rng.random() < .5:
        mechanisms['interest'] = {'policy_rate': rng.choice([0, .05, .1825, .3]), 'spread': rng.choice([0, .02]), 'day_count': 365,
                                  'insufficient': rng.choice(['defer', 'bankrupt'])}
    if rng.random() < .5:
        mechanisms['inventory_valuation'] = True
    if rng.random() < .4:
        mechanisms['price_feedback'] = {'initial_price': 10, 'minimum_price': 3, 'maximum_price': 20, 'target_inventory': rng.randint(0, 5),
                                        'adjustment': rng.choice([.1, .5]), 'unmet_demand_response': rng.choice([0, .05])}
    if rng.random() < .4:
        mechanisms['demand_feedback'] = {'reference_price': 10, 'elasticity': rng.choice([0, .5, 1]), 'energy_response': .5, 'rate_response': .25}
    if rng.random() < .3:
        for household in households: household['labor_capacity'] = rng.randint(0, 12)
    if rng.random() < .3:
        for firm in firms: firm['labor_per_unit'] = rng.randint(1, 3)
    banks = []
    for bid in bank_ids:
        accounts = {a['id']: rng.choice([0, 3, 20, 55.55]) for a in households + firms if a['bank'] == bid}
        reserves = rng.choice([0, 5, 40, 500])
        equity = reserves - 0 + 0
        loans = {}
        for firm in firms:
            if firm['bank'] == bid and rng.random() < .3:
                loans[firm['id']] = rng.choice([1, 10])
        equity = reserves + sum(loans.values()) - sum(accounts.values())
        banks.append({'id': bid, 'reserves': reserves, 'equity': round(equity, 2), 'accounts': accounts, 'loans': loans})
    config = {'banks': banks, 'firms': firms, 'households': households}
    if mechanisms: config['mechanisms'] = mechanisms
    policies, shocks = [], []
    for _ in range(steps):
        policy = {'firms': {}, 'households': {}}
        for firm in firms:
            if rng.random() < .8:
                action = {'production': rng.randint(0, 8), 'credit_limit': rng.choice([0, 10, 100]), 'repay': rng.choice([0, 0, 2, 50])}
                if 'price_feedback' not in mechanisms or rng.random() < .3: action['price'] = rng.choice([1, 4, 10, 12.5])
                if rng.random() < .05:
                    action['bankrupt'] = True
                    if rng.random() < .5:
                        action['collateral_sale'] = {'buyer': rng.choice(households)['id'], 'units': 0, 'unit_price': 2}
                policy['firms'][firm['id']] = action
        for household in households:
            if rng.random() < .9:
                action = {'purchases': {f['id']: rng.randint(0, 6) for f in rng.sample(firms, rng.randint(0, len(firms)))}}
                if 'labor_capacity' in household and rng.random() < .2: action['labor_capacity'] = rng.randint(0, 12)
                policy['households'][household['id']] = action
        if 'interest' in mechanisms and rng.random() < .2: policy['policy_rate'] = .07
        shock = {}
        if rng.random() < .2: shock['unit_cost'] = {rng.choice(firms)['id']: rng.choice([1, 3])}
        if 'demand_feedback' in mechanisms and rng.random() < .3: shock['expected_energy_change'] = rng.choice([-.2, .3])
        policies.append(policy); shocks.append(shock)
    return config, policies, shocks


def comparable(state):
    """Work diagnostics intentionally changed (named limits); everything else must match."""
    state = json.loads(json.dumps(state))
    for record in state['history']:
        record.pop('work', None)
    return state


def outcome(function, *args):
    try:
        return 'ok', function(*args)
    except ValueError as error:
        return 'error', type(error).__name__


class LegacyEquivalenceTests(unittest.TestCase):
    def test_random_scenarios_match_frozen_reference_step_by_step(self):
        rng = random.Random(20260915)
        compared = errors = 0
        for _ in range(250):
            config, policies, shocks = random_scenario(rng)
            status, expected = outcome(legacy.initialize_economy, copy.deepcopy(config))
            got_status, got = outcome(initialize_economy, copy.deepcopy(config))
            self.assertEqual(status, got_status)
            if status != 'ok':
                errors += 1; continue
            self.assertEqual(expected, got)
            for policy, shock in zip(policies, shocks):
                status, next_expected = outcome(legacy.step_economy, expected, policy, shock)
                got_status, next_got = outcome(step_economy, got, policy, shock)
                self.assertEqual(status, got_status, (policy, shock))
                if status != 'ok':
                    errors += 1; break
                self.assertEqual(comparable(next_expected), comparable(next_got))
                expected, got = next_expected, next_got
                compared += 1
        self.assertGreater(compared, 800)
        config, policies, shocks = random_scenario(random.Random(3))
        bad = copy.deepcopy(policies[0]); bad['firms'] = {'ghost': {'production': 1}}
        for module in (legacy, __import__('worldmodel.coupled_economy', fromlist=['x'])):
            with self.assertRaises(ValueError): module.step_economy(module.initialize_economy(copy.deepcopy(config)), bad)

    def test_inputs_are_not_aliased_by_history_or_actor_copies(self):
        rng = random.Random(7)
        config, policies, shocks = random_scenario(rng, steps=3)
        state = initialize_economy(config)
        snapshot = copy.deepcopy(state)
        for policy, shock in zip(policies, shocks):
            try:
                following = step_economy(state, policy, shock)
            except ValueError:
                break
            self.assertEqual(state, snapshot)
            state, snapshot = following, copy.deepcopy(following)

    def test_summary_history_matches_full_projection_on_random_runs(self):
        rng = random.Random(99)
        checked = 0
        for _ in range(60):
            config, policies, shocks = random_scenario(rng)
            request = {'initial_state': config, 'policies': policies, 'shocks': shocks}
            try:
                full = simulate_coupled_economy(copy.deepcopy(request))
            except ValueError:
                continue
            summary = simulate_coupled_economy(copy.deepcopy(request), history='summary')
            self.assertEqual({k: v for k, v in summary.items() if k not in ('history', 'history_policy')}, {k: v for k, v in full.items() if k != 'history'})
            strip = lambda records: [{k: v for k, v in r.items() if k != 'work'} for r in records]
            self.assertEqual(strip(summary['history']), strip(summarize_record(r) for r in full['history']))
            checked += 1
        self.assertGreater(checked, 20)


if __name__ == '__main__':
    unittest.main()
