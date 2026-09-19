"""Offline tests for fred_county_laus_monthly_vintages (fixtures only, no network, no payloads).

Run from the project root:
    python3 -m unittest discover -s data/fred_county_laus_monthly_vintages/tests
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
ANNUAL = HERE.parent / 'fred_county_vintages'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeContext:
    """The slice of pipeline.Context that alfred.api_records uses."""

    def __init__(self, shards, retrieved='2026-09-19T00:00:00+00:00'):
        self._shards, self._retrieved = shards, retrieved

    def raw_coverage(self):
        return {'sampled': False, 'layout': 'shards'}

    def raw_shards(self, index=0):
        return self._shards

    def raw_receipt(self, index=0):
        return {'retrieved_at': self._retrieved}

    def raw_evidence(self, locator, index=0):
        return [{'input': {'dataset': 'fred_county_laus_monthly_vintages', 'artifact': '0' * 64},
                 'locator': locator}]


def family(metric, units, **extra):
    history = [{'realtime_start': start, 'realtime_end': end, 'source_units': source, 'unit': unit,
                'multiplier': multiplier, 'base_period': None}
               for start, end, source, unit, multiplier in units]
    return {'metric': metric, 'frequency': 'M', 'program': 'laus', 'release_id': 116,
            'release_name': 'Unemployment in States and Local Areas', 'originating_source': 'test',
            'geofred_series_group': '1224', 'geofred_title': metric, 'frequency_long': 'Monthly',
            'source_units': history[-1]['source_units'], 'unit': history[-1]['unit'],
            'multiplier': history[-1]['multiplier'], 'units_history': history,
            'units_change_across_vintages': len(history) > 1, 'units_history_exceptions': {}, **extra}


#: The labor force family really does change units mid-archive; the rate family really does not.
LABOR_FORCE = family('labor_force', [('2007-06-07', '2016-03-17', 'Thousands of Persons', 'persons', 1000.0),
                                     ('2016-03-18', '9999-12-31', 'Persons', 'persons', 1.0)])
LABOR_FORCE['units_history_exceptions'] = {
    'SDSHAN3LFN': [{'realtime_start': '2007-06-07', 'realtime_end': '9999-12-31',
                    'source_units': 'Thousands of Persons', 'unit': 'persons', 'multiplier': 1000.0,
                    'base_period': None}]}

CONFIG = {
    'families': {
        'laus_monthly_unemployment_rate': family(
            'unemployment_rate', [('2005-06-08', '9999-12-31', 'Percent', 'percent', 1.0)]),
        'laus_monthly_labor_force': LABOR_FORCE,
    },
    'counties': {'48201': 'Harris County, TX', '09001': 'Fairfield County, CT', '46102': 'Oglala Lakota County, SD'},
    'series': {'TXHARR1URN': ['laus_monthly_unemployment_rate', '48201', 'geofred'],
               'CTFAIR1URN': ['laus_monthly_unemployment_rate', '09001', 'release_structured_id'],
               'CTFAIR1LFN': ['laus_monthly_labor_force', '09001', 'release_structured_id'],
               'TXHARR1LFN': ['laus_monthly_labor_force', '48201', 'geofred'],
               'SDSHAN3LFN': ['laus_monthly_labor_force', '46102', 'geofred_alias_base']},
}


def shard(series_id, rows, index=0, count=None, window=('1776-07-04', '9999-12-31')):
    payload = {'realtime_start': window[0], 'realtime_end': window[1], 'count': len(rows) if count is None else count,
               'offset': 0, 'limit': 100000, 'observations': rows}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as stream:
        json.dump(payload, stream)
    return {'index': index, 'path': stream.name, 'retrieved_at': '2026-09-19T00:00:00+00:00',
            'request': {'params': {'series_id': series_id}}}


def row(start, end, day, value):
    return {'realtime_start': start, 'realtime_end': end, 'date': day, 'value': value}


class EmissionTests(unittest.TestCase):
    def setUp(self):
        self.alfred = load(HERE / 'alfred.py', 'laus_monthly_alfred_test')

    def records(self, *shards):
        return list(self.alfred.api_records(FakeContext(list(shards)), 'fred_county_laus_monthly_vintages', CONFIG))

    def observations(self, *shards):
        return [r for r in self.records(*shards) if r['kind'] == 'observation']

    def test_reference_periods_are_months(self):
        rows = [row('2007-06-07', '2007-07-19', '2007-04-01', '4.1'),
                row('2007-06-07', '2007-07-19', '2007-12-01', '4.4'),
                row('2007-06-07', '2007-07-19', '2008-01-01', '4.6')]
        observations = self.observations(shard('TXHARR1URN', rows))
        self.assertEqual([(o['valid_from'], o['valid_to']) for o in observations],
                         [('2007-04-01', '2007-05-01'), ('2007-12-01', '2008-01-01'),
                          ('2008-01-01', '2008-02-01')], 'a monthly row spans exactly its own month')
        self.assertEqual({o['subject'] for o in observations}, {'geo:US:county:48201'})
        self.assertEqual({o['unit'] for o in observations}, {'percent'})
        self.assertEqual([o['observed_at'] for o in observations], [o['dimensions']['vintage'] for o in observations])

    def test_every_vintage_of_a_month_survives(self):
        rows = [row('2007-06-07', '2007-07-19', '2007-04-01', '4.1'),
                row('2007-07-20', '2008-02-28', '2007-04-01', '4.3'),
                row('2008-03-01', '9999-12-31', '2007-04-01', '4.2')]
        observations = self.observations(shard('TXHARR1URN', rows))
        self.assertEqual(len(observations), 3)
        self.assertEqual([o['dimensions']['vintage'] for o in observations],
                         ['2007-06-07', '2007-07-20', '2008-03-01'])
        self.assertEqual([o['attributes']['realtime_end'] for o in observations],
                         ['2007-07-19', '2008-02-28', '9999-12-31'])
        self.assertEqual({o['id'] for o in observations},
                         {'fred:obs:TXHARR1URN:2007-04-01:2007-06-07', 'fred:obs:TXHARR1URN:2007-04-01:2007-07-20',
                          'fred:obs:TXHARR1URN:2007-04-01:2008-03-01'})

    def test_first_release_is_per_month_and_skips_the_opening_snapshot(self):
        rows = [row('2007-06-07', '2007-07-19', '2007-04-01', '4.1'),   # archive opening: revised history
                row('2007-06-07', '2007-07-19', '2007-05-01', '4.0'),
                row('2007-07-20', '9999-12-31', '2007-04-01', '4.3'),
                row('2007-07-20', '9999-12-31', '2007-06-01', '4.5')]   # 2007-06 first published 2007-07-20
        observations = self.observations(shard('TXHARR1URN', rows))
        self.assertEqual([(o['valid_from'], o['dimensions']['vintage'], o['dimensions']['first_release'])
                          for o in observations],
                         [('2007-04-01', '2007-06-07', False), ('2007-05-01', '2007-06-07', False),
                          ('2007-04-01', '2007-07-20', False), ('2007-06-01', '2007-07-20', True)])
        self.assertEqual([o['attributes']['realtime_start_clipped'] for o in observations],
                         [True, True, False, False])
        self.assertEqual({o['attributes']['series_first_vintage'] for o in observations}, {'2007-06-07'})

    def test_labor_force_is_scaled_by_the_units_of_its_own_vintage(self):
        """FRED re-expressed county labor force from thousands to persons on 2016-03-18, values and all."""
        rows = [row('2016-03-01', '2016-03-17', '2015-06-01', '489.537'),
                row('2016-03-18', '2016-04-26', '2015-06-01', '489537'),
                row('2026-01-01', '9999-12-31', '2015-06-01', '485442')]
        observations = self.observations(shard('CTFAIR1LFN', rows))
        self.assertEqual([o['value'] for o in observations], [489537, 489537, 485442],
                         'the same vintage-dated level must come out the same whichever side of the '
                         'units change it was published on')
        self.assertEqual([o['attributes']['source_units'] for o in observations],
                         ['Thousands of Persons', 'Persons', 'Persons'])
        self.assertEqual([o['attributes']['unit_multiplier'] for o in observations], [1000.0, 1.0, 1.0])
        self.assertEqual({o['unit'] for o in observations}, {'persons'})
        self.assertTrue(all(o['attributes']['units_change_across_vintages'] for o in observations))

    def test_a_series_discontinued_before_the_units_change_keeps_its_own_history(self):
        observations = self.observations(shard('SDSHAN3LFN', [row('2016-03-18', '9999-12-31', '2015-06-01', '3.579')]))
        self.assertEqual(observations[0]['value'], 3579)
        self.assertEqual(observations[0]['attributes']['source_units'], 'Thousands of Persons')
        self.assertEqual(observations[0]['subject'], 'geo:US:county:46102')

    def test_missing_values_are_kept_with_a_reason(self):
        observations = self.observations(shard('TXHARR1URN', [row('2007-06-07', '9999-12-31', '2007-04-01', '.')]))
        self.assertIsNone(observations[0]['value'])
        self.assertEqual(observations[0]['missing_reason'], 'not_available_in_vintage')

    def test_every_code_is_a_census_fips(self):
        observations = self.observations(shard('CTFAIR1URN', [row('2007-06-07', '9999-12-31', '2007-04-01', '5.1')]))
        self.assertEqual(observations[0]['attributes']['code_scheme'], 'fips')
        self.assertEqual(observations[0]['attributes']['fips_source'], 'release_structured_id')
        self.assertFalse(hasattr(self.alfred, 'BEA_COMBINATION'))

    def test_the_realtime_panel_contract_is_the_annual_datasets_contract(self):
        """worldmodel/embedding/realtime_panel.py reads attributes.realtime_start and valid_from."""
        observations = self.observations(shard('TXHARR1URN', [row('2007-06-07', '9999-12-31', '2007-04-01', '4.1')]))
        attributes = observations[0]['attributes']
        self.assertEqual(attributes['realtime_start'], observations[0]['dimensions']['vintage'])
        self.assertEqual(attributes['realtime_start'], observations[0]['observed_at'])
        for field in ('realtime_start', 'realtime_end', 'series_first_vintage', 'realtime_start_clipped',
                      'vintage_tier', 'retrieved_at', 'source_series'):
            self.assertIn(field, attributes)
        for field in ('series_id', 'family', 'program', 'frequency', 'vintage', 'first_release'):
            self.assertIn(field, observations[0]['dimensions'])

    def test_geography_chain_is_emitted_once(self):
        records = self.records(shard('TXHARR1URN', [row('2007-06-07', '9999-12-31', '2007-04-01', '4.1')], index=0),
                               shard('TXHARR1LFN', [row('2007-06-07', '9999-12-31', '2007-04-01', '2100000')], index=1))
        within = {(r['subject'], r['object']) for r in records if r.get('predicate') == 'within'}
        self.assertEqual(within, {('geo:US:state:48', 'geo:US'), ('geo:US:county:48201', 'geo:US:state:48')})
        self.assertEqual(len([r for r in records if r.get('entity_type') == 'county']), 1)
        described = {(r['subject'], r['object']) for r in records if r.get('predicate') == 'describes_location'}
        self.assertEqual(described, {('fred:TXHARR1URN', 'geo:US:county:48201'),
                                     ('fred:TXHARR1LFN', 'geo:US:county:48201')})

    def test_truncated_or_windowed_payloads_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'holds 1 of 5'):
            self.records(shard('TXHARR1URN', [row('2007-06-07', '9999-12-31', '2007-04-01', '4.1')], count=5))
        with self.assertRaisesRegex(ValueError, 'full real-time window'):
            self.records(shard('TXHARR1URN', [row('2020-01-01', '9999-12-31', '2007-04-01', '4.1')],
                               window=('2020-01-01', '9999-12-31')))
        with self.assertRaisesRegex(ValueError, 'not configured'):
            self.records(shard('TXURN', []))

    def test_records_validate(self):
        import sys
        sys.path.insert(0, str(HERE.parents[1]))
        from worldmodel.model import validate_record
        rows = [row('2016-03-01', '2016-03-17', '2015-06-01', '489.537'),
                row('2016-03-18', '9999-12-31', '2015-06-01', '.')]
        for record in self.records(shard('CTFAIR1LFN', rows)):
            validate_record(record)

    def test_there_is_no_current_vintage_code_path(self):
        """A fredgraph.csv reader would emit rows that are not real time; it must not exist here."""
        self.assertFalse(hasattr(self.alfred, 'graph_records'))
        self.assertNotIn('fredgraph', (HERE / 'pipeline.py').read_text())


class ConfigTests(unittest.TestCase):
    """The committed config.json and dataset.json agree, and every code came from a published id."""

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

    def test_the_declared_ceiling_is_not_exceeded(self):
        """The track declared a 2 GiB ceiling for this dataset; desired_bytes must sit under it."""
        self.assertLessEqual(self.declaration['acquisition']['desired_bytes'], 2 * 1024 ** 3)

    def test_every_series_has_a_county_code_and_a_known_family(self):
        for series_id, (name, fips, source) in self.config['series'].items():
            self.assertIn(name, self.config['families'], series_id)
            self.assertRegex(fips, r'^\d{5}$', series_id)
            self.assertIn(source, ('geofred', 'geofred_alias_base', 'release_structured_id'), series_id)
            self.assertIn(fips, self.config['counties'], series_id)
            self.assertTrue(series_id.endswith(self.config['families'][name]['alias_suffix']), series_id)

    def test_geofred_and_the_structured_ids_never_disagreed(self):
        """The place-label join is only usable because it was checked against published geography."""
        for name, entry in self.config['families'].items():
            check = entry['code_check']
            self.assertGreater(check['both_available'], 3000, name)
            self.assertEqual(check['agreed'], check['both_available'], name)
        self.assertEqual(self.config['structured_place_labels']['ambiguous'], {},
                         'a place label claimed by two FIPS codes could not be a join key')

    def test_only_the_two_retired_census_codes_were_rewritten(self):
        for entry in self.config['families'].values():
            for item in entry['code_changes_resolved']:
                self.assertIn(item['geofred_code'], self.config['code_changes'], item['series_id'])
                self.assertEqual(item['successor'],
                                 self.config['code_changes'][item['geofred_code']]['successor'])

    def test_skipped_series_are_not_counties(self):
        """Everything dropped for want of a code is a Federal Reserve district aggregate."""
        for name, entry in self.config['families'].items():
            self.assertTrue(entry['skipped'], name)
            for item in entry['skipped']:
                self.assertIn('FRB-', item['title'], f'{name}: {item["series_id"]} was dropped but is not an '
                                                     f'aggregate; a county must not lose its code silently')

    def test_families_record_their_depth_and_units(self):
        for name, entry in self.config['families'].items():
            self.assertEqual(entry['series'], sum(1 for v in self.config['series'].values() if v[0] == name), name)
            self.assertEqual(entry['frequency'], 'M', name)
            self.assertTrue(entry['units_history'], name)
            self.assertGreater(entry['vintage_probe']['max_vintages'], 200, name)
            self.assertLess(entry['vintage_probe']['earliest_first_vintage'], '2008-01-01', name)
        labor = self.config['families']['laus_monthly_labor_force']
        self.assertTrue(labor['units_change_across_vintages'],
                        'county labor force is re-expressed from thousands to persons on 2016-03-18')
        self.assertEqual(labor['units_history_verified'], 'every series')
        self.assertEqual([h['source_units'] for h in labor['units_history']],
                         ['Thousands of Persons', 'Persons'])

    def test_no_series_is_claimed_by_both_county_vintage_datasets(self):
        """The sibling split is only safe if the two raw artifacts never request the same series."""
        annual = json.loads((ANNUAL / 'config.json').read_text())
        self.assertEqual(set(annual['series']) & set(self.config['series']), set())


if __name__ == '__main__':
    unittest.main()
