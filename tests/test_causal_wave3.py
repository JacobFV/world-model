"""Wave-3 monthly panel construction on fictional inputs: months as periods, a top-decile dose, the
exact unit window, and the rule that a design's power is measured without reading its effect."""
import math
import unittest

from worldmodel.causal.did import event_study
from worldmodel.causal.power import calibrate
from worldmodel.causal import studies_wave3 as w3


class FakeStore:
    def __init__(self, records):
        self._records = records

    def records(self, ref):
        return iter(self._records)


def laus_record(fips, month, feature, value, available_at):
    year, index = int(month[:4]), int(month[5:7])
    nxt = f'{year + 1}-01-01' if index == 12 else f'{year}-{index + 1:02d}-01'
    return {'id': f'x:{fips}:{feature}:{month}', 'kind': 'observation', 'subject': f'geo:US:county:{fips}',
            'metric': feature, 'unit': 'persons', 'value': value, 'valid_from': f'{month}-01', 'valid_to': nxt,
            'observed_at': available_at, 'dimensions': {'available_at': available_at, 'revisions': 'none'}}


class MonthTests(unittest.TestCase):
    def test_month_index_makes_a_lag_of_one_a_month(self):
        self.assertEqual(w3.month_index('2009-05-01') - w3.month_index('2009-04-30'), 1)
        self.assertEqual(w3.month_index('2010-01-01') - w3.month_index('2009-12-15'), 1)
        self.assertEqual(w3.month_index('2024-07') - w3.month_index('2009-05'), 182)

    def test_month_label_round_trips(self):
        for month in ('2005-04', '2007-12', '2026-07'):
            self.assertEqual(w3.month_label(w3.month_index(month)), month)


class LoadTests(unittest.TestCase):
    def test_only_months_with_both_features_survive_and_availability_is_the_later_vintage(self):
        store = FakeStore([
            laus_record('29189', '2010-03', w3.RATE, 8.0, '2010-05-07'),
            laus_record('29189', '2010-03', w3.LABOR_FORCE, 500_000.0, '2010-05-19'),
            laus_record('29189', '2010-04', w3.RATE, 7.5, '2010-06-04'),          # no labour force for this month
            laus_record('29189', '2010-05', w3.LABOR_FORCE, 501_000.0, '2010-07-02'),   # no rate for this month
            {'id': 'e', 'kind': 'entity', 'subject': 'geo:US:county:29189'},
        ])
        series = w3.load_monthly_laus(store, {'dataset': 'county_monthly_realtime_panel'})
        self.assertEqual(sorted(series['29189']), [w3.month_index('2010-03')])
        rate, labor_force, available = series['29189'][w3.month_index('2010-03')]
        self.assertEqual((rate, labor_force, available), (8.0, 500_000.0, '2010-05-19'))

    def test_employment_is_the_laus_identity_on_the_two_series(self):
        months = {w3.month_index('2010-03'): (8.0, 500_000.0, '2010-05-19')}
        calendar = (w3.month_index('2007-05'), w3.month_index('2026-07'))
        employment = w3.outcome_series(months, 'laus_log_employment', calendar)
        labor_force = w3.outcome_series(months, 'laus_log_labor_force', calendar)
        rate = w3.outcome_series(months, 'laus_unemployment_rate', calendar)
        month = w3.month_index('2010-03')
        self.assertAlmostEqual(employment[month], math.log(500_000.0 * 0.92))
        self.assertAlmostEqual(labor_force[month], math.log(500_000.0))
        self.assertEqual(rate[month], 8.0)
        self.assertEqual(w3.outcome_series(months, 'laus_unemployment_rate', (0, 1)), {})


