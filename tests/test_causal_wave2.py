"""Wave-2 natural-experiment code on fictional inputs: dose units, tariff changes through a concordance,
programme waves, partner-share panels, the cluster placebo test, and the power / negative-control suite."""
import gzip
import math
import random
import statistics
import tempfile
import unittest
from pathlib import Path

from worldmodel.causal import Panel, evaluate_acceptance, run_did_design
from worldmodel.causal.placebo import placebo_cluster_test
from worldmodel.causal.power import (calibrate, minimum_detectable_effect, null_draws, panel_from_dict, panel_to_dict,
                                     power_at, ranking_test_power, simulate_null_panel, summarize_draws)
from worldmodel.causal.sources import load_baci_imports
from worldmodel.causal.sources_wave2 import baci_fields, load_baci92_partner_share
from worldmodel.causal.stats import normal_ppf
from worldmodel.causal import studies_wave2 as w2
from worldmodel.causal.synthetic import staggered_panel


class FakeStore:
    def __init__(self, records):
        self._records = records

    def records(self, ref):
        return iter(self._records)


def _event(kind, attributes):
    return {'event_type': kind, 'attributes': attributes}


class SplitTests(unittest.TestCase):
    def test_split_by_rank_is_balanced_and_deterministic(self):
        values = {f'u{i}': float(i % 7) for i in range(30)}
        groups = w2.split_by_rank(values, 3)
        self.assertEqual(sorted(groups.values()).count(0), 10)
        self.assertEqual(sorted(groups.values()).count(2), 10)
        self.assertEqual(groups, w2.split_by_rank(dict(reversed(list(values.items()))), 3))
        self.assertLess(max(values[k] for k, g in groups.items() if g == 0),
                        min(values[k] for k, g in groups.items() if g == 2) + 1e-9)


class FemaDoseTests(unittest.TestCase):
    TREATMENT = {'dose_incident_types': ['Tornado'], 'quiet_period_incident_types': ['Tornado', 'Flood'],
                 'incident_years': [2000, 2010], 'quiet_years': 3, 'treated_min_usd_per_capita': 100.0,
                 'control_max_usd_per_capita': 1.0, 'match_window': [-8, -5]}

    def _declarations(self):
        return {'fema:disaster:1': {'type': 'Tornado', 'begin': '2005-04-01', 'end': '2005-04-03',
                                    'counties': {'01001', '01003', '01005', '01007', '01009'}},
                'fema:disaster:2': {'type': 'Flood', 'begin': '2003-06-01', 'end': '2003-06-02', 'counties': {'01009'}},
                'fema:disaster:3': {'type': 'Hurricane', 'begin': '2005-09-01', 'end': '2005-09-02', 'counties': {'01001'}}}

    def test_roles_quiet_period_and_population(self):
        damage = {'01001': [('2005-04-02', 2_000_000.0), ('2005-05-01', 9e9)],  # second row outside the incident
                  '01003': [('2005-04-02', 5.0)], '01005': [('2005-04-01', 50_000.0)], '01009': [('2005-04-02', 1e9)]}
        population = {'01001': {2004: 10_000.0}, '01003': {2004: 10_000.0}, '01005': {2004: 10_000.0},
                      '01009': {2004: 10_000.0}}
        units, facts = w2.fema_dose_units(self._declarations(), damage, population, self.TREATMENT)
        roles = {u['fips']: u['role'] for u in units}
        self.assertEqual(roles, {'01001': 'treated', '01003': 'control', '01005': 'middle'})
        self.assertEqual(facts['excluded_recent_other_disaster'], 1)  # 01009 had a flood in 2003
        self.assertEqual(facts['excluded_no_population'], 1)  # 01007
        self.assertAlmostEqual(next(u['dose'] for u in units if u['fips'] == '01001'), 200.0)

    def test_panels_match_within_disaster(self):
        units = [{'unit': f'd1|0100{i}', 'disaster': 'd1', 'fips': f'0100{i}', 'g': 2005,
                  'role': 'treated' if i < 2 else 'control', 'dose': 0.0} for i in range(6)]
        units.append({'unit': 'd1|01009', 'disaster': 'd1', 'fips': '01009', 'g': 2005, 'role': 'middle', 'dose': 5.0})
        series = {f'0100{i}': {str(y): 100.0 * math.exp(0.01 * i * (y - 1990)) for y in range(1990, 2011)} for i in range(5)}
        series['01005'] = {'2004': 50.0}  # lacks the match window
        matched, unmatched, drop = w2.fema_dose_panels(units, series, calendar=[1990, 2010], match_window=[-8, -5])
        self.assertEqual(drop['units_without_match_window_outcome'], 1)
        self.assertEqual(len(matched.units), 5)
        self.assertEqual({matched.strata[u] for u in matched.units}, {'d1|lo', 'd1|hi'})
        self.assertEqual({unmatched.strata[u] for u in unmatched.units}, {'d1'})
        self.assertEqual(matched.cohorts['d1|01000'], 2005)
        self.assertIsNone(matched.cohorts['d1|01004'])
        self.assertEqual(matched.clusters['d1|01000'], '01')


