"""Offline tests for fred_state_industry_vintages (CES state x supersector ALFRED vintages).

Run from the project root:
    python3 -m unittest discover -s data/fred_state_industry_vintages/tests

The units-across-vintages tests are the point of this file. FRED restates the scale a series is
published in (TOTALSL went from billions of dollars to millions, and 11,612 of its 13,537 values
were wrong by a factor of 1000 when one present-day multiplier was applied to every vintage).
``test_units_change_across_vintages_is_applied_per_vintage`` fails if the pipeline ever goes back
to a single multiplier, and ``test_config_flags_every_series_whose_units_change`` fails if the
config stops recording the per-vintage history the pipeline reads.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
DATA = PROJECT / 'data'
DATASET = 'fred_state_industry_vintages'
API_READER = {'format': 'json', 'records_path': ['observations']}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def api_payload(rows, count=None, offset=0, limit=100000):
    return json.dumps({'realtime_start': 'x', 'count': len(rows) if count is None else count, 'offset': offset,
                       'limit': limit, 'observations': rows}).encode()


def obs(date, value, start, end):
    return {'realtime_start': start, 'realtime_end': end, 'date': date, 'value': value}


def run_units(source_units):
    return {'realtime_start': '2007-06-19', 'realtime_end': '9999-12-31', 'source_units': source_units}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((DATA / DATASET / 'config.json').read_text())
        self.build = load(DATA / DATASET / 'build_config.py', 'sae_build')

    def test_helper_copy_is_identical_to_the_panel(self):
        """fred_alfred.py is shared by value: a dataset code snapshot only captures local files."""
        self.assertEqual((DATA / DATASET / 'fred_alfred.py').read_bytes(),
                         (DATA / 'fred_macro_panel' / 'fred_alfred.py').read_bytes())

    def test_series_id_rule(self):
        self.assertEqual(self.build.series_id('06', 'CA', '30000000', None), 'SMS06000003000000001')
        self.assertEqual(self.build.series_id('11', 'DC', '65000000', None), 'SMS11000006500000001')
        self.assertEqual(self.build.series_id('06', 'CA', '00000000', '{abbr}NA'), 'CANA')
        for identifier in ('SMS06000003000000001', 'SMS11000006500000001'):
            self.assertEqual(len(identifier), 20)

    def test_unit_parser(self):
        cases = {'Thousands of Persons': ('persons', 1e3, True), 'Persons': ('persons', 1.0, True),
                 'Millions of Persons': ('persons', 1e6, True), 'Number': ('persons', 1.0, True),
                 'Percent': ('percent', 1.0, True), 'Index 2012=100': ('index_2012_100', 1.0, True)}
        for text, expected in cases.items():
            self.assertEqual(self.build.parse_units(text), expected, text)
        self.assertFalse(self.build.parse_units('Chained 2017 Dollars per Widget')[2])
        self.assertEqual(self.build.base_period('Index 2012=100'), '2012')
        self.assertIsNone(self.build.base_period('Thousands of Persons'))

    def test_units_history_collapses_unchanged_runs_and_keeps_changes(self):
        rows = [{'realtime_start': '2007-06-19', 'realtime_end': '2011-01-01', 'units': 'Thousands of Persons'},
                {'realtime_start': '2011-01-02', 'realtime_end': '2015-01-01', 'units': 'Thousands of Persons'},
                {'realtime_start': '2015-01-02', 'realtime_end': '9999-12-31', 'units': 'Persons'}]
        history = self.build.units_history(rows)
        self.assertEqual([(h['realtime_start'], h['realtime_end'], h['multiplier']) for h in history],
                         [('2007-06-19', '2015-01-01', 1e3), ('2015-01-02', '9999-12-31', 1.0)])

    def test_windows_split_at_the_vintage_limit(self):
        dates = [f'2000-01-{day:02d}' for day in range(1, 11)]
        self.assertEqual(self.build.windows(dates, 4), [['1776-07-04', '2000-01-04'], ['2000-01-05', '2000-01-08'],
                                                       ['2000-01-09', '9999-12-31']])
        self.assertEqual(self.build.windows(dates, 10), [['1776-07-04', '9999-12-31']])

    def test_config_matches_acquisition_and_is_internally_consistent(self):
        definition = json.loads((DATA / DATASET / 'dataset.json').read_text())
        if not self.config.get('series'):
            self.skipTest('config.json series not generated yet; run build_config.py')
        self.assertEqual(definition['acquisition']['combinations'], self.build.combinations(self.config))
        pairs = []
        for identifier, entry in self.config['series'].items():
            self.assertEqual(entry['tier'], 'vintages', identifier)
            self.assertTrue(entry['metric'] and entry['unit'] and entry['frequency'], identifier)
            self.assertTrue(entry['unit_normalized'], f'{identifier} has unrecognised units {entry["source_units"]!r}')
            self.assertTrue(entry['units_history'], identifier)
            pairs.append((entry['geography'], entry['metric']))
        self.assertEqual(len(pairs), len(set(pairs)), 'one (geography, metric) pair per industry')

    def test_config_flags_every_series_whose_units_change(self):
        """The flag must be derived from the recorded history, not asserted by hand."""
        if not self.config.get('series'):
            self.skipTest('config.json series not generated yet; run build_config.py')
        for identifier, entry in self.config['series'].items():
            changed = len({item['source_units'] for item in entry['units_history']}) > 1
            self.assertEqual(entry['units_change_across_vintages'], changed, identifier)
            for item in entry['units_history']:
                self.assertEqual(self.build.parse_units(item['source_units'])[1], item['multiplier'], identifier)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def build(self, shards, config):
        catalog = self.root / 'catalog'
        target = catalog / DATASET
        target.mkdir(parents=True)
        for path in (DATA / DATASET).iterdir():
            if path.is_file() and path.suffix in ('.json', '.py'):
                shutil.copyfile(path, target / path.name)
        (target / 'config.json').write_text(json.dumps(config))
        entries = []
        for number, (content, params) in enumerate(shards):
            path = self.root / f'shard{number}'
            path.write_bytes(content)
            entries.append({'path': path, 'request': {'method': 'GET', 'url': 'https://example.invalid',
                                                      'params': params},
                            'retrieved_at': '2026-09-17T12:00:00+00:00'})
        self.store.import_shards(DATASET, entries, {'publisher': 'fixture', 'acquisition': {'reader': API_READER}},
                                 complete=True)
        ref = Runner(Catalog(catalog), self.store, PROJECT).run(DATASET)
        return list(self.store.records(ref))

    @staticmethod
    def config(units_history, **overrides):
        entry = {'category': 'state_industry_employment', 'tier': 'vintages', 'metric': 'employment_manufacturing',
                 'geography': 'geo:US:state:06', 'industry_code': '30000000', 'industry_label': 'Manufacturing',
                 'industry_level': 'supersector', 'state': 'CA', 'title': 'All Employees: Manufacturing in California',
                 'source_units': units_history[-1]['source_units'], 'unit': 'persons', 'multiplier': 1e3,
                 'unit_normalized': True, 'frequency': 'M', 'frequency_long': 'Monthly',
                 'seasonal_adjustment': 'SA', 'third_party_copyright': [], 'units_history': units_history,
                 'units_change_across_vintages': len({h['source_units'] for h in units_history}) > 1,
                 'vintage_dates': len(units_history), 'windows': [['1776-07-04', '9999-12-31']]}
        entry.update(overrides)
        return {'states': {'CA': '06'}, 'max_vintage_dates_per_request': 1900,
                'series': {'SMS06000003000000001': entry}}

    @staticmethod
    def history(*runs):
        out = []
        for start, end, units in runs:
            unit, multiplier, _ = ('persons', {'Thousands of Persons': 1e3, 'Persons': 1.0,
                                               'Millions of Persons': 1e6}[units], True)
            out.append({'realtime_start': start, 'realtime_end': end, 'source_units': units, 'unit': unit,
                        'multiplier': multiplier, 'base_period': None, 'unit_normalized': True})
        return out

    def test_vintages_are_kept_and_the_industry_is_the_metric(self):
        config = self.config(self.history(('2007-06-19', '9999-12-31', 'Thousands of Persons')))
        shard = api_payload([obs('2020-01-01', '1300.4', '2020-02-21', '2020-03-19'),
                             obs('2020-01-01', '1301.2', '2020-03-20', '9999-12-31'),
                             obs('2026-08-01', '.', '2026-09-18', '9999-12-31')])
        records = self.build([(shard, {'series_id': 'SMS06000003000000001', 'realtime_start': '1776-07-04',
                                       'realtime_end': '9999-12-31', 'offset': 0})], config)
        observations = [r for r in records if r['kind'] == 'observation']
        self.assertEqual([(r['valid_from'], r['valid_to'], r['value'], r['attributes']['realtime_start'],
                           r['observed_at']) for r in observations],
                         [('2020-01-01', '2020-02-01', 1300400.0, '2020-02-21', '2020-02-21'),
                          ('2020-01-01', '2020-02-01', 1301200.0, '2020-03-20', '2020-03-20'),
                          ('2026-08-01', '2026-09-01', None, '2026-09-18', '2026-09-18')])
        self.assertEqual({r['metric'] for r in observations}, {'employment_manufacturing'})
        self.assertEqual({r['subject'] for r in observations}, {'geo:US:state:06'})
        self.assertEqual(observations[0]['dimensions'],
                         {'series_id': 'SMS06000003000000001', 'frequency': 'M', 'seasonal_adjustment': 'SA',
                          'vintage': '2020-02-21'})
        self.assertEqual(observations[-1]['missing_reason'], 'not_available_in_vintage')
        self.assertEqual({r['entity_id'] for r in records if r['kind'] == 'entity'},
                         {'geo:US', 'geo:US:state:06', 'fred:SMS06000003000000001'})
        self.assertEqual(len(records), len({r['id'] for r in records}))

    def test_units_change_across_vintages_is_applied_per_vintage(self):
        """A scale restatement must be read per vintage, not from the series' present units.

        The two vintages below publish the same January 2020 level in different scales. With one
        multiplier the earlier vintage would be 1000x wrong -- the TOTALSL bug exactly.
        """
        config = self.config(self.history(('2007-06-19', '2015-01-01', 'Thousands of Persons'),
                                          ('2015-01-02', '9999-12-31', 'Persons')))
        shard = api_payload([obs('2010-01-01', '1250.0', '2010-02-19', '2015-01-01'),
                             obs('2010-01-01', '1250000', '2015-01-02', '9999-12-31')])
        records = self.build([(shard, {'series_id': 'SMS06000003000000001', 'realtime_start': '1776-07-04',
                                       'realtime_end': '9999-12-31', 'offset': 0})], config)
        observations = [r for r in records if r['kind'] == 'observation']
        self.assertEqual([(r['attributes']['realtime_start'], r['attributes']['source_units'],
                           r['attributes']['unit_multiplier'], r['value']) for r in observations],
                         [('2010-02-19', 'Thousands of Persons', 1e3, 1250000.0),
                          ('2015-01-02', 'Persons', 1.0, 1250000.0)])
        # Same real quantity in both vintages: the normalization is what makes them comparable.
        self.assertEqual(len({r['value'] for r in observations}), 1)
        self.assertTrue(all(r['attributes']['units_change_across_vintages'] for r in observations))
        # And the naive single-multiplier path would have been wrong by exactly 1000x.
        naive = [float(v) * config['series']['SMS06000003000000001']['multiplier'] for v in ('1250.0', '1250000')]
        self.assertEqual(naive[1] / observations[1]['value'], 1000.0)

    def test_missing_units_history_is_refused(self):
        config = self.config(self.history(('2007-06-19', '9999-12-31', 'Thousands of Persons')))
        config['series']['SMS06000003000000001']['units_history'] = []
        shard = api_payload([obs('2020-01-01', '1300.4', '2020-02-21', '9999-12-31')])
        with self.assertRaisesRegex(Exception, 'units_history'):
            self.build([(shard, {'series_id': 'SMS06000003000000001', 'realtime_start': '1776-07-04',
                                 'realtime_end': '9999-12-31', 'offset': 0})], config)

    def test_truncated_response_is_rejected(self):
        config = self.config(self.history(('2007-06-19', '9999-12-31', 'Thousands of Persons')))
        shard = api_payload([obs('2020-01-01', '1300.4', '2020-02-21', '9999-12-31')], count=7)
        with self.assertRaisesRegex(Exception, 'truncated'):
            self.build([(shard, {'series_id': 'SMS06000003000000001', 'realtime_start': '1776-07-04',
                                 'realtime_end': '9999-12-31', 'offset': 0})], config)


if __name__ == '__main__':
    unittest.main()
