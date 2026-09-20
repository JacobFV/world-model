import gzip
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.embedding.county_monthly_realtime import FIRST_MONTH, LINE_CONTAINS, metric_of, unit_of
from worldmodel.embedding.realtime_panel import ANNUAL, MONTHLY, first_releases, records, summary
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
            # Canonical JSON, as published records use: the builder's line prefilter depends on it.
            stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')


def observation(subject, year, value, realtime_start, series='POP'):
    return {'id': f'src:{subject}:{year}:{realtime_start}', 'kind': 'observation', 'subject': subject,
            'metric': 'population', 'unit': 'persons', 'value': value, 'valid_from': f'{year}-01-01',
            'valid_to': f'{year + 1}-01-01', 'observed_at': '2026-09-18T00:00:00+00:00',
            'dimensions': {'series_id': series},
            'attributes': {'realtime_start': realtime_start, 'realtime_end': '9999-12-31'},
            'evidence': [{'input': dict(REF), 'record_id': 'x'}]}


MONTHLY_REF = {'dataset': 'fred_county_laus_monthly_vintages', 'stage': 'normalized', 'version': 'b' * 64}


def monthly(subject, month, value, realtime_start, *, family='laus_monthly_labor_force', first_release=True,
            unit='persons'):
    """A row in the ``fred_county_laus_monthly_vintages`` record contract."""
    year, index = int(month[:4]), int(month[5:7])
    nxt = f'{year + 1}-01-01' if index == 12 else f'{year}-{index + 1:02d}-01'
    return {'id': f'fred:obs:X:{month}-01:{realtime_start}', 'kind': 'observation', 'subject': subject,
            'metric': 'labor_force', 'unit': unit, 'value': value, 'valid_from': f'{month}-01', 'valid_to': nxt,
            'observed_at': realtime_start,
            'dimensions': {'family': family, 'first_release': first_release, 'frequency': 'M', 'program': 'laus',
                           'vintage': realtime_start},
            'attributes': {'realtime_start': realtime_start, 'realtime_end': '9999-12-31',
                           'realtime_start_clipped': not first_release},
            'evidence': [{'input': dict(MONTHLY_REF), 'record_id': 'x'}]}


class MonthlyPeriodTests(unittest.TestCase):
    """A monthly source keyed by ANNUAL collapses twelve months onto one; MONTHLY keeps them apart."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        write(self.directory.name, [
            monthly('geo:US:county:29189', '2008-03', 500000.0, '2008-05-07'),
            monthly('geo:US:county:29189', '2008-04', 501000.0, '2008-06-06'),
            monthly('geo:US:county:29189', '2008-04', 499000.0, '2008-07-04'),      # a later vintage of that month
            monthly('geo:US:county:29189', '2008-12', 498000.0, '2009-02-06'),
            # The archive's opening snapshot: already-revised history, never a release.
            monthly('geo:US:county:29189', '1995-06', 480000.0, '2007-06-27', first_release=False),
            # Out of scope: a 2016 vintage extending one series back to 1976.
            monthly('geo:US:county:11001', '1976-01', 300000.0, '2016-03-18'),
            # Not one of the two families in the panel.
            monthly('geo:US:county:29189', '2008-05', 1.0, '2008-07-04', family='laus_annual_labor_force'),
        ])
        self.store = FakeStore(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def collect(self, period):
        return first_releases(self.store, MONTHLY_REF, subject_prefix='geo:US:county:', metric_of=metric_of,
                              period=period, line_contains=LINE_CONTAINS, log=None)

    def test_annual_keying_would_collapse_the_months(self):
        values, _ = self.collect(ANNUAL)
        keys = [k for k in values if k[0] == 'geo:US:county:29189']
        self.assertEqual(keys, [('geo:US:county:29189', 'rt:laus_monthly_labor_force', 2008)])
        self.assertEqual(values[keys[0]][0], 500000.0)   # only the earliest month of 2008 survives

    def test_monthly_keying_keeps_one_first_release_per_month(self):
        values, counts = self.collect(MONTHLY)
        unit = 'geo:US:county:29189'
        feature = 'rt:laus_monthly_labor_force'
        self.assertEqual(sorted(k[2] for k in values), ['2008-03', '2008-04', '2008-12'])
        self.assertEqual(values[(unit, feature, '2008-04')], (501000.0, '2008-06-06', 'fred:obs:X:2008-04-01:2008-06-06'))
        self.assertEqual(counts['later_vintage_dropped'], 1)

    def test_the_opening_snapshot_and_the_1976_extension_are_not_releases(self):
        values, _ = self.collect(MONTHLY)
        self.assertNotIn(('geo:US:county:29189', 'rt:laus_monthly_labor_force', '1995-06'), values)
        self.assertFalse([k for k in values if k[2] < FIRST_MONTH])

    def test_records_carry_the_reference_month_and_validate(self):
        values, counts = self.collect(MONTHLY)
        rows = list(records(values, MONTHLY_REF, 'county_monthly_realtime_panel', '2026-09-19T00:00:00+00:00',
                            unit_of, period=MONTHLY))
        for row in rows:
            validate_record(row)
        row = next(r for r in rows if r['valid_from'] == '2008-12-01')
        self.assertEqual((row['valid_to'], row['unit']), ('2009-01-01', 'persons'))
        self.assertEqual(row['id'], 'county_monthly_realtime_panel:geo:US:county:29189:rt:laus_monthly_labor_force:2008-12')
        self.assertEqual(row['dimensions']['available_at'], '2009-02-06')
        report = summary(values, counts, MONTHLY_REF, 'county_monthly_realtime_panel',
                         'fred_county_laus_monthly_vintages', period=MONTHLY)
        self.assertEqual(report['period'], 'monthly')
        feature = report['features']['rt:laus_monthly_labor_force']
        self.assertEqual((feature['first_month'], feature['last_month'], feature['months']), ('2008-03', '2008-12', 3))


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
