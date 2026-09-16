import hashlib
import tempfile
import unittest
from pathlib import Path

from worldmodel.crosswalks import (REFERENCE, CountryCodes, Crosswalk, CrosswalkError, UsGeography, acquisition_declarations,
                                   naics_2022_codes, naics_aggregate_crosswalk, naics_concordance, naics_parent,
                                   reference_manifest, zcta_county_crosswalk)


class CountryCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codes = CountryCodes()

    def test_dated_state_system_links(self):
        convert = self.codes.convert
        self.assertEqual(convert('345', 'cow', 'iso3', at='2010-01-01')['value'], 'SRB')
        self.assertEqual(convert('345', 'cow', 'iso3', at='1995-01-01')['value'], 'YUG')
        self.assertEqual(convert('345', 'cow', 'iso3', at='2004-06-01')['value'], 'SCG')
        self.assertEqual(convert('340', 'gw', 'iso3', at='2010-01-01')['value'], 'SRB')
        self.assertEqual(convert('260', 'cow', 'iso3', at='1980-01-01')['value'], 'DEU')
        self.assertEqual(convert('265', 'gw', 'iso2', at='1980-01-01')['value'], 'DD')
        self.assertEqual(convert('DEU', 'iso3', 'gw', at='1980-01-01')['value'], '260')
        self.assertEqual(convert('XKX', 'world_bank', 'cow')['value'], '347')
        self.assertEqual(convert('240', 'cow', 'iso3')['status'], 'no_equivalent')
        self.assertEqual(convert('ZZZ', 'iso3', 'iso2')['status'], 'not_found')

    def test_reused_and_colliding_codes_are_refused(self):
        self.assertEqual(self.codes.convert('CS', 'iso2', 'iso3', at='1990-01-01')['value'], 'CSK')
        self.assertEqual(self.codes.convert('CS', 'iso2', 'iso3', at='2005-01-01')['value'], 'SCG')
        with self.assertRaisesRegex(CrosswalkError, 'date'):
            self.codes.convert('CS', 'iso2', 'iso3')
        with self.assertRaisesRegex(CrosswalkError, 'scheme'):
            self.codes.lookup('AUS')
        self.assertEqual(self.codes.convert('AUS', 'cow_abbrev', 'iso3')['value'], 'AUT')
        self.assertEqual(self.codes.convert('AUS', 'iso3', 'cow')['value'], '900')
        self.assertEqual(self.codes.convert('SWZ', 'cow_abbrev', 'iso3')['value'], 'CHE')
        self.assertEqual(self.codes.convert('840', 'iso_numeric', 'iso2')['value'], 'US')


class UsGeographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo = UsGeography()

    def test_states_and_county_validity(self):
        self.assertEqual(self.geo.state('TX')['fips'], '48')
        self.assertEqual(self.geo.state('6')['usps'], 'CA')
        self.assertEqual(self.geo.county('09001', at='2020-01-01')['status'], 'active')
        self.assertEqual(self.geo.county('09001', at='2023-01-01')['status'], 'not_active')
        self.assertEqual(self.geo.county('09120', at='2023-01-01')['name'], 'Greater Bridgeport Planning Region')
        self.assertEqual(self.geo.county('12025', at='2000-01-01')['status'], 'not_active')
        self.assertTrue(any(c['to_geoid'] == '46102' for c in self.geo.county('46113')['changes']))

    def test_county_crosswalk_conserves_and_refuses_unpublished_weights(self):
        walk = self.geo.county_crosswalk('2000-01-01', '2020-01-01')
        result = walk.apportion({'02232': 100, '51515': 10, '06001': 5, '12086': 1})
        self.assertAlmostEqual(result['values']['02230'], 100 * 862 / (862 + 2574))
        self.assertEqual(result['values']['51019'], 10)
        self.assertEqual(result['total_in'], result['total_out'])
        with self.assertRaisesRegex(CrosswalkError, 'without weights'):
            walk.apportion({'08001': 10})
        supplied = walk.apportion({'08001': 10}, weights={'08001': {'08001': 0.95, '08014': 0.05}})
        self.assertAlmostEqual(supplied['values']['08014'], 0.5)
        assumed = walk.apportion({'08001': 10}, unweighted='equal')
        self.assertEqual(assumed['assumptions'][0]['assumption'], 'equal_split')
        self.assertEqual(assumed['error_bound'], 10.0)
        ct = self.geo.county_crosswalk('2021-01-01', '2023-01-01')
        self.assertTrue(ct.targets('09001'))
        self.assertEqual(len(self.geo.ct_cousub_crosswalk()), 174)


