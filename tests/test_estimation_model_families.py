import math
import random
import unittest
from copy import deepcopy
from datetime import date, timedelta

from worldmodel import models
from worldmodel.estimation import (ModelFamilyEstimator, LeakageError, attach_calibration, calibration_record, estimator_for,
                                   family_components, load_requirements, validate_process)
from worldmodel.estimation.model_families import TIME_KEYS, _time
from worldmodel.process_library import default_registry


def monetary_data(**extra):
    observations = models.synthetic('monetary', 0)['data']['observations']
    return {'observations': observations, **extra}


def realtime(data):
    return dict(data, information_time='real_time')


def failing(report):
    return sorted(r['id'] for r in report['acceptance']['results'] if not r['passed'])


class ModelFamilyRequirementsTests(unittest.TestCase):
    def test_requirements_json_matches_live_family_declarations(self):
        document = load_requirements()
        self.assertEqual(set(family_components().values()), set(models.FAMILY_MODULES))
        for component, family in family_components().items():
            spec = document['components'][component]
            hooks = models.parameter_hooks(family)
            self.assertEqual(spec['family_requirements'], hooks['requirements'], family)
            scalar = {n for n, p in hooks['parameters'].items() if isinstance(p['value'], (int, float)) and not isinstance(p['value'], bool)}
            self.assertEqual({p['name'] for p in spec['parameters']}, scalar, family)
            estimator = ModelFamilyEstimator(family)
            self.assertEqual(spec['holdout_forecaster'], estimator.forecaster is not None, family)
            if estimator.forecaster is not None:
                self.assertEqual(spec['target'], estimator.forecaster['target'], family)
                self.assertEqual(spec['conditional_inputs'], list(estimator.forecaster['conditional_inputs']), family)
                self.assertEqual(document['processes'][f'{family}_model']['required_components'], [component])
                ids = [c['id'] for c in estimator.default_criteria()]
                self.assertEqual(len(ids), len(set(ids)), family)
                for criterion in estimator.default_criteria():
                    if criterion.get('baseline') not in (None, 'persistence', 'drift', 'historical_mean'):
                        self.assertIn(criterion['baseline'], estimator.forecaster['baselines'], family)
            else:
                self.assertEqual(spec['non_estimable'], models.family_module(family).NON_ESTIMABLE)
                self.assertEqual(document['processes'][f'{family}_model']['required_components'], [])
        for process in ('taylor_rule_policy_rate', 'two_region_migration', 'conflict_event_intensity'):
            self.assertIn(process, document['processes'])

    def test_model_processes_are_registered_and_unvalidated_by_default(self):
        described = {p['id']: p for p in default_registry().describe()['processes']}
        for family in models.FAMILY_MODULES:
            self.assertFalse(described[f'{family}_model'].get('validated'))
        self.assertIn('taylor_rule_policy_rate', described)


class ModelFamilyEstimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.estimator = estimator_for('monetary_model_parameters')
        cls.data = monetary_data(information_time='real_time')
        cls.report = validate_process(cls.estimator, cls.data, train_end='2000-12-31', validation_end='2004-12-31', cutoff='2009-12-01')

    def test_fit_recovers_parameters_and_records_process_paths(self):
        estimate = self.estimator.fit(self.data, cutoff='2009-12-31')
        truth = models.synthetic('monetary', 0)['truth']
        self.assertAlmostEqual(estimate.parameters['rho'], truth['rho'], delta=0.05)
        self.assertAlmostEqual(estimate.parameters['phi_pi'], truth['phi_pi'], delta=0.25)
        body = estimate.to_dict()
        self.assertEqual(body['process_parameters']['parameters.rho'], estimate.parameters['rho'])
        self.assertEqual(body['process_id'], 'monetary_model')
        self.assertTrue(all(body['bounds_check'].values()))
        self.assertIsNotNone(body['standard_errors']['rho'])

    def test_fit_windows_respect_cutoff(self):
        early = self.estimator.fit(self.data, cutoff='1995-12-31')
        window = early.data_audit['series']['policy_observations']
        self.assertLessEqual(window['max_available_at'], '1995-12-31T23:59:59+00:00')
        self.assertGreater(window['rows_excluded_after_cutoff'], 0)
        restricted = self.estimator.visible_data(self.data, '1995-12-31')
        self.assertTrue(all(r['date'] <= '1995-12-31' for r in restricted['observations']))

    def test_validation_and_registry_flip_only_on_pass(self):
        self.assertTrue(self.report['validated'], [r for r in self.report['acceptance']['results'] if not r['passed']])
        self.assertEqual(self.report['test']['leakage_audit']['violations'], 0)
        for forecast in self.report['test']['forecasts']:
            self.assertLess(forecast['origin_cutoff'], forecast['time'])
        registry = default_registry()
        record = attach_calibration(registry, self.report)
        attach_calibration(registry, self.report, process_id='taylor_rule_policy_rate')
        described = {p['id']: p for p in registry.describe()['processes']}
        self.assertTrue(record['validated'])
        self.assertTrue(described['monetary_model']['validated'])
        self.assertTrue(described['taylor_rule_policy_rate']['validated'])
        self.assertFalse(described['trade_model'].get('validated'))
        self.assertIn('parameters.rho', registry.calibrated_parameters('monetary_model'))

    def test_valid_time_rows_fail_revision_criterion(self):
        report = validate_process(self.estimator, monetary_data(), train_end='2000-12-31', validation_end='2004-12-31', cutoff='2009-12-01')
        self.assertFalse(report['validated'])
        self.assertEqual(failing(report), ['no_revision_leakage'])
        registry = default_registry()
        attach_calibration(registry, report)
        self.assertFalse({p['id']: p for p in registry.describe()['processes']}['monetary_model']['validated'])

    def test_holdout_changes_cannot_alter_selection(self):
        altered = deepcopy(self.data)
        for index, row in enumerate(altered['observations']):
            if row['date'] > '2004-12-31':
                row['policy_rate'] += 0.2 if index % 2 else -0.2  # Perturb holdout values without destroying identification.
        candidates = [('default', self.estimator), ('copy', ModelFamilyEstimator('monetary'))]
        kwargs = dict(train_end='2000-12-31', validation_end='2004-12-31', cutoff='2009-12-01', candidates=candidates)
        first = validate_process(self.estimator, self.data, **kwargs)
        second = validate_process(self.estimator, altered, **kwargs)
        self.assertEqual(first['selection'], second['selection'])
        self.assertNotEqual(first['test']['metrics']['model']['mae'], second['test']['metrics']['model']['mae'])

    def test_leaking_family_fit_is_rejected(self):
        class Leaky(ModelFamilyEstimator):
            def fit(self, data, *, cutoff, vintage_policy='family_rows', **options):
                return super().fit(data, cutoff='2009-12-31', vintage_policy=vintage_policy)
        leaky = Leaky('monetary')
        with self.assertRaises(LeakageError):
            leaky.backtest(self.data, start='2004-12-31', end='2006-12-01', evaluation_cutoff='2009-12-01')

    def test_non_estimable_sanctions_cannot_validate(self):
        estimator = ModelFamilyEstimator('sanctions')
        self.assertIsNone(estimator.forecaster)
        self.assertIn('legal-rule', estimator.non_estimable)
        with self.assertRaisesRegex(ValueError, 'holdout_forecaster.*non-estimable|non-estimable.*holdout_forecaster'):
            validate_process(estimator, {'flows': []}, train_end='2000-12-31', validation_end='2001-12-31', cutoff='2002-12-31')

    def test_conflict_hawkes_holdout(self):
        synthetic = models.family_module('conflict').synthetic(1, countries=6, months=72)
        data = dict(synthetic['data'], information_time='real_time')
        estimator = ModelFamilyEstimator('conflict')
        report = validate_process(estimator, data, train_end='2003-06-30', validation_end='2004-06-30', cutoff='2005-12-01', refit_every=6)
        self.assertGreater(report['test']['metrics']['model']['count'], 50)
        self.assertIn('persistence', report['test']['diebold_mariano'])
        record = calibration_record(report)
        self.assertEqual(record['validated'], report['validated'])


