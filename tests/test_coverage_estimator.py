"""Coverage estimates: a stated population, a measured count, and a denominator only where one is sourced."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_unify_scale import Fixture, entity

from worldmodel import coverage_estimator as C


class CoverageEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        # a register that holds fewer entities than its declaration states
        self.fixture.publish('iso_mic_venues', [entity('mic:XNAS', 'NASDAQ', 'trading_venue'),
                                                entity('mic:XNYS', 'NYSE', 'trading_venue'),
                                                entity('lei:549300L8X1Q78ERXFD06', 'NASDAQ INC')])
        self._declare_coverage('iso_mic_venues', 'Complete ISO 10383 MIC list (2,883 MICs)')
        # a county-keyed dataset: two 2020 counties and one Connecticut planning region (2022+)
        self.fixture.publish('fema_nri', [entity('geo:US:county:01001', 'Autauga'),
                                          entity('geo:US:county:01003', 'Baldwin'),
                                          entity('geo:US:county:09110', 'Capitol Planning Region'),
                                          entity('geo:US:tract:01001020100', 'Tract 201')])
        # a dataset measured against another catalog dataset's list
        self.fixture.publish('census_geography', [entity('geo:US:tract:01001020100', 'Tract 201'),
                                                  entity('geo:US:tract:01001020200', 'Tract 202')])

    def tearDown(self):
        self.tmp.cleanup()

    def _declare_coverage(self, dataset, scope):
        path = self.fixture.catalog_root / dataset / 'dataset.json'
        declaration = json.loads(path.read_text())
        declaration['coverage'] = {'scope': scope, 'completeness': 'complete_for_declared_scope',
                                   'representative': False}
        path.write_text(json.dumps(declaration))

    def estimate(self, name):
        return C.estimate_one(self.fixture.catalog, self.fixture.store, name, C.POPULATIONS[name])

    def test_measured_over_declared_exposes_an_acquisition_gap(self):
        row = self.estimate('iso_mic_venues')
        self.assertEqual(row['measured']['value'], 2)                 # the LEI entity is not a MIC
        self.assertEqual(row['measured_over_declared'], round(2 / 2883, 6))
        self.assertTrue(row['declared_count']['supported_by_declaration'])
        self.assertIsNone(row['population_denominator'])
        self.assertIn('1 by construction', row['reason_no_denominator'])

    def test_county_coverage_against_the_dated_reference_list(self):
        row = self.estimate('fema_nri')
        denominator = row['population_denominator']
        self.assertEqual(denominator['value'], 3222)                  # 50 states + DC + Puerto Rico, 2020
        self.assertEqual(row['covered_of_population']['present'], 2)
        # a planning region is a later vintage: reported as outside the population, never counted
        self.assertEqual(row['covered_of_population']['outside_examples'], ['09110'])

    def test_tract_coverage_against_another_catalog_dataset(self):
        row = self.estimate('fema_nri:tracts')
        self.assertEqual(row['population_denominator']['value'], 2)
        self.assertEqual(row['covered_of_population']['fraction'], 0.5)
        self.assertEqual(row['population_denominator']['source']['dataset'], 'census_geography')

    def test_an_unpublished_dataset_is_reported_unmeasured_not_zero(self):
        self.fixture.declare('sec_gleif')
        row = self.estimate('sec_gleif')
        self.assertIsNone(row['measured'])
        self.assertTrue(row['unmeasured_reason'])

    def test_every_declared_count_is_quoted_from_the_real_declaration(self):
        catalog_root = Path(__file__).resolve().parents[1] / 'data'
        for name, spec in C.POPULATIONS.items():
            if not spec.get('declared'):
                continue
            dataset = spec.get('dataset', name)
            declaration = json.loads((catalog_root / dataset / 'dataset.json').read_text())
            self.assertTrue(C.declared_count_supported(declaration, spec), name)

    def test_reference_populations(self):
        self.assertEqual(len(C.reference_members('us_counties_2020')), 3222)
        countries = C.reference_members('iso3166_1')
        self.assertIn('USA', countries)
        self.assertNotIn('YUG', countries)                           # formerly used, not current

    def test_whole_report_and_command_line(self):
        report = C.estimate_coverage(self.fixture.catalog, self.fixture.store, datasets=['fema_nri'])
        self.assertEqual({e['estimate'] for e in report['estimates']}, {'fema_nri', 'fema_nri:tracts'})
        self.assertEqual(report['summary']['with_population_denominator'], 2)
        output = subprocess.run([sys.executable, '-m', 'worldmodel', '--data-root', str(self.fixture.data_root),
                                 '--catalog-root', str(self.fixture.catalog_root), 'coverage-estimate',
                                 '--datasets', 'iso_mic_venues', '--no-measure'],
                                capture_output=True, text=True, check=True)
        parsed = json.loads(output.stdout)
        self.assertEqual(parsed['estimates'][0]['population'], C.POPULATIONS['iso_mic_venues']['population'])
        self.assertNotIn('measured', parsed['estimates'][0])


if __name__ == '__main__':
    unittest.main()