class DoseTests(unittest.TestCase):
    TREATMENT = {'dose_incident_types': ['Tornado'], 'quiet_period_incident_types': ['Tornado', 'Flood'],
                 'incident_months': [w3.month_index('2009-05'), w3.month_index('2024-07')], 'quiet_months': 36}

    DECLARATIONS = {
        'fema:disaster:1': {'type': 'Tornado', 'begin': '2011-04-27', 'end': '2011-04-28',
                            'counties': {'01001', '01003', '01005', '01007', '01009'}},
        # 01009 was in a flood 30 months earlier, inside the 36-month quiet window.
        'fema:disaster:2': {'type': 'Flood', 'begin': '2008-10-01', 'end': '2008-10-02', 'counties': {'01009'}},
        # Before the estimated cohort window and outside 01001's quiet window: out of scope entirely.
        'fema:disaster:3': {'type': 'Tornado', 'begin': '2007-04-01', 'end': '2007-04-02', 'counties': {'01001'}}}

    def _units(self):
        damage = {'01001': [('2011-04-27', 10_000_000.0), ('2011-06-01', 9e9)],   # second row outside the incident
                  '01003': [('2011-04-27', 5.0)], '01005': [('2011-04-27', 1_000_000.0)],
                  '01009': [('2011-04-27', 9e9)]}
        population = {f: {2010: 10_000.0} for f in ('01001', '01003', '01005', '01009')}
        return w3.dose_distribution(self.DECLARATIONS, damage, population, self.TREATMENT)

    def test_scope_quiet_window_and_dose(self):
        units, facts = self._units()
        self.assertEqual({u['fips'] for u in units}, {'01001', '01003', '01005'})
        self.assertEqual(facts['excluded_recent_other_disaster'], 1)   # 01009
        self.assertEqual(facts['excluded_no_population'], 1)           # 01007
        self.assertEqual(facts['pairs_in_scope'], 5)                   # disaster 3 is outside the cohort months
        self.assertAlmostEqual(next(u['dose'] for u in units if u['fips'] == '01001'), 1000.0)
        self.assertEqual({u['g'] for u in units}, {w3.month_index('2011-04')})

    def test_the_quiet_window_is_inclusive_at_g_minus_36(self):
        damage = {'01001': [('2011-04-27', 10_000_000.0)]}
        population = {'01001': {2010: 10_000.0}}
        for month, kept in (('2008-04-01', False), ('2008-03-01', True)):   # g-36 is inside, g-37 is not
            declarations = {'fema:disaster:1': {'type': 'Tornado', 'begin': '2011-04-27', 'end': '2011-04-28',
                                                'counties': {'01001'}},
                            'fema:disaster:3': {'type': 'Tornado', 'begin': month, 'end': month,
                                                'counties': {'01001'}}}
            units, facts = w3.dose_distribution(declarations, damage, population, self.TREATMENT)
            self.assertEqual([u['fips'] for u in units], ['01001'] if kept else [], month)
            self.assertEqual(facts['excluded_recent_other_disaster'], 0 if kept else 1, month)

    def test_the_decile_over_damaged_pairs_ignores_undamaged_ones(self):
        units = [{'dose': float(d)} for d in list(range(1, 11)) + [0.0] * 90]
        self.assertEqual(w3.decile_threshold(units, quantile=0.9, over='damaged'), 9.0)
        self.assertEqual(w3.decile_threshold(units, quantile=0.9, over='all'), 0.0)

    def test_assign_roles_splits_at_the_threshold(self):
        units, _ = self._units()
        facts = w3.assign_roles(units, treated_min=500.0, control_max=1.0)
        self.assertEqual(facts, {'treated': 1, 'control': 1, 'middle': 1})
        self.assertEqual({u['fips']: u['role'] for u in units},
                         {'01001': 'treated', '01003': 'control', '01005': 'middle'})


class PanelTests(unittest.TestCase):
    G = w3.month_index('2011-04')

    def _units(self, n=6):
        return [{'unit': f'd1|0100{i}', 'disaster': 'd1', 'fips': f'0100{i}', 'g': self.G, 'dose': 0.0,
                 'role': 'treated' if i < 2 else 'control'} for i in range(n)]

    def _series(self, n=6):
        return {f'0100{i}': {m: 10.0 + 0.001 * m + 0.01 * i for m in range(self.G - 60, self.G + 60)}
                for i in range(n)}

    def test_unit_window_clips_to_the_stack_and_loses_no_estimable_month(self):
        units, series = self._units(), self._series()
        calendar = (w3.month_index('2007-05'), w3.month_index('2026-07'))
        matched, _, facts = w3.monthly_dose_panel(units, series, calendar=calendar, match_window=[-24, -13],
                                                  unit_window=(-24, 24))
        self.assertEqual(facts['units_without_match_window_outcome'], 0)
        self.assertEqual(matched.periods, list(range(self.G - 24, self.G + 25)))
        self.assertEqual(len(matched.units), 6)
        self.assertEqual(len(matched.treated_units()), 2)
        self.assertEqual(set(matched.clusters.values()), {'01'})
        # Comparisons happen inside a disaster and a growth half: two strata, both named for the disaster.
        self.assertEqual(sorted(set(matched.strata.values())), ['d1|hi', 'd1|lo'])

    def test_a_unit_without_the_match_window_is_dropped(self):
        units, series = self._units(), self._series()
        series['01003'] = {m: 1.0 for m in range(self.G - 12, self.G + 24)}   # no month at g-24
        calendar = (w3.month_index('2007-05'), w3.month_index('2026-07'))
        matched, _, facts = w3.monthly_dose_panel(units, series, calendar=calendar, match_window=[-24, -13],
                                                  unit_window=(-24, 24))
        self.assertEqual(facts['units_without_match_window_outcome'], 1)
        self.assertNotIn('d1|01003', matched.outcomes)

    def test_middle_dose_units_never_enter(self):
        units = self._units()
        units[3]['role'] = 'middle'
        calendar = (w3.month_index('2007-05'), w3.month_index('2026-07'))
        matched, _, _ = w3.monthly_dose_panel(units, self._series(), calendar=calendar, match_window=[-24, -13],
                                              unit_window=(-24, 24))
        self.assertNotIn('d1|01003', matched.outcomes)


