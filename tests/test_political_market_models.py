import json
import math
from pathlib import Path
import random
from statistics import correlation
import tempfile
from types import SimpleNamespace
import unittest

from worldmodel import models
from worldmodel.models import (assets, commodities, conflict, elections, influence, legislative, market_abm, monetary, regional,
                               sanctions, trade)
from worldmodel.models.base import numpy_available, ols, poisson_fe
from worldmodel.processes import ProcessRegistry

PROJECT = Path(__file__).resolve().parents[1]


class FamilyContractTests(unittest.TestCase):
    def test_every_family_declares_estimable_parameters_requirements_and_is_unvalidated(self):
        ids = set()
        for family in models.families():
            ids.add(family['id'])
            self.assertIs(family['validated'], False)
            self.assertTrue(family['requirements'])
            for requirement in family['requirements']:
                self.assertTrue(requirement['url'].startswith('https://'))
                self.assertTrue(requirement['series'])
            for spec in family['parameters'].values():
                self.assertIn('unit', spec)
            module = models.family_module(family['id'])
            for name in ('fit', 'simulate', 'synthetic'):
                self.assertTrue(callable(getattr(module, name)))
            hooks = models.parameter_hooks(family['id'])
            self.assertEqual(hooks['parameters'], family['parameters'])
        self.assertEqual(ids, set(models.FAMILY_MODULES))

    def test_examples_run_in_declared_modes(self):
        paths = sorted(PROJECT.glob('examples/models-*.json'))
        families = set()
        for path in paths:
            request = json.loads(path.read_text())
            if 'family' not in request:
                continue
            families.add(request['family'])
            report = models.simulate(request['family'], request['config'], request['mode'], request['seed'])
            json.dumps(report, allow_nan=False)
        self.assertEqual(families, set(models.FAMILY_MODULES))

    def test_cutoff_excludes_future_rows_and_evidence_records_windows(self):
        data = monetary.synthetic(1, periods=120)['data']
        result = monetary.fit(data, cutoff='1995-06-01')
        window = result['evidence']['data_windows'][0]
        self.assertGreater(window['rows_excluded_after_cutoff'], 0)
        self.assertLessEqual(window['latest_time_used'][:10], '1995-06-01')
        self.assertIs(result['evidence']['validated'], False)
        with self.assertRaises(ValueError):
            models.fit('unknown', {}, None)


