"""Fat-tail interval methods (interval wave, 2026-09-18): known coverage on synthetic data.

``student_t_mle``, ``empirical_quantile`` and ``conformal_rolling`` make no Gaussian
assumption. Each case below has a predictive distribution known by construction, so
"the interval is right" is checkable. The negative control from the earlier interval
tests is kept: an error scale that trends upward through the holdout must still defeat
every backward-looking method, including the conformal one.
"""
import math
import random
import unittest

from worldmodel.estimation import estimator_for, validate_process
from worldmodel.estimation import intervals as iv
from worldmodel.estimation.distributions import t_ppf
from worldmodel.estimation.regression import ols

from tests.test_estimation_intervals import (NOMINAL, TOLERANCE, _inventory_backtest, _inventory_records,
                                             _regional_rows, coverage)

FAT_TAIL = ('student_t_mle', 'empirical_quantile', 'conformal_rolling')


def _student_t(rng, df):
    return rng.gauss(0, 1) / math.sqrt(rng.gammavariate(df / 2, 2) / df)


class StudentTMaximumLikelihoodTests(unittest.TestCase):
    def test_recovers_degrees_of_freedom_and_scale(self):
        rng = random.Random(3)
        values = [2.0 * _student_t(rng, 4.0) for _ in range(6000)]
        scale, df = iv.student_t_mle(values)
        self.assertAlmostEqual(df, 4.0, delta=0.6)
        self.assertAlmostEqual(scale, 2.0, delta=0.1)

    def test_gaussian_sample_goes_to_the_upper_bound(self):
        rng = random.Random(5)
        scale, df = iv.student_t_mle([rng.gauss(0, 1.5) for _ in range(4000)])
        self.assertGreater(df, 30)
        self.assertAlmostEqual(scale, 1.5, delta=0.06)

    def test_is_deterministic_and_refuses_tiny_samples(self):
        rng = random.Random(8)
        values = [_student_t(rng, 3.0) for _ in range(300)]
        self.assertEqual(iv.student_t_mle(values), iv.student_t_mle(list(values)))
        with self.assertRaises(ValueError):
            iv.student_t_mle(values[:7])


class ConformalQuantileTests(unittest.TestCase):
    def test_order_statistics_use_the_finite_sample_ranks(self):
        values = list(range(1, 20))                     # n = 19: ranks floor(20*0.1)=2 and ceil(20*0.9)=18
        self.assertEqual(iv.conformal_quantile(values, 0.1), 2)
        self.assertEqual(iv.conformal_quantile(values, 0.9), 18)
        scale, nodes = iv.conformal_nodes([-v for v in values])      # predicted - actual orientation
        spec = iv.empirical(scale, nodes)
        low, high = iv.interval(spec, 0.0, 0.8)
        self.assertAlmostEqual(low, 2.0)
        self.assertAlmostEqual(high, 18.0)
        self.assertAlmostEqual(iv.quantile(spec, 0.0, 0.5), 10.0)

    def test_exchangeable_errors_cover_at_the_nominal_rate(self):
        """Split-conformal validity: n = 19 calibration errors give exactly 16/20 = 0.80 coverage."""
        rng = random.Random(11)
        hits, trials = 0, 6000
        for _ in range(trials):
            calibration = [_student_t(rng, 2.5) for _ in range(19)]
            scale, nodes = iv.conformal_nodes(calibration, sign=1.0)
            low, high = iv.interval(iv.empirical(scale, nodes), 0.0, 0.8)
            hits += low <= _student_t(rng, 2.5) <= high
        self.assertAlmostEqual(hits / trials, 0.80, delta=0.015)

    def test_too_few_errors_fail_closed(self):
        with self.assertRaises(ValueError):
            iv.conformal_nodes([1.0] * (iv.MIN_CONFORMAL_ERRORS - 1))
        with self.assertRaises(ValueError):
            iv.from_errors([1.0] * 40, method='conformal_rolling')           # no out-of-sample errors supplied


