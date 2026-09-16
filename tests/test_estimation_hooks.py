"""Estimation hooks: defaults, Python/numpy parity, calibration binding and end-to-end recovery."""
import copy
import math
import random
import unittest
from datetime import date

from worldmodel.backends import numpy_available
from worldmodel.coupled_economy import initialize_economy, step_economy, simulate_coupled_economy, parameter_provenance, calibrate_state
from worldmodel import economy_mechanisms as mechanisms
from worldmodel.estimation import ObservationSet, estimator_for, validate_process, attach_calibration
from worldmodel.estimation.binding import parameter_bindings
from worldmodel.estimation.synthetic import component_dataset, observation_records, periods
from worldmodel.process_library import default_registry


def outcome(function, *args, **kwargs):
    try:
        return 'ok', function(*args, **kwargs)
    except ValueError:
        return 'error', None


def hook_scenario(rng, steps=10):
    """Small economies exercising every coupled-economy hook together with the existing mechanisms."""
    banks = ['a', 'b']
    households = [{'id': 'h%02d' % i, 'bank': rng.choice(banks)} for i in range(rng.randint(3, 10))]
    labor = rng.random() < .5
    if labor or rng.random() < .3:
        for household in households: household['labor_capacity'] = rng.randint(2, 30)
    firms = [{'id': 'f%02d' % i, 'bank': rng.choice(banks), 'worker': rng.choice(households[:3])['id'], 'inventory': rng.randint(0, 20),
              'capacity': rng.randint(1, 25), 'unit_cost': rng.choice([1, 2, 3.33])} for i in range(rng.randint(2, 9))]
    interest = {'policy_rate': rng.choice([.01, .05]), 'spread': .02, 'day_count': 365, 'insufficient': rng.choice(['defer', 'bankrupt'])}
    if rng.random() < .8:
        interest.update(pass_through=rng.choice([.5, 1.2]), adjustment_speed=rng.choice([.05, .5, 1]))
        if rng.random() < .5: interest['impact_pass_through'] = rng.choice([.3, .9])
    rule = rng.random() < .4
    if rule:
        interest['policy_rule'] = {'reference_rate': .03, 'inflation_target': .02, 'inflation_response': 1.5, 'output_response': .5,
                                   'minimum_rate': 0, 'maximum_rate': .2, 'smoothing': rng.choice([.5, .9]), 'period_days': 5}
    options = {'interest': interest}
    if rng.random() < .6: options['deposit_interest'] = {'spread': -.005, 'pass_through': .8, 'adjustment_speed': .3, 'impact_pass_through': .2}
    if rng.random() < .7:
        options['default_hazard'] = {'intercept': -1.0, 'persistence': .5, 'unemployment_sensitivity': 8, 'rate_sensitivity': 5,
                                     'seed': rng.randrange(2 ** 64), 'hazard': .3, 'unemployment': .05, 'period_days': 4}
    if rng.random() < .5: options['deposit_growth'] = {'mean_growth_per_month': .01, 'persistence': .3, 'rate_semi_elasticity': -2, 'period_days': 3}
    if rng.random() < .5: options['credit_growth'] = {'mean_growth_per_month': .02, 'persistence': .5, 'base_credit_limit': 50, 'period_days': 3}
    if rng.random() < .5:
        options['price_feedback'] = {'initial_price': 7, 'minimum_price': 2, 'maximum_price': 30, 'target_inventory': 10,
                                     'adjustment': .3, 'unmet_demand_response': .07, 'cost_pass_through': rng.choice([.4, 1.1])}
    if rng.random() < .5: options['inventory_valuation'] = True
    if labor: options['labor'] = {'employment_output_elasticity': rng.choice([.5, 1.5])}
    bank_rows = []
    for bid in banks:
        accounts = {x['id']: rng.choice([0, 5, 12.5, 40, 90]) for x in households + firms if x['bank'] == bid}
        loans = {f['id']: rng.choice([0, 3, 30]) for f in firms if f['bank'] == bid and rng.random() < .6}
        reserves = rng.choice([10, 60, 1000])
        bank_rows.append({'id': bid, 'reserves': reserves, 'accounts': accounts, 'loans': loans,
                          'equity': round(reserves + sum(loans.values()) - sum(accounts.values()), 2)})
    config = {'banks': bank_rows, 'firms': firms, 'households': households, 'mechanisms': options}
    policies, shocks = [], []
    for _ in range(steps):
        actions = {}
        for f in firms:
            action = {'production': rng.randint(0, 15), 'repay': rng.choice([0, 1, 10])}
            if 'credit_growth' not in options or rng.random() < .3: action['credit_limit'] = rng.choice([0, 20, 200])
            if 'price_feedback' not in options or rng.random() < .2: action['price'] = rng.choice([2, 5, 7.5])
            actions[f['id']] = action
        purchases = {h['id']: {'purchases': {f['id']: rng.randint(0, 6) for f in rng.sample(firms, rng.randint(0, len(firms)))}} for h in households}
        policy = {'firms': actions, 'households': purchases}
        if not rule or rng.random() < .3:
            if rng.random() < .4: policy['policy_rate'] = rng.choice([0, .02, .08])
        shock = {}
        if rule and 'policy_rate' not in policy: shock.update(inflation=rng.choice([0, .03, .06]), output_gap=rng.choice([-.02, 0, .01]))
        if rng.random() < .3: shock['unit_cost'] = {firms[-1]['id']: rng.choice([1.5, 4])}
        if 'default_hazard' in options and rng.random() < .4: shock['unemployment'] = rng.choice([.03, .09])
        policies.append(policy); shocks.append(shock)
    return config, policies, shocks


