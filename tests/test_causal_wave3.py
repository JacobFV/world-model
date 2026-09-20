"""Wave-3 on fictional inputs: months as periods, a top-decile dose, the exact unit window, the rule
that a design's power is measured without reading its effect, and the registered study runner.

Every panel here is built in this file and every effect in it was planted here. The runner is checked
against planted effects, not against the real monthly panel, because the contrast the real panel would
give is the thing the registration exists to bind.
"""
import importlib.util
import json
import math
import random
import unittest
from pathlib import Path

from worldmodel.causal.did import event_study
from worldmodel.causal.power import calibrate
from worldmodel.causal.registration import validate_registration
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

# -- the registered wave-3 study ----------------------------------------------------------------------
#
# Every panel below is built in this file and every effect in it was planted here. No test reads the
# real monthly panel: producing a treated-versus-control contrast is what the runner is for, and the
# design that binds it is registered before the contrast is read, never after.

STATUS = {'path': 'fixtures/registration.json', 'sha256': 'f' * 64, 'commit': None, 'committed_clean': False}

#: A measured power table in the shape the draft records: two horizons correctly sized, one outside the
#: size limit and therefore registered as a bound.
FIXTURE_SIZES = {
    '3': {'mde_80': 0.010, 'null_rejection_rate': 0.075, 'size_limit': 0.1, 'size_acceptable': True,
          'verdict': 'can detect the effect worth finding'},
    '6': {'mde_80': 0.012, 'null_rejection_rate': 0.090, 'size_limit': 0.1, 'size_acceptable': True,
          'verdict': 'can detect the effect worth finding'},
    '12': {'mde_80': 0.015, 'null_rejection_rate': 0.115, 'size_limit': 0.1, 'size_acceptable': False,
           'verdict': 'can only bound: the minimum detectable effect clears but the test over-rejects a true null'}}


def fixture_registration(**overrides):
    """A registration shaped like the wave-3 draft, sized so that the fixtures below can pass it."""
    registration = {
        'schema': 'worldmodel.causal_registration/1', 'study_id': 'fixture_monthly_dose', 'registered_at': None,
        'question': 'fictional', 'identification_strategy': 'staggered_difference_in_differences',
        'treatment': {'estimated_cohort_months': ['2010-01', '2014-12'], 'match_window': [-24, -13],
                      'treated_min_usd_per_capita': 300.0, 'control_max_usd_per_capita': 1.0,
                      'dose_quantile': 0.9, 'dose_quantile_over': 'damaged'},
        'units': {'definition': '(disaster, county) pairs, fictional'},
        'windows': {'e_min': -6, 'e_max': 12, 'calendar_months': ['2007-05', '2026-07'], 'reference_event_time': -1,
                    'horizons': [3, 6, 12], 'primary_horizon_months': 6, 'long_lead_window_non_gating': -12},
        'controls': {'control_group': 'never_treated'},
        'outcomes': {'primary': {'id': 'laus_log_employment', 'label': 'log LAUS employment', 'unit': 'log points'},
                     'secondary': [{'id': 'laus_log_labor_force', 'label': 'log LAUS labour force',
                                    'unit': 'log points'},
                                   {'id': 'laus_unemployment_rate', 'label': 'LAUS unemployment rate',
                                    'unit': 'percentage points'}]},
        'estimator': {'primary': 'callaway_santanna_unconditional_within_disaster_and_growth_half',
                      'estimand': 'fictional', 'anticipation': 0, 'balance': None,
                      'robustness': ['stacked_did', 'twfe_static'],
                      'non_gating_robustness': ['unmatched_strata_disaster_only: strata = disaster',
                                                'long_lead_window: e_min = -12, reported, not gating']},
        'inference': {'cluster': 'state', 'bootstrap': 99, 'seed': 7, 'alpha': 0.05},
        'placebo_tests': {'placebo_date': {'shift': 6},
                          'placebo_unit': {'replications': 10, 'seed': 13, 'min_never_treated': 20}},
        'acceptance_criteria': [{'id': 'enough_treated_counties', 'type': 'min_treated_units', 'value': 20},
                                {'id': 'enough_state_clusters', 'type': 'min_clusters', 'value': 5},
                                {'id': 'no_pre_trends', 'type': 'pre_trend_wald_p_min', 'value': 0.05},
                                {'id': 'placebo_date_null', 'type': 'placebo_date_p_min', 'value': 0.05},
                                {'id': 'placebo_unit_size', 'type': 'placebo_unit_rejection_rate_max', 'value': 0.1},
                                {'id': 'stacked_agrees', 'type': 'robustness_ci_overlap', 'against': 'stacked_ci'}],
        'assumptions': ['the panel is fictional'],
        'power': {'measured': {'horizons': FIXTURE_SIZES, 'verdict': {'can_only_bound_at': ['12 months']}}},
        'does_not_establish': ['A fixture establishes nothing.'],
        'data': {'inputs': []},
        'outcome_data_examined_before_registration': 'none: the panel is fictional',
    }
    registration.update(overrides)
    return registration