class FromErrorsTests(unittest.TestCase):
    def test_extra_components_widen_without_changing_the_shape(self):
        rng = random.Random(2)
        errors = [_student_t(rng, 3.0) for _ in range(500)]
        for method in FAT_TAIL:
            with self.subTest(method=method):
                kwargs = {'out_of_sample': errors[-200:]} if method == 'conformal_rolling' else {}
                plain = iv.from_errors(errors, method=method, **kwargs)
                # The extra component is added in quadrature to the residual scale.
                wider = iv.from_errors(errors, method=method, extra={'revision': plain['components']['residual']}, **kwargs)
                self.assertAlmostEqual(iv.standard_deviation(wider) / iv.standard_deviation(plain), math.sqrt(2), places=6)
                self.assertEqual(plain['family'], wider['family'])

    def test_in_sample_methods_apply_the_degrees_of_freedom_correction(self):
        errors = [(-1.0) ** i * (1 + i % 7) for i in range(40)]
        loose = iv.standard_deviation(iv.from_errors(errors, method='empirical_quantile', dof=0))
        tight = iv.standard_deviation(iv.from_errors(errors, method='empirical_quantile', dof=4))
        self.assertAlmostEqual(tight / loose, math.sqrt(40 / 36), places=9)

    def test_student_t_interval_uses_the_fitted_tails(self):
        rng = random.Random(4)
        errors = [_student_t(rng, 3.0) for _ in range(5000)]
        spec = iv.from_errors(errors, method='student_t_mle')
        self.assertEqual(spec['family'], 'student_t')
        high = iv.interval(spec, 0.0, 0.8)[1]
        self.assertAlmostEqual(high, t_ppf(0.9, 3.0), delta=0.08)


class RecursiveErrorTests(unittest.TestCase):
    """The conformal errors are genuine out-of-sample forecasts: each from a fit that excluded its target."""

    def test_incremental_normal_equations_match_refitting_every_prefix(self):
        def sigma(t, rng):
            return rng.gauss(0, 400.0)
        records, dates = _inventory_records(sigma, 160, seed=9)
        estimator = estimator_for('inventory_balance', options={'interval_method': 'conformal_rolling',
                                                                'interval_window': 30})
        frame = estimator.frame(records, cutoff=dates[-1].isoformat(), vintage_policy='strict')
        cols = estimator._columns(frame)
        options = estimator.options
        names = estimator.names(options)
        rows = [(t, estimator.response(cols, t, options), estimator.design(cols, t, options), [])
                for t in range(1, len(frame))]
        fast = estimator.recursive_errors(cols, rows, names, options)
        slow = []
        for k in range(len(rows) - 30, len(rows)):
            fit = ols([r[1] for r in rows[:k]], [r[2] for r in rows[:k]], names=names)
            t, _, row, _ = rows[k]
            predicted = estimator.level(cols, t, sum(fit['params'][n] * v for n, v in zip(names, row)), options)
            slow.append(predicted - cols[estimator.target][t])
        self.assertEqual(len(fast), 30)
        for a, b in zip(fast, slow):
            self.assertAlmostEqual(a, b, delta=1e-6 * max(1.0, abs(b)))

    def test_estimate_reports_the_out_of_sample_count(self):
        def sigma(t, rng):
            return rng.gauss(0, 400.0)
        records, dates = _inventory_records(sigma, 120, seed=1)
        estimator = estimator_for('inventory_balance', options={'interval_method': 'conformal_rolling', 'interval_window': 52})
        spec = estimator.fit(records, cutoff=dates[-1].isoformat(), vintage_policy='strict').diagnostics['predictive']
        self.assertEqual(spec['method'], 'conformal_rolling')
        self.assertEqual(spec['shape_observations'], 52)
        self.assertEqual(len(spec['nodes']), iv.CONFORMAL_NODES)