class DefaultBehaviorTests(unittest.TestCase):
    def test_absent_hook_keys_keep_audit_and_interest_identical(self):
        options = {'interest': {'policy_rate': .04, 'spread': .02, 'insufficient': 'defer'}}
        audit = mechanisms.apply_rate(options, {}, {})
        self.assertEqual(set(audit), {'policy_rate', 'spread', 'annual_loan_rate', 'duration_days', 'day_count', 'source'})
        self.assertNotIn('loan_rate', options['interest'])
        self.assertEqual(mechanisms.interest_due(123456789, options['interest']), round(123456789 * .06 / 365))

    def test_pass_through_adjusts_persisted_loan_rate_and_smoothing(self):
        options = {'interest': {'policy_rate': .02, 'spread': .01, 'pass_through': 1.0, 'adjustment_speed': .25, 'impact_pass_through': .5,
                                'insufficient': 'defer'}}
        mechanisms.apply_rate(options, {}, {})
        self.assertAlmostEqual(options['interest']['loan_rate'], .03)
        audit = mechanisms.apply_rate(options, {'policy_rate': .06}, {})
        pre = .03 + .5 * .04
        self.assertAlmostEqual(audit['annual_loan_rate'], pre + .25 * (.07 - pre), places=12)
        rule = {'interest': {'policy_rate': .05, 'spread': 0, 'insufficient': 'defer', 'policy_rule': {
            'reference_rate': .01, 'inflation_target': .02, 'inflation_response': 1, 'output_response': 0, 'minimum_rate': 0, 'maximum_rate': 1,
            'smoothing': .8, 'period_days': 2}}}
        audit = mechanisms.apply_rate(rule, {}, {'inflation': .02, 'output_gap': 0})
        rho = .8 ** .5
        self.assertAlmostEqual(audit['policy_rate'], rho * .05 + (1 - rho) * .01, places=12)
        self.assertEqual(audit['source'], 'smoothed_bounded_rule')

    def test_malformed_hook_options_raise(self):
        base = {'banks': [{'id': 'b', 'reserves': 10, 'equity': 10, 'accounts': {'h': 0, 'f': 0}, 'loans': {}}],
                'firms': [{'id': 'f', 'bank': 'b', 'worker': 'h', 'inventory': 0, 'capacity': 1, 'unit_cost': 1}], 'households': [{'id': 'h', 'bank': 'b'}]}
        for mechanism in ({'deposit_interest': {'spread': 0, 'pass_through': 1}},
                          {'default_hazard': {'intercept': 0, 'persistence': .5, 'unemployment_sensitivity': 1, 'rate_sensitivity': 1, 'seed': 1, 'hazard': 1, 'unemployment': .1}},
                          {'labor': {}}, {'credit_growth': {'mean_growth_per_month': 0, 'persistence': 2, 'base_credit_limit': 1}}):
            with self.assertRaises(ValueError):
                initialize_economy(dict(base, mechanisms=mechanism))
        state = initialize_economy(base)
        with self.assertRaisesRegex(ValueError, 'unemployment shock requires default_hazard'):
            step_economy(state, {}, {'unemployment': .1})