class TariffTests(unittest.TestCase):
    def test_one_to_one_drops_splits_and_merges(self):
        recs = [{'kind': 'assertion', 'predicate': 'maps_to', 'attributes': {'crosswalk': 'x'}, 'subject': f'hs22:{s}',
                 'object': f'hs17:{t}'} for s, t in [('000001', '100001'), ('000002', '100002'), ('000002', '100003'),
                                                     ('000004', '100004'), ('000005', '100004')]]
        recs.append({'kind': 'assertion', 'predicate': 'maps_to', 'attributes': {'crosswalk': 'y'},
                     'subject': 'hs22:000009', 'object': 'hs17:100009'})
        self.assertEqual(w2.one_to_one(FakeStore(recs), None, 'x'), {'000001': '100001'})

    def test_series_through_concordance_and_first_changes(self):
        def obs(rep, rev, code, year, value):
            return {'kind': 'observation', 'metric': 'mfn_applied_tariff_simple_avg', 'value': value, 'subject': rep,
                    'valid_from': f'{year}-01-01', 'dimensions': {'hs_revision': rev, 'product': f'hs:{code}'}}
        recs = [obs('iso3:AAA', 'HS2017', '100001', y, 10.0) for y in (2018, 2019, 2020, 2021)]
        recs.append(obs('iso3:AAA', 'HS2022', '000001', 2022, 7.0))  # decrease of 3 pp across the revision switch
        recs += [obs('iso3:AAA', 'HS2017', '100002', y, v) for y, v in ((2018, 5.0), (2019, 8.0))]  # increase
        recs += [obs('iso3:AAA', 'HS2017', '100003', y, v) for y, v in ((2018, 5.0), (2020, 1.0))]  # gap
        recs += [obs('iso3:AAA', 'HS2017', '100004', y, 4.0) for y in (2018, 2019)]  # never
        recs.append(obs('iso3:AAA', 'HS2017', '100005', 2019, 4.0))  # not in 2018 universe
        series, universe = w2.mfn_series_hs17(FakeStore(recs), None, {'000001': '100001'})
        self.assertEqual(series[('iso3:AAA', '100001')][2022], 7.0)
        self.assertNotIn(('iso3:AAA', '100005'), universe)
        first = w2.first_mfn_changes(series, universe, min_change=0.5)
        cohorts, excluded, facts = w2.tariff_decrease_cohorts(first, max_decrease=-2.0)
        self.assertEqual(cohorts, {('iso3:AAA', '100001'): 2022})
        self.assertEqual(excluded, {('iso3:AAA', '100002'), ('iso3:AAA', '100003')})
        self.assertEqual(facts['first_change_spans_reporting_gap'], 1)
        self.assertEqual(facts['never_changed'], 1)

    def test_panels_require_concordance_and_positive_years(self):
        universe = {('iso3:AAA', f'1000{i:02d}') for i in range(9)}
        flows = {}
        for i in range(9):
            for y in range(2009, 2025):
                flows[('iso3:AAA', f'9000{i:02d}', y)] = (100.0 * (1 + i) * (1.0 + 0.01 * i) ** (y - 2009), 1.0, 1, 0)
        flows[('iso3:AAA', '900007', 2013)] = (0.0, 0.0, 1, 1)
        hs17_to_hs92 = {f'1000{i:02d}': f'9000{i:02d}' for i in range(8)}  # 100008 has no one-to-one code
        cohorts = {('iso3:AAA', '100000'): 2020}
        matched, unmatched, facts = w2.tariff_decrease_panels(
            universe, cohorts, set(), hs17_to_hs92, flows, outcome_id='baci92_log_import_value', calendar=(2009, 2024),
            positive_years=[2009, 2013, 2017], growth_window=[2009, 2013])
        self.assertEqual(facts, {'units_without_one_to_one_hs92': 1, 'units_without_positive_required_years': 1})
        self.assertEqual(len(matched.units), 7)
        self.assertEqual(len({matched.strata[u] for u in matched.units}), 3)
        self.assertEqual(matched.cohorts['iso3:AAA|100000'], 2020)
        self.assertEqual(matched.clusters['iso3:AAA|100000'], 'iso3:AAA|10')
        self.assertEqual({unmatched.strata[u] for u in unmatched.units}, {'iso3:AAA'})


