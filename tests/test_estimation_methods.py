import json
import math
import random
import unittest
from copy import deepcopy
from pathlib import Path

from worldmodel.estimation import linalg as la
from worldmodel.estimation.adjustment import fit_error_correction, fit_partial_adjustment, error_correction_predict
from worldmodel.estimation.bootstrap import bootstrap, resample_indices
from worldmodel.estimation.discrete import binomial_glm, discrete_hazard, gravity_ppml, poisson_rate
from worldmodel.estimation.distributions import chi2_sf, f_sf, t_cdf, norm_ppf
from worldmodel.estimation.growth import fit_exponential_growth, fit_logistic_growth, logistic_value
from worldmodel.estimation.kalman import fit_local_level, local_level_filter, local_level_forecast
from worldmodel.estimation.regression import ols, tsls, delta_method, wald_test
from worldmodel.estimation.simulation import abc_rejection, config_simulator, smm
from worldmodel.estimation.spec import ParameterSpec
from worldmodel.estimation.timeseries import (arima_forecast, fit_ar, fit_arima, fit_var, select_ar_order,
                                              select_var_order, var_forecast, ar_forecast)

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


class DistributionTests(unittest.TestCase):
    def test_reference_quantiles(self):
        self.assertAlmostEqual(t_cdf(2.228138852, 10), 0.975, places=6)
        self.assertAlmostEqual(t_cdf(-2.776445105, 4), 0.025, places=6)
        self.assertAlmostEqual(chi2_sf(3.841458821, 1), 0.05, places=6)
        self.assertAlmostEqual(chi2_sf(5.991464547, 2), 0.05, places=6)
        self.assertAlmostEqual(f_sf(4.351243478, 1, 20), 0.05, places=5)
        self.assertAlmostEqual(norm_ppf(0.975), 1.959963985, places=6)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(3)
        self.x = [[1.0, rng.gauss(0, 1)] for _ in range(800)]
        self.y = [2 + 3 * row[1] + rng.gauss(0, 1 + abs(row[1])) for row in self.x]

    def test_ols_recovery_and_classical_se_formula(self):
        fit = ols(self.y, self.x, names=['c', 'b'])
        self.assertAlmostEqual(fit['params']['b'], 3, delta=0.15)
        xs = [r[1] for r in self.x]
        mean = sum(xs) / len(xs)
        sxx = sum((v - mean) ** 2 for v in xs)
        self.assertAlmostEqual(fit['bse']['b'], math.sqrt(fit['sigma2'] / sxx), places=10)

    def test_robust_covariances_are_consistent(self):
        hc1 = ols(self.y, self.x, cov_type='HC1')
        hac0 = ols(self.y, self.x, cov_type='HAC', hac_lags=0)
        for a, b in zip(hc1['bse'].values(), hac0['bse'].values()):
            self.assertAlmostEqual(a, b, places=12)
        hc3 = ols(self.y, self.x, cov_type='HC3')
        self.assertGreater(hc3['bse']['x1'], ols(self.y, self.x, cov_type='HC0')['bse']['x1'])
        self.assertGreater(ols(self.y, self.x, cov_type='HC0')['bse']['x1'], ols(self.y, self.x)['bse']['x1'])

    def test_wls_and_wald_and_delta_method(self):
        weights = [1 / (1 + abs(r[1])) ** 2 for r in self.x]
        fit = ols(self.y, self.x, weights=weights, names=['c', 'b'])
        self.assertAlmostEqual(fit['params']['b'], 3, delta=0.1)
        test = wald_test(fit, [{'b': 1.0}], [3.0])
        self.assertGreater(test['chi2_pvalue'], 0.01)
        values, ses, _ = delta_method(lambda p: [p[1] / p[0]], [fit['params']['c'], fit['params']['b']], fit['cov']['matrix'])
        self.assertAlmostEqual(values[0], 1.5, delta=0.1)
        self.assertGreater(ses[0], 0)

    def test_two_stage_least_squares_removes_endogeneity_bias(self):
        rng = random.Random(9)
        z = [rng.gauss(0, 1) for _ in range(3000)]
        u = [rng.gauss(0, 1) for _ in range(3000)]
        x = [zi + 0.8 * ui + rng.gauss(0, 0.5) for zi, ui in zip(z, u)]
        y = [1 + 2 * xi + ui for xi, ui in zip(x, u)]
        naive = ols(y, [[1.0, v] for v in x])['params']['x1']
        fit = tsls(y, [[1.0] for _ in x], [[v] for v in x], [[v] for v in z], exog_names=['c'], endog_names=['x'])
        self.assertGreater(naive - 2, 0.2)
        self.assertAlmostEqual(fit['params']['x'], 2, delta=0.08)
        self.assertGreater(fit['first_stage'][0]['partial_f'], 10)
        self.assertIsNone(fit['overidentification'])

    def test_singular_design_is_explicit(self):
        with self.assertRaisesRegex(ValueError, 'singular'):
            ols([1, 2, 3, 4], [[1, 1], [1, 1], [1, 1], [1, 1]])