@unittest.skipUnless(numpy_available(), 'numpy backend requires the optional fast extra')
class HookParityTests(unittest.TestCase):
    def test_every_hook_matches_reference_step_by_step(self):
        from worldmodel.coupled_economy_numpy import ArrayEconomy
        coverage = {'hazard_defaults': 0, 'deposit_interest': 0, 'retained': 0, 'generated_credit': 0, 'cost_prices': 0, 'labor_scaled': 0}
        compared = vectorized = 0
        for seed in range(120):
            config, policies, shocks = hook_scenario(random.Random(seed))
            status, state = outcome(initialize_economy, copy.deepcopy(config), history='summary')
            if status != 'ok':
                continue
            economy = ArrayEconomy.from_state(state)
            self.assertEqual(economy.to_state(), state)
            for policy, shock in zip(policies, shocks):
                expected_status, expected = outcome(step_economy, state, policy, shock)
                before = economy.to_state()
                got_status, _ = outcome(economy.step, policy, shock)
                self.assertEqual(expected_status, got_status, (seed, policy, shock))
                if expected_status != 'ok':
                    self.assertEqual(economy.to_state(), before)
                    break
                self.assertEqual(economy.to_state(), expected, seed)
                record = expected['history'][-1]
                coverage['hazard_defaults'] += record.get('default_hazard', {}).get('defaults', 0)
                coverage['deposit_interest'] += record['accounting'].get('deposit_interest_paid', 0) > 0
                coverage['retained'] += 'deposit_growth' in record
                coverage['generated_credit'] += 'credit_growth' in record and record['accounting']['loan_created'] > 0
                coverage['cost_prices'] += any('last_unit_cost' in f and f['last_unit_cost'] != f['unit_cost'] for f in expected['firms'])
                coverage['labor_scaled'] += any(a.get('labor_capacity') != b.get('labor_capacity') for a, b in zip(state['households'], expected['households'])
                                                if 'labor_output' in b) and not any('labor_capacity' in a for a in policy.get('households', {}).values())
                state = expected; compared += 1
            vectorized += economy.diagnostics['vectorized_steps']
        self.assertGreater(compared, 500)
        self.assertGreater(vectorized, 500)
        for key, value in coverage.items():
            self.assertGreater(value, 0, key)

    def test_full_and_every_n_history_and_checkpoint_with_hooks(self):
        import tempfile
        from pathlib import Path
        from worldmodel.coupled_economy_numpy import ArrayEconomy
        for seed in range(10):
            config, policies, shocks = hook_scenario(random.Random(1000 + seed), steps=6)
            request = {'initial_state': config, 'policies': policies, 'shocks': shocks}
            for history in (None, {'mode': 'every_n', 'every': 2}):
                status, expected = outcome(simulate_coupled_economy, copy.deepcopy(request), history=history)
                got_status, got = outcome(simulate_coupled_economy, copy.deepcopy(request), history=history, backend='numpy')
                self.assertEqual(status, got_status)
                if status == 'ok':
                    self.assertEqual(expected, got)
        config, policies, shocks = hook_scenario(random.Random(4), steps=3)
        state = initialize_economy(config, history='summary')
        economy = ArrayEconomy.from_state(state)
        for policy, shock in zip(policies, shocks):
            outcome(economy.step, policy, shock)
        with tempfile.TemporaryDirectory() as directory:
            economy.checkpoint(Path(directory) / 'economy')
            self.assertEqual(ArrayEconomy.restore(Path(directory) / 'economy').to_state(), economy.to_state())