class LegislativeTests(unittest.TestCase):
    def test_ideal_points_recover_truth_with_anchor_orientation(self):
        fixture = legislative.synthetic(1)
        result = legislative.fit(fixture['data'])
        estimate, truth = result['estimate']['ideal_points'], fixture['truth']['ideal_points']
        ids = sorted(estimate)
        self.assertGreater(correlation([truth[i][0] for i in ids], [estimate[i][0] for i in ids]), 0.95)
        self.assertGreater(estimate['m001'][0], 0)
        self.assertTrue(result['diagnostics']['converged'])
        self.assertGreater(result['diagnostics']['apre'], 0.3)

    def test_two_dimensional_scaling_classifies_votes(self):
        fixture = legislative.synthetic(3, members=40, rollcalls=120, dims=2)
        result = legislative.fit(fixture['data'])
        self.assertEqual(len(next(iter(result['estimate']['ideal_points'].values()))), 2)
        self.assertGreater(result['diagnostics']['classification_accuracy'], 0.75)

    def test_exact_passage_matches_monte_carlo_including_committee_gate(self):
        config = legislative.synthetic(1)['config']
        config['n_simulations'] = 6000
        exact = legislative.simulate(config)['passage_probability']
        sampled = legislative.simulate(config, 'stochastic', seed=5)
        self.assertLess(abs(exact - sampled['simulated_passage_frequency']), 4 * sampled['monte_carlo_standard_error'] + 1e-3)

    def test_coalitions_thresholds_and_discipline(self):
        members = [{'id': f'm{i}', 'party': 'A' if i < 6 else 'B', 'ideal_point': [(-1 if i < 6 else 1) * 0.2]} for i in range(10)]
        bill = {'proposal': [-0.5], 'status_quo': [0.5]}
        free = legislative.member_probabilities({'members': members, 'bill': bill, 'parameters': {'discipline': 0}})[0]
        whipped = legislative.member_probabilities({'members': members, 'bill': bill, 'parameters': {'discipline': 3}})[0]
        self.assertGreater(whipped['m0'], free['m0'])
        self.assertLess(whipped['m9'], free['m9'])
        everyone = legislative.simulate({'members': members, 'bill': bill, 'coalition': {'yea': [m['id'] for m in members]},
                                         'chambers': [{'id': 'senate', 'threshold': {'type': 'fraction', 'value': 0.6}}]})
        self.assertAlmostEqual(everyone['passage_probability'], 1.0)
        with self.assertRaises(ValueError):
            legislative.simulate({'members': members, 'bill': bill, 'coalition': {'yea': ['m0'], 'nay': ['m0']}})

    def test_voteview_cast_codes(self):
        data = legislative.from_voteview(
            [{'congress': 118, 'chamber': 'House', 'rollnumber': 1, 'icpsr': 7, 'cast_code': 1},
             {'congress': 118, 'chamber': 'House', 'rollnumber': 1, 'icpsr': 8, 'cast_code': 6},
             {'congress': 118, 'chamber': 'House', 'rollnumber': 1, 'icpsr': 9, 'cast_code': 9}],
            [{'congress': 118, 'chamber': 'House', 'icpsr': i, 'party_code': 100} for i in (7, 8, 9)],
            [{'congress': 118, 'chamber': 'House', 'rollnumber': 1, 'date': '2023-01-03'}])
        self.assertEqual([v['vote'] for v in data['votes']], [1, 0])


class ElectionTests(unittest.TestCase):
    def test_fit_recovers_district_coefficients_and_variance(self):
        fixture = elections.synthetic(2, cycles=12, districts=150)
        result = elections.fit(fixture['data'])
        truth, estimate = fixture['truth'], result['estimate']
        self.assertAlmostEqual(estimate['coefficients']['pvi'], truth['coefficients']['pvi'], delta=0.05)
        self.assertAlmostEqual(estimate['coefficients']['incumbent'], truth['coefficients']['incumbent'], delta=0.01)
        self.assertAlmostEqual(estimate['coefficients']['fundraising'], truth['coefficients']['fundraising'], delta=0.005)
        self.assertAlmostEqual(estimate['sigma_district'], truth['sigma_district'], delta=0.005)
        self.assertEqual(result['diagnostics']['share_model']['se_type'], 'cluster_robust_cr1')

    def test_national_swing_variance_uses_between_cycle_degrees_of_freedom(self):
        data = elections.synthetic(0, cycles=3)['data']
        few = elections.fit(data)
        self.assertEqual(few['diagnostics']['cycle_level_regressors'], 3)
        self.assertEqual(few['diagnostics']['between_cycle_dof'], 0)
        self.assertIsNone(few['estimate']['sigma_national'])
        many = elections.fit(elections.synthetic(0, cycles=16)['data'])
        self.assertEqual(many['diagnostics']['between_cycle_dof'], 13)
        self.assertAlmostEqual(many['estimate']['sigma_national'], 0.03, delta=0.012)

    def test_seat_distribution_is_consistent_across_modes_and_responds_to_incumbency(self):
        config = elections.synthetic(0)['config']
        exact = elections.simulate(config)
        distribution = exact['dem_seat_distribution']
        self.assertAlmostEqual(sum(distribution), 1.0, places=9)
        self.assertAlmostEqual(sum(k * p for k, p in enumerate(distribution)), exact['expected_dem_seats'], delta=0.1)
        sampled = elections.simulate({**config, 'n_simulations': 4000}, 'stochastic', seed=3)
        self.assertAlmostEqual(sampled['simulated_dem_majority_frequency'], exact['dem_majority_probability'], delta=0.05)
        incumbents = {**config, 'districts': [dict(d, incumbent=1) for d in config['districts']]}
        self.assertGreater(elections.simulate(incumbents)['expected_dem_seats'], exact['expected_dem_seats'])


