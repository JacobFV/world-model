"""Declared predictive-interval methods: nominal coverage where they should hold, failure where it should not.

Every case below is synthetic data whose predictive distribution is known by
construction, so "the interval is right" is a checkable statement rather than a
hope. The misspecified cases are as important as the calibrated ones: a method
that reaches nominal coverage on a break it cannot see would be fitting the
holdout, not describing uncertainty.
"""
from datetime import date, timedelta
import math
import random
import unittest

from worldmodel.estimation import estimator_for
from worldmodel.estimation import intervals as iv
from worldmodel.estimation.data import ObservationSet, period_end
from worldmodel.estimation.distributions import norm_ppf, t_cdf, t_ppf
from worldmodel.estimation.synthetic import observation_records
from worldmodel.estimation.validation import crps_gaussian, rolling_origin_backtest
from worldmodel.models import regional

NOMINAL = 0.8
TOLERANCE = 0.15          # the declared interval_coverage tolerance


def coverage(result):
    return result['metrics']['model']['interval_coverage']


# ----------------------------------------------------------------------------- distributions

class PredictiveDistributionTests(unittest.TestCase):
    def test_student_t_quantile_inverts_the_cdf(self):
        for df in (2.5, 4.0, 12.0, 60.0):
            for p in (0.01, 0.1, 0.5, 0.9, 0.975):
                self.assertAlmostEqual(t_cdf(t_ppf(p, df), df), p, places=9)
        self.assertAlmostEqual(t_ppf(0.975, 10), 2.2281388, places=6)
        with self.assertRaises(ValueError):
            t_ppf(0.0, 5)

    def test_normal_family_reproduces_the_gaussian_default(self):
        spec = iv.normal(2.0)
        self.assertEqual(iv.standard_deviation(spec), 2.0)
        low, high = iv.interval(spec, 1.0, 0.8)
        self.assertAlmostEqual(low, 1.0 - 2.0 * norm_ppf(0.9))
        self.assertAlmostEqual(high, 1.0 + 2.0 * norm_ppf(0.9))
        for actual in (-3.0, 0.0, 1.5):
            self.assertAlmostEqual(iv.crps(spec, 1.0, actual), crps_gaussian(actual, 1.0, 2.0), places=12)

    def test_student_t_scale_matches_its_standard_deviation(self):
        spec = iv.student_t(1.0, 5.0)
        self.assertAlmostEqual(iv.standard_deviation(spec), math.sqrt(5 / 3))
        self.assertAlmostEqual(iv.interval(spec, 0.0, 0.8)[1], t_ppf(0.9, 5.0))
        with self.assertRaises(ValueError):
            iv.student_t(1.0, 2.0)

    def test_student_t_crps_matches_numerical_integration(self):
        def numeric(df, z, n=20000, span=40.0):
            step = 2 * span / n
            total = 0.0
            for i in range(n + 1):
                x = -span + i * step
                weight = 0.5 if i in (0, n) else 1.0
                total += weight * (t_cdf(x, df) - (1.0 if x >= z else 0.0)) ** 2 * step
            return total
        for df, z in ((4.0, 0.0), (4.0, 1.5), (9.0, -2.0)):
            self.assertAlmostEqual(iv.crps(iv.student_t(1.0, df), 0.0, z), numeric(df, z), delta=0.003)

    def test_empirical_family_with_normal_nodes_matches_the_closed_form(self):
        nodes = [norm_ppf((i + 0.5) / iv.DEFAULT_NODES) for i in range(iv.DEFAULT_NODES)]
        spec = iv.empirical(2.0, nodes)
        self.assertAlmostEqual(iv.standard_deviation(spec), 2.0, delta=0.05)
        low, high = iv.interval(spec, 0.0, 0.8)
        self.assertAlmostEqual(high, 2.0 * norm_ppf(0.9), delta=0.05)
        self.assertAlmostEqual(low, -2.0 * norm_ppf(0.9), delta=0.05)
        for actual in (0.0, 1.0, -4.0):
            closed = crps_gaussian(actual, 0.0, 2.0)
            self.assertLess(abs(iv.crps(spec, 0.0, actual) - closed) / closed, 0.01)
        self.assertIsNone(iv.log_score(spec, 0.0, 0.0))

    def test_unknown_families_and_methods_fail_closed(self):
        with self.assertRaises(ValueError):
            iv.standard_deviation({'family': 'cauchy', 'scale': 1.0})
        with self.assertRaises(ValueError):
            iv.from_errors([1.0, 2.0], method='shrink_until_it_passes')
        with self.assertRaises(ValueError):
            iv.from_errors([1.0] * 40, method='gaussian_trailing')          # no declared window
        self.assertEqual(sorted(iv.strip(iv.from_errors([1.0] * 40, method='gaussian_trailing', window=10))), ['family', 'scale'])


