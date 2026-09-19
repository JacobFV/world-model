"""Offline tests for fred_county_vintages (fixtures only, no network).

Run from the project root:
    python3 -m unittest discover -s data/fred_county_vintages/tests
"""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeContext:
    """The slice of pipeline.Context that alfred.api_records uses."""

    def __init__(self, shards, retrieved='2026-09-18T00:00:00+00:00'):
        self._shards, self._retrieved = shards, retrieved

    def raw_coverage(self):
        return {'sampled': False, 'layout': 'shards'}

    def raw_shards(self, index=0):
        return self._shards

    def raw_receipt(self, index=0):
        return {'retrieved_at': self._retrieved}

    def raw_evidence(self, locator, index=0):
        return [{'input': {'dataset': 'fred_county_vintages', 'artifact': '0' * 64}, 'locator': locator}]


def family(metric, frequency, units, program='bea_regional', **extra):
    history = [{'realtime_start': start, 'realtime_end': end, 'source_units': source, 'unit': unit,
                'multiplier': multiplier, 'base_period': base} for start, end, source, unit, multiplier, base in units]
    return {'metric': metric, 'frequency': frequency, 'program': program, 'release_id': 1, 'release_name': 'test',
            'originating_source': 'test', 'geofred_series_group': '1', 'geofred_title': metric,
            'source_units': history[-1]['source_units'], 'unit': history[-1]['unit'],
            'units_history': history, 'units_change_across_vintages': len(history) > 1,
            'units_history_exceptions': {}, **extra}


CONFIG = {
    'families': {
        'per_capita_personal_income': family('per_capita_personal_income', 'A',
                                             [('2014-05-30', '9999-12-31', 'Dollars', 'USD', 1.0, None)]),
        'population': family('population', 'A', [('2007-03-22', '9999-12-31', 'Thousands of Persons', 'persons',
                                                   1000.0, None)], program='census_population_estimates'),
        'real_gdp': family('real_gdp', 'A', [
            ('2018-12-12', '2023-12-06', 'Thousands of Chained 2012 U.S. Dollars', 'USD_chained_2012', 1000.0, '2012'),
            ('2023-12-07', '9999-12-31', 'Thousands of Chained 2017 U.S. Dollars', 'USD_chained_2017', 1000.0, '2017')]),
        'private_establishments': family('establishment_count', 'Q',
                                         [('2017-03-07', '9999-12-31', 'Establishments', 'establishments', 1.0, None)],
                                         program='qcew', ownership='private'),
    },
    'counties': {'48201': 'Harris County, TX', '51901': 'Albemarle + Charlottesville, VA'},
    'series': {'PCPI48201': ['per_capita_personal_income', '48201', 'geofred+series_id'],
               'TXHARR1POP': ['population', '48201', 'geofred'],
               'REALGDPALL48201': ['real_gdp', '48201', 'geofred+series_id'],
               'PCPI51901': ['per_capita_personal_income', '51901', 'geofred+series_id'],
               'ENU4820120510': ['private_establishments', '48201', 'geofred+series_id']},
}


def shard(series_id, rows, index=0, count=None, window=('1776-07-04', '9999-12-31')):
    payload = {'realtime_start': window[0], 'realtime_end': window[1], 'count': len(rows) if count is None else count,
               'offset': 0, 'limit': 100000, 'observations': rows}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as stream:
        json.dump(payload, stream)
    return {'index': index, 'path': stream.name, 'retrieved_at': '2026-09-18T00:00:00+00:00',
            'request': {'params': {'series_id': series_id}}}


def row(start, end, day, value):
    return {'realtime_start': start, 'realtime_end': end, 'date': day, 'value': value}