class InfluenceTests(unittest.TestCase):
    def test_two_way_fixed_effects_remove_unit_confounding_and_label_correlational(self):
        fixture = influence.synthetic(3)
        result = influence.fit(fixture['data'])
        self.assertAlmostEqual(result['estimate']['influence_coefficient'], 0.3, delta=0.06)
        self.assertEqual(result['evidence']['identification'], 'correlational')
        rows = fixture['data']['panel']
        pooled = ols([[1.0, r['exposure']] for r in rows], [r['outcome'] for r in rows])
        self.assertGreater(pooled['coefficients']['x1'], 0.6)
        iv = influence.fit({**fixture['data'], 'design': {'type': 'instrumental_variable', 'instrument': 'z'}})
        self.assertIn('instrumental_variable', iv['evidence']['identification'])

    def test_money_networks_conserve_amounts_including_unattributed_transfers(self):
        config = influence.synthetic(0)['config']
        report = influence.simulate(config)
        totals = report['lobbying']['totals']
        self.assertAlmostEqual(totals['client_to_registrant'], totals['registrant_to_issue'])
        self.assertEqual(report['lobbying']['filings_without_amount'], 1)
        network = influence.contribution_network([{'donor': 'a', 'committee': 'c', 'amount': 100}],
                                                 [{'committee': 'c', 'candidate': 'k', 'amount': 150}])
        attributed = {(e['source'], e['target']): e['amount'] for e in network['attributed_edges']}
        self.assertAlmostEqual(attributed[('donor:a', 'candidate:k')], 100)
        self.assertAlmostEqual(attributed[('unattributed:committee:c', 'candidate:k')], 50)
        exposure = report['campaign_finance']['exposure']['targets']['candidate:X']
        self.assertAlmostEqual(sum(exposure['attribute_exposure'].values()), 1.0)