class FamilyHoldoutCase:
    """Shared checks: the correct model validates, the misspecified one does not, and forecasts cannot see the future.

    Subclasses define ``family``, ``protocol`` (validate_process windows), ``data()``,
    ``misspecified()``, ``expected_failures`` (criteria that must fail on the misspecified
    data), ``target_field`` and ``perturb(row)`` for the leakage checks.
    """
    family = None
    protocol = {}
    expected_failures = ()

    def unaffected_by_target_perturbation(self, target):
        """Forecasts that must not change when realized targets change (conditional inputs of other rows may)."""
        return True

    @classmethod
    def setUpClass(cls):
        cls.correct = cls.data()
        cls.report = validate_process(ModelFamilyEstimator(cls.family), realtime(cls.correct), **cls.protocol)

    def test_correct_model_passes_every_criterion(self):
        self.assertTrue(self.report['validated'], failing(self.report))
        test = self.report['test']
        self.assertEqual(test['leakage_audit']['violations'], 0)
        self.assertGreater(test['leakage_audit']['origins_checked'], 0)
        for forecast in test['forecasts']:
            self.assertLess(forecast['origin_cutoff'], forecast['time'])
        registry = default_registry()
        record = attach_calibration(registry, self.report)
        self.assertTrue(record['validated'])
        self.assertTrue({p['id']: p for p in registry.describe()['processes']}[f'{self.family}_model']['validated'])

    def test_misspecified_model_fails(self):
        report = validate_process(ModelFamilyEstimator(self.family), realtime(self.misspecified()), **self.protocol)
        self.assertFalse(report['validated'])
        self.assertTrue(set(self.expected_failures) & set(failing(report)), failing(report))
        registry = default_registry()
        attach_calibration(registry, report)
        self.assertFalse({p['id']: p for p in registry.describe()['processes']}[f'{self.family}_model']['validated'])

    def _backtest(self, data, estimator=None):
        estimator = estimator or ModelFamilyEstimator(self.family)
        return estimator.backtest(realtime(data), start=self.protocol['validation_end'], end=self.protocol['cutoff'],
                                  evaluation_cutoff=self.protocol['cutoff'], refit_every=self.protocol.get('refit_every', 1))

    def _rows(self, data):
        return data[ModelFamilyEstimator(self.family).forecaster['rows_key']]

    def _target_times(self, data):
        spec = ModelFamilyEstimator(self.family).forecaster
        lo, hi = _time(self.protocol['validation_end']), _time(self.protocol['cutoff'])
        return sorted({r[spec['time_key']] for r in data[spec['rows_key']] if lo < _time(r[spec['time_key']]) <= hi}, key=_time)

    def test_forecasts_ignore_realized_targets(self):
        """Changing realized target values at the last holdout time changes actuals only, never forecasts."""
        spec = ModelFamilyEstimator(self.family).forecaster
        last = self._target_times(self.correct)[-1]
        altered = deepcopy(self.correct)
        self.perturb_targets(altered, last)
        base, other = self._backtest(self.correct), self._backtest(altered)
        key = lambda r: [(f['target'], f['time'], round(f['mean'], 10), round(f['sd'], 10)) for f in r['forecasts']
                         if self.unaffected_by_target_perturbation(f['target'])]
        self.assertTrue(base['forecasts'])
        self.assertEqual(key(base), key(other))
        final = _time(last).isoformat()
        self.assertNotEqual([f['actual'] for f in base['forecasts'] if f['time'] == final and f['group'] == spec['target']],
                            [f['actual'] for f in other['forecasts'] if f['time'] == final and f['group'] == spec['target']])

    def test_forecasts_before_a_split_ignore_later_rows(self):
        spec = ModelFamilyEstimator(self.family).forecaster
        times = self._target_times(self.correct)
        split = times[len(times) // 2]
        altered = deepcopy(self.correct)
        self.perturb_after(altered, split)
        cut = _time(split).isoformat()
        early = lambda r: [(f['target'], f['time'], f['mean'], f['sd'], f['actual']) for f in r['forecasts'] if f['time'] <= cut]
        base, other = self._backtest(self.correct), self._backtest(altered)
        self.assertTrue(early(base))
        self.assertEqual(early(base), early(other))
        self.assertNotEqual([f['actual'] for f in base['forecasts'] if f['time'] > cut and f['group'] == spec['target']],
                            [f['actual'] for f in other['forecasts'] if f['time'] > cut and f['group'] == spec['target']])

    def test_forecaster_sees_only_rows_at_or_before_target(self):
        test = self

        class Audited(ModelFamilyEstimator):
            def __init__(self, family):
                super().__init__(family)
                inner = self.forecaster['forecast']
                self.calls = 0

                def audited(parameters, history, rows, data, **extra):
                    times = {r[self.forecaster['time_key']] for r in rows}
                    test.assertEqual(len(times), 1)
                    target = _time(times.pop())
                    test.assertTrue(all(_time(h[self.forecaster['time_key']]) < target for h in history))
                    for value in data.values():
                        if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
                            key = next((k for k in TIME_KEYS if k in value[0]), None)
                            if key is not None:
                                test.assertTrue(all(_time(r[key]) <= target for r in value))
                    self.calls += 1
                    return inner(parameters, history, rows, data, **extra)
                self.forecaster = dict(self.forecaster, forecast=audited)

        audited = Audited(self.family)
        result = self._backtest(self.correct, audited)
        self.assertGreater(audited.calls, 0)
        self.assertTrue(result['forecasts'])

    def test_leaking_fit_is_rejected(self):
        cutoff = self.protocol['cutoff']

        class Leaky(ModelFamilyEstimator):
            def fit(self, data, *, cutoff=None, vintage_policy='family_rows', **options):
                return super().fit(data, cutoff=cutoff_value, vintage_policy=vintage_policy)
        cutoff_value = cutoff
        with self.assertRaises(LeakageError):
            self._backtest(self.correct, Leaky(self.family))


# ----------------------------------------------------------------------------- legislative

class LegislativeHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'legislative'
    protocol = dict(train_end='2014-12-31', validation_end='2016-12-31', cutoff='2019-12-31', refit_every=4)
    expected_failures = ('brier_skill_vs_member_base_rate', 'beats_revealed_share_dm')

    @staticmethod
    def data():
        return models.synthetic('legislative', 1)['data']

    @classmethod
    def misspecified(cls):
        """Coin-flip votes: no spatial structure for ideal points to predict."""
        data = cls.data()
        rng = random.Random(11)
        for vote in data['votes']:
            vote['vote'] = int(rng.random() < 0.5)
        return data

    def perturb_targets(self, data, time):
        calls = {r['id'] for r in data['rollcalls'] if r['date'] == time}
        revealed = models.family_module('legislative').revealed_members(data['members'], data.get('options'))
        for vote in data['votes']:
            if vote['rollcall'] in calls and vote['member'] not in revealed:
                vote['vote'] = 1 - vote['vote']

    def perturb_after(self, data, time):
        calls = {r['id'] for r in data['rollcalls'] if _time(r['date']) > _time(time)}
        for vote in data['votes']:
            if vote['rollcall'] in calls:
                vote['vote'] = 1 - vote['vote']

    def test_probability_scores_are_reported(self):
        model = self.report['test']['metrics']['model']
        self.assertGreater(model['brier_skill'], 0.2)
        self.assertLess(model['calibration']['max_abs_deviation'], 0.15)
        self.assertIn('rollcall_revealed_share', self.report['test']['diebold_mariano'])


# ----------------------------------------------------------------------------- elections

class ElectionsHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'elections'
    protocol = dict(train_end='2011-12-31', validation_end='2015-12-31', cutoff='2032-12-31')
    expected_failures = ('beats_district_mean_dm',)

    @staticmethod
    def data():
        return models.family_module('elections').synthetic(0, cycles=14)['data']

    @classmethod
    def misspecified(cls):
        """A persistent district effect the fundamentals regression omits."""
        data = cls.data()
        rng = random.Random(5)
        effect = {}
        for race in data['races']:
            shift = effect.setdefault(race['district'], rng.gauss(0, 0.06))
            race['dem_share'] = min(0.99, max(0.01, race['dem_share'] + shift))
        return data

    def perturb_targets(self, data, time):
        for race in data['races']:
            if race['date'] == time:
                race['dem_share'] = min(0.99, race['dem_share'] + 0.1)

    def perturb_after(self, data, time):
        for race in data['races']:
            if _time(race['date']) > _time(time):
                race['dem_share'] = min(0.99, race['dem_share'] + 0.1)
                race['incumbent'] = -race['incumbent']

    def test_seat_counts_are_scored_as_a_secondary_group(self):
        seats = self.report['test']['secondary']['dem_seats']['metrics']['model']
        self.assertGreaterEqual(seats['count'], 8)
        self.assertIn('crps', seats)
        self.assertEqual(self.report['test']['metrics']['model']['count'],
                         sum(1 for f in self.report['test']['forecasts'] if f['group'] == 'dem_share'))


# ----------------------------------------------------------------------------- influence

class InfluenceHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'influence'
    protocol = dict(train_end='2014-12-31', validation_end='2016-12-31', cutoff='2021-12-31')
    expected_failures = ('beats_persistence_dm', 'beats_fixed_effects_only_dm')

    @staticmethod
    def data():
        return models.synthetic('influence', 0)['data']

    @classmethod
    def misspecified(cls):
        """Outcomes follow unit random walks unrelated to exposure (no fixed-effects structure)."""
        data = cls.data()
        rng = random.Random(3)
        level = {}
        for row in sorted(data['panel'], key=lambda r: (r['unit'], r['period'])):
            level[row['unit']] = level.get(row['unit'], rng.gauss(0, 1)) + rng.gauss(0, 1)
            row['outcome'] = level[row['unit']]
        return data

    def perturb_targets(self, data, time):
        for row in data['panel']:
            if row['date'] == time:
                row['outcome'] += 5.0

    def perturb_after(self, data, time):
        for row in data['panel']:
            if _time(row['date']) > _time(time):
                row['outcome'] += 5.0
                row['exposure'] -= 3.0


# ----------------------------------------------------------------------------- trade

class TradeHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'trade'
    protocol = dict(train_end='2017-12-31', validation_end='2019-12-31', cutoff='2022-12-31', refit_every=2)
    expected_failures = ('beats_frictionless_dm',)

    @staticmethod
    def data():
        return models.family_module('trade').synthetic(0, countries=6, years=8)['data']

    @classmethod
    def misspecified(cls):
        """Trade costs removed from the generating process: exporter/importer size and noise only."""
        data = cls.data()
        truth = models.family_module('trade').synthetic(0, countries=6, years=8)['truth']['coefficients']
        for row in data['flows']:
            row['value'] /= math.exp(truth['ln_distance'] * math.log(row['distance_km']) + truth['contiguous'] * row['contiguous']
                                     + truth['ln_one_plus_tariff'] * math.log1p(row['tariff']))
        return data

    def perturb_targets(self, data, time):
        """Move flows around a rectangle i->n, j->m, i->m, j->n so every exporter and importer total is unchanged."""
        rows = {(r['exporter'], r['importer']): r for r in data['flows'] if r['date'] == time}
        for i, j, n, m in (('C0', 'C1', 'C2', 'C3'), ('C4', 'C5', 'C0', 'C1')):
            amount = 0.3 * min(rows[(i, m)]['value'], rows[(j, n)]['value'])
            rows[(i, n)]['value'] += amount
            rows[(j, m)]['value'] += amount
            rows[(i, m)]['value'] -= amount
            rows[(j, n)]['value'] -= amount

    def perturb_after(self, data, time):
        for row in data['flows']:
            if _time(row['date']) > _time(time):
                row['value'] *= 1.5 if row['exporter'] < row['importer'] else 0.5
                row['tariff'] += 0.1


# ----------------------------------------------------------------------------- regional

def regional_noise_employment(seed=0, regions=30, years=8):
    """Region growth shared by all its industries and independent of industry composition."""
    rng = random.Random(seed)
    industries = ['manufacturing', 'energy', 'agriculture', 'finance', 'local_services']
    level = {(f'R{r:02d}', k): rng.uniform(5e3, 5e4) for r in range(regions) for k in industries}
    rows = []
    for y in range(years):
        for (region, k), value in sorted(level.items()):
            rows.append({'region': region, 'industry': k, 'year': 2010 + y, 'date': f'{2010 + y}-12-31', 'employment': value})
        growth = {f'R{r:02d}': rng.gauss(0, 0.05) for r in range(regions)}
        level = {(region, k): value * math.exp(growth[region]) for (region, k), value in level.items()}
    return {'employment': rows, 'shock_industries': industries[:4]}


class RegionalHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'regional'
    protocol = dict(train_end='2012-12-31', validation_end='2014-12-31', cutoff='2017-12-31')
    expected_failures = ('beats_year_effect_only_dm',)

    @staticmethod
    def data():
        data = models.synthetic('regional', 0)['data']
        return {'employment': data['employment'], 'shock_industries': data['shock_industries']}

    @classmethod
    def misspecified(cls):
        return regional_noise_employment(4)

    def unaffected_by_target_perturbation(self, target):
        # Other regions condition on R03's realized industry employment (their leave-one-out national growth).
        return target == 'employment_growth:R03'

    def perturb_targets(self, data, time):
        for row in data['employment']:
            if row['date'] == time and row['region'] == 'R03':
                row['employment'] *= 1.2

    def perturb_after(self, data, time):
        for row in data['employment']:
            if _time(row['date']) > _time(time):
                row['employment'] *= 1.3 if row['industry'] == 'energy' else 0.9

    def test_migration_process_is_not_validated_by_employment_holdout(self):
        registry = default_registry()
        with self.assertRaisesRegex(ValueError, 'not declared'):
            attach_calibration(registry, self.report, process_id='two_region_migration')
        described = {p['id']: p for p in registry.describe()['processes']}
        self.assertFalse(described['two_region_migration'].get('validated'))


# ----------------------------------------------------------------------------- commodities

class CommoditiesHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'commodities'
    protocol = dict(train_end='1993-12-31', validation_end='1995-12-31', cutoff='1999-12-31')
    expected_failures = ('beats_persistence_dm', 'minimum_test_forecasts', 'interval_coverage')

    @staticmethod
    def data():
        return models.synthetic('commodities', 0)['data']

    @classmethod
    def misspecified(cls):
        """Prices follow a random walk unrelated to the balance sheet."""
        data = cls.data()
        rng = random.Random(8)
        price = data['balances'][0]['price']
        for row in data['balances']:
            price *= math.exp(rng.gauss(0, 0.05))
            row['price'] = price
        return data

    def perturb_targets(self, data, time):
        for row in data['balances']:
            if row['date'] == time:
                row['price'] *= 1.5
                row['ending_stocks'] *= 1.2

    def perturb_after(self, data, time):
        for row in data['balances']:
            if _time(row['date']) > _time(time):
                row['price'] *= 1.5
                row['production'] *= 0.8

    def test_inventories_are_scored_as_a_secondary_group(self):
        stocks = self.report['test']['secondary']['ending_stocks']
        self.assertEqual(stocks['metrics']['model']['count'], self.report['test']['metrics']['model']['count'])
        self.assertLess(stocks['diebold_mariano']['persistence']['squared']['pvalue'], 0.1)


# ----------------------------------------------------------------------------- assets

def constant_volatility_assets(seed=0, days=600):
    """Factor returns with i.i.d. idiosyncratic noise: no volatility clustering for GARCH to forecast."""
    rng = random.Random(seed)
    betas = {'A': {'MKT': 1.2, 'SMB': 0.3}, 'B': {'MKT': 0.7, 'SMB': -0.2}}
    price = {s: 100.0 for s in betas}
    start = date(2010, 1, 4)
    bars = [{'symbol': s, 'date': start.isoformat(), 'close': price[s]} for s in betas]
    factors = []
    for t in range(1, days + 1):
        day = (start + timedelta(days=t)).isoformat()
        f = {'MKT': rng.gauss(0.0003, 0.01), 'SMB': rng.gauss(0.0, 0.005)}
        factors.append({'date': day, **f})
        for s, b in betas.items():
            price[s] *= math.exp(sum(b[n] * f[n] for n in b) + rng.gauss(0, 0.01))
            bars.append({'symbol': s, 'date': day, 'close': price[s]})
    return {'bars': bars, 'factors': factors, 'factor_names': ['MKT', 'SMB']}


class AssetsHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'assets'
    protocol = dict(train_end='2010-09-30', validation_end='2010-12-31', cutoff='2011-08-27', refit_every=20)
    expected_failures = ('volatility_crps_skill',)

    @staticmethod
    def data():
        return models.family_module('assets').synthetic(0, days=600)['data']

    @classmethod
    def misspecified(cls):
        return constant_volatility_assets(0)

    def perturb_targets(self, data, time):
        for row in data['bars']:
            if row['date'] == time:
                row['close'] *= 1.05

    def perturb_after(self, data, time):
        for row in data['bars']:
            if _time(row['date']) > _time(time):
                row['close'] *= 1.1
        for row in data['factors']:
            if _time(row['date']) > _time(time):
                row['MKT'] += 0.02

    def test_quantile_and_volatility_scores(self):
        test = self.report['test']
        model, constant = test['metrics']['model'], test['metrics']['baselines']['constant_volatility']
        self.assertLess(model['crps'], constant['crps'])
        self.assertEqual(set(model['pinball']), {'0.1', '0.5', '0.9'})
        self.assertIn('unconditional_log_return', test['secondary'])


# ----------------------------------------------------------------------------- market ABM

class MarketAbmHoldoutTests(FamilyHoldoutCase, unittest.TestCase):
    family = 'market_abm'
    protocol = dict(train_end='2000-03-03', validation_end='2000-04-12', cutoff='2000-10-29')
    expected_failures = ('interval_coverage', 'beats_persistence_dm', 'beats_historical_mean_dm')

    @staticmethod
    def data():
        return models.family_module('market_abm').synthetic(0, steps=300, agents_per_type=5, window=20)['data']

    @classmethod
    def misspecified(cls):
        """Fundamental volatility three times the value fixed in the SMM base configuration (outside the fitted grid)."""
        return models.family_module('market_abm').synthetic(0, steps=300, agents_per_type=5, window=20,
                                                           truth={'chartist_strength': 40.0, 'fundamental_volatility': 0.03})['data']

    def perturb_targets(self, data, time):
        start = next(w['start'] for w in data['windows'] if w['date'] == time)
        for k, bar in enumerate(data['bars']):
            if start < bar['date'] <= time:
                bar['close'] *= 1.02 if k % 2 else 0.98

    def perturb_after(self, data, time):
        for k, bar in enumerate(data['bars']):
            if bar['date'] > time:
                bar['close'] *= 1.03 if k % 2 else 0.97

    def test_simulated_paths_are_prefix_consistent_and_moments_are_grouped(self):
        abm = models.family_module('market_abm')
        config = dict(abm.example_config(), steps=40)
        self.assertEqual(abm.simulate(config, 'stochastic', 7)['prices_cents'],
                         abm.simulate(dict(config, steps=80), 'stochastic', 7)['prices_cents'][:41])
        self.assertEqual(set(self.report['test']['secondary']), {'window_abs_autocorr_1', 'window_excess_kurtosis'})
        self.assertGreaterEqual(self.report['test']['metrics']['model']['count'], 10)


if __name__ == '__main__':
    unittest.main()