class TimeSeriesTests(unittest.TestCase):
    def test_ar_recovery_bic_selection_and_forecast_variance(self):
        rng = random.Random(1)
        y = [0.0, 0.0]
        for _ in range(1500):
            y.append(0.3 + 0.5 * y[-1] + 0.3 * y[-2] + rng.gauss(0, 1))
        self.assertEqual(select_ar_order(y, 5)['order'], 2)
        fit = fit_ar(y, 2)
        self.assertAlmostEqual(fit['params']['phi1'], 0.5, delta=0.06)
        self.assertAlmostEqual(fit['params']['phi2'], 0.3, delta=0.06)
        forecast = ar_forecast(fit, y, 3)
        self.assertLess(forecast['sd'][0], forecast['sd'][2])

    def test_arima_ma_recovery(self):
        rng = random.Random(2)
        e = [rng.gauss(0, 1) for _ in range(1200)]
        y = [0.0]
        for t in range(1, 1200):
            y.append(y[-1] + e[t] + 0.5 * e[t - 1])
        fit = fit_arima(y, (0, 1, 1))
        self.assertAlmostEqual(fit['ma'][0], 0.5, delta=0.08)
        forecast = arima_forecast(fit, y, 4)
        self.assertEqual(len(forecast['mean']), 4)
        self.assertLess(forecast['sd'][0], forecast['sd'][3])

    def test_var_recovery_and_selection(self):
        rng = random.Random(4)
        data = [[0.0, 0.0]]
        for _ in range(1500):
            p = data[-1]
            data.append([0.5 * p[0] + 0.1 * p[1] + rng.gauss(0, 1), 0.2 * p[0] + 0.3 * p[1] + rng.gauss(0, 1)])
        self.assertEqual(select_var_order(data, 4)['order'], 1)
        fit = fit_var(data, 1)
        for got, want in zip(sum(fit['lags'][0], []), [0.5, 0.1, 0.2, 0.3]):
            self.assertAlmostEqual(got, want, delta=0.06)
        self.assertTrue(fit['stable'])
        forecast = var_forecast(fit, data, 3)
        self.assertLess(forecast['sd'][0][0], forecast['sd'][2][0])


class AdjustmentTests(unittest.TestCase):
    def test_partial_adjustment_and_error_correction_recovery(self):
        rng = random.Random(5)
        x = [rng.gauss(2, 1) for _ in range(1000)]
        y = [5.0]
        for t in range(1, 1000):
            y.append(1 + 0.6 * y[-1] + 0.8 * x[t] + rng.gauss(0, 0.2))
        fit = fit_partial_adjustment(y, x)
        self.assertAlmostEqual(fit['long_run_response'], 2.0, delta=0.1)
        self.assertAlmostEqual(fit['half_life_periods'], math.log(0.5) / math.log(0.6), delta=0.3)
        policy = [2.0]
        for _ in range(999):
            policy.append(max(0, policy[-1] + rng.gauss(0, 0.3)))
        rate = [5.0]
        for t in range(1, 1000):
            rate.append(rate[-1] + 0.5 * (policy[t] - policy[t - 1]) - 0.25 * (rate[-1] - 3 - 1.0 * policy[t - 1]) + rng.gauss(0, 0.05))
        ecm = fit_error_correction(rate, policy)
        self.assertAlmostEqual(ecm['pass_through'], 1.0, delta=0.03)
        self.assertAlmostEqual(ecm['spread'], 3.0, delta=0.1)
        self.assertAlmostEqual(ecm['adjustment_speed'], 0.25, delta=0.04)
        self.assertTrue(ecm['error_correcting'])
        prediction = error_correction_predict(ecm, rate[:-1], policy[:-1], policy[-1])
        self.assertLess(abs(prediction - rate[-1]), 0.3)