class ContaminatedTailCoverageTests(unittest.TestCase):
    """Contaminated-normal errors (8% at a fourteen-times scale), stationary and exchangeable.

    A Gaussian matched on variance over-covers the central 80% badly. The empirical
    quantiles and the conformal order statistics take the middle from the data and must
    reach nominal coverage; the Student-t MLE downweights the contamination rather than
    matching the variance, so it must at least be closer to nominal than the Gaussian.
    """

    @classmethod
    def setUpClass(cls):
        def sigma(t, rng):
            return rng.gauss(0, 10000.0) if rng.random() < 0.08 else rng.gauss(0, 700.0)
        cls.records, cls.dates = _inventory_records(sigma, 600, seed=11)
        cls.gaussian = coverage(_inventory_backtest(cls.records, cls.dates, {}, holdout=200))
        cls.results = {method: coverage(_inventory_backtest(cls.records, cls.dates,
                                                            {'interval_method': method, 'interval_window': 150}
                                                            if method == 'conformal_rolling' else {'interval_method': method},
                                                            holdout=200))
                       for method in FAT_TAIL}

    def test_gaussian_over_covers(self):
        self.assertGreater(self.gaussian, NOMINAL + 0.10)

    def test_quantile_methods_reach_nominal_coverage(self):
        for method in ('empirical_quantile', 'conformal_rolling'):
            with self.subTest(method=method):
                self.assertLessEqual(abs(self.results[method] - NOMINAL), TOLERANCE, self.results)

    def test_student_t_mle_is_closer_than_the_gaussian(self):
        self.assertLess(abs(self.results['student_t_mle'] - NOMINAL), abs(self.gaussian - NOMINAL), self.results)


class StudentTErrorCoverageTests(unittest.TestCase):
    """Student-t(3) errors: the one case where the t family is the true shape."""

    @classmethod
    def setUpClass(cls):
        def sigma(t, rng):
            return 600.0 * _student_t(rng, 3.0)
        cls.records, cls.dates = _inventory_records(sigma, 600, seed=21)

    def test_every_fat_tail_method_reaches_nominal_coverage(self):
        for method in FAT_TAIL:
            options = {'interval_method': method, **({'interval_window': 150} if method == 'conformal_rolling' else {})}
            with self.subTest(method=method):
                result = _inventory_backtest(self.records, self.dates, options, holdout=200)
                self.assertLessEqual(abs(coverage(result) - NOMINAL), 0.06, method)
                self.assertIn('predictive', result['forecasts'][0])


class GrowingScaleStillFailsTests(unittest.TestCase):
    """Negative control: a variance trending up through the holdout defeats every backward-looking method."""

    def test_every_fat_tail_method_still_fails(self):
        def sigma(t, rng):
            return rng.gauss(0, 300.0 * math.exp(0.02 * max(0, t - 500)))
        records, dates = _inventory_records(sigma, 620, seed=5)
        for method in FAT_TAIL:
            options = {'interval_method': method, **({'interval_window': 52} if method == 'conformal_rolling' else {})}
            with self.subTest(method=method):
                result = _inventory_backtest(records, dates, options, holdout=110)
                self.assertGreater(abs(coverage(result) - NOMINAL), TOLERANCE, method)


