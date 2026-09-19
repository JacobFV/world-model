"""Staggered difference-in-differences engine validated on synthetic panels with known effects."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from worldmodel.causal import (FAILED_DIAGNOSTICS, IDENTIFIED_DID, NOT_ESTIMABLE, Panel, att_gt, compare_rankings,
                               did_verdict, evaluate_acceptance, event_study, load_registration, paired_sign_flip,
                               placebo_date_test, placebo_unit_test, run_did_design, score_event, stacked_did,
                               top_k_capture, twfe_static, validate_registration)
from worldmodel.causal.stats import chi2_sf, normal_ppf, ranks, solve, spearman
from worldmodel.causal.synthetic import staggered_panel

DYNAMIC = dict(n_units=600, periods=range(2000, 2020), cohorts={2004: 0.5, 2012: 0.5}, never_share=0.0,
               effect=lambda e, g: 0.3 * (e + 1))


def _true_overall(truth, post):
    return sum(truth['att_e'][e] for e in post) / len(post)


class StatsTests(unittest.TestCase):
    def test_distribution_helpers(self):
        self.assertAlmostEqual(normal_ppf(0.975), 1.959963984540054, places=9)
        self.assertAlmostEqual(normal_ppf(0.01), -2.3263478740408408, places=8)
        self.assertAlmostEqual(chi2_sf(3.841458820694124, 1), 0.05, places=9)
        self.assertAlmostEqual(chi2_sf(18.307038053275146, 10), 0.05, places=9)
        self.assertAlmostEqual(chi2_sf(100.0, 3), 1.6e-21, delta=1e-21)
        self.assertEqual(chi2_sf(0.0, 4), 1.0)

    def test_ranks_and_spearman(self):
        self.assertEqual(ranks([10, 20, 20, 5]), [2.0, 3.5, 3.5, 1.0])
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))

    def test_solve(self):
        x = solve([[4.0, 1.0], [1.0, 3.0]], [1.0, 2.0])
        self.assertAlmostEqual(x[0], 1 / 11)
        self.assertAlmostEqual(x[1], 7 / 11)
        with self.assertRaises(ValueError):
            solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 1.0])


class EstimatorTests(unittest.TestCase):
    def test_homogeneous_effect_recovered_by_every_estimator(self):
        panel, _ = staggered_panel(n_units=400, seed=1, effect=lambda e, g: 2.0)
        cs = event_study(panel, e_min=-4, e_max=4, bootstrap=0)
        self.assertAlmostEqual(cs['overall']['att'], 2.0, delta=0.05)
        self.assertAlmostEqual(stacked_did(panel, e_min=-3, e_max=3)['overall']['att'], 2.0, delta=0.05)
        self.assertAlmostEqual(twfe_static(panel)['beta'], 2.0, delta=0.05)

    def test_staggered_dynamic_effects_bias_twfe_but_not_callaway_santanna(self):
        panel, truth = staggered_panel(seed=3, **DYNAMIC)
        post = range(0, 7)
        target = _true_overall(truth, post)
        cs = event_study(panel, e_min=-4, e_max=6, bootstrap=0)
        twfe = twfe_static(panel)
        # Naive TWFE uses already-treated units as controls; here it even has the wrong sign.
        self.assertGreater(abs(twfe['beta'] - target), 0.5)
        self.assertLess(twfe['beta'], 0)
        self.assertAlmostEqual(cs['overall']['att'], target, delta=0.05)
        for row in cs['event_time']:
            expected = truth['att_e'].get(row['e'], 0.0)
            self.assertLess(abs(row['att'] - expected), 5 * row['se'] + 1e-9, row)
        stacked = stacked_did(panel, e_min=-3, e_max=3)
        self.assertAlmostEqual(stacked['overall']['att'], _true_overall(truth, range(0, 4)), delta=0.06)

    def test_not_yet_treated_controls_exclude_the_cohort_itself_and_treated_units(self):
        panel, _ = staggered_panel(seed=5, **DYNAMIC)
        gt = att_gt(panel)
        sizes = panel.cohort_sizes()
        for cell in gt['cells']:
            if cell['g'] == 2004 and cell['t'] >= 2012:
                self.fail('no not-yet-treated control exists after the last cohort is treated')
            if cell['g'] == 2012:
                self.assertLessEqual(cell['n_control'], sizes[2004])

    def test_pre_trend_violation_is_detected(self):
        bad, _ = staggered_panel(n_units=400, seed=7, treated_trend=0.05, effect=lambda e, g: 1.0)
        result = event_study(bad, e_min=-4, e_max=3, bootstrap=199, seed=1)
        self.assertLess(result['pre_trend']['wald']['p'], 0.001)
        self.assertLess(result['pre_trend']['sup_t']['p'], 0.01)
        self.assertFalse(placebo_date_test(bad, shift=3)['passed'])
        good, _ = staggered_panel(n_units=400, seed=7, effect=lambda e, g: 1.0)
        result = event_study(good, e_min=-4, e_max=3, bootstrap=199, seed=1)
        self.assertGreater(result['pre_trend']['wald']['p'], 0.01)
        self.assertTrue(placebo_date_test(good, shift=3)['passed'])

    def test_bootstrap_se_matches_analytic_and_bands_cover_pointwise(self):
        panel, _ = staggered_panel(n_units=500, seed=11, effect=lambda e, g: 0.5)
        result = event_study(panel, e_min=-3, e_max=3, bootstrap=499, seed=4)
        for row in result['event_time']:
            self.assertLess(0.75, row['se'] / row['analytic_se'], row)
            self.assertLess(row['se'] / row['analytic_se'], 1.33, row)
            self.assertLessEqual(row['band_low'], row['ci_low'])
            self.assertGreaterEqual(row['band_high'], row['ci_high'])
        self.assertGreater(result['bootstrap']['uniform_critical_value'], 1.96)

    def test_confidence_interval_coverage(self):
        covered = 0
        reps = 40
        for seed in range(reps):
            panel, truth = staggered_panel(n_units=120, seed=100 + seed, noise=0.3,
                                           effect=lambda e, g: 0.2 + 0.1 * e)
            post = range(0, 4)
            r = event_study(panel, e_min=-2, e_max=3, bootstrap=0)
            target = _true_overall(truth, post)
            covered += r['overall']['ci_low'] <= target <= r['overall']['ci_high']
        self.assertGreaterEqual(covered / reps, 0.85)

    def test_clustered_errors_widen_with_common_cluster_shocks(self):
        # Units share an outcome shock within clusters of 10 and treatment is assigned by cluster;
        # unit-level SEs then understate uncertainty.
        panel, _ = staggered_panel(n_units=400, seed=13, effect=lambda e, g: 0.0, clusters_per=10)
        shocked = {}
        import random
        rng = random.Random(1)
        shocks = {}
        for unit, series in panel.outcomes.items():
            c = panel.clusters[unit]
            shocked[unit] = {t: y + shocks.setdefault((c, t), rng.gauss(0, 1.0)) for t, y in series.items()}
        # Treatment is assigned by cluster (the first member's cohort), as with state-level policies.
        first = {}
        for unit in sorted(panel.outcomes):
            first.setdefault(panel.clusters[unit], panel.cohorts[unit])
        cohorts = {u: first[panel.clusters[u]] for u in panel.outcomes}
        clustered = Panel(shocked, cohorts, clusters=panel.clusters)
        unclustered = Panel(shocked, cohorts)
        se_c = event_study(clustered, e_min=-2, e_max=3, bootstrap=0)['overall']['se']
        se_u = event_study(unclustered, e_min=-2, e_max=3, bootstrap=0)['overall']['se']
        self.assertGreater(se_c, 1.5 * se_u)

    def test_stratification_removes_confounding_by_stratum_shocks(self):
        panel, truth = staggered_panel(n_units=800, seed=17, strata=4, stratum_shock_sd=0.0, selection_on_stratum=1.2,
                                       effect=lambda e, g: 0.0)
        # Give high-numbered (more often treated) strata a rising trend: confounded without strata.
        outcomes = {u: {t: y + (0.08 * (t - 2000) if panel.strata[u] in ('s2', 's3') else 0.0)
                        for t, y in s.items()} for u, s in panel.outcomes.items()}
        stratified = Panel(outcomes, panel.cohorts, strata=panel.strata)
        pooled = Panel(outcomes, panel.cohorts)
        naive = event_study(pooled, e_min=-3, e_max=3, bootstrap=0)['overall']['att']
        matched = event_study(stratified, e_min=-3, e_max=3, bootstrap=0)['overall']['att']
        self.assertGreater(abs(naive), 0.05)
        self.assertLess(abs(matched), 0.03)

    def test_anticipation_window_moves_the_base_period(self):
        panel, _ = staggered_panel(n_units=400, seed=19, effect=lambda e, g: 1.0, anticipation_effect=0.5)
        naive = event_study(panel, e_min=-3, e_max=2, bootstrap=0)
        allowed = event_study(panel, e_min=-3, e_max=2, anticipation=1, bootstrap=0)
        rows = {r['e']: r for r in allowed['event_time']}
        self.assertEqual(allowed['reference_event_time'], -2)
        self.assertAlmostEqual(rows[-1]['att'], 0.5, delta=0.05)
        self.assertAlmostEqual(rows[0]['att'], 1.0, delta=0.05)
        self.assertAlmostEqual(naive['overall']['att'], 0.5, delta=0.05)  # base period contaminated

    def test_placebo_unit_test_has_nominal_size(self):
        panel, _ = staggered_panel(n_units=300, seed=23, never_share=0.6, effect=lambda e, g: 1.0)
        observed = event_study(panel, e_min=-3, e_max=3, bootstrap=0)['overall']['att']
        result = placebo_unit_test(panel, replications=40, seed=2, e_min=-3, e_max=3, observed=observed)
        self.assertTrue(result['ran'])
        self.assertLessEqual(result['rejection_rate'], 0.15)
        self.assertLess(abs(result['placebo_mean']), 0.05)
        self.assertLess(result['permutation_p'], 0.05)
        few = placebo_unit_test(panel.subset(panel.treated_units()[:50]), replications=5, e_min=-3, e_max=3)
        self.assertFalse(few['ran'])


class RegistrationAndResultTests(unittest.TestCase):
    def registration(self):
        return {
            'schema': 'worldmodel.causal_registration/1', 'study_id': 'synthetic', 'registered_at': '2026-09-18',
            'question': 'Does fictional treatment raise a fictional outcome?',
            'identification_strategy': 'staggered_difference_in_differences',
            'treatment': {'definition': 'fictional'}, 'units': {'definition': 'fictional units'},
            'windows': {'e_min': -4, 'e_max': 3, 'post': [0, 1, 2, 3]},
            'controls': {'control_group': 'not_yet_treated'},
            'outcomes': {'primary': {'id': 'y', 'label': 'y', 'unit': 'units'}},
            'estimator': {'primary': 'callaway_santanna', 'estimand': 'ATT', 'anticipation': 0,
                          'robustness': ['stacked_did', 'twfe_static', 'never_treated_controls']},
            'inference': {'alpha': 0.05, 'bootstrap': 99, 'seed': 1, 'cluster': 'unit'},
            'placebo_tests': {'placebo_date': {'shift': 3}, 'placebo_unit': {'replications': 10, 'seed': 3}},
            'acceptance_criteria': [
                {'id': 'enough_treated', 'type': 'min_treated_units', 'value': 30},
                {'id': 'pre_trends', 'type': 'pre_trend_wald_p_min', 'value': 0.05},
                {'id': 'placebo_date', 'type': 'placebo_date_p_min', 'value': 0.05},
                {'id': 'placebo_unit', 'type': 'placebo_unit_rejection_rate_max', 'value': 0.2},
                {'id': 'stacked_agrees', 'type': 'robustness_ci_overlap', 'against': 'stacked_ci'}],
            'assumptions': ['fictional data'], 'data': {'inputs': []},
            'outcome_data_examined_before_registration': 'none'}

    def test_validation_rejects_incomplete_or_unknown_criteria(self):
        reg = self.registration()
        validate_registration(reg)
        broken = dict(reg); broken.pop('assumptions')
        with self.assertRaises(ValueError):
            validate_registration(broken)
        broken = dict(reg, acceptance_criteria=[{'id': 'x', 'type': 'looks_good'}])
        with self.assertRaises(ValueError):
            validate_registration(broken)

    def test_uncommitted_registration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(['git', *a], cwd=tmp, check=True, capture_output=True)
            run('init', '-q'); run('config', 'user.email', 't@example.invalid'); run('config', 'user.name', 't')
            path = Path(tmp) / 'reg.json'
            path.write_text(json.dumps(self.registration()))
            with self.assertRaises(ValueError):
                load_registration(path)
            run('add', 'reg.json'); run('commit', '-qm', 'register')
            document, status = load_registration(path)
            self.assertTrue(status['committed_clean'])
            self.assertEqual(len(status['commit']), 40)
            path.write_text(json.dumps(dict(self.registration(), question='changed after commit')))
            with self.assertRaises(ValueError):
                load_registration(path)

    def test_end_to_end_design_passes_on_clean_data_and_fails_on_trending_data(self):
        status = {'path': 'synthetic', 'sha256': '0' * 64, 'commit': None, 'committed_clean': False}
        good, _ = staggered_panel(n_units=300, seed=29, never_share=0.5, effect=lambda e, g: 0.4)
        result = run_did_design(good, self.registration(), status, outcome={'id': 'y', 'label': 'y', 'unit': 'units'})
        self.assertEqual(result['identification']['label'], IDENTIFIED_DID, result['acceptance'])
        self.assertIn('increase', result['verdict'])
        self.assertIn('Parallel trends', ' '.join(result['identification']['assumptions']))
        self.assertEqual(len(result['result_id']), 64)
        json.dumps(result)
        bad, _ = staggered_panel(n_units=300, seed=29, never_share=0.5, treated_trend=0.06, effect=lambda e, g: 0.4)
        result = run_did_design(bad, self.registration(), status, outcome={'id': 'y', 'label': 'y', 'unit': 'units'})
        self.assertEqual(result['identification']['label'], FAILED_DIAGNOSTICS)
        self.assertIn('No causal claim', result['verdict'])

    def test_unmeasured_criteria_fail_and_small_samples_are_not_estimable(self):
        acceptance = evaluate_acceptance([{'id': 'p', 'type': 'placebo_date_p_min', 'value': 0.05}], {})
        self.assertFalse(acceptance[0]['passed'])
        overall = {'att': 0.1, 'ci_low': -0.1, 'ci_high': 0.3}
        label, _ = did_verdict(evaluate_acceptance([{'id': 'n', 'type': 'min_treated_units', 'value': 10}],
                                                   {'treated_units': 3}), overall, outcome_label='y')
        self.assertEqual(label, NOT_ESTIMABLE)
        label, text = did_verdict(evaluate_acceptance([{'id': 'n', 'type': 'min_treated_units', 'value': 1}],
                                                      {'treated_units': 3}), overall, outcome_label='y')
        self.assertEqual(label, IDENTIFIED_DID)
        self.assertIn('null', text)


class RankingTests(unittest.TestCase):
    def test_top_k_capture_and_scoring(self):
        self.assertEqual(top_k_capture([3, 2, 1, 0], [30, 20, 10, 0], 2), 1.0)
        self.assertEqual(top_k_capture([0, 1, 2, 3], [30, 20, 10, 0], 2), 0.0)
        units = ['a', 'b', 'c', 'd', 'e']
        scored = score_event(units, {'good': dict(zip(units, [5, 4, 3, 2, 1])), 'bad': dict(zip(units, [1, 2, 3, 4, 5]))},
                             dict(zip(units, [9, 7, 5, 3, 1])), k_frac=0.4)
        self.assertAlmostEqual(scored['good']['spearman'], 1.0)
        self.assertAlmostEqual(scored['bad']['spearman'], -1.0)
        self.assertEqual(scored['k'], 2)

    def test_paired_sign_flip(self):
        exact = paired_sign_flip([0.1, 0.2, 0.3, 0.4])
        self.assertEqual(exact['method'], 'exact')
        self.assertAlmostEqual(exact['p'], 1 / 16)
        mc = paired_sign_flip([0.1] * 30, replications=999)
        self.assertLess(mc['p'], 0.01)
        null = paired_sign_flip([0.1, -0.1] * 10, replications=999)
        self.assertGreater(null['p'], 0.3)
        events = [{'a': {'spearman': 0.5}, 'b': {'spearman': 0.1}}] * 5
        cmp = compare_rankings(events, 'a', 'b')
        self.assertEqual(cmp['events_candidate_better'], 5)
        self.assertAlmostEqual(cmp['mean_difference'], 0.4)


if __name__ == '__main__':
    unittest.main()