class SanctionsTests(unittest.TestCase):
    def test_programme_waves(self):
        def ev(date, programs, source='ofac_sanctions'):
            return _event('sanctions_designation', {'date': date, 'programs': programs, 'source': {'dataset': source}})
        recs = [ev('2014-03-01', ['UKRAINE-EO13662']) for _ in range(5)] + [ev('2013-01-01', ['MAGNIT']) for _ in range(9)]
        recs += [ev('2010-01-01', ['IRAN']) for _ in range(4)] + [ev('2011-01-01', ['IRAN', 'SDGT']) for _ in range(6)]
        recs += [ev('2011-01-01', ['IRAN'], source='other_sanctions_lists') for _ in range(20)]
        targets = {'RUS': ['UKRAINE-EO13662'], 'IRN': ['IRAN']}
        first, counts = w2.programme_waves(FakeStore(recs), None, targets, min_designations=5)
        self.assertEqual(first, {'RUS': 2014, 'IRN': 2011})
        self.assertEqual(counts['IRN'], {2010: 4, 2011: 6})

    def test_partner_share_panel(self):
        shares = {('iso3:RUS', '27'): {2012: (10.0, 90.0), 2013: (5.0, 95.0), 2014: (0.0, 100.0)},
                  ('iso3:CUB', '27'): {2012: (1.0, 9.0)}, ('iso3:USA', '27'): {2012: (1.0, 1.0)},
                  ('baci:area:490', '27'): {2012: (1.0, 1.0)}, ('iso3:FRA', '27'): {2012: (2.0, 8.0)}}
        panel, facts = w2.partner_share_panel(shares, {'RUS': 2014, 'CUB': 1986}, estimated=[2000, 2021],
                                              calendar=[1995, 2024])
        self.assertEqual(sorted(panel.units), ['iso3:FRA|27', 'iso3:RUS|27'])
        self.assertAlmostEqual(panel.outcomes['iso3:RUS|27'][2012], math.log(10.0 / 90.0))
        self.assertNotIn(2014, panel.outcomes['iso3:RUS|27'])
        self.assertEqual(panel.cohorts['iso3:RUS|27'], 2014)
        self.assertEqual(panel.clusters['iso3:RUS|27'], 'iso3:RUS')
        self.assertEqual(panel.strata['iso3:FRA|27'], '27')
        self.assertEqual(facts['dropped_first_wave_before_window'], ['CUB'])

    def test_partner_share_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'share.tsv.gz'
            with gzip.open(path, 'wt') as f:
                f.write('exports\tiso3:RUS\t27\t2012\t10.0\t90.0\nimports\tiso3:RUS\t27\t2012\t3.0\t4.0\n')
            self.assertEqual(load_baci92_partner_share(path, 'exports'), {('iso3:RUS', '27'): {2012: (10.0, 90.0)}})
            self.assertEqual(load_baci92_partner_share(path, 'imports'), {('iso3:RUS', '27'): {2012: (3.0, 4.0)}})