class CrosswalkMechanicsTests(unittest.TestCase):
    def test_weights_errors_unmapped_temporal_and_intensive(self):
        walk = Crosswalk('fixture', 'a', 'b', [
            {'source': 'x', 'target': 'p', 'weight': 0.6, 'weight_error': 0.05},
            {'source': 'x', 'target': 'q', 'weight': 0.5},
            {'source': 'y', 'target': 'q', 'weight': 1}])
        result = walk.apportion({'x': 100, 'y': 10})
        self.assertAlmostEqual(result['values']['p'] + result['values']['q'], 110)
        self.assertAlmostEqual(result['normalized_sources'][0]['deviation'], 0.1)
        self.assertEqual(result['error_bound'], 5.0)
        self.assertEqual(walk.check_partition()[0]['issue'], 'weights_do_not_sum_to_one')
        with self.assertRaises(CrosswalkError):
            walk.apportion({'x': 1}, normalize=False)
        with self.assertRaises(CrosswalkError):
            walk.apportion({'z': 1})
        self.assertEqual(walk.apportion({'z': 1, 'y': 1}, unmapped='report')['unmapped'], {'z': 1.0})
        mean = walk.weighted_mean({'x': 0.1, 'y': 0.3}, {'x': 1000, 'y': 1000})
        self.assertAlmostEqual(mean['values']['p'], 0.1)
        self.assertAlmostEqual(mean['values']['q'], (0.1 * 1000 * 0.5 / 1.1 + 0.3 * 1000) / (1000 * 0.5 / 1.1 + 1000))
        dated = Crosswalk('dated', 'a', 'b', [{'source': 'x', 'target': 'old', 'weight': 1, 'valid_to': '2020-01-01'},
                                              {'source': 'x', 'target': 'new', 'weight': 1, 'valid_from': '2020-01-01'}])
        with self.assertRaisesRegex(CrosswalkError, 'at='):
            dated.apportion({'x': 1})
        self.assertEqual(dated.apportion({'x': 1}, at='2021-01-01')['values'], {'new': 1.0})
        chained = walk.compose(Crosswalk('second', 'b', 'c', [{'source': 'p', 'target': 'r'}, {'source': 'q', 'target': 'r'}]))
        self.assertEqual(chained.apportion({'x': 5, 'y': 5})['values'], {'r': 10.0})

    def test_naics_concordances_and_hierarchy(self):
        walk = naics_concordance(2017, 2022)
        description = walk.describe()
        self.assertGreater(description['rows'], 1000)
        self.assertGreater(description['split_source_codes'], 0)
        split = next(code for code, rows in walk._by_source.items() if len(rows) > 1)
        with self.assertRaises(CrosswalkError):
            walk.apportion({split: 1})
        self.assertTrue(naics_concordance(2012, 2022).describe()['rows'] > 1000)
        self.assertEqual(naics_parent('445110', 2), '44-45')
        self.assertEqual(naics_parent('311111', 3), '311')
        self.assertIn('44-45', naics_2022_codes())
        self.assertEqual(naics_aggregate_crosswalk(['445110', '311111'], 2).apportion({'445110': 1, '311111': 2})['values'],
                         {'31-33': 2.0, '44-45': 1.0})

    def test_zcta_relationship_file_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rel.txt'
            path.write_text('GEOID_ZCTA5_20|GEOID_COUNTY_20|AREALAND_PART\n'
                            '00001|01001|300\n00001|01003|100\n00002|01003|50\n|01005|10\n', encoding='utf-8')
            walk = zcta_county_crosswalk(path)
            result = walk.apportion({'00001': 40, '00002': 2})
            self.assertEqual(result['values'], {'01001': 30.0, '01003': 12.0})
            self.assertEqual(result['error_bound'], 40 * 0.75 + 40 * 0.75)


class ReferenceFileTests(unittest.TestCase):
    def test_manifest_hashes_and_declarations(self):
        manifest = reference_manifest()
        for output in manifest['outputs']:
            self.assertEqual(hashlib.sha256((REFERENCE / output['file']).read_bytes()).hexdigest(), output['sha256'], output['file'])
        for name, source in manifest['sources'].items():
            self.assertTrue(source['url'].startswith('http') and source['licence'], name)
        declarations = acquisition_declarations()
        self.assertGreaterEqual(len(declarations['declarations']), 10)
        for item in declarations['declarations']:
            self.assertIn(item['acquisition']['strategy'], ('files', 'url_list', 'paged_api'))
            self.assertGreater(item['acquisition']['desired_bytes'], 0)
            self.assertTrue(item['licence'] and item['loader'])


if __name__ == '__main__':
    unittest.main()