class DiscreteTests(unittest.TestCase):
    def test_logit_and_grouped_cloglog_hazard(self):
        rng = random.Random(6)
        rows, outcomes = [], []
        for _ in range(4000):
            v = rng.gauss(0, 1)
            rows.append([1.0, v])
            outcomes.append(1.0 if rng.random() < 1 / (1 + math.exp(-(-1 + 2 * v))) else 0.0)
        fit = binomial_glm(outcomes, rows, names=['c', 'b'])
        self.assertAlmostEqual(fit['params']['b'], 2, delta=0.2)
        self.assertAlmostEqual(fit['params']['c'], -1, delta=0.15)
        events, exposures, design = [], [], []
        for _ in range(300):
            v = rng.uniform(0, 2)
            p = 1 - math.exp(-math.exp(-3 + 1.0 * v))
            n = 2000
            events.append(sum(rng.random() < p for _ in range(n)))
            exposures.append(n)
            design.append([1.0, v])
        hazard = discrete_hazard(events, exposures, design, names=['c', 'b'])
        self.assertAlmostEqual(hazard['params']['b'], 1.0, delta=0.08)
        self.assertAlmostEqual(hazard['params']['c'], -3.0, delta=0.1)
        with self.assertRaises(ValueError):
            discrete_hazard([5], [2], [[1.0]])

    def test_separation_is_reported(self):
        with self.assertRaisesRegex(ValueError, 'separation'):
            binomial_glm([0, 0, 0, 1, 1, 1], [[1, -3], [1, -2], [1, -1], [1, 1], [1, 2], [1, 3]])

    def test_ppml_gravity_recovers_elasticities(self):
        rng = random.Random(7)
        flows, origins, destinations, dist, mo, md = [], [], [], [], [], []
        for i in range(40):
            for j in range(40):
                if i == j:
                    continue
                a, b, d = rng.uniform(1, 100), rng.uniform(1, 100), rng.uniform(10, 5000)
                flows.append(math.exp(0.5 + 0.8 * math.log(a) + 0.9 * math.log(b) - 1.1 * math.log(d)) * rng.gammavariate(10, 0.1))
                origins.append(i); destinations.append(j); dist.append(d); mo.append(a); md.append(b)
        fit = gravity_ppml(flows, origins=origins, destinations=destinations, distance=dist, origin_mass=mo, destination_mass=md)
        self.assertAlmostEqual(fit['distance_elasticity'], -1.1, delta=0.06)
        self.assertAlmostEqual(fit['params']['log_origin_mass'], 0.8, delta=0.06)
        self.assertEqual(poisson_rate(0, 10)['interval'][0], 0.0)


class GrowthAndKalmanTests(unittest.TestCase):
    def test_growth_models(self):
        rng = random.Random(8)
        years = list(range(1950, 2021))
        values = [1e6 * math.exp(0.012 * (y - 1950) + rng.gauss(0, 0.005)) for y in years]
        self.assertAlmostEqual(fit_exponential_growth(years, values)['rate_per_year'], 0.012, delta=0.0005)
        logistic = [logistic_value(y, 1000, 0.15, 2000) * math.exp(rng.gauss(0, 0.01)) for y in range(1960, 2041)]
        fit = fit_logistic_growth(list(range(1960, 2041)), logistic)
        self.assertAlmostEqual(fit['capacity'], 1000, delta=40)
        self.assertAlmostEqual(fit['rate_per_year'], 0.15, delta=0.02)

    def test_filter_matches_hand_computation_and_skips_missing(self):
        result = local_level_filter([1.0, 2.0], 1.0, 0.5, a0=0.0, p0=10.0)
        self.assertAlmostEqual(result['filtered'][0], 10 / 11)
        self.assertAlmostEqual(result['filtered'][1], 1.547170, places=5)
        self.assertAlmostEqual(result['filtered_var'][1], 0.584906, places=5)
        f = 1.409091 + 1.0
        v = 2.0 - 10 / 11
        self.assertAlmostEqual(result['loglik'], -0.5 * (math.log(2 * math.pi) + math.log(f) + v * v / f), places=4)
        gap = local_level_filter([1.0, None, 3.0], 1.0, 1.0)
        self.assertIsNone(gap['innovations'][1])

    def test_local_level_mle(self):
        rng = random.Random(11)
        level, y = 0.0, []
        for _ in range(600):
            level += rng.gauss(0, 0.5)
            y.append(level + rng.gauss(0, 1.0))
        fit = fit_local_level(y)
        self.assertAlmostEqual(fit['sigma2_eta'], 0.25, delta=0.15)
        self.assertAlmostEqual(fit['sigma2_eps'], 1.0, delta=0.3)
        self.assertEqual(len(local_level_forecast(fit, 3)['sd']), 3)


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_se_and_determinism(self):
        rng = random.Random(12)
        rows = [rng.gauss(0, 1) for _ in range(200)]
        statistic = lambda sample: {'mean': sum(sample) / len(sample)}
        iid = bootstrap(rows, statistic, method='iid', replications=400, seed=5)
        self.assertAlmostEqual(iid['summary']['mean']['se'], 1 / math.sqrt(200), delta=0.015)
        self.assertEqual(bootstrap(rows, statistic, replications=100, seed=5), bootstrap(rows, statistic, replications=100, seed=5))
        for method in ('moving', 'circular', 'stationary'):
            indices = resample_indices(50, random.Random(1), method=method, block_length=5)
            self.assertEqual(len(indices), 50)
            self.assertTrue(all(0 <= i < 50 for i in indices))