class TradeTests(unittest.TestCase):
    def test_ppml_gravity_recovers_structural_coefficients(self):
        fixture = trade.synthetic(4, countries=10, years=3)
        result = trade.fit(fixture['data'])
        coefficients = result['estimate']['coefficients']
        self.assertAlmostEqual(coefficients['ln_distance'], -0.9, delta=0.12)
        self.assertAlmostEqual(result['estimate']['trade_elasticity'], 4.0, delta=0.8)
        self.assertTrue(result['diagnostics']['converged'])

    def test_zero_shock_is_identity_and_accounting_closes(self):
        config = trade.example_config()
        report = trade.counterfactual(config['baseline'], {}, config['parameters'])
        self.assertTrue(all(abs(v) < 1e-10 for v in report['real_income_change'].values()))
        for checks in report['accounting'].values():
            self.assertTrue(all(v < 1e-8 for v in checks.values()))

    def test_tariff_diverts_trade_and_prohibitive_sanction_closes_route(self):
        config = trade.example_config()
        report = trade.simulate(config)
        self.assertLess(report['bilateral_trade_change']['CHN->USA'], -0.1)
        self.assertGreater(report['bilateral_trade_change']['ROW->USA'], 0)
        self.assertGreater(report['consumer_price_index_change']['USA'], 0)
        partial = trade.simulate({**config, 'fidelity': 'partial'})
        self.assertLess(partial['bilateral_trade_change']['CHN->USA'], report['bilateral_trade_change']['CHN->USA'])
        shock = {'trade_cost_hat': [{'exporter': 'USA', 'importer': 'CHN', 'multiplier': 1e6}]}
        closed = trade.counterfactual(config['baseline'], shock, config['parameters'])
        self.assertLess(closed['bilateral_trade_change']['USA->CHN'], -0.999)
        self.assertLess(closed['accounting']['counterfactual']['trade_balance_plus_deficit_max_residual'], 1e-8)

    def test_symmetric_economies_respond_symmetrically_and_inconsistent_baselines_fail(self):
        baseline = {'countries': ['A', 'B'], 'sectors': ['x'], 'flows': {'x': {'A': {'A': 80, 'B': 20}, 'B': {'A': 20, 'B': 80}}}}
        shock = {'tariffs': [{'importer': 'A', 'exporter': 'B', 'tariff': 0.1}, {'importer': 'B', 'exporter': 'A', 'tariff': 0.1}]}
        report = trade.counterfactual(baseline, shock, {'trade_elasticity': 4})
        self.assertAlmostEqual(report['real_income_change']['A'], report['real_income_change']['B'], places=10)
        self.assertLess(report['real_income_change']['A'], 0)
        exporter = {**baseline, 'io_coefficients': {'A': {'x': {'x': 0.5}}, 'B': {'x': {'x': 0.5}}},
                    'flows': {'x': {'A': {'A': 10, 'B': 90}, 'B': {'A': 5, 'B': 95}}}}
        with self.assertRaisesRegex(ValueError, 'negative final demand'):
            trade.counterfactual(exporter, {}, {})
        with self.assertRaisesRegex(ValueError, 'Value-added'):
            trade.counterfactual({**baseline, 'io_coefficients': {'A': {'x': {'x': 1.0}}}}, {}, {})

    @unittest.skipUnless(numpy_available(), 'numpy not installed')
    def test_numpy_paths_match_pure_python(self):  # pragma: no cover - optional dependency
        config = trade.example_config()
        pure = trade.counterfactual(config['baseline'], config['shock'], config['parameters'], backend='python')
        fast = trade.counterfactual(config['baseline'], config['shock'], config['parameters'], backend='numpy')
        for country, value in pure['real_income_change'].items():
            self.assertAlmostEqual(value, fast['real_income_change'][country], places=8)
        fixture = legislative.synthetic(2, members=30, rollcalls=60)
        python = legislative.estimate_ideal_points(fixture['data'], backend='python', anchors=['m001'])
        vector = legislative.estimate_ideal_points(fixture['data'], backend='numpy', anchors=['m001'])
        ids = sorted(python['ideal_points'])
        self.assertGreater(correlation([python['ideal_points'][i][0] for i in ids], [vector['ideal_points'][i][0] for i in ids]), 0.999)


class SanctionsTests(unittest.TestCase):
    OWNERSHIP = [{'owner': 'X', 'owned': 'A', 'share': 0.5}, {'owner': 'A', 'owned': 'C', 'share': 0.5},
                 {'owner': 'X', 'owned': 'D', 'share': 0.25}, {'owner': 'Y', 'owned': 'D', 'share': 0.25},
                 {'owner': 'X', 'owned': 'E', 'share': 0.4}, {'owner': 'E', 'owned': 'F', 'share': 1.0}]

    def test_fifty_percent_rule_aggregates_only_through_blocked_owners(self):
        result = sanctions.propagate_blocking(self.OWNERSHIP, ['X'])
        self.assertEqual(set(result['blocked']), {'X', 'A', 'C'})
        self.assertIn('D', sanctions.propagate_blocking(self.OWNERSHIP, ['X', 'Y'])['blocked'])
        look = sanctions.look_through_exposure(self.OWNERSHIP, ['X'])['exposure']
        self.assertAlmostEqual(look['F'], 0.4)
        self.assertAlmostEqual(look['C'], 0.25)
        controlled = sanctions.propagate_blocking(self.OWNERSHIP, ['X'], control=[{'controller': 'X', 'controlled': 'E'}], apply_control=True)
        self.assertEqual(controlled['blocked']['E']['basis'], 'control')
        self.assertIn('F', controlled['blocked'])
        with self.assertRaisesRegex(ValueError, '100 percent'):
            sanctions.propagate_blocking([{'owner': 'X', 'owned': 'A', 'share': 0.7}, {'owner': 'Y', 'owned': 'A', 'share': 0.7}], ['X'])

    def test_exposure_categories_routes_and_trade_coupling(self):
        report = sanctions.simulate(sanctions.synthetic(5)['config'])
        bank = report['counterparty_exposure']['Bank1']
        self.assertAlmostEqual(bank['blocked'] + bank['look_through'] + bank['clean'], bank['total'])
        pair = report['routes']['pairs'][0]
        self.assertEqual(pair['status'], 'rerouted')
        self.assertAlmostEqual(pair['trade_cost_multiplier'], math.exp(0.5 * 0.2))
        self.assertLess(report['trade_effects']['bilateral_trade_change']['CHN->USA'], 0)
        none = sanctions.route_impacts([{'from': 'P', 'to': 'Q', 'cost': 1, 'operator': 'Z'}], [{'exporter': 'P', 'importer': 'Q'}], blocked={'Z'})
        self.assertEqual(none['pairs'][0]['status'], 'no_permitted_route')

    def test_stochastic_designations_and_gravity_fit(self):
        fixture = sanctions.synthetic(5)
        sampled = sanctions.simulate(fixture['config'], 'stochastic', seed=4)
        self.assertAlmostEqual(sampled['blocked_probability']['D'], 0.3, delta=0.08)
        fit = sanctions.fit(fixture['data'])
        self.assertAlmostEqual(fit['estimate']['sanction_coefficient'], -1.2, delta=0.45)