def primary_only(registration):
    """The same registration with one outcome, for tests that do not need the secondary ones."""
    return {**registration, 'outcomes': {'primary': registration['outcomes']['primary'], 'secondary': []}}


def fixture_inputs(effect=0.0, early_lead=0.0, seed=11, stacks=20, treated=3, controls=7, noise=0.004, states=16):
    """Fictional ``(units, laus)``: 20 declarations, 3 damaged and 7 undamaged counties each.

    ``effect`` is planted on the damaged counties' labour force from event month 0 on, so it reaches the
    two log outcomes and not the unemployment rate. ``early_lead`` is planted on event months -12..-8
    only: it is invisible to the gating lead window (-6..-2) and to the match window (g-24, g-13), and
    visible only to the longer, non-gating one.
    """
    rng = random.Random(seed)
    units, laus = [], {}
    for stack in range(stacks):
        g = w3.month_index('2011-01') + 2 * stack
        for i in range(treated + controls):
            fips = f'{10 + stack % states:02d}{stack * 10 + i:03d}'
            damaged = i < treated
            units.append({'unit': f'dr{stack}|{fips}', 'disaster': f'dr{stack}', 'fips': fips, 'g': g,
                          'dose': 1000.0 if damaged else 0.5, 'role': None})
            level, months = rng.gauss(0.0, 0.1), {}
            for month in range(g - 30, g + 18):
                e = month - g
                value = 11.0 + level + 0.001 * e + rng.gauss(0.0, noise)
                if damaged and e >= 0:
                    value += effect
                if damaged and -12 <= e <= -8:
                    value += early_lead
                months[month] = (5.0 + rng.gauss(0.0, 0.2), math.exp(value), '2020-01-01')
            laus[fips] = months
    return units, laus


def run_fixture(registration=None, **kwargs):
    """``{outcome id: result record}`` from the runner's core on fictional inputs."""
    registration = registration or fixture_registration()
    units, laus = fixture_inputs(**kwargs)
    results = w3.monthly_dose_results(registration, STATUS, units, laus)
    return {r['estimates']['outcome']['id']: r for r in results}