class FieldHookTests(unittest.TestCase):
    def world(self):
        cells = [{'id': 'cell:a', 'measure': 1e6}, {'id': 'cell:b', 'measure': 2e6}, {'id': 'cell:c', 'measure': 1.5e6}]
        edges = [{'id': 'ab', 'source': 'cell:a', 'target': 'cell:b', 'conductance': .4, 'transport_rate': 1e-7},
                 {'id': 'bc', 'source': 'cell:b', 'target': 'cell:c', 'conductance': .2, 'transport_rate': 0}]
        return {'cells': cells, 'edges': edges, 'fields': {'q': {'kind': 'intensive', 'unit': 'kg/m2', 'decay_rate': 3e-6, 'source_rate': 2e-5,
                                                                 'values': {'cell:a': 100.0, 'cell:b': 10.0, 'cell:c': 0.0}}}}

    def test_decay_source_balance_and_analytic_single_cell(self):
        from worldmodel.fields import FieldWorld
        result = FieldWorld(self.world()).evolve({'duration_seconds': 86400, 'step_seconds': 600})
        balance = result['conservation']['q']
        self.assertAlmostEqual(balance['initial_integral'] + balance['external_input'], balance['final_integral'], delta=1e-6 * balance['final_integral'])
        k, s = 1e-5, 3e-4
        single = {'cells': [{'id': 'cell:x', 'measure': 2.0}], 'fields': {'q': {'kind': 'intensive', 'unit': 'u', 'decay_rate': k, 'source_rate': s, 'values': {'cell:x': 1.0}}}}
        value = FieldWorld(single).evolve({'duration_seconds': 86400, 'step_seconds': 60})['state']['fields']['q']['values']['cell:x']
        exact = s / k + (1.0 - s / k) * math.exp(-k * 86400)
        self.assertAlmostEqual(value, exact, delta=5e-3 * exact)

    @unittest.skipUnless(numpy_available(), 'numpy backend requires the optional fast extra')
    def test_open_field_backends_are_bit_identical(self):
        from worldmodel.fields import FieldWorld
        from worldmodel.field_dynamics import evolve_fields
        from worldmodel.field_arrays import evolve_field_arrays
        world = self.world()
        self.assertEqual(FieldWorld(world, backend='python').evolve({'duration_seconds': 86400, 'step_seconds': 900}),
                         FieldWorld(world, backend='numpy').evolve({'duration_seconds': 86400, 'step_seconds': 900}))
        signed = copy.deepcopy(world); signed['measure_unit'] = 'm2'
        signed['fields']['v'] = {'kind': 'extensive', 'unit': 'kg', 'value_type': 'vector', 'decay_rate': 1e-6, 'source_rate': -1e-7,
                                 'values': {'cell:a': [1.0, -2.0], 'cell:b': [0.5, 3.0], 'cell:c': [0.0, 0.0]}}
        request = {'duration_seconds': 3600 * 30, 'step_seconds': 3600, 'boundary': 'closed', 'edge_units': {'conductance': 'm2/second', 'transport_rate': '1/second'}}
        kwargs = {'coordinate_system': {'kind': 'cartesian', 'axes': ['x', 'y'], 'unit': 'km'}, 'component_frames': {'v': {'kind': 'fixed_global', 'axes': ['x', 'y']}}}
        python = evolve_fields(signed, request, backend='python', **kwargs)
        self.assertEqual(python, evolve_fields(signed, request, backend='numpy', **kwargs))
        self.assertIn('external_input', python['conservation']['v'])
        arrays = {name: {'kind': f['kind'], 'decay_rate': f['decay_rate'], 'source_rate': f['source_rate'],
                         'values': [f['values'][c['id']] for c in world['cells']]} for name, f in signed['fields'].items()}
        topology = ([c['measure'] for c in world['cells']], arrays, [0, 1], [1, 2], [.4, .2], [1e-7, 0])
        one = evolve_field_arrays(*topology, duration_seconds=86400, step_seconds=3600, backend='python')
        two = evolve_field_arrays(*topology, duration_seconds=86400, step_seconds=3600, backend='numpy')
        self.assertEqual(one['conservation'], two['conservation'])
        for name in arrays:
            self.assertEqual([list(map(float, v)) if isinstance(v, list) else float(v) for v in one['fields'][name]],
                             two['fields'][name].tolist())