class ConflictTests(unittest.TestCase):
    def test_hawkes_em_recovers_excitation_and_decay(self):
        fixture = conflict.synthetic(6, countries=10, months=150)
        result = conflict.fit(fixture['data'])
        estimate = result['estimate']
        self.assertAlmostEqual(estimate['self_excitation'], 0.45, delta=0.1)
        self.assertAlmostEqual(estimate['neighbor_excitation'], 0.25, delta=0.12)
        self.assertAlmostEqual(estimate['decay'], 0.6, delta=0.15)
        self.assertTrue(result['diagnostics']['stationary'])

    def test_expectation_matches_simulation_and_diffusion_decays_with_distance(self):
        config = conflict.synthetic(0, countries=8, months=24)['config']
        config.update(horizon=6, n_simulations=1500)
        exact = conflict.simulate(config)
        sampled = conflict.simulate(config, 'stochastic', seed=2)
        first = config['countries'][0]
        shocked_total = exact['expected_total'][first] + config['shock']['events']
        self.assertAlmostEqual(sampled['total_events_quantiles'][first]['q50'], shocked_total + exact['diffusion_response'][first], delta=0.25 * shocked_total)
        response = exact['diffusion_response']
        self.assertGreater(response[config['countries'][1]], response[config['countries'][3]])
        self.assertGreater(response[config['countries'][3]], 0)

    def test_panel_contract_and_negative_binomial_alternative(self):
        fixture = conflict.synthetic(7, countries=6, months=60)
        rows = fixture['data']['events'][1:]
        with self.assertRaisesRegex(ValueError, 'balanced'):
            conflict.fit({**fixture['data'], 'events': rows})
        nb = conflict.fit({**fixture['data'], 'model': 'negative_binomial'})
        self.assertGreater(nb['estimate']['coefficients']['lag_log1p_count'], 0)


class AssetTests(unittest.TestCase):
    def test_factor_betas_and_garch_are_recovered(self):
        fixture = assets.synthetic(1)
        estimate = assets.fit(fixture['data'])['estimate']['symbols']
        for symbol, truth in fixture['truth'].items():
            self.assertAlmostEqual(estimate[symbol]['betas']['MKT'], truth['betas']['MKT'], delta=0.05)
            self.assertAlmostEqual(estimate[symbol]['garch']['alpha'], truth['garch'][0], delta=0.03)
            self.assertAlmostEqual(estimate[symbol]['garch']['beta'], truth['garch'][1], delta=0.04)

    def test_risk_forecasts_agree_across_modes(self):
        config = assets.synthetic(0, days=10)['config']
        exact = assets.simulate(config)
        path = exact['variance']['A']['daily_idiosyncratic_variance_path']
        self.assertLess(abs(path[-1] - 1e-4), abs(path[0] - 1e-4))
        sampled = assets.simulate({**config, 'n_simulations': 3000}, 'stochastic', seed=9)
        self.assertAlmostEqual(sampled['value_at_risk_95'], exact['portfolio']['value_at_risk_95'], delta=0.3 * exact['portfolio']['value_at_risk_95'])