class SimulationTests(unittest.TestCase):
    @staticmethod
    def ar_moments(parameters, seed):
        rng = random.Random(seed)
        y, value = [], 0.0
        for _ in range(600):
            value = parameters['phi'] * value + rng.gauss(0, 1)
            y.append(value)
        mean = sum(y) / len(y)
        var = sum((v - mean) ** 2 for v in y) / len(y)
        acf = lambda k: sum((y[t] - mean) * (y[t - k] - mean) for t in range(k, len(y))) / len(y) / var
        return {'variance': var, 'acf1': acf(1), 'acf2': acf(2)}

    def test_smm_recovers_ar_coefficient_with_standard_errors(self):
        targets = self.ar_moments({'phi': 0.6}, 999)
        spec = ParameterSpec('phi', '1', lower=-0.95, upper=0.95)
        omega = {'matrix': [[0.02, 0, 0], [0, 0.0015, 0], [0, 0, 0.002]]}
        result = smm(self.ar_moments, targets, [spec], weighting='optimal', target_cov=omega, simulations=4, seed=3, max_evaluations=200)
        self.assertAlmostEqual(result['parameters']['phi'], 0.6, delta=0.07)
        self.assertGreater(result['standard_errors']['phi'], 0)
        self.assertIsNotNone(result['j_test'])
        again = smm(self.ar_moments, targets, [spec], weighting='optimal', target_cov=omega, simulations=4, seed=3, max_evaluations=200)
        self.assertEqual(result['parameters'], again['parameters'])

    def test_abc_posterior_is_seeded_and_near_truth(self):
        def simulate(parameters, seed):
            rng = random.Random(seed)
            return {'mean': sum(rng.gauss(parameters['mu'], 1) for _ in range(50)) / 50}
        spec = ParameterSpec('mu', '1', prior={'distribution': 'normal', 'mean': 0, 'sd': 2})
        result = abc_rejection(simulate, {'mean': 1.5}, [spec], draws=3000, accept_fraction=0.03, seed=4)
        self.assertAlmostEqual(result['posterior']['mu']['mean'], 1.5, delta=0.25)
        self.assertEqual(result, abc_rejection(simulate, {'mean': 1.5}, [spec], draws=3000, accept_fraction=0.03, seed=4))

    def test_existing_simulator_as_black_box(self):
        from worldmodel.coupled_economy import simulate_coupled_economy
        baseline = json.loads((EXAMPLES / 'sensitivity-coupled-economy.json').read_text())['baseline']
        original = deepcopy(baseline)
        simulate = config_simulator(simulate_coupled_economy, baseline, {'production': ['policies', 0, 'firms', 'firm', 'production']},
                                    {'inventory': {'path': ['firms', 0, 'inventory'], 'reduce': 'last'}},
                                    transforms={'production': lambda v: int(round(v))})
        expected = simulate_coupled_economy(baseline)['firms'][0]['inventory']
        self.assertEqual(simulate({'production': 4.2}, 0)['inventory'], expected)
        self.assertEqual(baseline, original)
        spec = ParameterSpec('production', 'units', lower=0, upper=10)
        result = abc_rejection(simulate, {'inventory': expected}, [spec], draws=60, accept_fraction=0.2, seed=2)
        self.assertEqual(result['draws'][0]['_distance'], 0.0)


class OptionalAccelerationTests(unittest.TestCase):
    @unittest.skipIf(la._np is None, 'NumPy not installed; pure-Python reference path is used')
    def test_numpy_inverse_matches_reference(self):
        rng = random.Random(1)
        n = la.ACCELERATION_THRESHOLD + 2
        a = [[rng.gauss(0, 1) + (n if i == j else 0) for j in range(n)] for i in range(n)]
        accelerated = la.inverse(a)
        product = la.matmul(a, accelerated)
        self.assertAlmostEqual(product[3][3], 1.0, places=8)
        self.assertIsInstance(accelerated[0][0], float)

    def test_backend_reports_available_path(self):
        self.assertIn(la.backend(), ('python', 'numpy'))


if __name__ == '__main__':
    unittest.main()