class EmissionTests(unittest.TestCase):
    def setUp(self):
        self.alfred = load(HERE / 'alfred.py', 'county_alfred_test')

    def records(self, *shards):
        return list(self.alfred.api_records(FakeContext(list(shards)), 'fred_county_vintages', CONFIG))

    def observations(self, *shards):
        return [r for r in self.records(*shards) if r['kind'] == 'observation']

    def test_county_subject_and_every_vintage_survive(self):
        rows = [row('2014-05-30', '2014-11-19', '2012-01-01', '50000'),   # archive snapshot of revised history
                row('2014-11-20', '2015-11-18', '2012-01-01', '50100'),
                row('2014-11-20', '2015-11-18', '2013-01-01', '51000'),   # 2013 first published 2014-11-20
                row('2015-11-19', '9999-12-31', '2013-01-01', '51200')]
        observations = self.observations(shard('PCPI48201', rows))
        self.assertEqual(len(observations), 4, 'every real-time period of every year must survive')
        self.assertEqual({o['subject'] for o in observations}, {'geo:US:county:48201'})
        self.assertEqual([o['dimensions']['vintage'] for o in observations],
                         ['2014-05-30', '2014-11-20', '2014-11-20', '2015-11-19'])
        self.assertEqual([o['attributes']['realtime_end'] for o in observations],
                         ['2014-11-19', '2015-11-18', '2015-11-18', '9999-12-31'])
        self.assertEqual({(o['valid_from'], o['valid_to']) for o in observations},
                         {('2012-01-01', '2013-01-01'), ('2013-01-01', '2014-01-01')})
        self.assertEqual([o['observed_at'] for o in observations], [o['dimensions']['vintage'] for o in observations])

    def test_first_release_excludes_the_archive_opening_snapshot(self):
        rows = [row('2014-05-30', '2014-11-19', '2012-01-01', '50000'),
                row('2014-11-20', '2015-11-18', '2012-01-01', '50100'),
                row('2014-11-20', '2015-11-18', '2013-01-01', '51000'),
                row('2015-11-19', '9999-12-31', '2013-01-01', '51200')]
        observations = self.observations(shard('PCPI48201', rows))
        flags = [(o['valid_from'], o['dimensions']['vintage'], o['dimensions']['first_release']) for o in observations]
        # 2012 was already published (and revised) before ALFRED's first vintage: it has no first release,
        # and its later revision is not one either.
        self.assertEqual(flags, [('2012-01-01', '2014-05-30', False), ('2012-01-01', '2014-11-20', False),
                                 ('2013-01-01', '2014-11-20', True), ('2013-01-01', '2015-11-19', False)])
        self.assertEqual([o['attributes']['realtime_start_clipped'] for o in observations], [True, False, False, False])
        self.assertEqual({o['attributes']['series_first_vintage'] for o in observations}, {'2014-05-30'})

    def test_thousands_are_scaled_without_binary_noise(self):
        observations = self.observations(shard('TXHARR1POP', [row('2007-03-22', '9999-12-31', '2006-01-01', '3.372')]))
        self.assertEqual(observations[0]['value'], 3372)
        self.assertEqual(observations[0]['unit'], 'persons')
        self.assertEqual(observations[0]['attributes']['unit_multiplier'], 1000.0)

    def test_rebased_vintages_carry_their_own_units(self):
        rows = [row('2018-12-12', '2023-12-06', '2017-01-01', '400000000'),
                row('2023-12-07', '9999-12-31', '2017-01-01', '450000000')]
        observations = self.observations(shard('REALGDPALL48201', rows))
        self.assertEqual([o['unit'] for o in observations], ['USD_chained_2012', 'USD_chained_2017'])
        self.assertEqual([o['dimensions']['base_period'] for o in observations], ['2012', '2017'])
        self.assertTrue(all(o['attributes']['units_change_across_vintages'] for o in observations))

    def test_missing_values_are_kept_with_a_reason(self):
        observations = self.observations(shard('PCPI48201', [row('2014-05-30', '9999-12-31', '2012-01-01', '.')]))
        self.assertIsNone(observations[0]['value'])
        self.assertEqual(observations[0]['missing_reason'], 'not_available_in_vintage')

    def test_quarterly_periods_and_ownership(self):
        observations = self.observations(shard('ENU4820120510', [row('2017-03-07', '9999-12-31', '2016-07-01', '120000')]))
        self.assertEqual((observations[0]['valid_from'], observations[0]['valid_to']), ('2016-07-01', '2016-10-01'))
        self.assertEqual(observations[0]['dimensions']['ownership'], 'private')
        self.assertEqual(observations[0]['metric'], 'establishment_count')

    def test_bea_combination_areas_are_marked(self):
        observations = self.observations(shard('PCPI51901', [row('2014-05-30', '9999-12-31', '2012-01-01', '45000')]))
        self.assertEqual(observations[0]['attributes']['code_scheme'], 'bea_combination_area')
        county = next(r for r in self.records(shard('PCPI51901', [row('2014-05-30', '9999-12-31', '2012-01-01', '1')]))
                      if r.get('entity_type') == 'county')
        self.assertEqual(county['attributes']['code_scheme'], 'bea_combination_area')

    def test_geography_chain_is_emitted_once(self):
        records = self.records(shard('PCPI48201', [row('2014-05-30', '9999-12-31', '2012-01-01', '1')], index=0),
                               shard('TXHARR1POP', [row('2007-03-22', '9999-12-31', '2006-01-01', '1')], index=1))
        within = {(r['subject'], r['object']) for r in records if r.get('predicate') == 'within'}
        self.assertEqual(within, {('geo:US:state:48', 'geo:US'), ('geo:US:county:48201', 'geo:US:state:48')})
        counties = [r for r in records if r.get('entity_type') == 'county']
        self.assertEqual(len(counties), 1)
        self.assertEqual(counties[0]['label'], 'Harris County, TX')
        described = {(r['subject'], r['object']) for r in records if r.get('predicate') == 'describes_location'}
        self.assertEqual(described, {('fred:PCPI48201', 'geo:US:county:48201'), ('fred:TXHARR1POP', 'geo:US:county:48201')})

    def test_truncated_or_windowed_payloads_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'holds 1 of 5'):
            self.records(shard('PCPI48201', [row('2014-05-30', '9999-12-31', '2012-01-01', '1')], count=5))
        with self.assertRaisesRegex(ValueError, 'full real-time window'):
            self.records(shard('PCPI48201', [row('2020-01-01', '9999-12-31', '2012-01-01', '1')],
                               window=('2020-01-01', '9999-12-31')))
        with self.assertRaisesRegex(ValueError, 'not configured'):
            self.records(shard('XXPCPI', []))

    def test_records_validate(self):
        import sys
        sys.path.insert(0, str(HERE.parents[1]))
        from worldmodel.model import validate_record
        rows = [row('2014-05-30', '2014-11-19', '2012-01-01', '50000'), row('2014-11-20', '9999-12-31', '2012-01-01', '.')]
        for record in self.records(shard('PCPI48201', rows)):
            validate_record(record)

    def test_there_is_no_current_vintage_code_path(self):
        """A fredgraph.csv reader would emit rows that are not real time; it must not exist here."""
        self.assertFalse(hasattr(self.alfred, 'graph_records'))
        pipeline_source = (HERE / 'pipeline.py').read_text()
        self.assertNotIn('fredgraph', pipeline_source)


