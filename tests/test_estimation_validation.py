import math
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from worldmodel.model import instant
from worldmodel.estimation import (ObservationSet, SeriesRequirement, LeakageError, estimator_for, validate_process,
                                   rolling_origin_backtest, diebold_mariano, evaluate_criteria, publish_validation_report,
                                   verify_report_id, attach_calibration, calibration_record)
from worldmodel.estimation.families import PopulationGrowthEstimator
from worldmodel.estimation.synthetic import component_dataset, observation_records, periods
from worldmodel.estimation.validation import (baseline_forecast, brier_score, calibration_curve, crps_ensemble, crps_gaussian,
                                              pinball_loss, brier_skill_score)
from worldmodel.processes import ProcessRegistry
from worldmodel.process_library import default_registry
from datetime import date


def record(value, valid_from, *, realtime=None, observed='2026-09-01T00:00:00Z', valid_to=None, rid='r'):
    attributes = {'source_series': 'X'}
    if realtime:
        attributes['realtime_start'] = realtime
    return {'kind': 'observation', 'id': f'x:{rid}', 'metric': 'm', 'unit': 'u', 'subject': 's:x', 'value': value,
            'valid_from': valid_from, 'valid_to': valid_to, 'observed_at': observed, 'dimensions': {}, 'attributes': attributes}


REQ = SeriesRequirement(name='x', metric='m', unit='u', frequency='monthly', source_series='X', publication_lag_days=10, revisions='major')


class MetricReferenceTests(unittest.TestCase):
    def test_diebold_mariano_matches_hand_computed_reference(self):
        model, base = [1, 2, 3, 4, 5], [2, 2, 5, 4, 8]
        one = diebold_mariano(model, base, horizon=1)
        self.assertAlmostEqual(one['mean_loss_difference'], -1.2)
        self.assertAlmostEqual(one['long_run_variance'], 1.36)
        self.assertAlmostEqual(one['statistic'], -1.2 / math.sqrt(1.36 / 5) * math.sqrt(0.8), places=10)
        self.assertAlmostEqual(one['statistic'], -2.057983, places=5)
        from worldmodel.estimation.distributions import t_cdf
        self.assertAlmostEqual(one['pvalue'], t_cdf(one['statistic'], 4), places=12)
        self.assertTrue(0.05 < one['pvalue'] < 0.06)
        two = diebold_mariano(model, base, horizon=2)
        self.assertEqual(two['kernel'], 'bartlett')
        self.assertAlmostEqual(two['long_run_variance'], 0.592)
        self.assertAlmostEqual(two['statistic'], -1.2 / math.sqrt(0.592 / 5) * math.sqrt(0.48), places=10)
        uncorrected = diebold_mariano(model, base, harvey_correction=False, alternative='two-sided')
        self.assertAlmostEqual(uncorrected['statistic'], -2.300895, places=5)
        self.assertTrue(diebold_mariano([1, 1, 1], [1, 1, 1])['degenerate'])

    def test_scoring_rules_and_baselines(self):
        self.assertAlmostEqual(crps_gaussian(0, 0, 1), (math.sqrt(2) - 1) / math.sqrt(math.pi), places=10)
        rng = random.Random(1)
        self.assertAlmostEqual(crps_ensemble(0.3, [rng.gauss(0, 1) for _ in range(20000)]), crps_gaussian(0.3, 0, 1), delta=0.01)
        self.assertEqual(crps_ensemble(2, [5]), 3)
        self.assertAlmostEqual(pinball_loss(10, 8, 0.9), 1.8)
        self.assertAlmostEqual(pinball_loss(8, 10, 0.9), 0.2)
        self.assertEqual(brier_score([1, 0], [1, 0]), 0)
        self.assertGreater(brier_skill_score([0.9, 0.1], [1, 0], [0.5, 0.5]), 0)
        curve = calibration_curve([0.1, 0.1, 0.9, 0.9], [0, 0, 1, 1], bins=10)
        self.assertAlmostEqual(curve['max_abs_deviation'], 0.1)
        history = [1, 2, 3, 4, 5, 6, 7, 8]
        self.assertEqual(baseline_forecast('persistence', history)['mean'], 8)
        self.assertEqual(baseline_forecast('drift', history, 2)['mean'], 10)
        self.assertEqual(baseline_forecast('historical_mean', history)['mean'], 4.5)
        self.assertEqual(baseline_forecast('seasonal_naive', history, 1, season=4)['mean'], 5)