class MarketAbmTests(unittest.TestCase):
    def test_both_clearing_mechanisms_conserve_cash_and_shares(self):
        config = market_abm.example_config()
        config['steps'] = 80
        initial_cash = sum(a['count'] * a['cash_cents'] for a in config['agents'])
        initial_shares = sum(a['count'] * a['shares'] for a in config['agents'])
        for clearing in ('walrasian', 'order_book'):
            report = market_abm.simulate({**config, 'clearing': clearing}, 'stochastic', seed=3)
            self.assertEqual(sum(a['cash'] for a in report['agents_final']), initial_cash)
            self.assertEqual(sum(a['shares'] for a in report['agents_final']), initial_shares)
            self.assertTrue(all(a['cash'] >= 0 and a['shares'] >= 0 for a in report['agents_final']))
            self.assertEqual(report, market_abm.simulate({**config, 'clearing': clearing}, 'stochastic', seed=3))

    def test_fundamentalists_anchor_price_and_chartists_add_volatility(self):
        config = {'initial_price_cents': 10000, 'fundamental': {'initial_cents': 12000}, 'steps': 20,
                  'agents': [{'type': 'fundamentalist', 'count': 10, 'cash_cents': 6_000_000, 'shares': 500}]}
        report = market_abm.simulate(config)
        self.assertLess(abs(report['prices_cents'][-1] / 12000 - 1), 0.05)
        base = market_abm.example_config()
        calm = market_abm.simulate({**base, 'parameters': {**base['parameters'], 'chartist_strength': 0.0}}, 'stochastic', seed=1)
        trend = market_abm.simulate({**base, 'parameters': {**base['parameters'], 'chartist_strength': 80.0}}, 'stochastic', seed=1)
        self.assertGreater(trend['moments']['sd'], calm['moments']['sd'])

    def test_simulated_method_of_moments_recovers_generating_grid_point(self):
        fixture = market_abm.synthetic(2)
        result = market_abm.fit(fixture['data'])
        self.assertEqual(result['estimate']['chartist_strength'], fixture['truth']['chartist_strength'])


class CommodityTests(unittest.TestCase):
    def test_balance_regressions_recover_elasticities_and_label_identification(self):
        fixture = commodities.synthetic(3)
        result = commodities.fit(fixture['data'])
        self.assertAlmostEqual(result['estimate']['storage_elasticity'], 2.0, delta=0.1)
        self.assertAlmostEqual(result['estimate']['demand_elasticity'], -0.3, delta=0.05)
        self.assertAlmostEqual(result['estimate']['target_stocks_to_use'], 0.2, delta=0.02)
        self.assertIn('instrumented', result['evidence']['identification'])
        ols_fit = commodities.fit({**fixture['data'], 'demand_instrument': None})
        self.assertEqual(ols_fit['evidence']['identification'], 'correlational')

    def test_identity_holds_and_supply_shock_raises_prices_and_draws_stocks(self):
        config = commodities.example_config()
        baseline = commodities.simulate({**config, 'supply_shocks': [1.0]})['periods']
        shocked = commodities.simulate(config)['periods']
        self.assertLess(commodities.simulate(config)['accounting']['max_identity_residual'], 1e-9)
        self.assertGreater(shocked[2]['price'], baseline[2]['price'])
        self.assertLess(shocked[2]['ending_stocks'], baseline[2]['ending_stocks'])


