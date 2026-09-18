"""Offline tests for fred_state_employment_vintages.

Run from the project root:
    python3 -m unittest discover -s data/fred_state_employment_vintages/tests
"""
import importlib.util
import json
from pathlib import Path
import unittest

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeContext:
    """The slice of pipeline.Context that alfred.api_records uses."""

    def __init__(self, shards, retrieved='2026-09-17T00:00:00+00:00'):
        self._shards, self._retrieved = shards, retrieved

    def raw_coverage(self):
        return {'sampled': False, 'layout': 'shards'}

    def raw_shards(self, index=0):
        return self._shards

    def raw_receipt(self, index=0):
        return {'retrieved_at': self._retrieved}

    def raw_evidence(self, locator, index=0):
        return [{'input': {'dataset': 'fred_state_employment_vintages', 'artifact': 'test'}, 'locator': locator}]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((HERE / 'config.json').read_text())

    def test_every_state_publishes_the_whole_panel(self):
        panel = [entry for entry in self.config['series'].values() if entry['panel_role'] == 'panel']
        industries = {entry['industry'] for entry in panel}
        by_state = {}
        for entry in panel:
            by_state.setdefault(entry['state'], set()).add(entry['industry'])
        self.assertEqual(len(by_state), 51, 'fifty states plus DC; PR and the Virgin Islands have no alias form')
        for state, seen in sorted(by_state.items()):
            self.assertEqual(seen, industries, state)
        self.assertIn('total_nonfarm', industries)
        # The residual is derived by the consumer and must not be published as a series.
        self.assertNotIn('mining_logging_construction', industries)

    def test_every_series_is_a_monthly_nsa_jobs_series_with_vintages(self):
        for series_id, entry in self.config['series'].items():
            self.assertEqual(entry['frequency'], 'M', series_id)
            self.assertEqual(entry['seasonal_adjustment'], 'NSA', series_id)
            self.assertEqual(entry['metric'], 'employment', series_id)
            self.assertEqual(entry['unit'], 'jobs', series_id)
            self.assertEqual(entry['multiplier'], 1000.0, series_id)   # published thousands of persons
            self.assertEqual(entry['tier'], 'vintages', series_id)
            self.assertGreater(entry['vintage_count'], 1, series_id)
            self.assertTrue(entry['geography'].startswith('geo:US:state:'), series_id)

    def test_coverage_reports_the_binding_first_vintage_not_the_earliest(self):
        coverage = self.config['coverage']
        panel = [e for e in self.config['series'].values() if e['panel_role'] == 'panel']
        self.assertEqual(coverage['binding_first_vintage'], max(e['first_vintage'] for e in panel))
        self.assertGreater(coverage['binding_first_vintage'], min(e['first_vintage'] for e in panel),
                           'if these were equal the binding constraint would be trivial; check the probe')

    def test_declared_combinations_match_the_configured_series(self):
        declaration = json.loads((HERE / 'dataset.json').read_text())
        declared = [combination['series_id'] for combination in declaration['acquisition']['combinations']]
        self.assertEqual(declared, sorted(self.config['series']))
        self.assertEqual({c['realtime_start'] for c in declaration['acquisition']['combinations']}, {'1776-07-04'},
                         'every request must ask for the whole real-time window, or vintages are lost')


class EmissionTests(unittest.TestCase):
    """The industry dimension is the reason this dataset does not reuse the shared helper."""

    def setUp(self):
        self.alfred = load(HERE / 'alfred.py', 'sae_alfred_test')
        self.meta = {'TXMFGN': {'metric': 'employment', 'unit': 'jobs', 'multiplier': 1000.0, 'frequency': 'M',
                                'frequency_long': 'Monthly', 'seasonal_adjustment': 'NSA',
                                'geography': 'geo:US:state:48', 'geography_label': 'Texas', 'state': 'Texas',
                                'title': 'All Employees: Manufacturing in Texas', 'tier': 'vintages',
                                'industry': 'manufacturing', 'ces_industry_code': '30000000',
                                'panel_role': 'panel', 'category': 'employment'}}

    def _records(self, rows):
        shard = {'index': 0, 'path': None, 'retrieved_at': '2026-09-17T00:00:00+00:00',
                 'request': {'params': {'series_id': 'TXMFGN', 'realtime_start': '1776-07-04',
                                        'realtime_end': '9999-12-31'}}}
        payload = {'count': len(rows), 'offset': 0, 'limit': 100000, 'observations': rows}
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as stream:
            json.dump(payload, stream)
            shard['path'] = stream.name
        context = FakeContext([shard])
        return list(self.alfred.api_records(context, 'fred_state_employment_vintages', self.meta,
                                            lambda series_id: 'vintages'))

    def test_observations_carry_the_industry_and_the_vintage(self):
        records = self._records([
            {'realtime_start': '2015-07-17', 'realtime_end': '2016-03-13', 'date': '2015-06-01', 'value': '900.1'},
            {'realtime_start': '2016-03-14', 'realtime_end': '9999-12-31', 'date': '2015-06-01', 'value': '901.4'},
        ])
        observations = [r for r in records if r['kind'] == 'observation']
        self.assertEqual(len(observations), 2, 'both real-time periods of one month must survive')
        self.assertEqual({o['dimensions']['industry'] for o in observations}, {'manufacturing'})
        self.assertEqual({o['dimensions']['ces_industry_code'] for o in observations}, {'30000000'})
        self.assertEqual({o['dimensions']['panel_role'] for o in observations}, {'panel'})
        self.assertEqual([o['dimensions']['vintage'] for o in observations], ['2015-07-17', '2016-03-14'])
        self.assertEqual([o['value'] for o in observations], [900100, 901400])   # thousands -> jobs
        self.assertEqual({o['subject'] for o in observations}, {'geo:US:state:48'})
        self.assertEqual({(o['valid_from'], o['valid_to']) for o in observations}, {('2015-06-01', '2015-07-01')})
        series = next(r for r in records if r.get('entity_type') == 'economic_series')
        self.assertEqual(series['attributes']['industry'], 'manufacturing')

    def test_there_is_no_current_vintage_code_path(self):
        """A fredgraph.csv reader would emit rows that are not real time; it must not exist here."""
        self.assertFalse(hasattr(self.alfred, 'graph_records'))
        pipeline = load(HERE / 'pipeline.py', 'sae_pipeline_test')
        self.assertTrue(hasattr(pipeline, 'run'))


if __name__ == '__main__':
    unittest.main()