class DraftDesignTests(unittest.TestCase):
    """The drafted wave-3 design, read as a registration. Nothing here touches outcome data."""

    DRAFT = (Path(__file__).resolve().parents[1]
             / 'examples/natural-experiments/drafts/fema_monthly_dose_county_employment.draft.json')

    @classmethod
    def setUpClass(cls):
        cls.draft = json.loads(cls.DRAFT.read_text())

    def test_the_draft_is_a_valid_registration(self):
        self.assertEqual(validate_registration(self.draft)['study_id'], 'fema_monthly_dose_county_employment')
        self.assertIsNone(self.draft['registered_at'])

    def test_month_dated_windows_resolve_and_nothing_else_is_derived(self):
        resolved = w3.resolved_registration(self.draft)
        self.assertEqual(resolved['windows']['post'], list(range(0, 7)))         # the primary estimand, 0..6
        self.assertEqual(resolved['treatment']['estimated_cohorts'],
                         [w3.month_index('2009-05'), w3.month_index('2024-07')])
        added = {k: v for k, v in resolved['windows'].items() if self.draft['windows'].get(k) != v}
        self.assertEqual(sorted(added), ['post'])
        self.assertEqual(sorted(k for k, v in resolved['treatment'].items() if self.draft['treatment'].get(k) != v),
                         ['estimated_cohorts'])
        self.assertEqual({k: v for k, v in resolved.items() if k not in ('windows', 'treatment')},
                         {k: v for k, v in self.draft.items() if k not in ('windows', 'treatment')})

    def test_the_bounding_only_horizons_are_the_ones_measured_outside_the_size_limit(self):
        self.assertEqual(w3.bounding_only_horizons(self.draft), [12, 24])
        self.assertEqual(self.draft['windows']['primary_horizon_months'], 6)
        statement = w3.bounding_only_statement(self.draft)
        self.assertIn('BOUNDING ONLY', statement)
        self.assertIn('12 months 11.5%', statement)
        self.assertIn('24 months 11.5%', statement)
        self.assertIn('not an effect', statement)

    def test_a_design_whose_prose_and_measured_sizes_disagree_is_refused(self):
        draft = json.loads(self.DRAFT.read_text())
        draft['power']['measured']['horizons']['24']['size_acceptable'] = True
        with self.assertRaises(ValueError):
            w3.bounding_only_horizons(draft)
        draft = json.loads(self.DRAFT.read_text())
        draft['power']['measured']['horizons'].pop('24')
        with self.assertRaises(ValueError):
            w3.bounding_only_horizons(draft)

    def test_the_gating_lead_window_is_shorter_than_the_one_reported_beside_it(self):
        self.assertEqual(self.draft['windows']['e_min'], -6)
        self.assertEqual(self.draft['windows']['long_lead_window_non_gating'], -12)
        self.assertLess(self.draft['windows']['long_lead_window_non_gating'], self.draft['windows']['e_min'])
        # the window used to match still ends before every tested lead
        self.assertLess(self.draft['treatment']['match_window'][1],
                        self.draft['windows']['long_lead_window_non_gating'])

    def test_every_variant_the_draft_names_is_implemented_and_none_gates(self):
        roles = w3.robustness_roles(self.draft)
        self.assertEqual({name: role['gating'] for name, role in roles.items()},
                         {'stacked_did': True, 'twfe_static': False,
                          'unmatched_strata_disaster_only': False, 'long_lead_window': False})
        extra = w3.non_gating_panels(self.draft, unmatched='a panel')
        self.assertEqual(extra['unmatched_strata_disaster_only'], 'a panel')
        self.assertEqual(extra['long_lead_window']['e_min'], -12)
        self.assertEqual(extra['long_lead_window']['seed'], self.draft['inference']['seed'])

    def test_an_unimplemented_variant_is_refused_rather_than_silently_dropped(self):
        draft = json.loads(self.DRAFT.read_text())
        draft['estimator']['non_gating_robustness'].append('donut_hole: a variant nobody wrote')
        with self.assertRaises(ValueError):
            w3.non_gating_panels(draft, unmatched=None)

    def test_the_study_is_wired_into_the_runner(self):
        self.assertEqual(w3.RUNNERS, {'fema_monthly_dose_county_employment': w3.run_fema_monthly_dose})
        path = Path(__file__).resolve().parents[1] / 'examples/natural-experiments/run_studies.py'
        spec = importlib.util.spec_from_file_location('run_studies_for_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(module.ALL['fema_monthly_dose_county_employment'], w3.run_fema_monthly_dose)
        self.assertEqual(module.MODULES['fema_monthly_dose_county_employment'], 'studies_wave3')


class MonthlyDoseStudyTests(unittest.TestCase):
    """The runner on fictional panels: one with a planted effect, one with none."""

    @classmethod
    def setUpClass(cls):
        cls.registration = fixture_registration()
        cls.planted = run_fixture(effect=0.05)
        cls.null = run_fixture(effect=0.0)

    def test_the_planted_effect_is_recovered_on_every_outcome_that_carries_it(self):
        primary = self.planted['laus_log_employment']['estimates']['primary']['overall']
        self.assertAlmostEqual(primary['att'], 0.05, delta=0.005)
        self.assertGreater(primary['ci_low'], 0.0)
        self.assertEqual(self.planted['laus_log_employment']['identification']['label'], 'quasi_experimental_did')
        self.assertTrue(all(a['passed'] for a in self.planted['laus_log_employment']['acceptance']))
        self.assertIn('increase', self.planted['laus_log_employment']['verdict'])
        # planted on the labour force, so the two log outcomes carry it and the rate does not
        self.assertAlmostEqual(self.planted['laus_log_labor_force']['estimates']['primary']['overall']['att'],
                               0.05, delta=0.005)
        rate = self.planted['laus_unemployment_rate']['estimates']['primary']['overall']
        self.assertLess(rate['ci_low'], 0.0)
        self.assertGreater(rate['ci_high'], 0.0)

    def test_a_panel_with_no_effect_gives_an_identified_null(self):
        for outcome, record in self.null.items():
            overall = record['estimates']['primary']['overall']
            self.assertLess(overall['ci_low'], 0.0, outcome)
            self.assertGreater(overall['ci_high'], 0.0, outcome)
            self.assertEqual(record['identification']['label'], 'quasi_experimental_did', outcome)
            self.assertIn('null', record['verdict'], outcome)
        self.assertLess(abs(self.null['laus_log_employment']['estimates']['primary']['overall']['att']), 0.01)

    def test_every_registered_outcome_is_returned_with_its_role(self):
        self.assertEqual(sorted(self.planted), ['laus_log_employment', 'laus_log_labor_force',
                                                'laus_unemployment_rate'])
        self.assertEqual(self.planted['laus_log_employment']['estimates']['outcome']['role'], 'primary')
        self.assertEqual(self.planted['laus_unemployment_rate']['estimates']['outcome']['role'], 'secondary')
        self.assertEqual(self.planted['laus_unemployment_rate']['estimates']['outcome']['unit'], 'percentage points')

    def test_the_horizon_rows_carry_the_role_the_registration_gives_them(self):
        rows = {row['horizon_months']: row for row in self.planted['laus_log_employment']['estimates']['horizons']}
        self.assertEqual(sorted(rows), [3, 6, 12])
        self.assertEqual({h: row['role'] for h, row in rows.items()},
                         {3: 'reported', 6: 'primary', 12: 'bounding_only'})
        self.assertEqual([h for h, row in rows.items() if row['bounding_only']], [12])
        self.assertNotIn('reported_as', rows[3])
        self.assertNotIn('reported_as', rows[6])
        self.assertIn('may not be reported as an effect', rows[12]['reported_as'])
        self.assertEqual(rows[12]['measured_before_registration']['null_rejection_rate'], 0.115)
        self.assertEqual(rows[12]['measured_before_registration']['size_limit'], 0.1)
        # the primary row is the primary estimate, not a second estimate of it
        self.assertEqual(rows[6]['overall'], self.planted['laus_log_employment']['estimates']['primary']['overall'])
        self.assertEqual(rows[3]['estimand'], 'equal-weight mean of ATT(e) over event months 0..3')

    def test_a_rejection_at_a_bounding_horizon_is_still_reported_as_a_bound(self):
        record = self.planted['laus_log_employment']
        rows = {row['horizon_months']: row for row in record['estimates']['horizons']}
        self.assertGreater(rows[12]['overall']['ci_low'], 0.0)        # the 12-month horizon does reject
        self.assertTrue(rows[12]['bounding_only'])                    # and is still not an effect
        self.assertIn('not an effect', rows[12]['reported_as'])
        # the label and the verdict come from the primary horizon alone
        span = f"{rows[6]['overall']['att']:+.4f} log points"
        self.assertIn(span, record['verdict'])
        self.assertTrue(any('BOUNDING ONLY' in line for line in record['does_not_establish']))

    def test_the_result_says_what_it_does_not_establish(self):
        record = self.planted['laus_log_employment']
        self.assertIn('A fixture establishes nothing.', record['does_not_establish'])
        bounds = [line for line in record['does_not_establish'] if 'BOUNDING ONLY' in line]
        self.assertEqual(len(bounds), 1)
        self.assertIn('12 months 11.5%', bounds[0])
        self.assertIn('is not reported as one here', bounds[0])
        self.assertEqual(len(record['notes']), 3)

    def test_the_acceptance_criteria_are_the_registration_s_own(self):
        for record in self.planted.values():
            self.assertEqual([(a['id'], a['type'], a['threshold']) for a in record['acceptance']],
                             [(c['id'], c['type'], c.get('value')) for c in self.registration['acceptance_criteria']])
        gates = {a['id']: a for a in self.planted['laus_log_employment']['acceptance']}
        record = self.planted['laus_log_employment']
        self.assertEqual(gates['no_pre_trends']['value'], record['diagnostics']['pre_trend']['wald']['p'])
        self.assertEqual(gates['placebo_date_null']['value'], record['diagnostics']['placebo_date']['p'])
        self.assertEqual(gates['placebo_unit_size']['value'], record['diagnostics']['placebo_unit']['rejection_rate'])
        self.assertEqual(gates['enough_treated_counties']['value'], 60)

    def test_the_panel_is_the_registered_one(self):
        facts = self.planted['laus_log_employment']['data']['treatment_facts']
        self.assertEqual((facts['treated_in_panel'], facts['controls_in_panel']), (60, 140))
        self.assertEqual(facts['disasters_in_panel'], 20)
        self.assertEqual(facts['cohort_months_in_panel'], ['2011-01', '2014-03'])
        panel = self.planted['laus_log_employment']['data']['panel']
        self.assertEqual(panel['strata'], 40)                       # disaster x growth half
        self.assertEqual(panel['clusters'], 16)                     # state
        self.assertEqual(panel['observations'], 200 * 37)           # event months -24..+12, clipped per stack


class GatingTests(unittest.TestCase):
    """What gates the identification label and what only reports beside it."""

    @classmethod
    def setUpClass(cls):
        cls.registration = primary_only(fixture_registration())
        cls.record = run_fixture(registration=cls.registration, early_lead=0.02)['laus_log_employment']

    def test_a_pre_trend_outside_the_gating_window_fails_only_the_non_gating_variant(self):
        gate = {a['id']: a for a in self.record['acceptance']}['no_pre_trends']
        self.assertEqual(self.record['estimates']['primary']['pre_trend']['leads'], [-6, -5, -4, -3, -2])
        self.assertTrue(gate['passed'])
        self.assertEqual(gate['value'], self.record['diagnostics']['pre_trend']['wald']['p'])
        long_lead = self.record['estimates']['robustness']['long_lead_window']
        self.assertEqual(long_lead['pre_trend']['leads'], list(range(-12, -1)))
        self.assertLess(long_lead['pre_trend']['wald']['p'], 0.05)          # the longer window sees it
        self.assertEqual(self.record['identification']['label'], 'quasi_experimental_did')   # and does not gate

    def test_only_the_stacked_estimate_gates(self):
        roles = self.record['estimates']['robustness_roles']
        self.assertEqual({name: role['gating'] for name, role in roles.items()},
                         {'stacked_did': True, 'twfe_static': False,
                          'unmatched_strata_disaster_only': False, 'long_lead_window': False})
        self.assertEqual(sorted(self.record['estimates']['robustness']), sorted(roles))
        self.assertEqual([a['id'] for a in self.record['acceptance'] if a['type'] == 'robustness_ci_overlap'],
                         ['stacked_agrees'])
        overlap = {a['id']: a for a in self.record['acceptance']}['stacked_agrees']
        self.assertEqual(overlap['value'][1],
                         [self.record['estimates']['robustness']['stacked_did']['overall']['ci_low'],
                          self.record['estimates']['robustness']['stacked_did']['overall']['ci_high']])
        self.assertEqual(roles['long_lead_window']['registered_under'], 'estimator.non_gating_robustness')

    def test_the_non_gating_variants_are_the_same_design_reported_beside_it(self):
        unmatched = self.record['estimates']['robustness']['unmatched_strata_disaster_only']
        self.assertEqual(unmatched['reference_event_time'], -1)
        self.assertEqual(unmatched['control_group'], 'never_treated')
        self.assertIsNotNone(unmatched['overall'])


class AcceptanceAndDoseTests(unittest.TestCase):
    def test_a_treated_unit_gate_above_the_sample_makes_the_design_not_estimable(self):
        registration = primary_only(fixture_registration(
            acceptance_criteria=[{'id': 'enough_treated_counties', 'type': 'min_treated_units', 'value': 1000}]))
        record = run_fixture(registration=registration, effect=0.05)['laus_log_employment']
        self.assertEqual([(a['id'], a['passed'], a['value']) for a in record['acceptance']],
                         [('enough_treated_counties', False, 60)])
        self.assertEqual(record['identification']['label'], 'not_estimable')
        self.assertIn('is not an effect', record['verdict'])

    def test_roles_follow_the_pinned_threshold_and_not_this_sample_s_quantile(self):
        registration = primary_only(fixture_registration())
        units, laus = fixture_inputs()
        for unit in units[:2]:                      # two damaged pairs into the excluded middle
            unit['dose'] = 100.0
        record = w3.monthly_dose_results(registration, STATUS, units, laus)[0]
        facts = record['data']['treatment_facts']
        self.assertEqual(facts['dose_threshold_usd_per_capita'], 300.0)
        self.assertEqual((facts['treated'], facts['middle'], facts['control']), (58, 2, 140))
        self.assertEqual(facts['treated_in_panel'], 58)
        self.assertEqual(facts['dose_quantile_recomputed_on_this_sample'], 1000.0)
        self.assertIn('pinned before any outcome was read', facts['dose_threshold_source'])

if __name__ == '__main__':
    unittest.main()
