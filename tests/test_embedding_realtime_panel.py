import gzip
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.embedding.realtime_panel import first_releases, records, summary
from worldmodel.model import validate_record

REF = {'dataset': 'fred_county_vintages', 'stage': 'normalized', 'version': 'a' * 64}


class FakeStore:
    def __init__(self, directory):
        self.root = Path(directory)

    def version_dir(self, ref):
        return self.root


def write(directory, rows):
    with gzip.open(Path(directory) / 'records.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')


def observation(subject, year, value, realtime_start, series='POP'):
    return {'id': f'src:{subject}:{year}:{realtime_start}', 'kind': 'observation', 'subject': subject,
            'metric': 'population', 'unit': 'persons', 'value': value, 'valid_from': f'{year}-01-01',
            'valid_to': f'{year + 1}-01-01', 'observed_at': '2026-09-18T00:00:00+00:00',
            'dimensions': {'series_id': series},
            'attributes': {'realtime_start': realtime_start, 'realtime_end': '9999-12-31'},
            'evidence': [{'input': dict(REF), 'record_id': 'x'}]}


class FirstReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        write(self.directory.name, [
            observation('geo:US:county:01001', 2019, 100.0, '2020-03-26'),   # first release
            observation('geo:US:county:01001', 2019, 103.0, '2021-03-25'),   # a later vintage of the same year
            observation('geo:US:county:01001', 2020, 105.0, '2021-03-25'),
            observation('geo:US:state:01', 2019, 900.0, '2020-03-26'),       # not a county
            {**observation('geo:US:county:01003', 2019, 50.0, '2020-03-26'), 'attributes': {}},   # no vintage date
        ])
        self.store = FakeStore(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_keeps_the_earliest_vintage_of_each_value(self):
        values, counts = first_releases(self.store, REF, subject_prefix='geo:US:county:',
                                        metric_of=lambda r: 'pep:population', log=None)
        self.assertEqual(values[('geo:US:county:01001', 'pep:population', 2019)][0], 100.0)
        self.assertEqual(values[('geo:US:county:01001', 'pep:population', 2019)][1], '2020-03-26')
        self.assertEqual(counts['later_vintage_dropped'], 1)
        self.assertEqual(counts['no_realtime_start'], 1)
        self.assertNotIn(('geo:US:state:01', 'pep:population', 2019), values)

    def test_records_are_valid_and_declare_no_revisions(self):
        values, counts = first_releases(self.store, REF, subject_prefix='geo:US:county:',
                                        metric_of=lambda r: 'pep:population', log=None)
        rows = list(records(values, REF, 'county_realtime_panel', '2026-09-18T00:00:00+00:00', lambda f: 'persons'))
        for row in rows:
            validate_record(row)
        row = next(r for r in rows if r['valid_from'] == '2019-01-01')
        self.assertEqual(row['dimensions'], {'available_at': '2020-03-26', 'revisions': 'none', 'source': 'first_release'})
        report = summary(values, counts, REF, 'county_realtime_panel', 'fred_county_vintages')
        self.assertEqual(report['units'], 1)
        self.assertTrue(any('first release' in line for line in report['does_not_establish']))


if __name__ == '__main__':
    unittest.main()
