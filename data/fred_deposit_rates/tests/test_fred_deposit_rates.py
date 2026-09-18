"""Offline tests for fred_deposit_rates.

Run from the project root:
    python3 -m unittest discover -s data/fred_deposit_rates/tests -t data/fred_deposit_rates/tests
"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
DATA = PROJECT / 'data'
DATASET = 'fred_deposit_rates'
API_READER = {'format': 'json', 'records_path': ['observations']}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_package_module(directory, name):
    """Import ``directory/<name>.py`` as a package member so its relative imports resolve."""
    package = importlib.util.module_from_spec(
        importlib.util.spec_from_loader('wm_dataset_' + directory.name, loader=None, is_package=True))
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    try:
        return importlib.import_module('.' + name, package.__name__)
    finally:
        sys.modules.pop(package.__name__, None)
        sys.modules.pop(f'{package.__name__}.{name}', None)


def api_payload(rows):
    return json.dumps({'realtime_start': 'x', 'count': len(rows), 'offset': 0, 'limit': 100000,
                       'observations': rows}).encode()


def obs(date, value, start, end='9999-12-31'):
    return {'realtime_start': start, 'realtime_end': end, 'date': date, 'value': value}


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        self.definition = json.loads((DATA / DATASET / 'dataset.json').read_text())

    def test_helper_is_a_byte_copy_of_the_shared_one(self):
        reference = (DATA / 'fred_macro_panel' / 'fred_alfred.py').read_bytes()
        self.assertEqual((DATA / DATASET / 'fred_alfred.py').read_bytes(), reference)

    def test_every_acquired_series_is_declared_and_metrics_are_unique(self):
        series = self.definition['parameters']['series']
        requested = [c['series_id'] for c in self.definition['acquisition']['combinations']]
        self.assertEqual(sorted(requested), sorted(series))
        self.assertEqual(len(requested), len(set(requested)), 'one request window per series')
        pairs = [(entry['geography'], entry['metric']) for entry in series.values()]
        self.assertEqual(len(pairs), len(set(pairs)), 'metric names must be unique per geography')
        for series_id, entry in series.items():
            self.assertEqual(entry['unit'], 'percent', series_id)
            self.assertEqual(entry['tier'], 'vintages', series_id)
            self.assertFalse(entry['units_change_across_vintages'], series_id)
            self.assertEqual([item['unit'] for item in entry['units_history']], ['percent'], series_id)

    def test_m2own_records_its_commercial_input(self):
        series = self.definition['parameters']['series']
        self.assertTrue(series['M2OWN']['third_party_copyright'], 'iMoneyNet input must be recorded')
        self.assertEqual(series['SAVNRNJ']['third_party_copyright'], [])

    def test_the_component_substitutes_resolve_to_this_dataset(self):
        from worldmodel.estimation.loaders import DEPOSIT_RATE_SUBSTITUTES
        series = self.definition['parameters']['series']
        for series_id, (metric, dataset) in DEPOSIT_RATE_SUBSTITUTES.items():
            self.assertEqual(dataset, DATASET, series_id)
            self.assertEqual(series[series_id]['metric'], metric, series_id)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def build(self, shards):
        catalog = self.root / 'catalog'
        target = catalog / DATASET
        target.mkdir(parents=True)
        for path in (DATA / DATASET).iterdir():
            if path.is_file() and path.suffix in ('.json', '.py'):
                shutil.copyfile(path, target / path.name)
        entries = []
        for number, (content, params) in enumerate(shards):
            path = self.root / f'shard{number}'
            path.write_bytes(content)
            entries.append({'path': path, 'request': {'method': 'GET', 'url': 'https://example.invalid', 'params': params},
                            'retrieved_at': '2026-09-17T12:00:00+00:00'})
        self.store.import_shards(DATASET, entries, {'publisher': 'fixture', 'acquisition': {'reader': API_READER}},
                                 complete=True)
        ref = Runner(Catalog(catalog), self.store, PROJECT).run(DATASET)
        return list(self.store.records(ref))

    def test_weekly_and_monthly_periods_and_vintages(self):
        savings = {'series_id': 'SAVNRNJ', 'realtime_start': '1776-07-04', 'realtime_end': '9999-12-31'}
        own = {'series_id': 'M2OWN', 'realtime_start': '1776-07-04', 'realtime_end': '9999-12-31'}
        records = self.build([
            # A Monday "as of" value spans the following week, and is never revised.
            (api_payload([obs('2009-05-18', '0.22', '2014-03-10'), obs('2021-03-29', '0.04', '2021-03-29')]), savings),
            # A monthly value revised once: two records, the earlier one closed.
            (api_payload([obs('2019-05-01', '0.530', '2019-06-21', '2019-07-11'),
                          obs('2019-05-01', '0.536', '2019-07-12'),
                          obs('2019-06-01', '.', '2019-07-12')]), own),
        ])
        observations = {(r['dimensions']['series_id'], r['valid_from'], r['attributes']['realtime_start']): r
                        for r in records if r['kind'] == 'observation'}
        week = observations[('SAVNRNJ', '2009-05-18', '2014-03-10')]
        self.assertEqual((week['valid_to'], week['value'], week['unit'], week['metric']),
                         ('2009-05-25', 0.22, 'percent', 'national_rate_non_jumbo_savings'))
        # observed_at is the ALFRED publication day: pre-archive history is only knowable from 2014-03.
        self.assertEqual(week['observed_at'], '2014-03-10')
        self.assertEqual(week['dimensions'], {'series_id': 'SAVNRNJ', 'frequency': 'W', 'seasonal_adjustment': 'NSA',
                                             'vintage': '2014-03-10'})
        live = observations[('SAVNRNJ', '2021-03-29', '2021-03-29')]
        self.assertEqual(live['observed_at'], '2021-03-29', 'published in the week it refers to')
        first = observations[('M2OWN', '2019-05-01', '2019-06-21')]
        revised = observations[('M2OWN', '2019-05-01', '2019-07-12')]
        self.assertEqual((first['value'], first['attributes']['realtime_end']), (0.53, '2019-07-11'))
        self.assertEqual((revised['value'], revised['valid_to']), (0.536, '2019-06-01'))
        self.assertEqual(revised['attributes']['third_party_copyright'],
                         ["iMoneyNet (Informa Financial Intelligence): money market mutual fund rates are one input "
                          "to the St. Louis Fed's M2 own-rate construction"])
        missing = observations[('M2OWN', '2019-06-01', '2019-07-12')]
        self.assertIsNone(missing['value'])
        self.assertEqual(missing['missing_reason'], 'not_available_in_vintage')
        self.assertEqual({r['entity_id'] for r in records if r['kind'] == 'entity'},
                         {'geo:US', 'fred:SAVNRNJ', 'fred:M2OWN'})
        self.assertEqual(len(records), len({r['id'] for r in records}))

    def test_payloads_without_shards_are_refused(self):
        module = load_package_module(DATA / DATASET, 'pipeline')

        class Empty:
            raw_inputs = ()

        class Sampled:
            raw_inputs = ('one',)

            def raw_coverage(self):
                return {'sampled': True, 'layout': 'single'}

        for context in (Empty(), Sampled()):
            with self.assertRaises(ValueError):
                list(module.run(context))


if __name__ == '__main__':
    unittest.main()
