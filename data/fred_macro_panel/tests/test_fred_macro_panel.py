"""Offline tests for the FRED/ALFRED datasets (fred_macro_panel and the fred_* anchors).

Run from the project root:  python3 -m unittest discover -s data/fred_macro_panel/tests
"""
import gzip
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
ANCHORS = ('fred_cpi', 'fred_oil_price', 'fred_policy_rate', 'fred_treasury10y', 'fred_treasury2y', 'fred_breakeven10y')
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


class HelperTests(unittest.TestCase):
    def test_helper_copies_are_identical(self):
        reference = (DATA / 'fred_macro_panel' / 'fred_alfred.py').read_bytes()
        for dataset in ANCHORS:
            self.assertEqual((DATA / dataset / 'fred_alfred.py').read_bytes(), reference, dataset)

    def test_periods_by_frequency(self):
        helper = load(DATA / 'fred_macro_panel' / 'fred_alfred.py', 'fred_alfred_test')
        self.assertEqual(helper.period('2024-12-01', 'M'), ('2024-12-01', '2025-01-01'))
        self.assertEqual(helper.period('2024-10-01', 'Q'), ('2024-10-01', '2025-01-01'))
        self.assertEqual(helper.period('2024-01-01', 'A'), ('2024-01-01', '2025-01-01'))
        self.assertEqual(helper.period('2024-01-02', 'D'), ('2024-01-02', '2024-01-03'))
        self.assertEqual(helper.period('2024-01-06', 'W', 'Weekly, Ending Saturday'), ('2023-12-31', '2024-01-07'))
        self.assertEqual(helper.period('2024-01-03', 'W', 'Weekly, As of Wednesday'), ('2024-01-03', '2024-01-10'))
        self.assertIsNone(helper.numeric('.', 1e9))
        self.assertEqual(helper.numeric('1.5', 1e9), 1500000000)

    def test_unit_parser_and_windows(self):
        build = load(DATA / 'fred_macro_panel' / 'build_config.py', 'fred_build_test')
        cases = {'Billions of Dollars': ('USD', 1e9), 'Billions of Chained 2017 Dollars': ('USD_chained_2017', 1e9),
                 'Thousands of Persons': ('persons', 1e3), 'Index 1982-1984=100': ('index_1982_1984_100', 1),
                 'Percent': ('percent', 1), 'Dollars per Barrel': ('USD_per_barrel', 1),
                 'U.S. Dollars to One Euro': ('USD_per_EUR', 1), 'Japanese Yen to One U.S. Dollar': ('JPY_per_USD', 1),
                 'Index 1980:Q1=100': ('index_1980_q1_100', 1), 'Millions of Dollars': ('USD', 1e6),
                 'Billions of US Dollars': ('USD', 1e9), 'Level in Thousands': ('count', 1e3),
                 'Millions of 1982-84 CPI Adjusted Dollars': ('USD_1982_1984_cpi_adjusted', 1e6),
                 'U.S. Dollars to One U.K. Pound Sterling': ('USD_per_GBP', 1), 'Percent of GDP': ('percent_of_gdp', 1)}
        for text, (unit, multiplier) in cases.items():
            parsed = build.parse_units(text)
            self.assertEqual(parsed[:2], (unit, multiplier), text)
            self.assertTrue(parsed[2], text)
        sources = ["Moody's", 'ICE', 'S&P']
        self.assertEqual(build.copyright_holders('Consumer price notice; prices of services.', sources), [])
        self.assertEqual(build.copyright_holders("Source: ICE Benchmark Administration; Moody's.", sources), ["Moody's", 'ICE'])
        self.assertEqual(build.copyright_holders('Copyright, 2016, the publisher.', sources), ['see series notes'])
        dates = [f'2000-01-{d:02d}' for d in range(1, 11)]
        self.assertEqual(build.windows(dates, 4), [['1776-07-04', '2000-01-04'], ['2000-01-05', '2000-01-08'],
                                                   ['2000-01-09', '9999-12-31']])
        self.assertEqual(build.windows(dates, 10), [['1776-07-04', '9999-12-31']])

    def test_panel_config_matches_acquisition(self):
        config = json.loads((DATA / 'fred_macro_panel' / 'config.json').read_text())
        definition = json.loads((DATA / 'fred_macro_panel' / 'dataset.json').read_text())
        if not config.get('series'):
            self.skipTest('config.json series not generated yet')
        build = load(DATA / 'fred_macro_panel' / 'build_config.py', 'fred_build_cfg')
        self.assertEqual(definition['acquisition']['combinations'], build.combinations(config))
        for series_id, entry in config['series'].items():
            self.assertTrue(entry['metric'] and entry['unit'] and entry['frequency'], series_id)
            self.assertIn(entry['tier'], ('vintages', 'as_of'))
        pairs = [(e['geography'], e['metric']) for e in config['series'].values()]
        self.assertEqual(len(pairs), len(set(pairs)), 'metric names must be unique per geography')


