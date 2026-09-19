"""Coverage estimates: a stated population, a measured count, and a denominator only where one is sourced."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # discover and dotted module paths alike
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

    def test_an_exclude_rule_keeps_a_sibling_namespace_out_of_the_count(self):
        self.fixture.publish('federal_register_documents', [
            entity('federalregister:2021-00001', 'A rule'), entity('federalregister:2021-00002', 'A notice'),
            entity('federalregister:agency:epa', 'EPA')])
        row = self.estimate('federal_register_documents')
        self.assertEqual(row['measured']['value'], 2)                 # the agency entity is not a document
        self.assertIsNone(row['population_denominator'])

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

    def test_the_derived_category_names_its_inputs_and_invents_no_population(self):
        self.fixture.declare('county_panel')
        report = C.estimate_coverage(self.fixture.catalog, self.fixture.store,
                                     datasets=['county_panel'], measure=False)
        self.assertEqual([row['dataset'] for row in report['derived']], ['county_panel'])
        row = report['derived'][0]
        self.assertIn('bls_labor', row['derived_from'])
        self.assertIn('inherited from its inputs', row['reason'])
        self.assertNotIn('population', row)                          # a derived output states none
        self.assertEqual(report['summary']['derived'], 1)
        self.assertEqual(report['uncurated'], [])                    # and it is not reported as uncurated

    def test_an_uncurated_declaration_says_what_is_missing(self):
        self.fixture.declare('usgs_earthquakes')
        report = C.estimate_coverage(self.fixture.catalog, self.fixture.store,
                                     datasets=['usgs_earthquakes'], measure=False)
        self.assertEqual([row['dataset'] for row in report['uncurated']], ['usgs_earthquakes'])
        reason = report['uncurated'][0]['reason']
        self.assertIn('no population statement curated', reason)     # the pre-existing sentence, unchanged
        self.assertIn('emits no entity records', reason)             # plus the specific missing thing
        self.assertEqual(report['summary']['uncurated'], 1)


class CuratedStatementTests(unittest.TestCase):
    """Invariants of the curated statements themselves, checked against the real declarations."""

    catalog_root = Path(__file__).resolve().parents[1] / 'data'

    def declaration(self, dataset):
        return json.loads((self.catalog_root / dataset / 'dataset.json').read_text())

    def test_every_population_entry_is_complete_and_names_a_real_dataset(self):
        for name, spec in C.POPULATIONS.items():
            dataset = spec.get('dataset', name)
            self.assertTrue((self.catalog_root / dataset / 'dataset.json').is_file(), name)
            self.assertIn(spec['kind'], ('register', 'subset', 'sample'), name)
            for field in ('population', 'unit', 'not_covered'):
                self.assertTrue(spec.get(field) and spec[field].strip(), '%s: %s' % (name, field))
            prefix = spec['count']['prefix']
            self.assertTrue(prefix, name)
            for excluded in spec['count'].get('exclude', ()):
                self.assertTrue(excluded.startswith(prefix), '%s excludes %s' % (name, excluded))
            self.assertIn(spec['count'].get('key'), (None, 'suffix', 'symbol'), name)

    def test_the_three_categories_are_disjoint_and_cover_every_declaration(self):
        declared = {path.parent.name for path in self.catalog_root.glob('*/dataset.json')}
        curated = {spec.get('dataset', name) for name, spec in C.POPULATIONS.items()}
        derived, refused = set(C.DERIVED), set(C.UNCURATED_REASONS)
        self.assertEqual(curated & derived, set())
        self.assertEqual(curated & refused, set())
        self.assertEqual(derived & refused, set())
        # every declaration is either curated, derived, or refused with a stated reason: a new dataset
        # has to be triaged rather than silently joining an anonymous "uncurated" pile
        self.assertEqual(sorted(declared - curated - derived - refused), [])
        self.assertEqual(sorted((curated | derived | refused) - declared), [])

    def test_every_derived_entry_names_inputs_that_match_its_declaration(self):
        for dataset, spec in C.DERIVED.items():
            declaration = self.declaration(dataset)
            self.assertTrue(spec['reason'].strip(), dataset)
            dependencies = declaration.get('dependencies') or []
            if dependencies:
                self.assertEqual(spec['derived_from'], dependencies, dataset)
            for source in spec['derived_from']:
                self.assertTrue((self.catalog_root / source / 'dataset.json').is_file(),
                                '%s <- %s' % (dataset, source))

    def test_every_refusal_states_the_specific_missing_thing(self):
        for dataset, reason in C.UNCURATED_REASONS.items():
            self.assertTrue((self.catalog_root / dataset / 'dataset.json').is_file(), dataset)
            self.assertGreater(len(reason), 40, dataset)             # a reason, not a label

    def test_a_declared_count_is_only_cited_from_a_field_that_holds_it(self):
        for name, spec in C.POPULATIONS.items():
            declared = spec.get('declared')
            if not declared:
                continue
            declaration = self.declaration(spec.get('dataset', name))
            self.assertTrue(C.declared_count_supported(declaration, spec), name)
            section = declared['field'].partition('.')[0]
            self.assertIn(section, declaration, name)


if __name__ == '__main__':
    unittest.main()
