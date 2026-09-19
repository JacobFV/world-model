"""Influence panel builder on fixtures shaped like the published normalized records."""
import unittest

from worldmodel.panels import influence


def entity(dataset, entity_id, entity_type, label=None, **attributes):
    return {'kind': 'entity', 'id': f'{dataset}:entity:{entity_id}', 'entity_id': entity_id, 'entity_type': entity_type,
            'label': label, 'attributes': attributes, 'observed_at': '2026-09-15T00:00:00+00:00'}


def assertion(record_id, subject, predicate, obj=None, value=None, **extra):
    record = {'kind': 'assertion', 'id': record_id, 'subject': subject, 'predicate': predicate,
              'observed_at': '2026-09-15T00:00:00+00:00', 'attributes': extra.pop('attributes', {})}
    if obj is not None:
        record['object'] = obj
    if value is not None:
        record['value'] = value
    record.update(extra)
    return record


def observation(record_id, subject, metric, value, dimensions=None, **extra):
    return {'kind': 'observation', 'id': record_id, 'subject': subject, 'metric': metric, 'value': value,
            'dimensions': dimensions or {}, 'attributes': extra.pop('attributes', {}), **extra}


class FixtureSource:
    def __init__(self, records):
        self.records = records

    def has(self, dataset):
        return dataset in self.records

    def ref(self, dataset):
        return {'dataset': dataset, 'stage': 'normalized', 'version': f'v-{dataset}'}

    def stream(self, dataset, needles=()):
        import json
        for record in self.records[dataset]:
            line = json.dumps(record, sort_keys=True, separators=(',', ':'))
            if needles and not any(needle in line for needle in needles):
                continue
            yield record


def positions(congress, rollnumber, yea, nay, not_voting=()):
    chamber = 'H'
    return {'kind': 'event', 'event_type': 'roll_call_member_positions',
            'id': f'voteview_rollcalls:positions:{chamber}{congress}:{rollnumber}', 'occurred_at': '2025-03-01',
            'attributes': {'congress': congress, 'chamber': 'House', 'rollnumber': rollnumber,
                           'rollcall_event': f'voteview_rollcalls:rollcall:{chamber}{congress}:{rollnumber}',
                           'positions': {'yea': list(yea), 'nay': list(nay), 'not_voting': list(not_voting)}}}


def rollcall(congress, rollnumber, bill_number):
    return {'kind': 'event', 'event_type': 'roll_call_vote', 'id': f'voteview_rollcalls:rollcall:H{congress}:{rollnumber}',
            'occurred_at': '2025-03-01', 'attributes': {'congress': congress, 'chamber': 'House', 'rollnumber': rollnumber,
                                                        'bill_number': bill_number, 'vote_question': 'On Passage',
                                                        'vote_result': 'Passed', 'yea_count': 3, 'nay_count': 2}}


def service(icpsr, congress, party):
    return assertion(f'voteview_rollcalls:service:{congress}:House:{icpsr}', f'icpsr:{icpsr}', 'congressional_service',
                     value={'congress': congress, 'chamber': 'House', 'party_code': party, 'state_abbrev': 'TX',
                            'district_code': icpsr % 10})