class CompositionGravityTests(unittest.TestCase):
    def config(self):
        from tests.test_composition import config
        request = config()
        request['world']['cells'] = [{'id': 'cell:depot', 'measure': 2}, {'id': 'cell:near', 'measure': 1}, {'id': 'cell:far', 'measure': 1}]
        request['world']['fields']['resource']['values'] = {'cell:depot': 20, 'cell:near': 0, 'cell:far': 0}
        request['steps'] = [{'demand_kg': 3, 'events': []}, {'demand_kg': 4, 'events': []}]
        request['observations']['cells'] = ['cell:near', 'cell:far']
        request['gravity_demand'] = {'distance_elasticity': -0.5, 'routes': [
            {'source': 'cell:depot', 'target': 'cell:near', 'distance_km': 100}, {'source': 'cell:depot', 'target': 'cell:far', 'distance_km': 400}]}
        return request

    def test_gravity_allocation_calibration_and_backend_parity(self):
        from worldmodel.composition import materialize_composition, gravity_allocation
        allocation, shares = gravity_allocation(3, self.config()['gravity_demand']['routes'], -1.0)
        self.assertEqual(allocation, [2, 1]); self.assertAlmostEqual(shares[0], 0.8)
        dataset = component_dataset('bilateral_flow_gravity', seed=1)
        estimate = estimator_for('bilateral_flow_gravity').fit(ObservationSet(dataset['records']), cutoff=dataset['cutoff'])
        result = materialize_composition(self.config(), calibration=estimate)
        binding = result['calibration']['bindings']['gravity_demand.distance_elasticity']
        self.assertEqual(binding['estimate_id'], estimate.to_dict()['estimate_id'])
        self.assertAlmostEqual(binding['value'], -1.0, delta=0.05)
        first = result['frames'][0]['gravity_allocation']
        self.assertEqual([row['allocated_kg'] for row in first], [2, 1])
        self.assertEqual(result['frames'][0]['observation']['resources'], {'cell:near': 2, 'cell:far': 1})
        self.assertEqual([row['allocated_kg'] for row in result['frames'][1]['gravity_allocation']], [3, 1])
        self.assertEqual(sum(f['purchased_kg'] for f in result['frames']), 7)
        if numpy_available():
            self.assertEqual(result, materialize_composition(self.config(), calibration=estimate, backend='numpy'))