class HorizonTests(unittest.TestCase):
    """One att_gt pass, several post windows -- and standard errors without the point estimate."""

    SPEC = {'e_min': -6, 'e_max': 12, 'anticipation': 0, 'control_group': 'never_treated', 'alpha': 0.05,
            'cohorts': None}

    def _panel(self):
        from worldmodel.causal import Panel
        import random
        rng = random.Random(7)
        outcomes, cohorts, strata, clusters = {}, {}, {}, {}
        for stack in range(12):
            g = 24000 + stack
            for i in range(8):
                unit = f's{stack}|u{i}'
                level = rng.gauss(0.0, 0.2)
                outcomes[unit] = {t: level + 0.001 * t + rng.gauss(0.0, 0.01) for t in range(g - 24, g + 13)}
                cohorts[unit] = g if i < 3 else None
                strata[unit] = f's{stack}'
                clusters[unit] = f'c{i % 6}'
        return Panel(outcomes, cohorts, strata=strata, clusters=clusters)

    def test_horizon_standard_errors_match_event_study_and_return_no_estimate(self):
        panel = self._panel()
        spec = {**self.SPEC, 'cohorts': sorted(panel.cohort_sizes())}
        ses = w3.horizon_standard_errors(panel, spec, [3, 6, 12])
        self.assertEqual(sorted(ses), [3, 6, 12])
        for horizon, se in ses.items():
            self.assertIsInstance(se, float)
            reference = event_study(panel, e_min=spec['e_min'], e_max=spec['e_max'],
                                    post=list(range(0, horizon + 1)), control_group=spec['control_group'],
                                    anticipation=0, alpha=spec['alpha'], bootstrap=0, cohorts=spec['cohorts'])
            self.assertAlmostEqual(se, reference['overall']['analytic_se'], places=12)
        # The contract is that nothing in the returned object is a treated-versus-control estimate.
        self.assertEqual(set(ses), {3, 6, 12})
        self.assertTrue(all(isinstance(v, float) for v in ses.values()))

    def test_a_longer_horizon_needs_no_second_att_gt_pass(self):
        panel = self._panel()
        spec = {**self.SPEC, 'cohorts': sorted(panel.cohort_sizes())}
        cells, n_clusters = w3.horizon_cells(panel, spec)
        self.assertEqual(n_clusters, 6)
        self.assertLessEqual(min(cells), spec['e_min'])
        self.assertGreaterEqual(max(cells), spec['e_max'])

    def test_null_draws_carry_no_effect_and_are_deterministic(self):
        panel = self._panel()
        spec = {**self.SPEC, 'cohorts': sorted(panel.cohort_sizes()), 'placebo_shift': 3}
        params = calibrate(panel, anticipation=0)
        first = w3.null_draw_horizons(panel, spec, params, 11, [3, 12])
        again = w3.null_draw_horizons(panel, spec, params, 11, [3, 12])
        self.assertEqual(first, again)
        self.assertEqual(sorted(first['horizons']), [3, 12])
        for horizon in (3, 12):
            self.assertLess(abs(first['horizons'][horizon]['att']), 8 * first['horizons'][horizon]['se'])
        self.assertIsNotNone(first['pre_trend_p'])
        self.assertIsNotNone(first['placebo_date_p'])


if __name__ == '__main__':
    unittest.main()