class UnitsAcrossVintagesTests(unittest.TestCase):
    """The one defect class that produced wrong numbers rather than missing ones.

    FRED restates the *scale* a series is published in, not only its index base. Applying one
    present-day multiplier to every vintage silently corrupts whole eras: TOTALSL was published in
    billions and later in millions, and 11,612 of its 13,537 values were wrong by a factor of 1000,
    which surfaced only as an implausible 22.5% output gap. The config tests below run offline; the
    published-artifact test measures the shipped records and skips when nothing is published.
    """

    #: Periods and vintages hand-checked against the FRED API on 2026-09-17. Each pair is the same
    #: period published on either side of a units change, with the raw value FRED served.
    HAND_CHECKED = {
        # (series, period): [(vintage, raw FRED value, source units, expected normalized value)]
        ('TOTALSL', '1998-03-01'): [('2019-05-07', 1332.90344, 'Billions of Dollars', 1332.90344),
                                    ('2025-02-07', 1332903.44, 'Millions of Dollars', 1332.90344)],
        ('CAOTOT', '2000-01-01'): [('2018-09-25', 1102918418.0, 'Thousands of Dollars', 1102918418000.0),
                                   ('2018-12-20', 1102918.4, 'Millions of Dollars', 1102918400000.0)],
    }

    def setUp(self):
        self.config = json.loads((DATA / 'fred_macro_panel' / 'config.json').read_text())
        if not self.config.get('series'):
            self.skipTest('config.json series not generated yet')

    def test_the_units_change_flag_is_derived_from_the_recorded_history(self):
        scale_changes = []
        for series_id, entry in self.config['series'].items():
            history = entry.get('units_history') or []
            if not history:
                continue
            changed = len({item['source_units'] for item in history}) > 1
            self.assertEqual(bool(entry.get('units_change_across_vintages')), changed, series_id)
            if len({item['multiplier'] for item in history}) > 1:
                scale_changes.append(series_id)
        # A scale change is the dangerous kind: a rebasing changes what a level means, a scale
        # change changes it by a factor of 1000. This asserts the audit's measured count so that a
        # rebuild which loses per-vintage units cannot pass quietly.
        self.assertIn('TOTALSL', scale_changes)
        self.assertGreaterEqual(len(scale_changes), 59, sorted(scale_changes))
        self.assertIn('CAOTOT', scale_changes)          # 51 state personal-income series round-trip

    def test_hand_checked_values_normalize_to_the_same_quantity_in_both_units(self):
        """The regression test the TOTALSL bug asks for, against values checked at the publisher.

        For each pair the raw FRED values differ by a factor of 1000 and the normalized values
        agree, so a single-multiplier pipeline fails here by exactly that factor.
        """
        for (series_id, period), rows in self.HAND_CHECKED.items():
            entry = self.config['series'].get(series_id)
            self.assertIsNotNone(entry, series_id)
            self.assertTrue(entry['units_change_across_vintages'], series_id)
            # pipeline.load_config applies estimation_overrides[series].unit_scale, which is how the
            # requirement's publisher-scale unit (billion_USD) is reached from base units.
            scale = (self.config.get('estimation_overrides', {}).get(series_id) or {}).get('unit_scale', 1)
            normalized = []
            for vintage, raw, source_units, expected in rows:
                item = next((h for h in entry['units_history']
                             if h['realtime_start'] <= vintage <= h['realtime_end']), None)
                self.assertIsNotNone(item, f'{series_id} has no units run covering {vintage}')
                self.assertEqual(item['source_units'], source_units, f'{series_id}@{vintage}')
                value = raw * item['multiplier'] / scale
                self.assertAlmostEqual(value / expected, 1.0, places=6, msg=f'{series_id}@{vintage} {period}')
                normalized.append(value)
            ratio = max(r[1] for r in rows) / min(r[1] for r in rows)
            self.assertAlmostEqual(ratio, 1000.0, places=3, msg='raw values must differ by 1000x')
            self.assertAlmostEqual(max(normalized) / min(normalized), 1.0, places=6)

    def test_published_records_carry_their_own_vintage_multiplier(self):
        from worldmodel.store import Store
        store = Store(DATA)
        try:
            ref = store.latest('fred_macro_panel', 'normalized')
        except (FileNotFoundError, NotADirectoryError):
            self.skipTest('fred_macro_panel/normalized is not published locally')
        path = store.version_dir(ref) / 'records.jsonl.gz'
        if not path.exists():
            self.skipTest('no records.jsonl.gz in the published version')
        series = sorted({series_id for series_id, _ in self.HAND_CHECKED})
        needles = tuple(f'"series_id":"{series_id}"' for series_id in series)
        found = {}
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                if not any(needle in line for needle in needles):
                    continue
                record = json.loads(line)
                if record.get('kind') != 'observation':
                    continue
                series_id = (record.get('dimensions') or {}).get('series_id')
                key = (series_id, str(record.get('valid_from'))[:10])
                if key in self.HAND_CHECKED:
                    found.setdefault(key, {})[record['attributes']['realtime_start']] = record
        for key, rows in self.HAND_CHECKED.items():
            self.assertIn(key, found, f'{key} is absent from the published records')
            for vintage, _, source_units, expected in rows:
                record = found[key].get(vintage)
                self.assertIsNotNone(record, f'{key} has no vintage {vintage}')
                self.assertEqual(record['attributes']['source_units'], source_units, f'{key}@{vintage}')
                self.assertAlmostEqual(record['value'] / expected, 1.0, places=6, msg=f'{key}@{vintage}')
            multipliers = {found[key][v]['attributes']['unit_multiplier'] for v, _, _, _ in rows}
            self.assertEqual(len(multipliers), 2, f'{key} must carry a different multiplier per vintage')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def build(self, dataset, shards, reader, config=None):
        catalog = self.root / 'catalog'
        target = catalog / dataset
        target.mkdir(parents=True)
        for path in (DATA / dataset).iterdir():
            if path.is_file() and path.suffix in ('.json', '.py'):
                shutil.copyfile(path, target / path.name)
        if config is not None:
            (target / 'config.json').write_text(json.dumps(config))
        entries = []
        for number, (content, params) in enumerate(shards):
            path = self.root / f'shard{number}'
            path.write_bytes(content)
            entries.append({'path': path, 'request': {'method': 'GET', 'url': 'https://example.invalid', 'params': params},
                            'retrieved_at': '2026-09-15T12:00:00+00:00'})
        self.store.import_shards(dataset, entries, {'publisher': 'fixture', 'acquisition': {'reader': reader}},
                                 complete=True)
        ref = Runner(Catalog(catalog), self.store, PROJECT).run(dataset)
        self.assertTrue((self.store.version_dir(ref) / 'records.jsonl.gz').exists())
        return list(self.store.records(ref))

    def test_alfred_windows_are_merged_and_vintages_kept(self):
        window1 = {'series_id': 'DFF', 'realtime_start': '1776-07-04', 'realtime_end': '2013-08-29'}
        window2 = {'series_id': 'DFF', 'realtime_start': '2013-08-30', 'realtime_end': '9999-12-31'}
        first = api_payload([obs('2000-01-03', '5.43', '2005-06-28', '2013-08-29'),
                             obs('2013-08-01', '0.08', '2013-08-02', '2013-08-29'),
                             obs('2013-08-02', '0.09', '2013-08-05', '2013-08-29')])
        second = api_payload([obs('2000-01-03', '5.43', '2013-08-30', '9999-12-31'),
                              obs('2013-08-01', '0.08', '2013-08-30', '9999-12-31'),
                              obs('2013-08-02', '0.10', '2013-08-30', '9999-12-31'),
                              obs('2013-09-03', '.', '2013-09-04', '9999-12-31')])
        records = self.build('fred_policy_rate', [(first, window1), (second, window2)], API_READER)
        observations = {(r['valid_from'], r['attributes']['realtime_start']): r for r in records if r['kind'] == 'observation'}
        self.assertEqual(sorted(observations), [('2000-01-03', '2005-06-28'), ('2013-08-01', '2013-08-02'),
                                                ('2013-08-02', '2013-08-05'), ('2013-08-02', '2013-08-30'),
                                                ('2013-09-03', '2013-09-04')])
        merged = observations[('2000-01-03', '2005-06-28')]
        self.assertEqual((merged['attributes']['realtime_end'], merged['value'], merged['unit'], merged['metric']),
                         ('9999-12-31', 5.43, 'percent', 'policy_rate'))
        self.assertEqual((merged['subject'], merged['observed_at'], merged['valid_to']), ('geo:US', '2005-06-28', '2000-01-04'))
        self.assertEqual(merged['dimensions'], {'series_id': 'DFF', 'frequency': 'D', 'seasonal_adjustment': 'NSA',
                                                'vintage': '2005-06-28'})
        self.assertEqual(merged['attributes']['series_id'], 'DFF')
        revised = observations[('2013-08-02', '2013-08-05')]
        self.assertEqual((revised['value'], revised['attributes']['realtime_end']), (0.09, '2013-08-29'))
        self.assertIsNone(observations[('2013-09-03', '2013-09-04')]['value'])
        self.assertEqual(observations[('2013-09-03', '2013-09-04')]['missing_reason'], 'not_available_in_vintage')
        entities = {r['entity_id'] for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities, {'geo:US', 'fred:DFF'})
        self.assertEqual(len(records), len({r['id'] for r in records}))

    def test_offset_slices_of_one_window_merge_into_the_next_window(self):
        window1 = {'series_id': 'DFF', 'realtime_start': '1776-07-04', 'realtime_end': '2013-08-29', 'offset': 0}
        slice2 = {**window1, 'offset': 1}
        window2 = {'series_id': 'DFF', 'realtime_start': '2013-08-30', 'realtime_end': '9999-12-31', 'offset': 0}
        # Two slices of one window, one row each (limit=1), together covering count=2.
        first = api_payload([obs('2000-01-03', '5.43', '2005-06-28', '2013-08-29')], count=2, offset=0, limit=1)
        second = api_payload([obs('2013-08-01', '0.08', '2013-08-02', '2013-08-29')], count=2, offset=1, limit=1)
        third = api_payload([obs('2000-01-03', '5.43', '2013-08-30', '9999-12-31'),
                             obs('2013-08-01', '0.08', '2013-08-30', '9999-12-31')])
        records = self.build('fred_policy_rate', [(first, window1), (second, slice2), (third, window2)], API_READER)
        observations = [r for r in records if r['kind'] == 'observation']
        # Both dates continue unchanged across the window boundary, so each is one merged record.
        self.assertEqual(sorted((r['valid_from'], r['attributes']['realtime_start'], r['attributes']['realtime_end'])
                                for r in observations),
                         [('2000-01-03', '2005-06-28', '9999-12-31'), ('2013-08-01', '2013-08-02', '9999-12-31')])

    def test_truncated_api_response_is_rejected(self):
        payload = api_payload([obs('2000-01-03', '1', '2011-04-06', '9999-12-31')], count=5)
        with self.assertRaisesRegex(Exception, 'truncated'):
            self.build('fred_oil_price', [(payload, {'series_id': 'DCOILWTICO', 'realtime_start': '1776-07-04',
                                                     'realtime_end': '9999-12-31'})], API_READER)

    def test_fred_cpi_api_vintages(self):
        shard = api_payload([obs('1947-01-01', '21.48', '1994-02-17', '9999-12-31'),
                             obs('2026-07-01', '333.1', '2026-08-12', '2026-09-10'),
                             obs('2026-07-01', '333.2', '2026-09-11', '9999-12-31')])
        records = self.build('fred_cpi', [(shard, {'series_id': 'CPIAUCSL', 'realtime_start': '1776-07-04',
                                                   'realtime_end': '9999-12-31'})], API_READER)
        observations = [r for r in records if r['kind'] == 'observation']
        self.assertEqual([(r['valid_from'], r['value'], r['attributes']['realtime_start'], r['attributes']['vintage_tier'])
                          for r in observations],
                         [('1947-01-01', 21.48, '1994-02-17', 'vintages'), ('2026-07-01', 333.1, '2026-08-12', 'vintages'),
                          ('2026-07-01', 333.2, '2026-09-11', 'vintages')])
        self.assertEqual({(r['metric'], r['unit'], r['attributes']['source_series']) for r in observations},
                         {('consumer_price_index', 'index_1982_1984_100', 'CPIAUCSL')})

    def test_fredgraph_current_vintage(self):
        shards = [(b'observation_date,CPIAUCSL\n1947-01-01,21.48\n1947-02-01,21.62\n', {'series': 'CPIAUCSL'}),
                  (b'observation_date,PCEPI\n1959-01-01,15.2\n', {'series': 'PCEPI'})]
        records = self.build('fred_cpi', shards, {'format': 'csv'})
        observations = [r for r in records if r['kind'] == 'observation']
        self.assertEqual([(r['metric'], r['unit'], r['valid_from'], r['valid_to']) for r in observations],
                         [('consumer_price_index', 'index_1982_1984_100', '1947-01-01', '1947-02-01'),
                          ('consumer_price_index', 'index_1982_1984_100', '1947-02-01', '1947-03-01'),
                          ('pce_price_index', 'index_2017_100', '1959-01-01', '1959-02-01')])
        self.assertEqual(observations[0]['dimensions']['vintage'], '2026-09-15')
        self.assertEqual(observations[0]['attributes']['vintage_tier'], 'current')
        self.assertEqual(observations[0]['evidence'][0]['locator'], 'shard:0/line:2')

    def test_panel_state_series_scaled_and_linked(self):
        config = {'as_of': '2026-09-01', 'states': {'CA': '06'}, 'series': {
            'CAOTOT': {'metric': 'personal_income', 'unit': 'USD', 'multiplier': 1e6, 'source_units': 'Millions of Dollars',
                       'frequency': 'Q', 'frequency_long': 'Quarterly', 'seasonal_adjustment': 'SAAR',
                       'geography': 'geo:US:state:06', 'tier': 'as_of', 'category': 'state_income', 'title': 'CA income'},
            'GDPC1': {'metric': 'real_gdp', 'unit': 'USD_chained_2017', 'multiplier': 1e9, 'frequency': 'Q',
                      'frequency_long': 'Quarterly', 'seasonal_adjustment': 'SAAR', 'geography': 'geo:US',
                      'tier': 'vintages', 'category': 'national_accounts', 'title': 'Real GDP',
                      'third_party_copyright': []}}}
        shards = [(api_payload([obs('2025-10-01', '3000000.5', '2026-09-01', '9999-12-31')]),
                   {'series_id': 'CAOTOT', 'realtime_start': '2026-09-01', 'realtime_end': '9999-12-31'}),
                  (api_payload([obs('1947-01-01', '1239.5', '1992-12-22', '1996-01-18'),
                                obs('1947-01-01', '.', '1996-01-19', '1997-05-06')]),
                   {'series_id': 'GDPC1', 'realtime_start': '1776-07-04', 'realtime_end': '9999-12-31'})]
        records = self.build('fred_macro_panel', shards, API_READER, config)
        income = next(r for r in records if r.get('metric') == 'personal_income')
        self.assertEqual((income['subject'], income['value'], income['valid_to']), ('geo:US:state:06', 3000000500000, '2026-01-01'))
        self.assertTrue(income['attributes']['realtime_start_clipped'])
        self.assertEqual(income['attributes']['unit_multiplier'], 1e6)
        self.assertTrue(any(r['kind'] == 'assertion' and r['subject'] == 'geo:US:state:06' and r['object'] == 'geo:US'
                            for r in records))
        gdp = [r for r in records if r.get('metric') == 'real_gdp']
        self.assertEqual([(r['value'], r['observed_at']) for r in gdp], [(1239500000000, '1992-12-22'), (None, '1996-01-19')])
        self.assertFalse(gdp[0]['attributes']['realtime_start_clipped'])

    def test_chained_dollar_vintages_keep_their_own_base_year(self):
        """Each ALFRED vintage is denominated in the base period current at that vintage."""
        history = [{'realtime_start': '1991-12-04', 'realtime_end': '1996-01-18', 'source_units': 'Billions of 1987 Dollars',
                    'unit': 'USD_1987', 'multiplier': 1e9, 'base_period': '1987'},
                   {'realtime_start': '1996-01-19', 'realtime_end': '2013-07-30',
                    'source_units': 'Billions of Chained 1992 Dollars', 'unit': 'USD_chained_1992', 'multiplier': 1e9,
                    'base_period': '1992'},
                   {'realtime_start': '2023-09-28', 'realtime_end': '9999-12-31',
                    'source_units': 'Billions of Chained 2017 Dollars', 'unit': 'USD_chained_2017', 'multiplier': 1e9,
                    'base_period': '2017'}]
        config = {'as_of': '2026-09-01', 'states': {}, 'estimation_overrides': {
            'GDPC1': {'metric': 'real_gdp', 'unit': 'billion_chained_2017_USD', 'unit_scale': 1e9}},
            'series': {'GDPC1': {'metric': 'real_gdp', 'unit': 'USD_chained_2017', 'multiplier': 1e9, 'frequency': 'Q',
                                 'frequency_long': 'Quarterly', 'seasonal_adjustment': 'SAAR', 'geography': 'geo:US',
                                 'tier': 'vintages', 'source_units': 'Billions of Chained 2017 Dollars',
                                 'units_history': history, 'units_change_across_vintages': True}}}
        shards = [(api_payload([obs('1995-01-01', '6000.1', '1995-04-28', '1996-01-18'),
                                obs('1995-01-01', '6500.2', '1996-01-19', '2013-07-30'),
                                obs('1995-01-01', '7000.3', '2023-09-28', '9999-12-31')]),
                   {'series_id': 'GDPC1', 'realtime_start': '1776-07-04', 'realtime_end': '9999-12-31'})]
        records = self.build('fred_macro_panel', shards, API_READER, config)
        rows = [(r['attributes']['realtime_start'], r['unit'], r['attributes']['source_units'],
                 r['dimensions']['base_period']) for r in records if r['kind'] == 'observation']
        self.assertEqual(rows, [('1995-04-28', 'billion_USD_1987', 'Billions of 1987 Dollars', '1987'),
                                ('1996-01-19', 'billion_USD_chained_1992', 'Billions of Chained 1992 Dollars', '1992'),
                                ('2023-09-28', 'billion_chained_2017_USD', 'Billions of Chained 2017 Dollars', '2017')])
        self.assertTrue(all(r['attributes']['units_change_across_vintages']
                            for r in records if r['kind'] == 'observation'))

    def test_estimation_overrides_match_requirements(self):
        from worldmodel.estimation.data import SeriesRequirement
        requirements = json.loads((PROJECT / 'worldmodel' / 'estimation' / 'requirements.json').read_text())
        wanted = {s['metric']: s for component in requirements['components'].values() for s in component.get('series', [])}
        config = {'as_of': '2026-09-01', 'states': {}, 'estimation_overrides': {
            'TOTALSL': {'metric': 'consumer_credit_outstanding', 'unit': 'billion_USD', 'unit_scale': 1e9},
            'GDPPOT': {'metric': 'potential_gdp', 'unit': 'billion_chained_2017_USD', 'unit_scale': 1e9}},
            'series': {
                'TOTALSL': {'metric': 'consumer_credit_outstanding', 'unit': 'USD', 'multiplier': 1e6, 'frequency': 'M',
                            'frequency_long': 'Monthly', 'seasonal_adjustment': 'SA', 'geography': 'geo:US',
                            'tier': 'vintages', 'source_units': 'Millions of U.S. Dollars'},
                'GDPPOT': {'metric': 'real_potential_gdp', 'unit': 'USD_chained_2017', 'multiplier': 1e9, 'frequency': 'Q',
                           'frequency_long': 'Quarterly', 'seasonal_adjustment': 'NSA', 'geography': 'geo:US',
                           'tier': 'vintages', 'source_units': 'Billions of Chained 2017 Dollars'}}}
        shards = [(api_payload([obs('2026-06-01', '5061234.5', '2026-08-07', '9999-12-31')]),
                   {'series_id': 'TOTALSL', 'realtime_start': '1776-07-04', 'realtime_end': '9999-12-31'}),
                  (api_payload([obs('2026-04-01', '24380.1', '2026-01-27', '9999-12-31')]),
                   {'series_id': 'GDPPOT', 'realtime_start': '1776-07-04', 'realtime_end': '9999-12-31'})]
        records = self.build('fred_macro_panel', shards, API_READER, config)
        credit = next(r for r in records if r.get('metric') == 'consumer_credit_outstanding')
        self.assertAlmostEqual(credit['value'], 5061.2345)
        self.assertEqual((credit['unit'], credit['attributes']['source_series']), ('billion_USD', 'TOTALSL'))
        for metric in ('consumer_credit_outstanding', 'potential_gdp'):
            requirement = SeriesRequirement.from_dict({k: v for k, v in wanted[metric].items()
                                                       if k in SeriesRequirement.__dataclass_fields__})
            self.assertTrue(any(requirement.matches(r) for r in records), metric)


if __name__ == '__main__':
    unittest.main()