def fixture():
    members = {1: ('A000001', '100'), 2: ('B000002', '100'), 3: ('C000003', '200'), 4: ('D000004', '200'),
               5: ('E000005', '100')}
    people = [entity('congress_people', f'bioguide:{b}', 'person', label=f'Member {b}') for b, _ in members.values()]
    people += [assertion(f'cp:icpsr:{i}', f'bioguide:{b}', 'same_as', f'icpsr:{i}') for i, (b, _) in members.items()]
    people += [assertion('cp:fec:1', 'bioguide:A000001', 'same_as', 'fec:candidate:H0TX01001'),
               assertion('cp:fec:1s', 'bioguide:A000001', 'same_as', 'fec:candidate:S0TX00001'),
               assertion('cp:fec:3', 'bioguide:C000003', 'same_as', 'fec:candidate:H0TX03003'),
               assertion('cp:cm:1', 'bioguide:A000001', 'committee_member', 'congress:committee:hsju00',
                         attributes={'rank': 1, 'title': 'Chairman', 'side': 'majority'})]
    voteview = [service(i, congress, party) for i, (_, party) in members.items() for congress in (118, 119)]
    voteview += [assertion(f'vv:same_as:{i}', f'icpsr:{i}', 'same_as', f'bioguide:{b}') for i, (b, _) in members.items()]
    voteview += [observation('vv:np:1:119', 'icpsr:1', 'nokken_poole_dim1', -0.3, {'congress': 119, 'chamber': 'House'})]
    voteview += [rollcall(119, 1, 'HR1'), rollcall(119, 2, 'PN12'), rollcall(118, 1, 'S5')]
    # Roll call 1 is party-unity: D majority yea (1, 2 vs 5 nay), R majority nay (3, 4). Member 5 defects.
    voteview += [positions(119, 1, yea=[1, 2], nay=[3, 4, 5]), positions(119, 2, yea=[1, 2, 3, 4, 5], nay=[]),
                 positions(118, 1, yea=[1, 2, 3], nay=[4, 5], not_voting=[])]
    billstatus = [entity('govinfo_billstatus', 'congress:bill:119-hr-1', 'law', congress=119, title='One Big Bill',
                         policy_area='Taxation', introduced_date='2025-01-03', measure_status='enacted'),
                  assertion('bs:sponsor:119-hr-1', 'bioguide:A000001', 'sponsored_measure', 'congress:bill:119-hr-1'),
                  assertion('bs:cos:119-hr-1:B', 'bioguide:B000002', 'cosponsored_measure', 'congress:bill:119-hr-1'),
                  assertion('bs:cos:119-hr-1:E', 'bioguide:E000005', 'cosponsored_measure', 'congress:bill:119-hr-1',
                            attributes={'withdrawn_date': '2025-02-01'}),
                  assertion('bs:ref:119-hr-1', 'congress:bill:119-hr-1', 'referred_to_committee', 'congress:committee:hswm00'),
                  entity('govinfo_billstatus', 'congress:bill:118-hr-9', 'law', congress=118, title='Old bill'),
                  assertion('bs:sponsor:118-hr-9', 'bioguide:A000001', 'sponsored_measure', 'congress:bill:118-hr-9')]

    def filing(uuid, filing_type, posted, income, client='lda:client:1', registrant='lda:registrant:9',
               period='first_quarter', amendment=False):
        return entity('lda_lobbying', f'lda:filing:{uuid}', 'publication', client=client, registrant=registrant,
                      filing_type=filing_type, filing_year=2025, filing_period=period, posted_at=posted,
                      income=income, expenses=None, is_amendment=amendment)

    def issue(uuid, index, text, code='TAX'):
        return assertion(f'lda:issue:{uuid}:{index}', f'lda:filing:{uuid}', 'lobbying_issue',
                         value={'description': text, 'general_issue_code': code})

    lda = [filing('orig', 'Q1', '2025-04-10', 50000), filing('amend', '1A', '2025-05-01', 60000, amendment=True),
           filing('reg', 'RR', '2025-01-10', None, period='first_quarter'),
           filing('other', 'Q2', '2025-07-10', 20000, client='lda:client:2', period='second_quarter'),
           issue('orig', 0, 'H.R. 1, tax provisions'), issue('amend', 0, 'H.R. 1, tax provisions; S. 5'),
           issue('reg', 0, 'H.R. 1 registration statement'),
           issue('other', 0, 'Implementation of H.R.748, CARES Act (P.L. 116-136); support HR 1', code='HCR')]
    fec = [assertion('fec:registration:2026:C1', 'fec:committee:C1', 'fec_committee_registration',
                     value={'cycle': 2026, 'committee_type': 'Q', 'interest_group_category': 'C'}),
           assertion('fec:registration:2026:C2', 'fec:committee:C2', 'fec_committee_registration',
                     value={'cycle': 2026, 'committee_type': 'Q', 'interest_group_category': 'L'}),
           observation('fec:weball:2026:H0TX01001:total_receipts', 'fec:candidate:H0TX01001', 'total_receipts', 1000000.0,
                       {'cycle': 2026, 'report_basis': 'fec_weball_candidate_summary', 'coverage_end_date': '2026-06-30'}),
           observation('fec:weball:2026:H0TX01001:other', 'fec:candidate:H0TX01001', 'other_committee_contributions',
                       250000.0, {'cycle': 2026, 'report_basis': 'fec_weball_candidate_summary'}),
           observation('fec:weball:2026:S0TX00001:total_receipts', 'fec:candidate:S0TX00001', 'total_receipts', 9.0e6,
                       {'cycle': 2026, 'report_basis': 'fec_weball_candidate_summary'}),
           observation('fec:pas2:1', 'fec:committee:C1', 'committee_to_candidate_amount', 5000.0,
                       {'candidate': 'fec:candidate:H0TX01001', 'cycle': 2026, 'transaction_type': '24K'}),
           observation('fec:pas2:2', 'fec:committee:C2', 'committee_to_candidate_amount', 2500.0,
                       {'candidate': 'fec:candidate:H0TX01001', 'cycle': 2026, 'transaction_type': '24K'}),
           observation('fec:pas2:3', 'fec:committee:C9', 'committee_to_candidate_amount', 700.0,
                       {'candidate': 'fec:candidate:H0TX01001', 'cycle': 2026, 'transaction_type': '24E'})]
    candidates = [assertion('fec_candidates:pcc:2026:H0TX01001', 'fec:candidate:H0TX01001', 'principal_campaign_committee',
                            'fec:committee:C0001', valid_from='2025-01-01', valid_to='2027-01-01')]
    individual = [observation('ind:occ:1', 'fec:committee:C0001', 'individual_contributions_amount', 1200.0,
                              {'aggregation': 'committee_occupation', 'cycle': 2026, 'occupation_category': 'legal'}),
                  observation('ind:band:1', 'fec:committee:C0001', 'individual_contributions_amount', 800.0,
                              {'aggregation': 'committee_size_band', 'cycle': 2026, 'size_band': 'under_200'}),
                  observation('ind:occ:x', 'fec:committee:C0001', 'individual_contribution_count', 3,
                              {'aggregation': 'committee_occupation', 'cycle': 2026, 'occupation_category': 'legal'})]
    return {'congress_people': people, 'voteview_rollcalls': voteview, 'govinfo_billstatus': billstatus,
            'lda_lobbying': lda, 'fec': fec, 'fec_candidates': candidates, 'fec_individual_contributions': individual}