class CrpsSelectionTests(unittest.TestCase):
    """The declared selection rule: lowest validation-window CRPS over common rows, incumbent on a tie or too few rows."""

    @classmethod
    def setUpClass(cls):
        def sigma(t, rng):
            return rng.gauss(0, 10000.0) if rng.random() < 0.08 else rng.gauss(0, 700.0)
        cls.records, cls.dates = _inventory_records(sigma, 420, seed=13)
        cls.splits = dict(train_end=cls.dates[199].isoformat(), validation_end=cls.dates[299].isoformat(),
                          cutoff=cls.dates[-1].isoformat())

    def _candidates(self):
        return [('incumbent', estimator_for('inventory_balance')),
                ('empirical_quantile', estimator_for('inventory_balance', options={'interval_method': 'empirical_quantile'})),
                ('conformal_rolling', estimator_for('inventory_balance', options={'interval_method': 'conformal_rolling',
                                                                                  'interval_window': 150}))]

    def _validate(self, **kwargs):
        candidates = self._candidates()
        return validate_process(candidates[0][1], self.records, candidates=candidates, refit_every=10,
                                vintage_policy='strict', **self.splits, **kwargs)

    def test_default_mse_rule_cannot_tell_interval_methods_apart(self):
        report = self._validate()
        scores = report['selection']['scores']
        self.assertEqual(report['selection']['selected'], 'incumbent')          # identical MSE: declaration order
        self.assertAlmostEqual(scores['incumbent']['mse'], scores['empirical_quantile']['mse'])
        self.assertNotIn('rule', report['selection'])
        self.assertNotIn('unselected_holdout', report)

    def test_crps_rule_selects_on_validation_and_records_the_rest(self):
        report = self._validate(selection_metric='crps', min_selection_periods=8, score_unselected=True)
        selection = report['selection']
        scores = selection['scores']
        best = min(scores, key=lambda name: scores[name]['common_crps'])
        self.assertEqual(selection['selected'], best)
        self.assertEqual(selection['rule']['metric'], 'crps')
        self.assertGreaterEqual(selection['rule']['common_forecasts'], 8)
        self.assertNotIn('fallback', selection['rule'])
        unselected = report['unselected_holdout']['candidates']
        self.assertEqual(sorted(unselected), sorted(set(scores) - {best}))
        for summary in unselected.values():
            self.assertIn('interval_coverage', summary['metrics'])
        # The rejected candidates' holdout numbers do not enter the verdict.
        self.assertEqual([r['id'] for r in report['acceptance']['results']],
                         [c['id'] for c in estimator_for('inventory_balance').default_criteria()])

    def test_too_few_common_forecasts_keep_the_incumbent(self):
        report = self._validate(selection_metric='crps', min_selection_periods=10000)
        self.assertEqual(report['selection']['selected'], 'incumbent')
        self.assertIn('fallback', report['selection']['rule'])

    def test_unknown_selection_metric_fails_closed(self):
        with self.assertRaises(ValueError):
            self._validate(selection_metric='coverage_closest_to_nominal')


class FamilyRecalibrationTests(unittest.TestCase):
    """Model families: the forecaster keeps its mean and sd; the shape comes from pre-origin standardized errors."""

    @classmethod
    def setUpClass(cls):
        cls.data = _regional_rows(years=16, regions=30, seed=4)

    def _backtest(self, **options):
        estimator = estimator_for('regional_model_parameters', options={'interval_method': 'per_unit_year_mean', **options})
        return estimator.backtest(self.data, start='2011-12-31', end='2015-12-31', evaluation_cutoff='2015-12-31',
                                  refit_every=1)

    def test_point_forecasts_are_unchanged_and_the_shape_is_declared(self):
        plain = self._backtest()
        for method in FAT_TAIL:
            with self.subTest(method=method):
                result = self._backtest(family_interval_method=method)
                self.assertEqual([f['mean'] for f in result['forecasts']], [f['mean'] for f in plain['forecasts']])
                self.assertTrue(all('predictive' in f for f in result['forecasts']))
                self.assertLessEqual(abs(coverage(result) - NOMINAL), TOLERANCE, method)

    def test_pool_uses_only_periods_before_the_origin(self):
        estimator = estimator_for('regional_model_parameters', options={'interval_method': 'per_unit_year_mean',
                                                                         'family_interval_method': 'conformal_rolling'})
        base = estimator.backtest(self.data, start='2013-12-31', end='2014-12-31', evaluation_cutoff='2015-12-31')
        changed = {**self.data, 'employment': [dict(r, employment=r['employment'] * (1.5 if r['year'] == 2015 else 1.0))
                                               for r in self.data['employment']]}
        again = estimator.backtest(changed, start='2013-12-31', end='2014-12-31', evaluation_cutoff='2015-12-31')
        self.assertEqual([f['interval'] for f in base['forecasts']], [f['interval'] for f in again['forecasts']])

    def test_unknown_family_method_fails_closed(self):
        with self.assertRaises(ValueError):
            self._backtest(family_interval_method='widen_until_it_passes')


if __name__ == '__main__':
    unittest.main()