class EndToEndCalibrationTests(unittest.TestCase):
    """Fit synthetic data with known parameters, attach calibration, simulate, compare with the data-generating process."""

    def test_interest_pass_through_record_drives_loan_rate_response(self):
        dataset = component_dataset('interest_pass_through', seed=5)
        report = validate_process(estimator_for('interest_pass_through'), ObservationSet(dataset['records']), train_end=dataset['train_end'],
                                  validation_end=dataset['validation_end'], cutoff=dataset['cutoff'])
        registry = default_registry()
        record = attach_calibration(registry, report)
        config = {'banks': [{'id': 'bank', 'reserves': 1000, 'equity': 1000, 'accounts': {'firm': 1000000, 'worker': 1000}, 'loans': {'firm': 1001000}}],
                  'firms': [{'id': 'firm', 'bank': 'bank', 'worker': 'worker', 'inventory': 0, 'capacity': 0, 'unit_cost': 1}],
                  'households': [{'id': 'worker', 'bank': 'bank'}],
                  'mechanisms': {'interest': {'policy_rate': .02, 'spread': 0.0, 'pass_through': .5, 'adjustment_speed': 1, 'impact_pass_through': 0,
                                              'day_count': 365, 'insufficient': 'defer'}}}
        state = initialize_economy(config, history='summary', calibration=record)
        bound = state['calibration']['bindings']
        self.assertEqual(bound['mechanisms.interest.pass_through']['record_id'], record['record_id'])
        provenance = parameter_provenance(state)
        self.assertEqual(provenance['mechanisms.interest.adjustment_speed']['status'], 'estimated')
        self.assertEqual(provenance['mechanisms.interest.day_count'], {'status': 'assumed', 'value': 365})
        rates = []
        for day in range(62):
            state = step_economy(state, {'policy_rate': .02 if day == 0 else .05})
            rates.append(state['history'][-1]['monetary_policy']['annual_loan_rate'])
        # Monthly truth (synthetic.py): spread 3%, pass-through 1, impact 0.6, 30% of the gap closed per month.
        truth_long_run = .03 + 1.0 * .05
        truth_after_jump = .05 + .6 * .03
        for day, months in ((1, 0), (31, 30 / 30.4375), (61, 60 / 30.4375)):
            truth = truth_long_run + (truth_after_jump - truth_long_run) * (1 - .3) ** months
            self.assertAlmostEqual(rates[day], truth, delta=.004, msg=f'day {day}')
        principal = 1001000 * 100
        self.assertEqual(state['history'][-1]['totals']['interest_due'], round(principal * rates[-1] / 365) / 100)
        inputs = {'economy_state': {'value': initialize_economy(config), 'unit': None}, 'economy_policy': {'value': {'policy_rate': .05}, 'unit': None}}
        prediction = registry.predict('coupled_economy.deterministic', inputs, {'calibration': record}, {'dt_seconds': 86400, 'time': '2024-01-01T00:00:00Z'})
        self.assertEqual(prediction['diagnostics']['parameter_provenance']['mechanisms.interest.pass_through']['record_id'], record['record_id'])
        self.assertIn(record['record_id'], prediction['diagnostics']['calibration_sources'])

    def test_default_hazard_estimate_drives_simulated_default_rates(self):
        dataset = component_dataset('default_hazard', seed=1)
        estimate = estimator_for('default_hazard').fit(ObservationSet(dataset['records']), cutoff=dataset['cutoff'])
        count = 3000 if numpy_available() else 300
        firm_ids = ['f%05d' % i for i in range(count)]
        config = {'banks': [{'id': 'bank', 'reserves': 100, 'equity': 100 + count - 1, 'accounts': {'worker': 1, **{f: 0 for f in firm_ids}},
                             'loans': {f: 1 for f in firm_ids}}],
                  'firms': [{'id': f, 'bank': 'bank', 'worker': 'worker', 'inventory': 0, 'capacity': 0, 'unit_cost': 1} for f in firm_ids],
                  'households': [{'id': 'worker', 'bank': 'bank'}],
                  'mechanisms': {'default_hazard': {'intercept': 0.0, 'persistence': 0.0, 'unemployment_sensitivity': 0.0, 'rate_sensitivity': 0.0,
                                                    'seed': 20260915, 'hazard': .03, 'unemployment': .06}}}
        state = initialize_economy(config, history='summary', calibration=estimate)
        self.assertEqual(parameter_provenance(state)['mechanisms.default_hazard.persistence']['estimate_id'], estimate.to_dict()['estimate_id'])
        steps = 2 * 92
        policies = [{} for _ in range(steps)]
        if numpy_available():
            from worldmodel.coupled_economy_numpy import ArrayEconomy
            reference = state
            for policy in policies[:3]:
                reference = step_economy(reference, policy)
            economy = ArrayEconomy.from_state(state)
            for policy in policies[:3]:
                economy.step(policy)
            self.assertEqual(economy.to_state(), reference)
            for policy in policies[3:]:
                economy.step(policy)
            history = economy.history
            self.assertEqual(economy.diagnostics['reference_steps'], 0)
        else:
            for policy in policies:
                state = step_economy(state, policy)
            history = state['history']
        audits = [record['default_hazard'] for record in history]
        boundary = next(i for i, audit in enumerate(audits) if audit['updated'])
        periods_defaults = [sum(a['defaults'] for a in audits[:boundary]), sum(a['defaults'] for a in audits[boundary:boundary + boundary])]
        truth = lambda h: 1 / (1 + math.exp(-(-2.0 + .5 * math.log(h / (1 - h)) + 8 * .06 + 5 * 0)))
        h1 = truth(.03)
        self.assertAlmostEqual(audits[boundary]['hazard'], h1, delta=.006)
        active = count
        for index, (hazard, defaults) in enumerate(zip((.03, h1), periods_defaults)):
            days = boundary
            expected = 1 - (1 - hazard) ** (days / mechanisms.QUARTER_DAYS)
            sd = math.sqrt(expected * (1 - expected) / active)
            self.assertAlmostEqual(defaults / active, expected, delta=4 * sd + (.006 if index else 0), msg=f'period {index}')
            active -= defaults

    def test_field_decay_estimate_reproduces_data_generating_process(self):
        from worldmodel.fields import FieldWorld
        cells = [{'id': 'cell:a', 'measure': 1e6}, {'id': 'cell:b', 'measure': 2e6}, {'id': 'cell:c', 'measure': 1.5e6}, {'id': 'cell:d', 'measure': 1e6}]
        edges = [{'id': 'ab', 'source': 'cell:a', 'target': 'cell:b'}, {'id': 'bc', 'source': 'cell:b', 'target': 'cell:c'},
                 {'id': 'cd', 'source': 'cell:c', 'target': 'cell:d'}, {'id': 'ac', 'source': 'cell:a', 'target': 'cell:c'}]
        options = {'topology': {'cells': cells, 'edges': edges}}
        estimator = estimator_for('field_diffusion_transport', options=options)
        truth = {'conductance': 0.2, 'transport_rate': 5e-8, 'decay_rate': 2e-7, 'source_rate': 4e-6}
        rng = random.Random(3)
        state = {'cell:a': 200.0, 'cell:b': 20.0, 'cell:c': 80.0, 'cell:d': 5.0}
        columns = {c['id']: [] for c in cells}
        for _ in range(240):
            for cell in columns:
                columns[cell].append(state[cell] * (1 + rng.gauss(0, .0005)))
            state = estimator._step(state, truth, estimator.options)
        records = observation_records(estimator, columns, periods('daily', 240, date(2024, 1, 1)))
        estimate = estimator.fit(ObservationSet(records), cutoff='2024-09-30')
        world = {'cells': cells, 'edges': [dict(e, conductance=1.0, transport_rate=0.0) for e in edges],
                 'fields': {'pm': {'kind': 'intensive', 'unit': 'ug/m3', 'values': {c: columns[c][0] for c in columns}}}}
        simulated = FieldWorld(world, calibration=estimate.to_dict())
        self.assertAlmostEqual(simulated.config['fields']['pm']['decay_rate'], truth['decay_rate'], delta=.1 * truth['decay_rate'])
        truth_state = {c: columns[c][0] for c in columns}
        config = simulated.config
        for day in range(60):
            result = FieldWorld(config).evolve({'duration_seconds': 86400, 'step_seconds': 3600})
            config = result['state']; truth_state = estimator._step(truth_state, truth, estimator.options)
            if day in (0, 29, 59):
                for cell, value in truth_state.items():
                    self.assertAlmostEqual(config['fields']['pm']['values'][cell], value, delta=.03 * value, msg=f'{cell} day {day}')
        provenance = FieldWorld(simulated.config, calibration=estimate).evolve({'duration_seconds': 86400})['parameter_provenance']
        self.assertEqual(provenance['fields.pm.decay_rate']['status'], 'estimated')
        self.assertEqual(provenance['edges[ab].conductance']['estimate_id'], estimate.to_dict()['estimate_id'])