class MonetaryTests(unittest.TestCase):
    def test_taylor_and_nelson_siegel_estimates(self):
        fixture = monetary.synthetic(4)
        estimate = monetary.fit(fixture['data'])['estimate']
        truth = fixture['truth']
        self.assertAlmostEqual(estimate['rho'], truth['rho'], delta=0.05)
        self.assertAlmostEqual(estimate['phi_pi'], truth['phi_pi'], delta=0.25)
        self.assertAlmostEqual(estimate['phi_y'], truth['phi_y'], delta=0.15)
        self.assertAlmostEqual(estimate['ns_lambda'], truth['ns_lambda'], places=6)
        self.assertAlmostEqual(estimate['policy_loading'][1], 0.6, delta=0.1)
        factors, sse = monetary.fit_curve([0.25, 1, 2, 5, 10, 30], [sum(f * l for f, l in zip((4, -2, 1), monetary.ns_loadings(m, 0.6))) for m in [0.25, 1, 2, 5, 10, 30]], 0.6)
        self.assertLess(max(abs(a - b) for a, b in zip(factors, (4, -2, 1))), 1e-8)

    def test_lower_bound_binds_under_deflationary_path(self):
        config = monetary.synthetic(0)['config']
        config['paths'] = {'inflation': [-2.0], 'output_gap': [-6.0]}
        path = monetary.simulate(config)['path']
        self.assertTrue(path[-1]['at_lower_bound'])
        self.assertTrue(all(row['policy_rate'] >= 0.125 for row in path))


class RegionalTests(unittest.TestCase):
    def test_shift_share_and_migration_gravity_estimates(self):
        fixture = regional.synthetic(5)
        estimate = regional.fit(fixture['data'])['estimate']
        self.assertAlmostEqual(estimate['shift_share_elasticity'], fixture['truth']['shift_share_elasticity'], delta=0.12)
        self.assertAlmostEqual(estimate['destination_employment_elasticity'], 3.0, delta=0.5)
        self.assertAlmostEqual(estimate['distance_elasticity'], -1.1, delta=0.1)

    def test_population_accounting_is_conserved_and_growth_attracts_migrants(self):
        config = regional.synthetic(0)['config']
        deterministic = regional.simulate(config)
        for period in deterministic['periods']:
            self.assertLess(abs(period['accounting']['population_residual']), 1e-6)
        stochastic = regional.simulate(config, 'stochastic', seed=8)
        self.assertTrue(all(p['accounting'] == {'population_residual': 0, 'internal_migration_sum': 0} for p in stochastic['periods']))
        growth = {r['id']: sum(v * config['national_industry_growth'][0].get(k, 0) for k, v in r['industry_employment'].items()) / sum(r['industry_employment'].values())
                  for r in config['regions']}
        net = deterministic['periods'][0]['net_internal_migration']
        self.assertGreater(net[max(growth, key=growth.get)], net[min(growth, key=growth.get)])


class ProcessRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = models.register_model_processes(ProcessRegistry())

    def test_every_family_has_deterministic_and_stochastic_implementations(self):
        description = self.registry.describe()
        implementations = {i['id'] for i in description['implementations']}
        for family in models.FAMILY_MODULES:
            self.assertIn(f'{family}_model.deterministic', implementations)
            self.assertIn(f'{family}_model.stochastic', implementations)
        json.dumps(description, allow_nan=False)
        processes = {p['id']: p for p in description['processes']}
        for family in models.FAMILY_MODULES:
            self.assertEqual(set(processes[f'{family}_model']['inputs']), {f'{family}_config'})
            self.assertEqual(set(processes[f'{family}_model']['outputs']), {f'{family}_forecast'})
        result = self.registry.predict('monetary_model.stochastic', {'monetary_config': {'value': monetary.synthetic(0)['config'], 'unit': 'monetary-config'}},
                                       {}, {'dt_seconds': 86400, 'rng': random.Random(3)})
        self.assertEqual(result['pressures'][0]['port'], 'monetary_forecast')
        self.assertEqual(result['pressures'][0]['unit'], 'monetary-forecast')
        self.assertFalse(result['diagnostics']['validated'])

    def test_numeric_processes_integrate_through_the_materializer_with_conservation(self):
        from tests import test_materialize
        from worldmodel.materialize import materialize
        fixture = test_materialize.MaterializeTests()
        fixture.setUp()
        try:
            a, b = {'entity': 'asset:a', 'variable': 'population'}, {'entity': 'asset:b', 'variable': 'population'}
            rate = {'entity': 'asset:a', 'variable': 'policy_rate'}
            request = {'start': '2024-01-01T00:00:00Z', 'end': '2024-03-01T00:00:00Z', 'step_seconds': 30 * 86400, 'known_at': '2024-01-01T00:00:00Z',
                       'budget': 1000, 'targets': [a, b, rate],
                       'initial_state': [{**a, 'value': 1_000_000, 'unit': 'people'}, {**b, 'value': 500_000, 'unit': 'people'},
                                         {**rate, 'value': 5.0, 'unit': 'percent'}],
                       'bindings': [{'id': 'migration', 'process_id': 'two_region_migration', 'entity_id': 'asset:a', 'fidelity': 'deterministic',
                                     'inputs': {'employment_rate_a': {'value': 0.55, 'unit': 'ratio'}, 'employment_rate_b': {'value': 0.65, 'unit': 'ratio'},
                                                'base_outmigration_rate_a': {'value': 0.03, 'unit': 'per_year'}, 'base_outmigration_rate_b': {'value': 0.03, 'unit': 'per_year'}},
                                     'outputs': {'population_a': a, 'population_b': b}, 'parameters': {'outmigration_employment_rate_elasticity': -5.0}},
                                    {'id': 'policy', 'process_id': 'taylor_rule_policy_rate', 'entity_id': 'asset:a', 'fidelity': 'deterministic',
                                     'inputs': {'inflation': {'value': 1.0, 'unit': 'percent'}, 'output_gap': {'value': -1.0, 'unit': 'percent'}},
                                     'outputs': {'policy_rate': rate}, 'parameters': {'rho': 0.7}}]}
            view = materialize(fixture.store, fixture.graph, request, models.register_model_processes(ProcessRegistry()))
            population = {}
            for row in view['snapshots']:
                if row['variable'] == 'population':
                    population.setdefault(row['time'], 0.0)
                    population[row['time']] += row['value']
            self.assertTrue(all(abs(total - 1_500_000) < 1e-6 for total in population.values()))
            final = [row for row in view['snapshots'] if row['entity'] == 'asset:a' and row['variable'] == 'population'][-1]
            self.assertLess(final['value'], 1_000_000)
            rates = [row['value'] for row in view['snapshots'] if row['variable'] == 'policy_rate']
            self.assertLess(rates[-1], rates[0])
        finally:
            fixture.tearDown()


class CommandLineTests(unittest.TestCase):
    def args(self, **kwargs):
        defaults = dict(command='models', family=None, request=None, data=None, synthetic=False, cutoff=None, mode=None, seed=None,
                        focal_actor=None, publish=False, dataset=None)
        return SimpleNamespace(**{**defaults, **kwargs})

    def test_list_describe_fit_and_simulate_without_publishing(self):
        from worldmodel.models_cli import execute
        listed = execute(self.args(action='list'), None, None, PROJECT, None)
        self.assertEqual(len(listed), len(models.FAMILY_MODULES))
        described = execute(self.args(action='describe', family='sanctions'), None, None, PROJECT, None)
        self.assertEqual(described['process_id'], 'sanctions_model')
        fitted = execute(self.args(action='fit', family='monetary', synthetic=True, cutoff='2005-01-01'), None, None, PROJECT, None)
        self.assertIn('synthetic_truth', fitted)
        report = execute(self.args(action='simulate', request=PROJECT / 'examples/models-commodities.json'), None, None, PROJECT, None)
        self.assertEqual(report['family'], 'commodities')
        with self.assertRaisesRegex(ValueError, 'family'):
            execute(self.args(action='describe'), None, None, PROJECT, None)

    def test_published_simulation_is_an_immutable_report(self):
        from worldmodel.models_cli import execute
        from worldmodel.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'data')
            result = execute(self.args(action='simulate', request=PROJECT / 'examples/models-monetary.json', publish=True, dataset='monetary_run'),
                             None, store, PROJECT, None)
            store.verify(result['artifact'])
            self.assertEqual(result['family'], 'monetary')


if __name__ == '__main__':
    unittest.main()
