"""Panel construction and exposure scoring used by the registered real-data studies (fictional inputs)."""
import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path

from worldmodel.causal import exposure_study, studies
from worldmodel.causal.registration import validate_registration
from worldmodel.causal.sources import load_baci_imports, load_bls_county

ROOT = Path(__file__).resolve().parents[1]
REGISTRATIONS = ROOT / 'examples/natural-experiments/registrations'


class RegistrationFileTests(unittest.TestCase):
    def test_committed_registrations_are_valid(self):
        paths = sorted(REGISTRATIONS.glob('*.json'))
        self.assertGreaterEqual(len(paths), 4)
        for path in paths:
            document = validate_registration(json.loads(path.read_text()))
            self.assertEqual(document['study_id'], path.stem)


class FemaPanelTests(unittest.TestCase):
    def test_sample_rules(self):
        series = {'01001': {'1990': 100.0, '1991': 110.0}, '01003': {'1990': 50.0}, '01999': {'1990': 1.0},
                  '72001': {'1990': 9.0}, '01005': {'1990': 0.0}, '02013': {'1990': 7.0}}
        panel = studies.fema_panel({'01001': 1996}, early={'02013'}, series=series, lo=1990, hi=2024)
        self.assertEqual(sorted(panel.outcomes), ['01001', '01003'])  # 999, PR, zero-only and early excluded
        self.assertEqual(panel.cohorts['01001'], 1996)
        self.assertIsNone(panel.cohorts['01003'])
        self.assertAlmostEqual(panel.outcomes['01001'][1991], math.log(110.0))
        self.assertEqual(panel.strata['01001'], '01')
        self.assertEqual(panel.clusters['01003'], '01')

    def test_pinned_refuses_placeholders(self):
        reg = {'data': {'inputs': [{'dataset': 'event_library', 'stage': 'events', 'version': 'PIN_EVENT_LIBRARY'}]}}
        with self.assertRaises(ValueError):
            studies.pinned(reg, 'event_library')
        with self.assertRaises(ValueError):
            studies.pinned(reg, 'bls_labor')


class CacheFormatTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            bls = Path(tmp) / 'bls.tsv.gz'
            with gzip.open(bls, 'wt') as f:
                f.write('QCEW\temployment\t01001\t1990\t100.0\tid1\nLAUS\temployment\t01001\t1990-01\t90.0\tid2\n')
            self.assertEqual(load_bls_county(bls, 'QCEW', 'employment'), {'01001': {'1990': 100.0}})
            self.assertEqual(load_bls_county(bls, 'LAUS', 'employment'), {'01001': {'1990-01': 90.0}})
            baci = Path(tmp) / 'baci.tsv.gz'
            with gzip.open(baci, 'wt') as f:
                f.write('iso3:ZZZ\t010121\t2017\t5.5\t2.0\t3\t0\n')
            self.assertEqual(load_baci_imports(baci), {('iso3:ZZZ', '010121', 2017): (5.5, 2.0, 3, 0)})


class ExposureTests(unittest.TestCase):
    def test_month_arithmetic_and_seasonal_difference(self):
        self.assertEqual(exposure_study._month('2020-01', -2), '2019-11')
        self.assertEqual(exposure_study._month('2020-12', 2), '2021-02')
        series = {}
        for y in (2019, 2020):
            for m in range(1, 13):
                series[f'{y}-{m:02d}'] = 100.0 * (1.1 if m in (10, 11) else 1.0)  # seasonal bump
        series['2020-10'] = series['2020-11'] = 100.0 * 1.1 * 0.9  # a 10% loss on top of the bump
        loss = exposure_study.employment_loss(series, '2020-09')
        self.assertAlmostEqual(loss, -math.log(0.9), places=9)
        self.assertIsNone(exposure_study.employment_loss({}, '2020-09'))

    def test_modelled_wind_decays_outside_rmax(self):
        self.assertEqual(exposure_study.modelled_wind(100, 10), 100)
        self.assertAlmostEqual(exposure_study.modelled_wind(100, 160), 50.0)

    def test_event_rankings_on_a_fictional_track(self):
        centroids = {f'01{i:03d}': (30.0 + 0.05 * i, -88.0) for i in range(30)}
        centroids['06001'] = (37.0, -122.0)
        by_lat = sorted((lat, f) for f, (lat, _) in centroids.items())
        lats = [x for x, _ in by_lat]
        track = [('2020-09-01T00:00:00Z', 30.0, -88.0, 100), ('2020-09-01T06:00:00Z', 30.5, -88.0, 60)]
        employment = {}
        for f, (lat, _) in centroids.items():
            series = {exposure_study._month('2020-09', k): 100.0 for k in range(-15, 4)}
            loss = max(0.0, 0.1 - 0.05 * (lat - 30.0))
            for k in (1, 2):
                series[exposure_study._month('2020-09', k)] = 100.0 * (1 - loss)
            employment[f] = series
        graph = {'01000': {'01001': 5.0}, '01001': {'01000': 5.0}}
        ranked = exposure_study.event_rankings(track, centroids, by_lat, lats, graph, employment)
        self.assertFalse(ranked['skipped'])
        self.assertEqual(ranked['units'], 30)  # California is out of reach
        self.assertEqual(ranked['month'], '2020-09')
        from worldmodel.causal import score_event
        scored = score_event(ranked['units_list'], ranked['rankings'], ranked['outcome'])
        self.assertGreater(scored['naive']['spearman'], 0.8)
        self.assertGreater(scored['hazard']['spearman'], 0.8)


if __name__ == '__main__':
    unittest.main()