PARAMS = {'first_congress': 118, 'last_congress': 119}


def build(records=None):
    source = FixtureSource(records or fixture())
    bills = list(influence.build_bills(source, PARAMS))
    panel = list(influence.build_panel(source, bills, PARAMS, bills_ref={'dataset': 'influence_panel', 'stage': 'bills',
                                                                         'version': 'v-bills'}))
    return bills, panel


class CitationTests(unittest.TestCase):
    def test_citations_parse_published_numbers_and_label_the_congress(self):
        found = influence.bill_citations('H.R. 748, CARES Act (P.L. 116-136); S.2 and HR 1 One Big; S. Res. 12; U.S. 2; '
                                         'S-1 filing; H.Con.Res. 14 (117th Congress); section 3', 119)
        self.assertEqual(found, [('congress:bill:116-hr-748', 'stated_in_text'),
                                 ('congress:bill:119-s-2', 'inferred_from_filing_period'),
                                 ('congress:bill:119-hr-1', 'inferred_from_filing_period'),
                                 ('congress:bill:119-sres-12', 'inferred_from_filing_period'),
                                 ('congress:bill:117-hconres-14', 'stated_in_text')])

    def test_voteview_bill_numbers_map_to_measure_ids_and_other_items_do_not(self):
        self.assertEqual(influence.voteview_bill(119, 'HRES5'), 'congress:bill:119-hres-5')
        self.assertEqual(influence.voteview_bill(118, 'SJRES7'), 'congress:bill:118-sjres-7')
        self.assertIsNone(influence.voteview_bill(119, 'PN12'))
        self.assertIsNone(influence.voteview_bill(119, None))

    def test_calendar(self):
        self.assertEqual(influence.congress_period(118), ('2023-01-03', '2025-01-03'))
        self.assertEqual(influence.cycle_of(118), 2024)
        self.assertEqual(influence.congress_of_year(2025), 119)
        self.assertEqual(influence.congress_of_year(2026), 119)