class PointInTimeTests(unittest.TestCase):
    def test_later_vintage_is_invisible_before_release_and_supersedes_after(self):
        data = ObservationSet([record(1.0, '2020-01-01', realtime='2020-02-15', rid='v1'),
                               record(1.5, '2020-01-01', realtime='2020-08-01', rid='v2')])
        early = data.select(REQ, cutoff='2020-03-01', vintage_policy='strict')
        late = data.select(REQ, cutoff='2020-09-01', vintage_policy='strict')
        self.assertEqual(early.values, [1.0])
        self.assertEqual(late.values, [1.5])
        self.assertEqual(late.superseded_vintages, 1)
        self.assertEqual(late.points[0].first_available_at[:10], '2020-02-15')
        self.assertEqual(late.audit()['vintage_modes'], ['real_time'])
        self.assertFalse(late.audit()['revision_leakage_possible'])
        self.assertEqual(len(data.select(REQ, cutoff='2020-02-14', vintage_policy='strict')), 0)

    def test_strict_policy_blocks_current_vintage_and_retrospective_uses_declared_lag(self):
        data = ObservationSet([record(1.0, '2020-01-01', valid_to='2020-02-01')])
        self.assertEqual(len(data.select(REQ, cutoff='2025-01-01', vintage_policy='strict')), 0)
        self.assertEqual(len(data.select(REQ, cutoff='2020-02-10', vintage_policy='retrospective')), 0)
        series = data.select(REQ, cutoff='2020-02-11', vintage_policy='retrospective')
        self.assertEqual(series.values, [1.0])
        self.assertTrue(series.audit()['revision_leakage_possible'])

    def test_tampered_point_raises_leakage_and_future_valid_time_excluded(self):
        data = ObservationSet([record(1.0, '2020-01-01', realtime='2020-02-15'), record(2.0, '2030-01-01', realtime='2020-02-15', rid='f')])
        series = data.select(REQ, cutoff='2020-03-01')
        self.assertEqual(len(series), 1)
        series.points[0].available_at = '2021-01-01T00:00:00+00:00'
        with self.assertRaises(LeakageError):
            series.audit()

    def test_equal_availability_conflicts_require_reconciliation(self):
        data = ObservationSet([record(1.0, '2020-01-01', realtime='2020-02-15', rid='a'), record(2.0, '2020-01-01', realtime='2020-02-15', rid='b')])
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            data.select(REQ, cutoff='2020-03-01')

    def test_incomplete_aggregation_periods_are_excluded(self):
        daily = SeriesRequirement(name='x', metric='m', unit='u', frequency='monthly', native_frequency='daily', source_series='X', publication_lag_days=0, revisions='none')
        rows = [record(float(d), f'2020-01-{d:02d}', realtime=f'2020-01-{d:02d}', rid=f'd{d}') for d in range(1, 32)]
        rows += [record(5.0, '2020-02-01', realtime='2020-02-01', rid='feb1')]
        series = ObservationSet(rows).select(daily, cutoff='2020-02-10')
        self.assertEqual(series.times, ['2020-01-01'])
        self.assertAlmostEqual(series.values[0], 16.0)
        self.assertEqual(series.points[0].first_available_at[:10], '2020-02-01')


class BacktestLeakageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = component_dataset('interest_pass_through', seed=5)
        cls.data = ObservationSet(cls.dataset['records'])
        cls.estimator = estimator_for('interest_pass_through')

    def test_origins_precede_target_publication_and_holdout_is_frozen(self):
        ds = self.dataset
        result = rolling_origin_backtest(self.estimator, self.data, start=ds['validation_end'], end=ds['cutoff'], evaluation_cutoff=ds['cutoff'])
        releases = {(r['metric'], r['valid_from']): r['attributes']['realtime_start'] for r in ds['records']}
        self.assertGreater(len(result['forecasts']), 50)
        for forecast in result['forecasts']:
            self.assertLess(instant(forecast['anchor_time']), instant(forecast['time']))
            self.assertLess(instant(forecast['origin_cutoff']), instant(releases[('prime_loan_rate', forecast['time'])]))
            self.assertEqual(forecast['conditional_inputs'], ['policy_rate'])
        self.assertEqual(result['leakage_audit']['violations'], 0)

    def test_changing_holdout_values_cannot_change_selection(self):
        ds = self.dataset
        altered = deepcopy(ds['records'])
        for row in altered:
            if row['valid_from'] > ds['validation_end'] and row['metric'] == 'prime_loan_rate':
                row['value'] += 50
        candidates = [('ecm1', estimator_for('interest_pass_through', options={'ecm_lags': 1})),
                      ('ecm0', estimator_for('interest_pass_through', options={'ecm_lags': 0}))]
        kwargs = dict(train_end=ds['train_end'], validation_end=ds['validation_end'], cutoff=ds['cutoff'], candidates=candidates)
        first = validate_process(self.estimator, ObservationSet(ds['records']), **kwargs)
        second = validate_process(self.estimator, ObservationSet(altered), **kwargs)
        self.assertEqual(first['selection'], second['selection'])
        self.assertNotEqual(first['test']['metrics']['model']['mae'], second['test']['metrics']['model']['mae'])
        self.assertFalse(first['selection']['refit_after_selection'])

    def test_forecast_design_reading_target_is_leakage(self):
        class Peeking(PopulationGrowthEstimator):
            def design(self, cols, t, options):
                return [1.0 + 0 * cols['population'][t]]
        ds = component_dataset('population_growth_rate', seed=1)
        data = ObservationSet(ds['records'])
        estimator = Peeking()
        frame = estimator.frame(data, cutoff=ds['cutoff'])
        estimate = estimator.fit_frame(frame, cutoff=ds['cutoff'])
        with self.assertRaises(LeakageError):
            estimator.predict(estimate, frame)

    def test_targets_after_evaluation_cutoff_are_rejected(self):
        with self.assertRaises(LeakageError):
            rolling_origin_backtest(self.estimator, self.data, start='2020-01-01', end='2030-01-01', evaluation_cutoff='2024-01-01')


class AcceptanceAndRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = component_dataset('population_growth_rate', seed=2)
        cls.good = validate_process(PopulationGrowthEstimator(), ObservationSet(ds['records']),
                                    train_end=ds['train_end'], validation_end=ds['validation_end'], cutoff=ds['cutoff'])
        rng = random.Random(3)
        estimator = PopulationGrowthEstimator()
        walk = [1e6]
        for _ in range(69):
            walk.append(walk[-1] * math.exp(rng.gauss(0, 0.01)))
        records = observation_records(estimator, {'population': walk}, periods('annual', 70, date(1950, 1, 1)))
        cls.bad = validate_process(estimator, ObservationSet(records), train_end=ds['train_end'],
                                   validation_end=ds['validation_end'], cutoff=ds['cutoff'])

    def test_validation_requires_explicit_passing_criteria(self):
        self.assertTrue(self.good['validated'])
        self.assertFalse(self.bad['validated'])
        failing = [r['id'] for r in self.bad['acceptance']['results'] if not r['passed']]
        self.assertIn('beats_persistence_dm', failing)
        with self.assertRaises(ValueError):
            evaluate_criteria(self.good, [])
        unknown = evaluate_criteria(self.good, [{'id': 'mystery', 'type': 'astrology'}])
        self.assertFalse(unknown['passed'])
        verify_report_id(self.good)
        tampered = dict(self.good, validated=False)
        with self.assertRaises(ValueError):
            verify_report_id(tampered)

    def test_retrospective_vintages_of_revised_series_fail_revision_criterion(self):
        ds = component_dataset('population_growth_rate', seed=2)
        rows = deepcopy(ds['records'])
        for row in rows:
            row['attributes'].pop('realtime_start')
        report = validate_process(PopulationGrowthEstimator(), ObservationSet(rows), train_end=ds['train_end'],
                                  validation_end=ds['validation_end'], cutoff=ds['cutoff'], vintage_policy='retrospective')
        result = next(r for r in report['acceptance']['results'] if r['id'] == 'no_revision_leakage')
        self.assertFalse(result['passed'])
        self.assertFalse(report['validated'])

    def test_registry_validation_is_derived_from_records(self):
        registry = default_registry()
        described = {p['id']: p for p in registry.describe()['processes']}
        self.assertFalse(described['population_growth']['validated'])
        record = attach_calibration(registry, self.good)
        self.assertTrue(record['validated'])
        described = {p['id']: p for p in registry.describe()['processes']}
        self.assertTrue(described['population_growth']['validated'])
        parameters = registry.calibrated_parameters('population_growth')
        self.assertAlmostEqual(parameters['growth_rate_per_year']['value'], 0.01, delta=0.002)
        prediction = registry.predict('population_growth.deterministic', {'population': {'value': 100.0, 'unit': 'people'}},
                                      {'growth_rate_per_year': parameters['growth_rate_per_year']['value'], 'estimate_id': record['estimate_id']},
                                      {'dt_seconds': 86400, 'time': '2020-01-01T00:00:00Z'})
        self.assertTrue(prediction['diagnostics']['validated'])
        self.assertEqual(prediction['diagnostics']['estimate_id'], record['estimate_id'])
        failing = attach_calibration(registry, self.bad)
        self.assertFalse(failing['validated'])
        self.assertFalse({p['id']: p for p in registry.describe()['processes']}['population_growth']['validated'])
        self.assertEqual(registry.calibration('population_growth')['failing_components'], ['population_growth_rate'])
        with self.assertRaises(ValueError):
            registry.calibrated_parameters('population_growth')

    def test_forged_records_and_default_validated_flags_are_rejected(self):
        registry = default_registry()
        record = calibration_record(self.bad)
        forged = dict(record, validated=True)
        with self.assertRaisesRegex(ValueError, 'record_id'):
            registry.attach_calibration(forged)
        from worldmodel.util import digest
        forged.pop('record_id')
        forged['record_id'] = digest(forged)
        with self.assertRaisesRegex(ValueError, 'acceptance'):
            registry.attach_calibration(forged)
        with self.assertRaisesRegex(ValueError, 'validated'):
            ProcessRegistry().register_process({'id': 'p', 'inputs': {}, 'outputs': {}, 'topology': 't', 'description': 'd', 'validated': True})
        with self.assertRaisesRegex(ValueError, 'not declared'):
            attach_calibration(registry, self.good, process_id='banking_ledger')

    def test_partial_component_coverage_leaves_process_unvalidated(self):
        ds = component_dataset('interest_pass_through', seed=5)
        report = validate_process(estimator_for('interest_pass_through'), ObservationSet(ds['records']),
                                  train_end=ds['train_end'], validation_end=ds['validation_end'], cutoff=ds['cutoff'])
        registry = default_registry()
        record = attach_calibration(registry, report)
        self.assertTrue(record['validated'])
        state = registry.calibration('coupled_economy')
        self.assertIn('default_hazard', state['missing_components'])
        self.assertFalse({p['id']: p for p in registry.describe()['processes']}['coupled_economy']['validated'])
        attach_calibration(registry, report, process_id='bank_energy_business')
        self.assertEqual(registry.calibration('bank_energy_business')['missing_components'], ['energy_purchasing'])

    def test_report_publication_is_immutable_and_verifiable(self):
        from worldmodel.store import Store
        from worldmodel.artifacts import load_report
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'data')
            ref = publish_validation_report(store, self.good)
            loaded = load_report(store, ref)
            self.assertEqual(loaded['report_id'], self.good['report_id'])
            verify_report_id(loaded)
            self.assertTrue(calibration_record(loaded, report_ref=ref)['validated'])
            self.assertEqual(ref, publish_validation_report(store, self.good))


if __name__ == '__main__':
    unittest.main()