class BindingTests(unittest.TestCase):
    def test_sources_are_verified_and_conflicts_raise(self):
        dataset = component_dataset('interest_pass_through', seed=1)
        estimate = estimator_for('interest_pass_through').fit(ObservationSet(dataset['records']), cutoff=dataset['cutoff'])
        body = estimate.to_dict()
        bindings = parameter_bindings(body)['bindings']
        self.assertIn('mechanisms.interest.impact_pass_through', bindings)
        tampered = dict(body, parameters=dict(body['parameters'], pass_through=9.0))
        with self.assertRaises(ValueError):
            parameter_bindings(tampered)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            parameter_bindings({'mechanisms.interest.spread': {'value': .01}}, {'mechanisms.interest.spread': {'value': .02}})

    def test_process_library_and_bank_energy_business_report_provenance(self):
        registry = default_registry()
        dataset = component_dataset('inventory_balance', seed=1)
        estimate = estimator_for('inventory_balance').fit(ObservationSet(dataset['records']), cutoff=dataset['cutoff'])
        context = {'dt_seconds': 60, 'time': '2024-01-01T00:00:00Z'}
        inputs = {'inventory': {'value': 100, 'unit': 'barrel'}, 'inflow': {'value': 3, 'unit': 'barrel/second'}, 'outflow': {'value': 1, 'unit': 'barrel/second'}}
        result = registry.predict('resource_inventory.deterministic', inputs, {'calibration': estimate.to_dict()}, context)
        provenance = result['diagnostics']['parameter_provenance']
        self.assertEqual(provenance['flow_scale']['status'], 'estimated')
        expected = estimate.parameters['flow_scale'] * 2 + estimate.parameters['unmeasured_net_flow']
        self.assertAlmostEqual(result['pressures'][0]['value'], expected)
        plain = registry.predict('resource_inventory.deterministic', inputs, {}, context)['diagnostics']['parameter_provenance']
        self.assertEqual(plain['flow_scale'], {'status': 'assumed', 'value': 1})
        from worldmodel.economy import example_economy
        economy = example_economy(); economy['interest'] = {'policy_rate': .03, 'spread': .02}
        economy['shocks'] = [{'step': 10, 'energy_price': 14, 'policy_rate': .06}]
        state = {'config': economy, 'step': 12}
        record_dataset = component_dataset('interest_pass_through', seed=1)
        pass_estimate = estimator_for('interest_pass_through').fit(ObservationSet(record_dataset['records']), cutoff=record_dataset['cutoff'])
        out = registry.predict('bank_energy_business.deterministic', {'economy_state': {'value': state, 'unit': None}},
                               {'calibration': pass_estimate.to_dict()}, {'dt_seconds': 86400, 'time': '2024-01-01T00:00:00Z'})
        self.assertEqual(out['diagnostics']['parameter_provenance']['interest.pass_through']['status'], 'estimated')
        self.assertEqual(out['diagnostics']['parameter_provenance']['bank.annual_rate']['status'], 'assumed')
        self.assertNotEqual(out['pressures'][0]['value']['snapshot']['annual_rate'], .05)


if __name__ == '__main__':
    unittest.main()