class BillStageTests(unittest.TestCase):
    def setUp(self):
        self.bills, self.panel = build()
        self.by_bill = {row['bill']: row for row in self.bills if row.get('bill')}

    def test_bill_row_carries_sponsor_cosponsors_committees_and_roll_calls(self):
        row = self.by_bill['congress:bill:119-hr-1']
        self.assertTrue(row['in_billstatus'])
        self.assertEqual(row['sponsor'], 'bioguide:A000001')
        self.assertEqual(row['cosponsors'], ['bioguide:B000002'])
        self.assertEqual(row['withdrawn_cosponsors'], ['bioguide:E000005'])
        self.assertEqual(row['committees'], ['congress:committee:hswm00'])
        self.assertEqual([r['rollcall'] for r in row['roll_calls']], ['voteview_rollcalls:rollcall:H119:1'])

    def test_amendment_supersedes_original_and_registrations_are_not_activity(self):
        lobbying = self.by_bill['congress:bill:119-hr-1']['lobbying']
        self.assertEqual({m['filing'] for m in lobbying['mentions']}, {'lda:filing:amend', 'lda:filing:other'})
        amended = next(m for m in lobbying['mentions'] if m['filing'] == 'lda:filing:amend')
        # The amendment cites H.R. 1 and S. 5, so each gets half of its 60,000.
        self.assertEqual(amended['bills_cited_in_filing'], 2)
        self.assertAlmostEqual(amended['attributed_usd'], 30000.0)
        other = next(m for m in lobbying['mentions'] if m['filing'] == 'lda:filing:other')
        self.assertAlmostEqual(other['attributed_usd'], 10000.0)
        self.assertEqual(lobbying['clients'], ['lda:client:1', 'lda:client:2'])
        self.assertAlmostEqual(lobbying['attributed_usd'], 40000.0)

    def test_a_stated_public_law_moves_the_citation_out_of_the_inferred_congress(self):
        row = self.by_bill['congress:bill:116-hr-748']
        self.assertFalse(row['in_billstatus'])
        self.assertEqual(row['lobbying']['congress_basis'], ['stated_in_text'])
        construction = next(r for r in self.bills if r['id'].endswith('bills:construction'))['construction']
        self.assertEqual(construction['superseded_by_amendment'], 1)
        self.assertEqual(construction['citations_with_stated_congress'], 1)
        self.assertGreaterEqual(construction['cited_bills_not_in_billstatus'], 1)

    def test_every_bill_row_has_evidence_pinned_to_inputs(self):
        row = self.by_bill['congress:bill:119-hr-1']
        inputs = {e['input']['dataset'] for e in row['evidence']}
        self.assertEqual(inputs, {'govinfo_billstatus', 'voteview_rollcalls', 'lda_lobbying'})
        for item in row['evidence']:
            self.assertEqual(len(item['record_ids_sha256']), 64)
            self.assertTrue(item['records'] >= len(item['sample_record_ids']) > 0)