class ScaleEstimatorTests(unittest.TestCase):
    def test_trailing_scale_uses_only_the_window(self):
        errors = [0.0] * 100 + [10.0] * 10
        self.assertAlmostEqual(iv.trailing_scale(errors, 10), 10.0)
        self.assertLess(iv.trailing_scale(errors, 110), 4.0)

    def test_degrees_of_freedom_track_the_tails(self):
        rng = random.Random(7)
        light = [rng.gauss(0, 1) for _ in range(4000)]
        heavy = [rng.gauss(0, 1) / math.sqrt(rng.gammavariate(1.5, 2) / 3) for _ in range(4000)]
        self.assertGreater(iv.moment_df(light), 20)
        self.assertLess(iv.moment_df(heavy), 8)

    def test_standardized_nodes_flip_the_error_sign(self):
        errors = [1.0, -1.0] * 30 + [4.0] * 4
        nodes, count = iv.standardized_nodes(errors, 10, nodes=16)
        self.assertEqual(count, len(errors) - 10)
        self.assertLess(nodes[0], nodes[-1])
        self.assertLess(min(nodes), 0)           # a positive error is an actual below the mean


# ----------------------------------------------------------------------------- component coverage

def _inventory_records(sigma, count, *, seed=0, start=date(2010, 1, 5)):
    """Weekly crude balance whose one-step error scale is ``sigma(t)`` by construction."""
    rng = random.Random(seed)
    dates = [start]
    while len(dates) < count:
        dates.append(period_end(dates[-1], 'weekly'))
    production = [11000 + rng.gauss(0, 150) for _ in range(count)]
    imports = [6000 + rng.gauss(0, 400) for _ in range(count)]
    exports = [3000 + rng.gauss(0, 400) for _ in range(count)]
    refinery = [13950 + rng.gauss(0, 300) for _ in range(count)]
    stocks = [450000.0]
    for t in range(1, count):
        flow = 7 * (production[t] + imports[t] - exports[t] - refinery[t])
        stocks.append(stocks[-1] + 50 + flow + sigma(t, rng))
    columns = {'crude_stocks': stocks, 'production': production, 'imports': imports,
               'exports': exports, 'refinery_input': refinery}
    estimator = estimator_for('inventory_balance')
    return ObservationSet(observation_records(estimator, columns, dates)), dates


def _inventory_backtest(records, dates, options, *, holdout, level=NOMINAL):
    estimator = estimator_for('inventory_balance', options=options)
    start = dates[len(dates) - holdout - 1].isoformat()
    end = dates[-1].isoformat()
    cutoff = (dates[-1] + timedelta(days=60)).isoformat()
    return rolling_origin_backtest(estimator, records, start=start, end=end, evaluation_cutoff=cutoff,
                                   vintage_policy='strict', refit_every=4, interval_level=level)


