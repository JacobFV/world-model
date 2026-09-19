"""The influence family loader over the published influence_panel, and its pre-registered attempts."""
import tempfile
import unittest

from tests.test_estimation_loaders import FixtureStore
from worldmodel.panels import influence


def panel_row(unit, congress, party='D', defections=2, cast=100, receipts=1.0e6, pacs=2.0e5, business=1.0e4,
              chamber='House'):
    start, end = influence.congress_period(congress)
    return {'id': f'influence_panel:{unit}:{congress}:{chamber}', 'schema': influence.SCHEMA, 'unit': unit,
            'congress': congress, 'chamber': chamber, 'period_start': start, 'period_end': end,
            'period_complete': congress <= 118, 'party': party, 'state': 'TX',
            'roll_calls': {'party_unity_votes_cast': cast, 'party_line_defections': defections,
                           'party_line_defection_rate': defections / cast if cast else None},
            'receipts_by_source': {'total_receipts': receipts, 'other_committee_contributions': pacs},
            'committee_contributions': {'business_pac_direct_usd': business},
            'linked': {'fec_candidate_id': True}, 'evidence': []}


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = FixtureStore(self.temporary.name)
        rows = []
        for u in range(30):
            for congress in range(110, 120):
                rows.append(panel_row(f'bioguide:U{u:06d}', congress, party='D' if u % 2 else 'R',
                                      defections=(u % 7) + congress % 3, pacs=1.0e5 * (1 + (u + congress) % 5),
                                      business=1.0e3 * (1 + (u * congress) % 11)))
        rows.append(panel_row('bioguide:IND0001', 115, party='328'))
        rows.append(panel_row('bioguide:FEW0001', 115, cast=5))
        rows.append(panel_row('bioguide:SEN0001', 115, chamber='Senate'))
        rows.append({'id': 'influence_panel:panel:construction', 'schema': influence.SCHEMA, 'unit': None, 'evidence': []})
        self.store.write('influence_panel', rows)

    def test_loader_selects_declared_rows_and_declares_revisions(self):
        from worldmodel.estimation.loaders import influence_data
        data, evidence = influence_data(self.store)
        construction = data['construction']
        self.assertEqual(construction['rows'], 30 * 9)
        self.assertEqual(construction['periods'], list(range(110, 119)))
        self.assertEqual(construction['skipped'], {'outside_declared_congresses': 30, 'not_a_single_major_party': 1,
                                                   'too_few_party_unity_votes': 1})
        self.assertEqual(data['revisions'], 'fec_amendments_possible')
        self.assertEqual(data['information_time'], 'valid_time')
        first = data['panel'][0]
        self.assertEqual((first['period'], first['date']), (110, '2009-01-03'))
        self.assertEqual(evidence['inputs'][0]['dataset'], 'influence_panel')
        self.assertEqual(evidence['record_count'], 30 * 9)

    def test_loaded_mapping_fits_the_influence_family(self):
        from worldmodel import models
        from worldmodel.estimation.loaders import influence_data
        data, _ = influence_data(self.store, exposure='log_business_pac_direct_thousands')
        result = models.fit('influence', data, '2019-01-03')
        self.assertEqual(result['evidence']['identification'], 'correlational')
        self.assertIn('influence_coefficient', result['estimate'])

    def test_the_family_is_loadable_and_the_plan_attempts_it(self):
        from worldmodel.estimation.loaders import availability, BLOCKED_FAMILIES
        from worldmodel.estimation_cli import load_plan
        self.assertEqual(availability()['families']['influence'], {'status': 'available'})
        self.assertNotIn('influence', BLOCKED_FAMILIES)
        attempts = [a for a in load_plan()['attempts'] if a['target'] == 'influence']
        self.assertEqual(len(attempts), 2)
        for attempt in attempts:
            self.assertEqual(attempt['loader']['function'], 'influence_data')
            self.assertIn(attempt['loader']['options']['exposure'], influence.EXPOSURES)
        self.assertIn('no_revision_leakage', attempts[0]['expected_limits'], 'the declared revision failure is stated')


if __name__ == '__main__':
    unittest.main()