class ConfigTests(unittest.TestCase):
    """The committed config.json and dataset.json agree, and every code came from published geography."""

    def setUp(self):
        self.config = json.loads((HERE / 'config.json').read_text())
        self.declaration = json.loads((HERE / 'dataset.json').read_text())

    def test_declared_series_match_the_configured_series(self):
        acquisition = self.declaration['acquisition']
        self.assertEqual(acquisition['parameters']['series_id'], sorted(self.config['series']))
        self.assertIn('realtime_start=1776-07-04', acquisition['url_template'],
                      'every request must ask for the whole real-time window, or vintages are lost')
        self.assertIn('realtime_end=9999-12-31', acquisition['url_template'])
        self.assertEqual(acquisition['skip_statuses'], [400], 'FRED refuses non-ALFRED series with 400')
        self.assertGreaterEqual(acquisition['max_requests'], len(self.config['series']))

    def test_every_series_has_a_county_code_and_a_known_family(self):
        for series_id, (name, fips, source) in self.config['series'].items():
            self.assertIn(name, self.config['families'], series_id)
            self.assertRegex(fips, r'^\d{5}$', series_id)
            self.assertIn(source, ('geofred', 'series_id', 'geofred+series_id'), series_id)
            self.assertIn(fips, self.config['counties'], series_id)

    def test_codes_parsed_from_ids_agree_with_the_id_scheme(self):
        """Where the id carries the area code, the configured code is that code (GeoFRED agreed or was silent)."""
        for series_id, (name, fips, source) in self.config['series'].items():
            pattern = self.config['families'][name].get('id_pattern')
            if pattern:
                self.assertEqual(re.match(pattern, series_id)[1], fips, series_id)
            else:
                self.assertEqual(source, 'geofred', f'{series_id}: an id without a code needs published geography')

    def test_families_record_their_depth_and_units(self):
        for name, entry in self.config['families'].items():
            self.assertEqual(entry['series'], sum(1 for v in self.config['series'].values() if v[0] == name), name)
            self.assertTrue(entry['units_history'], name)
            self.assertGreater(entry['vintage_probe']['max_vintages'], 1, name)
            self.assertTrue(entry['vintage_probe']['earliest_first_vintage'], name)
        self.assertTrue(self.config['families']['real_gdp']['units_change_across_vintages'],
                        'county real GDP is rebased from chained 2012 to chained 2017 dollars')


if __name__ == '__main__':
    unittest.main()