class PanelStageTests(unittest.TestCase):
    def setUp(self):
        self.bills, self.panel = build()
        self.rows = {(r['unit'], r['congress']): r for r in self.panel if r.get('unit')}

    def test_one_row_per_member_congress_chamber(self):
        self.assertEqual(len(self.rows), 10)
        row = self.rows[('bioguide:A000001', 119)]
        self.assertEqual(row['identity']['icpsr'], ['icpsr:1'])
        self.assertEqual(row['identity']['fec_candidate_ids_for_chamber'], ['fec:candidate:H0TX01001'])
        self.assertEqual(row['identity']['icpsr_bioguide_published_by'], ['congress_people', 'voteview_rollcalls'])
        self.assertEqual((row['period_start'], row['period_end'], row['cycle']), ('2025-01-03', '2027-01-03', 2026))
        self.assertFalse(row['period_complete'])
        self.assertTrue(self.rows[('bioguide:A000001', 118)]['period_complete'])

    def test_party_line_defections_count_only_party_unity_roll_calls(self):
        defector = self.rows[('bioguide:E000005', 119)]['roll_calls']
        self.assertEqual(defector['party_unity_votes_cast'], 1)
        self.assertEqual(defector['party_line_defections'], 1)
        self.assertEqual(defector['party_line_defection_rate'], 1.0)
        loyal = self.rows[('bioguide:A000001', 119)]['roll_calls']
        self.assertEqual((loyal['yea'], loyal['nay'], loyal['party_line_defections']), (2, 0, 0))
        # 118/1 is not party-unity: Democrats 2-1 yea, Republicans 1-1 tied, so no Republican majority.
        self.assertEqual(self.rows[('bioguide:A000001', 118)]['roll_calls']['party_unity_votes_cast'], 0)
        self.assertIsNone(self.rows[('bioguide:A000001', 118)]['roll_calls']['party_line_defection_rate'])

    def test_receipts_come_from_the_chamber_matched_candidate_id_only(self):
        row = self.rows[('bioguide:A000001', 119)]
        self.assertEqual(row['receipts_by_source']['total_receipts'], 1000000.0)
        money = row['committee_contributions']
        self.assertEqual(money['direct_usd'], 7500.0)
        self.assertEqual(money['direct_by_interest_group_category'], {'C': 5000.0, 'L': 2500.0})
        self.assertEqual(money['business_pac_direct_usd'], 5000.0)
        self.assertEqual(money['independent_expenditures_support_usd'], 700.0)
        self.assertEqual(row['individual_itemized']['by_occupation_proxy_usd'], {'legal': 1200.0})
        self.assertEqual(row['individual_itemized']['by_size_band_usd'], {'under_200': 800.0})
        self.assertIsNone(self.rows[('bioguide:A000001', 118)]['receipts_by_source'])

    def test_lobbying_on_sponsored_cosponsored_and_voted_bills(self):
        sponsor = self.rows[('bioguide:A000001', 119)]['lobbying_on_linked_bills']
        self.assertEqual(sponsor['sponsored']['bills'], ['congress:bill:119-hr-1'])
        self.assertEqual(sponsor['sponsored']['filings'], 2)
        self.assertEqual(sponsor['voted']['bills'], ['congress:bill:119-hr-1'])
        withdrawn = self.rows[('bioguide:E000005', 119)]['lobbying_on_linked_bills']
        self.assertEqual(withdrawn['cosponsored']['bills'], [], 'a withdrawn cosponsorship is not a link')
        self.assertEqual(withdrawn['voted']['bills'], ['congress:bill:119-hr-1'])
        self.assertIsNone(self.rows[('bioguide:A000001', 118)]['lobbying_on_linked_bills'])
        self.assertIn('inferred', sponsor['link_basis'])

    def test_committee_assignments_attach_to_the_current_congress_only(self):
        current = self.rows[('bioguide:A000001', 119)]['committee_assignments']
        self.assertEqual(current['assignments'][0]['committee'], 'congress:committee:hsju00')
        self.assertIn('start dates are not published', current['basis'])
        self.assertIsNone(self.rows[('bioguide:A000001', 118)]['committee_assignments'])

    def test_sponsorship_counts_by_congress(self):
        self.assertEqual(self.rows[('bioguide:A000001', 118)]['sponsorship'], {'sponsored': 1, 'cosponsored': 0})
        self.assertEqual(self.rows[('bioguide:B000002', 119)]['sponsorship'], {'sponsored': 0, 'cosponsored': 1})

    def test_rows_carry_evidence_and_are_reproducible(self):
        row = self.rows[('bioguide:A000001', 119)]
        inputs = {e['input']['dataset'] for e in row['evidence']}
        self.assertTrue({'congress_people', 'voteview_rollcalls', 'fec', 'fec_candidates', 'fec_individual_contributions',
                         'influence_panel'} <= inputs)
        _, again = build()
        self.assertEqual([r['evidence'] for r in self.panel], [r['evidence'] for r in again])

    def test_coverage_reports_shares_per_input(self):
        report = influence.coverage(self.panel)
        self.assertEqual(report['rows'], 10)
        self.assertEqual(report['legislators'], 5)
        self.assertEqual(report['legislator_share']['committee_assignments'], 0.2)
        self.assertIn('119:House', report['by_congress_chamber'])

    def test_panel_observation_applies_the_declared_selection(self):
        row = self.rows[('bioguide:A000001', 119)]
        values, reason = influence.panel_observation(row, min_party_unity_votes=1)
        self.assertIsNone(reason)
        self.assertEqual(values, {'outcome': 0.0, 'exposure': 25.0})
        values, reason = influence.panel_observation(row, min_party_unity_votes=20)
        self.assertEqual((values, reason), (None, 'too_few_party_unity_votes'))
        values, _ = influence.panel_observation(row, exposure='log_business_pac_direct_thousands', min_party_unity_votes=1)
        self.assertAlmostEqual(values['exposure'], 1.791759469, places=6)
        with self.assertRaises(ValueError):
            influence.panel_observation(row, exposure='lobbying_dollars')



if __name__ == '__main__':
    unittest.main()
