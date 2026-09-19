import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.embedding import county_panel as cp
from worldmodel.model import validate_record

REF = {'dataset': 'bls_labor', 'stage': 'normalized', 'version': 'a' * 64}
EDGE_REF = {'dataset': 'irs_soi_migration', 'stage': 'normalized', 'version': 'b' * 64}
CBSA_REF = {'dataset': 'cbsa_delineations', 'stage': 'normalized', 'version': 'c' * 64}


def fake_results():
    qcew = cp.Collector('qcew', REF)
    qcew.unique('geo:US:county:01001', 'qcew:employment', 2019, 100.0, 'persons', 'bls:obs:1')
    qcew.unique('geo:US:county:01001', 'qcew:employment', 2019, 999.0, 'persons', 'bls:obs:dup')
    qcew.unique('geo:US:county:01001', 'qcew:employment', 2020, 90.0, 'persons', 'bls:obs:2')
    storms = cp.Collector('storms', {**REF, 'dataset': 'noaa_storm_events'})
    storms.add('geo:US:county:01001', 'storms:storm_damage_property', 2020, 10.0, 'USD', 'storm:1')
    storms.add('geo:US:county:01001', 'storms:storm_damage_property', 2020, 5.0, 'USD', 'storm:2')
    geography = cp.Collector('geography', {**REF, 'dataset': 'census_geography'})
    geography.unique('geo:US:county:01001', 'geography:latitude', cp.FIRST_YEAR, 32.5, 'degrees', 'geo:1')
    edges = {'migration_ref': EDGE_REF, 'cbsa_ref': CBSA_REF,
             'flows': {('geo:US:county:01001', 'geo:US:county:01003', '2019'):
                       (40.0, '2022-06-30', 'irs:1', '2019-01-01', '2021-01-01')},
             'cbsa': {('geo:US:county:01001', 'geo:US:cbsa:33860', '2023'): ('2023-07-21', 'cbsa:1')}}
    return {'_collect_qcew': qcew.result(), '_collect_storms': storms.result(),
            '_collect_geography': geography.result(), '_collect_edges': edges}


class DatesTests(unittest.TestCase):
    def test_add_months_lands_on_month_end(self):
        from datetime import date
        self.assertEqual(cp.add_months(date(2019, 12, 31), 9).isoformat(), '2020-09-30')
        self.assertEqual(cp.add_months(date(2019, 12, 31), 2).isoformat(), '2020-02-29')
        self.assertEqual(cp.add_months(date(2019, 12, 31), 0).isoformat(), '2019-12-31')
        self.assertEqual(cp.add_months(date(2019, 12, 31), 12).isoformat(), '2020-12-31')

    def test_year_available_follows_the_declared_lag(self):
        self.assertEqual(cp.year_available('qcew', 2019), '2020-09-30')
        self.assertEqual(cp.year_available('bea', 2019), '2020-12-31')
        self.assertEqual(cp.year_available('fema', 2019), '2019-12-31')

    def test_every_dated_source_declares_a_rule(self):
        for name, spec in cp.SOURCES.items():
            self.assertTrue(spec['rule'])
            self.assertIn(spec['revisions'], ('none', 'minor', 'major', 'static'))


class CountyTests(unittest.TestCase):
    def test_state_totals_and_unknown_counties_are_not_counties(self):
        self.assertTrue(cp.is_county('geo:US:county:01001'))
        self.assertFalse(cp.is_county('geo:US:county:01000'))
        self.assertFalse(cp.is_county('geo:US:county:01999'))
        self.assertFalse(cp.is_county('geo:US:county:1001'))
        self.assertFalse(cp.is_county('geo:US:state:01'))
        self.assertEqual(cp.state_of('geo:US:county:48453'), 'geo:US:state:48')


class CollectorTests(unittest.TestCase):
    def test_unique_keeps_the_first_value_and_counts_duplicates(self):
        result = fake_results()['_collect_qcew']
        self.assertEqual(result['values'][('geo:US:county:01001', 'qcew:employment', 2019)], 100.0)
        self.assertEqual(result['duplicates'], 1)

    def test_add_sums_and_keeps_every_contributing_record(self):
        result = fake_results()['_collect_storms']
        key = ('geo:US:county:01001', 'storms:storm_damage_property', 2020)
        self.assertEqual(result['values'][key], 15.0)
        self.assertEqual(result['ids'][key], ['storm:1', 'storm:2'])


class RecordTests(unittest.TestCase):
    def test_records_are_valid_and_carry_availability_and_evidence(self):
        records = list(cp.records(fake_results(), '2026-09-18T00:00:00+00:00'))
        for record in records:
            validate_record(record)
        observation = next(r for r in records if r['metric'] == 'qcew:employment' and r['valid_from'] == '2019-01-01')
        self.assertEqual(observation['dimensions']['available_at'], '2020-09-30')
        self.assertEqual(observation['dimensions']['revisions'], 'minor')
        self.assertEqual(observation['evidence'], [{'input': REF, 'record_id': 'bls:obs:1'}])
        static = next(r for r in records if r['metric'] == 'geography:latitude')
        self.assertEqual(static['dimensions']['available_at'], cp.FIRST_YEAR_DATE)
        flow = next(r for r in records if r.get('predicate') == 'migration_flow')
        self.assertEqual(flow['attributes']['available_at'], '2022-06-30')
        cbsa = next(r for r in records if r.get('predicate') == 'within_cbsa')
        self.assertEqual(cbsa['attributes']['available_at'], '2023-07-21')

    def test_summary_counts_features_edges_and_pins_inputs(self):
        report = cp.summary(fake_results())
        self.assertEqual(report['counties'], 1)
        self.assertEqual(report['features']['qcew:employment']['rows'], 2)
        self.assertEqual(report['edges'], {'migration_flow': 1, 'within_cbsa': 1})
        self.assertIn(EDGE_REF, report['inputs'])
        self.assertTrue(report['does_not_establish'])

    def test_load_reads_published_records_back(self):
        records = list(cp.records(fake_results(), '2026-09-18T00:00:00+00:00'))

        class FakeStore:
            def __init__(self, root):
                self.root = root

            def version_dir(self, ref):
                return self.root

        with tempfile.TemporaryDirectory() as directory:
            with (Path(directory) / 'records.jsonl').open('w') as handle:
                for record in records:
                    handle.write(json.dumps(record) + '\n')
            _, values, available, units, edges = cp.load(FakeStore(Path(directory)), {'dataset': 'county_panel'})
        self.assertEqual(values[('geo:US:county:01001', 'qcew:employment', 2020)], 90.0)
        self.assertEqual(available[('geo:US:county:01001', 'qcew:employment', 2020)], '2021-09-30')
        self.assertEqual(units['qcew:employment'], 'persons')
        self.assertEqual(len(edges), 2)


if __name__ == '__main__':
    unittest.main()