class HeteroskedasticCoverageTests(unittest.TestCase):
    """Error volatility that moves between long regimes: a full-sample scale cannot track it.

    The holdout sits inside a calm regime after a violent one, so the expanding in-sample
    scale is an average of both and the interval is much too wide. The same construction
    with the holdout in the violent regime makes it too narrow; either way the scale is a
    regime average rather than a forecast of the current regime.
    """

    @classmethod
    def setUpClass(cls):
        def sigma(t, rng):
            return rng.gauss(0, 1500.0 if (t // 150) % 2 else 250.0)
        cls.records, cls.dates = _inventory_records(sigma, 750, seed=3)

    def test_in_sample_gaussian_scale_misses_nominal_coverage(self):
        result = _inventory_backtest(self.records, self.dates, {}, holdout=120)
        self.assertNotIn('predictive', result['forecasts'][0])
        self.assertGreater(abs(coverage(result) - NOMINAL), TOLERANCE)

    def test_trailing_methods_reach_nominal_coverage(self):
        for method in ('gaussian_trailing', 'empirical_trailing'):
            with self.subTest(method=method):
                result = _inventory_backtest(self.records, self.dates,
                                             {'interval_method': method, 'interval_window': 52}, holdout=120)
                self.assertLessEqual(abs(coverage(result) - NOMINAL), TOLERANCE, method)
                row = result['forecasts'][0]
                self.assertEqual('predictive' in row, method == 'empirical_trailing')

    def test_declared_components_are_reported(self):
        estimator = estimator_for('inventory_balance', options={'interval_method': 'gaussian_trailing',
                                                                'interval_window': 52})
        estimate = estimator.fit(self.records, cutoff=self.dates[-1].isoformat(), vintage_policy='strict')
        spec = estimate.diagnostics['predictive']
        self.assertEqual(spec['method'], 'gaussian_trailing')
        self.assertEqual(spec['interval_window'], 52)
        self.assertEqual(sorted(spec['components']), ['residual'])


class HeavyTailCoverageTests(unittest.TestCase):
    """Contaminated-normal errors: the variance is inflated by rare large errors.

    A Gaussian matched on variance then over-covers in the middle — 8% of errors at a
    twelve-times scale raise the standard deviation far more than they widen the central
    80% of the distribution. The empirical shape takes the middle from the data instead.
    """

    @classmethod
    def setUpClass(cls):
        def sigma(t, rng):
            return rng.gauss(0, 10000.0) if rng.random() < 0.08 else rng.gauss(0, 700.0)
        cls.records, cls.dates = _inventory_records(sigma, 600, seed=11)
        cls.gaussian = coverage(_inventory_backtest(cls.records, cls.dates, {}, holdout=200))

    def test_gaussian_scale_over_covers_the_central_interval(self):
        self.assertGreater(self.gaussian, NOMINAL + 0.10)

    def test_empirical_shape_corrects_it(self):
        result = _inventory_backtest(self.records, self.dates,
                                     {'interval_method': 'empirical_trailing', 'interval_window': 150}, holdout=200)
        self.assertLessEqual(abs(coverage(result) - NOMINAL), TOLERANCE)
        self.assertLess(abs(coverage(result) - NOMINAL), abs(self.gaussian - NOMINAL))

    def test_student_t_tails_do_not_fix_a_wrong_middle(self):
        """Declared, and negative: matching kurtosis is not the same as matching the shape."""
        result = _inventory_backtest(self.records, self.dates,
                                     {'interval_method': 'student_t_trailing', 'interval_window': 150}, holdout=200)
        self.assertGreater(coverage(result), NOMINAL + 0.10)


class GrowingScaleTests(unittest.TestCase):
    """An error scale that keeps growing: every backward-looking scale is systematically behind.

    This is the negative control. Unconditional coverage is attainable whenever the error
    distribution is stationary and estimable at the origin, so the case that must still fail
    is one where it is neither: a variance trending upward through the holdout. A method that
    reached nominal coverage here would be reading the holdout.
    """

    @classmethod
    def setUpClass(cls):
        def sigma(t, rng):
            return rng.gauss(0, 300.0 * math.exp(0.02 * max(0, t - 500)))
        cls.records, cls.dates = _inventory_records(sigma, 620, seed=5)

    def test_every_declared_method_still_fails(self):
        for options in ({}, {'interval_method': 'gaussian_trailing', 'interval_window': 52},
                        {'interval_method': 'student_t_trailing', 'interval_window': 52},
                        {'interval_method': 'empirical_trailing', 'interval_window': 52}):
            with self.subTest(options=options):
                result = _inventory_backtest(self.records, self.dates, options, holdout=110)
                self.assertGreater(abs(coverage(result) - NOMINAL), TOLERANCE, options)


# ----------------------------------------------------------------------------- revision coverage

def _credit_records(count, *, seed=0, revision_sd=0.02, first_lag=38, final_lag=730):
    """Monthly consumer credit published twice: a first release and a later revised value.

    The final value is the truth; the first release carries a persistent level error, which
    is what a real benchmark revision looks like. A forecast anchored on the first release
    and scored against the final vintage therefore inherits the revision.
    """
    rng = random.Random(seed)
    dates, level, final = [date(1990, 1, 1)], 1000.0, []
    while len(dates) < count:
        dates.append(period_end(dates[-1], 'monthly'))
    for _ in range(count):
        level *= math.exp(0.004 + rng.gauss(0, 0.002))
        final.append(level)
    revision, records = 0.0, []
    for index, (day, value) in enumerate(zip(dates, final)):
        revision = 0.9 * revision + rng.gauss(0, revision_sd * math.sqrt(1 - 0.81))
        end = period_end(day, 'monthly')
        for suffix, published, lag in (('first', value * math.exp(-revision), first_lag), ('final', value, final_lag)):
            records.append({'kind': 'observation', 'id': f'credit:{suffix}:{index}', 'metric': 'consumer_credit_outstanding',
                            'unit': 'billion_USD', 'subject': 'geo:US', 'value': published,
                            'valid_from': day.isoformat(), 'valid_to': end.isoformat(),
                            'observed_at': '2026-09-01T00:00:00Z', 'dimensions': {},
                            'attributes': {'source_series': 'TOTALSL',
                                           'realtime_start': (end + timedelta(days=lag)).isoformat()},
                            'epistemic_status': 'observed'})
    return ObservationSet(records), dates


class RevisionCoverageTests(unittest.TestCase):
    """Real-time forecasts scored against final data: the revision belongs in the interval."""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.dates = _credit_records(360, seed=2)

    def _backtest(self, options, holdout=60):
        estimator = estimator_for('credit_growth', options=options)
        start = self.dates[len(self.dates) - holdout - 1].isoformat()
        cutoff = (self.dates[-1] + timedelta(days=900)).isoformat()
        return rolling_origin_backtest(estimator, self.records, start=start, end=self.dates[-1].isoformat(),
                                       evaluation_cutoff=cutoff, vintage_policy='strict', refit_every=6)

    def test_first_release_values_are_kept_on_every_point(self):
        estimator = estimator_for('credit_growth')
        frame = estimator.frame(self.records, cutoff=self.dates[-1].isoformat(), vintage_policy='strict')
        points = frame.series['consumer_credit'].points
        revised = [p for p in points if p.first_value is not None and abs(p.value - p.first_value) > 1e-9]
        self.assertGreater(len(revised), 100)

    def test_residual_only_interval_under_covers(self):
        self.assertLess(coverage(self._backtest({})), NOMINAL - TOLERANCE)

    def test_declared_revision_component_restores_coverage(self):
        options = {'interval_revision': {'window': 120, 'maturity': 24}}
        result = self._backtest(options)
        self.assertLessEqual(abs(coverage(result) - NOMINAL), TOLERANCE)
        estimator = estimator_for('credit_growth', options=options)
        estimate = estimator.fit(self.records, cutoff=self.dates[-1].isoformat(), vintage_policy='strict')
        spec = estimate.diagnostics['predictive']
        self.assertEqual(sorted(spec['components']), ['residual', 'revision'])
        self.assertGreater(spec['components']['revision'], spec['components']['residual'])
        self.assertTrue(spec['revision_diagnostics']['relative'])

    def test_maturity_shorter_than_the_final_vintage_lag_under_states_the_revision(self):
        """Declared, and negative: periods whose final vintage is not out yet show no revision."""
        immature = self._backtest({'interval_revision': {'window': 60, 'maturity': 12}})
        self.assertLess(coverage(immature), NOMINAL - TOLERANCE)

    def test_revision_component_needs_enough_mature_periods(self):
        estimator = estimator_for('credit_growth', options={'interval_revision': {'window': 400, 'maturity': 355}})
        with self.assertRaises(ValueError):
            estimator.fit(self.records, cutoff=self.dates[-1].isoformat(), vintage_policy='strict')
        with self.assertRaises(ValueError):
            estimator_for('credit_growth', options={'interval_revision': {'window': 4}}).fit(
                self.records, cutoff=self.dates[-1].isoformat(), vintage_policy='strict')


# ----------------------------------------------------------------------------- panel coverage

def _regional_rows(*, years, regions, seed=0):
    """A shift-share panel whose regions differ in idiosyncratic volatility by twenty times."""
    rng = random.Random(seed)
    industries = [f'naics_sector:{code}' for code in ('11', '21', '31-33', '52', '62')]
    names = [f'R{i:02d}' for i in range(regions)]
    noise = {name: 0.002 + 0.038 * (index / max(regions - 1, 1)) for index, name in enumerate(names)}
    level = {(name, industry): rng.uniform(2e4, 2e5) for name in names for industry in industries}
    rows = []
    for offset in range(years):
        year = 2000 + offset
        for (name, industry), value in level.items():
            rows.append({'region': name, 'industry': industry, 'year': year, 'date': f'{year}-12-31',
                         'employment': value})
        growth = {industry: rng.gauss(0.005, 0.03) for industry in industries}
        common = rng.gauss(0, 0.004)
        for name in names:
            shock = rng.gauss(0, noise[name])
            for industry in industries:
                level[(name, industry)] *= math.exp(growth[industry] + common + shock)
    return {'employment': rows, 'design': 'synthetic_shift_share', 'information_time': 'real_time', 'revisions': 'none'}


class PanelIntervalTests(unittest.TestCase):
    """Pooling one residual scale across units of very different volatility misprices both ends.

    Pooled coverage *averaged over units* can look acceptable while being wrong for every
    unit: the stable third is covered almost always and the volatile third far too rarely.
    Conditional coverage by volatility group is therefore the test, not the overall rate.
    """

    @classmethod
    def setUpClass(cls):
        cls.data = _regional_rows(years=16, regions=30, seed=4)
        names = sorted({row['region'] for row in cls.data['employment']})
        cls.quiet, cls.loud = set(names[:10]), set(names[-10:])      # _regional_rows orders by noise

    def _backtest(self, method):
        estimator = estimator_for('regional_model_parameters', options={'interval_method': method})
        return estimator.backtest(self.data, start='2011-12-31', end='2015-12-31', evaluation_cutoff='2015-12-31',
                                  refit_every=1)

    def _group_coverage(self, result, members):
        rows = [f for f in result['forecasts'] if f['target'].split(':', 1)[1] in members]
        return sum(f['interval'][0] <= f['actual'] <= f['interval'][1] for f in rows) / len(rows)

    def test_pooled_scale_misprices_both_ends(self):
        result = self._backtest('pooled_year_draw')
        self.assertGreaterEqual(self._group_coverage(result, self.quiet), 0.95)
        self.assertLessEqual(self._group_coverage(result, self.loud), 0.70)

    def test_per_unit_scale_reaches_nominal_coverage_in_both_groups(self):
        result = self._backtest('per_unit_year_mean')
        self.assertLessEqual(abs(coverage(result) - NOMINAL), TOLERANCE)
        for label, members in (('quiet', self.quiet), ('loud', self.loud)):
            with self.subTest(group=label):
                self.assertLessEqual(abs(self._group_coverage(result, members) - NOMINAL), TOLERANCE)
        spreads = sorted({round(f['sd'], 6) for f in result['forecasts']})
        self.assertGreater(len(spreads), 10)
        self.assertGreater(spreads[-1] / spreads[0], 2.0)

    def test_unknown_method_fails_closed(self):
        with self.assertRaises(ValueError):
            regional._holdout_forecast({'shift_share_elasticity': 1.0}, self.data['employment'], [], self.data,
                                       options={'interval_method': 'whatever_passes'})


# ----------------------------------------------------------------------------- elections baselines

class ElectionBaselineTests(unittest.TestCase):
    """The incumbent-party-holds rule is scored alongside persistence and the district mean."""

    @classmethod
    def setUpClass(cls):
        from worldmodel.models import elections
        cls.fixture = elections.synthetic(seed=2, cycles=10, districts=60)
        cls.estimator = estimator_for('elections_model_parameters')

    def test_supplied_baseline_is_scored_and_tested(self):
        from worldmodel.models.elections import INCUMBENT_PARTY_HOLDS
        data = dict(self.fixture['data'], information_time='valid_time', revisions='none')
        result = self.estimator.backtest(data, start='2016-12-31', end='2022-12-31', evaluation_cutoff='2022-12-31',
                                         refit_every=1)
        self.assertIn(INCUMBENT_PARTY_HOLDS, result['metrics']['baselines'])
        self.assertIn(INCUMBENT_PARTY_HOLDS, result['diebold_mariano'])
        scored = result['metrics']['baselines'][INCUMBENT_PARTY_HOLDS]
        self.assertGreater(scored['count'], 100)
        self.assertIn(INCUMBENT_PARTY_HOLDS, result['secondary']['dem_seats']['metrics']['baselines'])

    def test_holding_shares_separate_the_two_parties(self):
        from worldmodel.models.elections import _incumbent_party_holds
        groups = _incumbent_party_holds(self.fixture['data']['races'])
        self.assertEqual(sorted(groups), ['D', 'R'])
        self.assertGreater(groups['D'][0], groups['R'][0])


# ----------------------------------------------------------------------------- conditional-input vintages

class ConditionalRebaseTests(unittest.TestCase):
    """A conditional input read as a ratio against its own lag must come from one vintage.

    The backtest substitutes the realized value of a declared conditional input at the target
    period from the evaluation frame, while the rest of the design row comes from the origin's
    frame. Where the design reads that driver against its own lag, the two vintages must be
    reconciled or the ratio measures a rebasing rather than a growth rate.
    """

    def test_ratio_mode_carries_the_realized_movement_and_nothing_else(self):
        from worldmodel.estimation.validation import _conditional_inputs

        class Frame:
            def __init__(self, times, columns):
                self.times, self.columns = times, columns

        truth = Frame(['t0', 't1', 't2'], {'x': [100.0, 110.0, 121.0]})
        origin = Frame(['t0', 't1'], {'x': [80.0, 88.0]})       # same series on a different base

        class Estimator:
            conditional_inputs = ('x',)
            conditional_rebase = {'x': 'ratio'}

        values, rebased = _conditional_inputs(Estimator(), truth, origin, 2, 1, 't1')
        # The realized movement is 121/110; applied to the origin's own level 88 that is 96.8.
        self.assertAlmostEqual(values['x'][0], 96.8, places=9)
        self.assertAlmostEqual(math.log(values['x'][0] / origin.columns['x'][1]),
                               math.log(truth.columns['x'][2] / truth.columns['x'][1]), places=12)
        self.assertEqual(rebased['x']['mode'], 'ratio')

    def test_difference_mode_carries_the_realized_change(self):
        from worldmodel.estimation.validation import _conditional_inputs

        class Frame:
            def __init__(self, times, columns):
                self.times, self.columns = times, columns

        truth = Frame(['t0', 't1', 't2'], {'r': [5.0, 5.2, 5.5]})
        origin = Frame(['t0', 't1'], {'r': [5.1, 5.3]})

        class Estimator:
            conditional_inputs = ('r',)
            conditional_rebase = {'r': 'difference'}

        values, rebased = _conditional_inputs(Estimator(), truth, origin, 2, 1, 't1')
        self.assertAlmostEqual(values['r'][0] - origin.columns['r'][1],
                               truth.columns['r'][2] - truth.columns['r'][1], places=12)
        self.assertEqual(rebased['r']['mode'], 'difference')

    def test_none_is_a_no_op_and_records_nothing(self):
        from worldmodel.estimation.validation import _conditional_inputs

        class Frame:
            def __init__(self, times, columns):
                self.times, self.columns = times, columns

        truth = Frame(['t0', 't1', 't2'], {'x': [100.0, 110.0, 121.0]})
        origin = Frame(['t0', 't1'], {'x': [80.0, 88.0]})

        class Estimator:
            conditional_inputs = ('x',)
            conditional_rebase = {}

        values, rebased = _conditional_inputs(Estimator(), truth, origin, 2, 1, 't1')
        self.assertEqual(values['x'], [121.0])      # the realized value, handed over untouched
        self.assertEqual(rebased, {})

    def test_equal_vintages_leave_the_value_alone(self):
        """The declared rebasing is inert wherever the driver is neither revised nor rebased."""
        from worldmodel.estimation.validation import _conditional_inputs

        class Frame:
            def __init__(self, times, columns):
                self.times, self.columns = times, columns

        truth = Frame(['t0', 't1', 't2'], {'x': [100.0, 110.0, 121.0]})
        origin = Frame(['t0', 't1'], {'x': [100.0, 110.0]})

        class Estimator:
            conditional_inputs = ('x',)
            conditional_rebase = {'x': 'ratio'}

        values, rebased = _conditional_inputs(Estimator(), truth, origin, 2, 1, 't1')
        self.assertEqual(values['x'], [121.0])
        self.assertEqual(rebased, {})              # nothing moved, so nothing is audited

    def test_labor_demand_declares_the_rebasing_and_the_others_do_not(self):
        from worldmodel.estimation.families import CONDITIONAL_REBASE
        self.assertEqual(estimator_for('labor_demand').conditional_rebase, {'output': 'ratio'})
        for component in ('energy_purchasing', 'policy_rule', 'interest_pass_through',
                          'deposit_growth', 'default_hazard'):
            with self.subTest(component=component):
                self.assertEqual(estimator_for(component).conditional_rebase, {})
        self.assertEqual(CONDITIONAL_REBASE, ('none', 'ratio', 'difference'))


# ----------------------------------------------------------------------------- count dispersion

class ConflictDispersionTests(unittest.TestCase):
    """The Hawkes predictive must use the NB2 dispersion the family declares, not sqrt(mean)."""

    def test_equidispersed_counts_estimate_alpha_near_zero(self):
        from worldmodel.models.conflict import _nb2_dispersion
        rng = random.Random(11)
        gamma, es, en = [math.log(20.0)], 0.0, 0.0
        N = [[_poisson_draw(rng, 20.0) for _ in range(400)]]
        X = [[[1.0] for _ in range(400)]]
        S = [[0.0] * 400]
        alpha, diagnostics = _nb2_dispersion(N, X, S, S, gamma, es, en)
        self.assertLess(alpha, 0.01)
        self.assertLess(abs(diagnostics['pearson_dispersion'] - 1.0), 0.25)

    def test_overdispersed_counts_are_detected(self):
        from worldmodel.models.conflict import _nb2_dispersion
        rng = random.Random(12)
        gamma, es, en = [math.log(20.0)], 0.0, 0.0
        # Mixture over the intensity: mean 20, variance far above it.
        N = [[_poisson_draw(rng, 20.0 * math.exp(rng.gauss(0, 0.6))) for _ in range(600)]]
        X = [[[1.0] for _ in range(600)]]
        S = [[0.0] * 600]
        alpha, diagnostics = _nb2_dispersion(N, X, S, S, gamma, es, en)
        self.assertGreater(alpha, 0.1)
        self.assertGreater(diagnostics['pearson_dispersion'], 3.0)

    def test_zero_alpha_reproduces_the_poisson_standard_deviation(self):
        from worldmodel.estimation.model_families import _conflict_forecast
        history = [{'country': 'A', 'month': f'2020-{m:02d}', 'count': 10} for m in range(1, 13)]
        rows = [{'country': 'A', 'month': '2021-01', 'count': 12}]
        data = {'covariates': []}
        base = {'background_coefficients': {'const': math.log(5.0)}, 'self_excitation': 0.2,
                'neighbor_excitation': 0.0, 'decay': 0.5}
        poisson_out = _conflict_forecast(dict(base, dispersion=0.0), history, rows, data)
        self.assertAlmostEqual(poisson_out[0]['sd'], math.sqrt(poisson_out[0]['mean']), places=12)
        wider = _conflict_forecast(dict(base, dispersion=0.05), history, rows, data)
        self.assertGreater(wider[0]['sd'], poisson_out[0]['sd'])
        self.assertAlmostEqual(wider[0]['mean'], poisson_out[0]['mean'], places=12)


def _poisson_draw(rng, mean):
    """Knuth's method; adequate at the small means used above."""
    limit, total, k = math.exp(-mean), 1.0, 0
    while True:
        total *= rng.random()
        if total <= limit:
            return k
        k += 1


if __name__ == '__main__':
    unittest.main()