class BaciParseTests(unittest.TestCase):
    def test_fields(self):
        line = (b'{"attributes":{"quantity_t":0.347},"dimensions":{"frequency":"annual","importer":"iso3:SGP",'
                b'"product":"hs92:291412"},"id":"baci92:1995:036:702:291412","kind":"observation","metric":'
                b'"bilateral_trade_value","subject":"iso3:AUS","unit":"thousand_USD","valid_from":"1995-01-01",'
                b'"valid_to":"1996-01-01","value":0.978}\n')
        self.assertEqual(baci_fields(line), ('iso3:AUS', 'iso3:SGP', 'hs92:291412', 1995, 0.978, 0.347))
        no_q = line.replace(b'"quantity_t":0.347', b'"x":1')
        self.assertIsNone(baci_fields(no_q)[5])
        self.assertIsNone(baci_fields(b'{"kind":"entity","id":"x"}\n'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'b.tsv.gz'
            with gzip.open(path, 'wt') as f:
                f.write('iso3:SGP\t291412\t1995\t0.978\t0.347\t1\t0\n')
            self.assertEqual(load_baci_imports(path), {('iso3:SGP', '291412', 1995): (0.978, 0.347, 1, 0)})


def _cluster_panel(seed, n_clusters=40, units_per=8, effect=0.0, shock_sd=0.3, treated_share=0.3):
    """Treatment assigned to whole clusters, with cluster-by-period shocks (fictional)."""
    rng = random.Random(seed)
    periods = range(2000, 2012)
    outcomes, cohorts, strata, clusters = {}, {}, {}, {}
    for c in range(n_clusters):
        g = rng.choice([2004, 2006, 2008]) if rng.random() < treated_share else None
        shock = {t: rng.gauss(0, shock_sd) for t in periods}
        for j in range(units_per):
            u = f'c{c}|{j}'
            outcomes[u] = {t: shock[t] + rng.gauss(0, 0.2) + (effect if g and t >= g else 0.0) for t in periods}
            cohorts[u], strata[u], clusters[u] = g, f's{j}', f'c{c}'
    return Panel(outcomes, cohorts, strata, clusters)


class PlaceboClusterTests(unittest.TestCase):
    def test_size_close_to_nominal_and_gate(self):
        panel = _cluster_panel(3)
        out = placebo_cluster_test(panel, replications=40, seed=1, e_min=-3, e_max=2)
        self.assertTrue(out['ran'])
        self.assertLessEqual(out['rejection_rate'], 0.2)
        small = panel.subset([u for u in panel.units if panel.clusters[u] in {'c0', 'c1', 'c2'}])
        self.assertFalse(placebo_cluster_test(small, replications=5, seed=1, e_min=-3, e_max=2)['ran'])

    def test_run_did_design_hooks(self):
        panel = _cluster_panel(5, effect=0.5)
        reg = {'study_id': 'synthetic', 'treatment': {'estimated_cohorts': [2004, 2008]},
               'windows': {'e_min': -3, 'e_max': 2, 'post': [0, 1, 2]}, 'controls': {'control_group': 'not_yet_treated'},
               'estimator': {'anticipation': 0, 'robustness': ['stacked_did']},
               'inference': {'alpha': 0.05, 'bootstrap': 0, 'seed': 1},
               'placebo_tests': {'placebo_date': {'shift': 2}, 'placebo_cluster': {'replications': 10, 'seed': 2}},
               'acceptance_criteria': [{'id': 'clusters', 'type': 'min_events', 'value': 5},
                                       {'id': 'pc', 'type': 'placebo_cluster_rejection_rate_max', 'value': 0.5}],
               'assumptions': ['synthetic']}
        alt = panel.replace()
        record = run_did_design(panel, reg, {'path': 'x', 'sha256': 'y'}, outcome={'id': 'y', 'label': 'y'},
                                extra_robustness={'alternative': alt})
        self.assertIn('alternative', record['estimates']['robustness'])
        self.assertTrue(record['diagnostics']['placebo_cluster']['ran'])
        self.assertTrue(all(a['passed'] for a in record['acceptance']))
        self.assertAlmostEqual(record['estimates']['primary']['overall']['att'], 0.5, delta=0.25)

    def test_new_criteria(self):
        crit = [{'id': 'a', 'type': 'metric_max', 'metric': 'm', 'value': 1.0},
                {'id': 'b', 'type': 'placebo_cluster_rejection_rate_max', 'value': 0.1}]
        out = evaluate_acceptance(crit, {'m': 0.5, 'placebo_cluster_rejection_rate': 0.2})
        self.assertEqual([a['passed'] for a in out], [True, False])
        self.assertFalse(evaluate_acceptance(crit, {})[0]['passed'])


class PowerTests(unittest.TestCase):
    def _ar_panel(self, rho, sd, n=400, seed=0):
        rng = random.Random(seed)
        outcomes, cohorts = {}, {}
        for i in range(n):
            x = rng.gauss(0, sd / math.sqrt(1 - rho * rho))
            series = {}
            for t in range(2000, 2016):
                if t > 2000:
                    x = rho * x + rng.gauss(0, sd)
                series[t] = 3.0 + x + 0.1 * (t - 2000)
            outcomes[f'u{i}'] = series
            cohorts[f'u{i}'] = 2008 if i % 3 == 0 else None
        return Panel(outcomes, cohorts)

    def test_calibration_recovers_noise(self):
        for rho in (0.0, 0.5, 0.9):
            params = calibrate(self._ar_panel(rho, 0.1), anticipation=0)
            self.assertAlmostEqual(params['rho'], rho, delta=0.08)
            self.assertAlmostEqual(math.sqrt(params['innovation_var_pooled']), 0.1, delta=0.015)
            self.assertEqual(params['innovation_var_cluster'], 0.0)

    def test_calibration_uses_only_untreated_cells(self):
        panel = self._ar_panel(0.5, 0.1)
        shifted = panel.replace(outcomes={u: {t: y + (5.0 * (t - 2007) if panel.cohorts[u] and t >= 2008 else 0.0)
                                              for t, y in s.items()} for u, s in panel.outcomes.items()})
        a, b = calibrate(panel), calibrate(shifted)
        self.assertEqual(a['rho'], b['rho'])
        self.assertEqual(a['innovation_var_pooled'], b['innovation_var_pooled'])

    def test_cluster_component_detected(self):
        params = calibrate(_cluster_panel(7, shock_sd=0.3))
        self.assertGreater(params['innovation_var_cluster'], 0.02)

    def test_simulated_panel_keeps_structure(self):
        panel, _ = staggered_panel(n_units=60, seed=2, clusters_per=3, strata=2)
        sim = simulate_null_panel(panel, calibrate(panel), seed=5)
        self.assertEqual({u: sorted(s) for u, s in sim.outcomes.items()}, {u: sorted(s) for u, s in panel.outcomes.items()})
        self.assertEqual(sim.cohorts, panel.cohorts)
        self.assertEqual(sim.clusters, panel.clusters)
        self.assertEqual(sim.strata, panel.strata)
        again = simulate_null_panel(panel, calibrate(panel), seed=5)
        self.assertEqual(sim.digest(), again.digest())
        self.assertEqual(panel_from_dict(panel_to_dict(panel)).digest(), panel.digest())

    def test_mde_matches_normal_theory(self):
        rng = random.Random(1)
        draws = [{'att': rng.gauss(0, 0.01), 'se': 0.01} for _ in range(4000)]
        mde = minimum_detectable_effect(draws)
        self.assertAlmostEqual(mde, (normal_ppf(0.975) + normal_ppf(0.8)) * 0.01, delta=0.0015)
        self.assertAlmostEqual(power_at(draws, 0.0), 0.05, delta=0.015)
        summary = summarize_draws(draws, real_se=0.01, plausible=0.05)
        self.assertTrue(summary['calibration_ok'])
        self.assertLess(summary['mde_over_plausible'], 1.0)
        off = summarize_draws(draws, real_se=0.05, plausible=0.05)
        self.assertFalse(off['calibration_ok'])
        self.assertEqual(off['mde_80'], off['mde_80_analytic_from_real_se'])

    def test_null_draws_have_nominal_size_and_are_deterministic(self):
        panel, _ = staggered_panel(n_units=300, periods=range(2000, 2012), cohorts={2004: 0.3, 2007: 0.3},
                                   never_share=0.4, seed=4, clusters_per=5)
        params = calibrate(panel)
        spec = {'e_min': -3, 'e_max': 2, 'post': [0, 1, 2], 'control_group': 'not_yet_treated', 'anticipation': 0,
                'alpha': 0.05, 'cohorts': None, 'placebo_shift': 2}
        draws = null_draws(panel, spec, params, replications=40, seed=3)
        self.assertEqual(draws[:3], null_draws(panel, spec, params, replications=3, seed=3))
        self.assertLessEqual(power_at(draws, 0.0), 0.15)
        passing = sum(1 for d in draws if d['pre_trend_p'] >= 0.05) / len(draws)
        self.assertGreaterEqual(passing, 0.8)
        se = statistics.mean(d['se'] for d in draws)
        sd = statistics.pstdev(d['att'] for d in draws)
        self.assertAlmostEqual(se / sd, 1.0, delta=0.35)

    def _integrated_panel(self, n=300, trend_sd=0.0, walk_sd=0.05, noise_sd=0.02, seed=0):
        """Levels with a random walk, an optional unit trend and white noise (fictional)."""
        rng = random.Random(seed)
        outcomes, cohorts = {}, {}
        for i in range(n):
            slope, walk = rng.gauss(0, trend_sd), 0.0
            series = {}
            for t in range(2000, 2016):
                if t > 2000:
                    walk += rng.gauss(0, walk_sd)
                series[t] = walk + slope * (t - 2000) + rng.gauss(0, noise_sd)
            outcomes[f'u{i}'], cohorts[f'u{i}'] = series, (2008 if i % 3 == 0 else None)
        return Panel(outcomes, cohorts)

    def test_variogram_calibration_recovers_components(self):
        from worldmodel.causal.power import calibrate_variogram
        params = calibrate_variogram(self._integrated_panel(walk_sd=0.05, noise_sd=0.02, trend_sd=0.0, seed=1))
        fit = params['unit']
        self.assertAlmostEqual(math.sqrt(fit['random_walk_innovation_variance']), 0.05, delta=0.012)
        self.assertAlmostEqual(math.sqrt(fit['ar1_variance']), 0.02, delta=0.012)
        self.assertLess(math.sqrt(fit['trend_slope_variance']), 0.01)
        self.assertGreater(fit['weighted_r_squared'], 0.97)
        with_trend = calibrate_variogram(self._integrated_panel(walk_sd=0.0, noise_sd=0.01, trend_sd=0.02, seed=2))
        self.assertAlmostEqual(math.sqrt(with_trend['unit']['trend_slope_variance']), 0.02, delta=0.006)

    def test_variogram_simulation_reproduces_long_differences(self):
        from worldmodel.causal.power import calibrate_variogram
        panel = self._integrated_panel(walk_sd=0.05, noise_sd=0.02, trend_sd=0.01, seed=3)
        params = calibrate_variogram(panel)
        sim = simulate_null_panel(panel, params, seed=9)
        self.assertEqual(sorted(sim.outcomes['u0']), sorted(panel.outcomes['u0']))
        self.assertEqual(sim.digest(), simulate_null_panel(panel, params, seed=9).digest())

        def spread(p, lag):
            vals = [s[t + lag] - s[t] for s in p.outcomes.values() for t in s if t + lag in s]
            return statistics.pstdev(vals)

        for lag in (1, 5, 10):
            self.assertAlmostEqual(spread(sim, lag) / spread(panel, lag), 1.0, delta=0.25)

    def test_ranking_power(self):
        rng = random.Random(3)
        diffs = [rng.gauss(0.0, 0.1) for _ in range(40)]
        out = ranking_test_power(diffs, plausible=0.05, draws=600, seed=1)
        self.assertAlmostEqual(out['mde_80'], out['analytic_mde_80'], delta=0.012)
        self.assertLessEqual(out['null_rejection_rate'], 0.09)


if __name__ == '__main__':
    unittest.main()
