"""Offline fixtures for politics/procurement full-acquisition pipelines (FEC, Congress, LDA, FR, MIT, ParlGov, USAspending).

Each test writes a few fictional rows in the publisher's raw layout, publishes them as a sharded raw
artifact in a temporary data root and runs the dataset-local pipeline through the Runner.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.ontology import validate_typed_graph  # noqa: F401  (import keeps ontology extensions loaded)
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[1]
RETRIEVED = '2026-09-15T00:00:00+00:00'


class FullPipelineBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content):
        path = self.root / 'fixtures' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return path

    def zipped(self, name, members):
        path = self.root / 'fixtures' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def build(self, dataset, files):
        shards = [{'path': str(path), 'name': name, 'retrieved_at': RETRIEVED,
                   'request': {'method': 'GET', 'url': 'https://example.invalid/' + name, 'params': {}}}
                  for name, path in files]
        raw = self.store.import_shards(dataset, shards, {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(PROJECT / 'data'), self.store, PROJECT).run(dataset, raw_refs={dataset: [raw]})
        records = list(self.store.records(ref))
        self.assertEqual(len(records), len({r['id'] for r in records}))
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records))
        return records

    @staticmethod
    def by(records, **match):
        return [r for r in records if all(r.get(k) == v for k, v in match.items())]


class FecTests(FullPipelineBase):
    def test_candidate_master_cycles_and_principal_committee(self):
        cn24 = self.zipped('cn24.zip', {'cn.txt': 'H4ZZ00001|EXAMPLE, PAT|DEM|2024|ZZ|H|01|C|C|C00000001|1 MAIN ST||TOWN|ZZ|00000\n'
                                                  'S4ZZ00002|SAMPLE, LEE "LEE"|REP|2024|ZZ|S|00|I|C||||||\n'})
        cn22 = self.zipped('cn22.zip', {'cn.txt': 'H4ZZ00001|EXAMPLE, PATRICIA|DEM|2022|ZZ|H|01|C|N||||||\n'})
        records = self.build('fec_candidates', [('cn22.zip', cn22), ('cn24.zip', cn24)])
        entity = self.by(records, kind='entity', entity_id='fec:candidate:H4ZZ00001')
        self.assertEqual([r['label'] for r in entity], ['EXAMPLE, PAT'])  # newest cycle label
        candidacy = self.by(records, predicate='fec_candidacy', subject='fec:candidate:H4ZZ00001')
        self.assertEqual(sorted((r['value']['cycle'], r['valid_from'], r['valid_to']) for r in candidacy),
                         [(2022, '2021-01-01', '2023-01-01'), (2024, '2023-01-01', '2025-01-01')])
        self.assertEqual(self.by(records, predicate='principal_campaign_committee')[0]['object'], 'fec:committee:C00000001')
        self.assertFalse(any('MAIN ST' in json.dumps(r) for r in records))

    def test_bulk_files_aggregate_without_memo_double_counting(self):
        files = [
            ('cm24.zip', self.zipped('cm24.zip', {'cm.txt': 'C00000001|EXAMPLE PAC|TREASURER, T|1 ST||TOWN|CA|90000|B|Q||M|C|EXAMPLE CORP|\n'})),
            ('ccl24.zip', self.zipped('ccl24.zip', {'ccl.txt': 'H4ZZ00001|2024|2024|C00000002|H|P|123\n'})),
            ('weball24.zip', self.zipped('weball24.zip', {'weball24.txt':
                'H4ZZ00001|EXAMPLE, PAT|C|1|DEM|1000.5|0|900|0|10|110.5|0|0|0|0|0|0|800|ZZ|01||||W|55.2|200|0|06/30/2024|0|0\n'
                'H4ZZ00003|NOFILER, X|C|1|IND|0|0|0|0|0|0|0|0|0|0|0|0|0|ZZ|02||||||0|0||0|0\n'})),
            ('webk24.zip', self.zipped('webk24.zip', {'webk24.txt':
                'C00000001|EXAMPLE PAC|Q|B|M|5000|0|4000|0|0|0|0|3000|0|0|0|0|0|100|2100|0|0|2500|0|0|0|07/31/2024\n'})),
            ('pas224.zip', self.zipped('pas224.zip', {'itpas2.txt':
                'C00000001|N|Q2|G2024|1|24K|CCM|EXAMPLE FOR CONGRESS|TOWN|ZZ|00000|||05012024|1000|C00000002|H4ZZ00001|T1|1|||1\n'
                'C00000001|N|Q2|G2024|2|24K|CCM|EXAMPLE FOR CONGRESS|TOWN|ZZ|00000|||05202024|500|C00000002|H4ZZ00001|T2|1|||2\n'
                'C00000001|N|Q2|G2024|3|24K|CCM|EXAMPLE FOR CONGRESS|TOWN|ZZ|00000|||05202024|500|C00000002|H4ZZ00001|T3|1|X|MEMO|3\n'
                'C00000001|N|Q3|G2024|4|24A|CCM|OTHER|TOWN|ZZ|00000|||07012024|250||H4ZZ00003|T4|1|||4\n'})),
            ('oth24.zip', self.zipped('oth24.zip', {'itoth.txt':
                'C00000002|N|Q2|P2024|5|15|ORG|EXAMPLE TRIBE|TOWN|CA|90000|||06012024|300||T5|2|||5\n'
                'C00000002|N|Q2|P2024|6|18K|PAC|EXAMPLE PAC|TOWN|CA|90000|||06022024|1000|C00000001|T6|2|||6\n'})),
            ('oppexp24.zip', self.zipped('oppexp24.zip', {'oppexp.txt':
                'C00000002|N|2024|Q2|7|17|F3|SB|JANE PAYEE|TOWN|CA|90000|06/03/2024|75.25|P2024|RENT|001|Administrative|||IND|7|2|T7||\n'})),
        ]
        records = self.build('fec', files)
        self.assertEqual(self.by(records, kind='entity', entity_id='fec:committee:C00000001')[0]['entity_type'], 'political_committee')
        self.assertEqual(self.by(records, predicate='authorized_committee_of')[0]['object'], 'fec:candidate:H4ZZ00001')
        receipts = self.by(records, metric='total_receipts', subject='fec:candidate:H4ZZ00001')[0]
        self.assertEqual((receipts['value'], receipts['valid_to']), (1000.5, '2024-07-01'))
        self.assertEqual(self.by(records, metric='total_receipts', subject='fec:candidate:H4ZZ00003')[0]['missing_reason'],
                         'no_report_coverage_in_cycle')
        flows = self.by(records, metric='committee_to_candidate_amount')
        may = next(r for r in flows if r['dimensions']['month'] == '2024-05')
        self.assertEqual((may['value'], may['attributes']['transaction_count'], may['valid_from'], may['valid_to']),
                         (1500, 2, '2024-05-01', '2024-06-01'))
        self.assertEqual({(r['predicate'], r['object']) for r in records if r.get('predicate') in ('supports_candidate', 'opposes_candidate')},
                         {('supports_candidate', 'fec:candidate:H4ZZ00001'), ('opposes_candidate', 'fec:candidate:H4ZZ00003')})
        other = self.by(records, metric='committee_itemized_receipts_amount')
        self.assertEqual({r['dimensions']['counterpart'] for r in other}, {None, 'fec:committee:C00000001'})
        expense = self.by(records, metric='operating_expenditures_amount')[0]
        self.assertEqual((expense['value'], expense['dimensions']['category']), (75.25, '001'))
        self.assertFalse(any('JANE PAYEE' in json.dumps(r) for r in records))


    CONTRIBUTION_ROWS = [
        'C00000001|N|Q2|P2024|1|15|IND|DOE, JANE|SPRINGFIELD|IL|627011234|ACME HOSPITAL|PHYSICIAN|05012024|250|||1|||1',
        'C00000001|N|Q2|P2024|2|15|IND|ROE, RICK|SPRINGFIELD|IL|62702|SELF-EMPLOYED|WRITER|05202024|3500|||1|||2',
        'C00000001|N|Q2|P2024|3|15E|IND|ROE, RICK|SPRINGFIELD|IL|62702|SELF-EMPLOYED|WRITER|05202024|999|C00000009||1|X|EARMARK MEMO|3',
        'C00000001|N|Q2|P2024|4|15|ORG|EXAMPLE CORP|SPRINGFIELD|IL|62702|||05202024|5000|||1|||4',
        'C00000002|N|YE|G2024|5|15|IND|POE, PAT|AUSTIN|TX|78701|NONE|RETIRED|99999999|100|||1|||5',
        'short|row',
    ]

    def contribution_records(self, purpose):
        archive = self.zipped('indiv24.zip', {'itcont.txt': '\n'.join(self.CONTRIBUTION_ROWS) + '\n',
                                              'by_date/itcont_2024_1.txt': '\n'.join(self.CONTRIBUTION_ROWS) + '\n'})
        with mock.patch.dict(os.environ, {'WM_COMMERCIAL_USE': purpose}):
            return self.build('fec_individual_contributions', [('indiv24.zip', archive)])

    def test_a_declared_non_commercial_purpose_retains_contributors(self):
        records = self.contribution_records('0')
        rows = [r for r in records if r['dimensions']['aggregation'] == 'contribution']
        # Only the three non-memo individual rows: the memo line and the ORG row stay excluded.
        self.assertEqual({r['dimensions']['contributor_name'] for r in rows}, {'DOE, JANE', 'ROE, RICK', 'POE, PAT'})
        jane = next(r for r in rows if r['dimensions']['contributor_name'] == 'DOE, JANE')
        self.assertEqual((jane['value'], jane['subject'], jane['dimensions']['contributor_employer']),
                         (250, 'fec:committee:C00000001', 'ACME HOSPITAL'))
        self.assertEqual(jane['dimensions']['contributor_zip'], '627011234')
        self.assertEqual(jane['attributes']['rights_decision']['authority'], '52 U.S.C. 30111(a)(4)')
        self.assertTrue(jane['attributes']['rights_decision']['identified_persons_retained'])
        self.assertIn('no persistent person identity is asserted', jane['attributes']['identity_basis'])
        # The aggregates are unaffected by the flag.
        state = next(r for r in records if r['dimensions']['aggregation'] == 'committee_state_month'
                     and r['metric'] == 'individual_contributions_amount' and r['subject'] == 'fec:committee:C00000001')
        self.assertEqual(state['value'], 3750)

    def test_individual_contributions_are_aggregated_without_pii(self):
        rows = [
            'C00000001|N|Q2|P2024|1|15|IND|DOE, JANE|SPRINGFIELD|IL|627011234|ACME HOSPITAL|PHYSICIAN|05012024|250|||1|||1',
            'C00000001|N|Q2|P2024|2|15|IND|ROE, RICK|SPRINGFIELD|IL|62702|SELF-EMPLOYED|WRITER|05202024|3500|||1|||2',
            'C00000001|N|Q2|P2024|3|15E|IND|ROE, RICK|SPRINGFIELD|IL|62702|SELF-EMPLOYED|WRITER|05202024|999|C00000009||1|X|EARMARK MEMO|3',
            'C00000001|N|Q2|P2024|4|15|ORG|EXAMPLE CORP|SPRINGFIELD|IL|62702|||05202024|5000|||1|||4',
            'C00000002|N|YE|G2024|5|15|IND|POE, PAT|AUSTIN|TX|78701|NONE|RETIRED|99999999|100|||1|||5',
            'short|row',
        ]
        self.assertEqual(rows, self.CONTRIBUTION_ROWS)
        # Declared explicitly: relying on the unset default would make this pass by accident.
        records = self.contribution_records('1')
        self.assertEqual([r for r in records if r['dimensions']['aggregation'] == 'contribution'], [])
        state = {r['metric']: r for r in records if r['dimensions']['aggregation'] == 'committee_state_month'
                 and r['subject'] == 'fec:committee:C00000001'}
        self.assertEqual((state['individual_contributions_amount']['value'], state['individual_contribution_count']['value']), (3750, 2))
        self.assertEqual((state['individual_contributions_amount']['valid_from'], state['individual_contributions_amount']['valid_to']),
                         ('2024-05-01', '2024-06-01'))
        undated = next(r for r in records if r['subject'] == 'fec:committee:C00000002' and r['dimensions']['aggregation'] == 'committee_state_month')
        self.assertEqual((undated['dimensions']['month'], undated['valid_from']), (None, '2023-01-01'))
        zip3 = {r['dimensions']['month']: r['value'] for r in records if r['subject'] == 'geo:US:zip3:627' and r['metric'] == 'individual_contributions_amount'}
        self.assertEqual(zip3, {'2024-05': 3750})
        occupations = {r['dimensions']['occupation_category'] for r in records if r['dimensions']['aggregation'] == 'committee_occupation'}
        self.assertEqual(occupations, {'health', 'arts_media', 'retired'})
        bands = {r['dimensions']['size_band'] for r in records if r['dimensions']['aggregation'] == 'committee_size_band' and r['subject'] == 'fec:committee:C00000001'}
        self.assertEqual(bands, {'200_to_499', '3300_and_over'})
        text = json.dumps(records)
        for secret in ('DOE, JANE', 'ROE, RICK', 'ACME HOSPITAL', 'SPRINGFIELD', '627011234'):
            self.assertNotIn(secret, text)


class CongressTests(FullPipelineBase):
    def test_legislators_crosswalks_committees_and_memberships(self):
        legislator = {'id': {'bioguide': 'Z000001', 'icpsr': 99001, 'fec': ['H4ZZ00001'], 'govtrack': 1},
                      'name': {'first': 'Pat', 'last': 'Example', 'official_full': 'Pat Example'}, 'bio': {'birthday': '1970-01-01'},
                      'terms': [{'type': 'rep', 'start': '2023-01-03', 'end': '2025-01-03', 'state': 'ZZ', 'district': 1, 'party': 'Independent',
                                 'phone': '555-0100'}]}
        executive = [{'id': {'govtrack': 5, 'icpsr_prez': 99999}, 'name': {'first': 'Fictional', 'last': 'President'},
                      'terms': [{'type': 'prez', 'start': '2001-01-20', 'end': '2005-01-20', 'party': 'None'}]}]
        committees = [{'type': 'house', 'name': 'House Committee on Examples', 'thomas_id': 'HSZZ',
                       'subcommittees': [{'name': 'Fixtures', 'thomas_id': '01'}]}]
        files = [('legislators-current.json', self.write('legislators-current.json', json.dumps([legislator]))),
                 ('executive.json', self.write('executive.json', json.dumps(executive))),
                 ('committees-current.json', self.write('committees-current.json', json.dumps(committees))),
                 ('committee-membership-current.json', self.write('committee-membership-current.json',
                                                                  json.dumps({'HSZZ': [{'bioguide': 'Z000001', 'rank': 1, 'party': 'majority'}],
                                                                              'HSZZ01': [{'bioguide': 'Z000001', 'rank': 2}]}))),
                 ('legislators-social-media.json', self.write('legislators-social-media.json',
                                                              json.dumps([{'id': {'bioguide': 'Z000001'}, 'social': {'twitter': 'PatExample'}}])))]
        records = self.build('congress_people', files)
        same = {r['object'] for r in self.by(records, predicate='same_as')}
        self.assertEqual(same, {'icpsr:99001', 'fec:candidate:H4ZZ00001', 'icpsr:99999'})
        self.assertEqual({r['object'] for r in self.by(records, predicate='committee_member')},
                         {'congress:committee:hszz00', 'congress:committee:hszz01'})
        self.assertEqual(self.by(records, predicate='part_of')[0]['object'], 'congress:committee:hszz00')
        roles = self.by(records, predicate='holds_role')
        self.assertEqual(sorted((r['subject'], r['valid_from']) for r in roles),
                         [('bioguide:Z000001', '2023-01-03'), ('govtrack:5', '2001-01-20')])
        self.assertIn({'namespace': 'twitter', 'value': 'PatExample'}, [r['value'] for r in self.by(records, predicate='identifier_assignment')])
        self.assertFalse(any('555-0100' in json.dumps(r) for r in records))

    def test_voteview_members_rollcalls_and_grouped_positions(self):
        members = ('congress,chamber,icpsr,state_icpsr,district_code,state_abbrev,party_code,occupancy,last_means,bioname,bioguide_id,born,died,'
                   'nominate_dim1,nominate_dim2,nominate_log_likelihood,nominate_geo_mean_probability,nominate_number_of_votes,'
                   'nominate_number_of_errors,conditional,nokken_poole_dim1,nokken_poole_dim2\n'
                   '118,House,99001,99,1,ZZ,100,,,"EXAMPLE, Pat",Z000001,1970.0,,-0.4,0.1,-10.0,0.9,500,5,,-0.35,0.2\n'
                   '118,House,99002,99,2,ZZ,200,,,"SAMPLE, Lee",,1960.0,,0.5,,,,,,,,\n')
        rollcalls = ('congress,chamber,rollnumber,date,session,clerk_rollnumber,majority_requirement,yea_count,nay_count,nominate_mid_1,'
                     'nominate_mid_2,nominate_spread_1,nominate_spread_2,nominate_log_likelihood,bill_number,vote_result,vote_desc,vote_question,dtl_desc\n'
                     '118,House,1,2023-01-03,1,1,1/2,1,1,0.1,0.2,0.3,0.4,-1.0,HR1,Passed,,On Passage,"Fictional bill"\n')
        parties = 'congress,chamber,party_code,party_name,n_members,nominate_dim1_median,nominate_dim2_median\n118,House,100,Democratic Party,1,-0.4,0.1\n'
        votes = 'congress,chamber,rollnumber,icpsr,cast_code,prob\n118,House,1,99002,6,99.0\n118,House,1,99001,1,99.0\n'
        records = self.build('voteview_rollcalls', [('HSall_members.csv', self.write('m.csv', members)),
                                                    ('HSall_rollcalls.csv', self.write('r.csv', rollcalls)),
                                                    ('HSall_parties.csv', self.write('p.csv', parties)),
                                                    ('H118_votes.csv', self.write('v.csv', votes))])
        score = self.by(records, metric='dw_nominate_dim1', subject='icpsr:99001')[0]
        self.assertEqual((score['value'], score['valid_from'], score['valid_to']), (-0.4, '2023-01-03', '2025-01-03'))
        self.assertEqual(self.by(records, predicate='same_as')[0]['object'], 'bioguide:Z000001')
        positions = self.by(records, event_type='roll_call_member_positions')[0]
        self.assertEqual(positions['attributes']['positions'], {'nay': [99002], 'yea': [99001]})
        self.assertEqual(positions['occurred_at'], '2023-01-03')
        self.assertEqual(self.by(records, event_type='roll_call_vote')[0]['attributes']['yea_count'], 1)

    def test_billstatus_sponsors_cosponsors_actions_and_law(self):
        xml = '''<?xml version="1.0" encoding="utf-8"?><billStatus><version>3.0.0</version><bill>
          <number>7</number><type>HR</type><congress>118</congress><introducedDate>2023-01-09</introducedDate>
          <originChamber>House</originChamber><title>Fictional Example Act</title><policyArea><name>Government Operations and Politics</name></policyArea>
          <sponsors><item><bioguideId>Z000001</bioguideId><party>I</party><state>ZZ</state><district>1</district><isByRequest>N</isByRequest></item></sponsors>
          <cosponsors><item><bioguideId>Z000002</bioguideId><sponsorshipDate>2023-01-10</sponsorshipDate><isOriginalCosponsor>False</isOriginalCosponsor>
            <sponsorshipWithdrawnDate>2023-02-01</sponsorshipWithdrawnDate></item>
            <item><bioguideId>Z000002</bioguideId><sponsorshipDate>2023-01-10</sponsorshipDate><isOriginalCosponsor>False</isOriginalCosponsor></item></cosponsors>
          <committees><item><systemCode>hszz00</systemCode><name>Examples Committee</name><chamber>House</chamber>
            <activities><item><name>Referred To</name><date>2023-01-09T15:00:00Z</date></item></activities></item></committees>
          <subjects><legislativeSubjects><item><name>Fixtures</name></item></legislativeSubjects></subjects>
          <laws><item><type>Public Law</type><number>118-99</number></item></laws>
          <relatedBills><item><congress>118</congress><type>S</type><number>9</number><relationshipDetails><item><type>Identical bill</type><identifiedBy>CRS</identifiedBy></item></relationshipDetails></item></relatedBills>
          <actions><item><actionDate>2023-03-01</actionDate><text>Became Public Law No: 118-99.</text><type>BecameLaw</type><actionCode>36000</actionCode>
            <sourceSystem><name>Library of Congress</name></sourceSystem></item>
            <item><actionDate>2023-01-09</actionDate><text>Referred to the Committee.</text><type>IntroReferral</type>
            <committees><item><systemCode>hszz00</systemCode></item></committees></item></actions>
          <latestAction><actionDate>2023-03-01</actionDate><text>Became Public Law No: 118-99.</text></latestAction></bill></billStatus>'''
        legacy = ('<?xml version="1.0"?><billStatus><bill><billNumber>9</billNumber><billType>HR</billType><congress>117</congress>'
                  '<introducedDate>2021-01-03</introducedDate><committees><billCommittees><item><systemCode>hszz00</systemCode>'
                  '<name>Examples</name><activities><item><name>Referred to</name><date>2021-01-03T00:00:00Z</date></item></activities>'
                  '</item></billCommittees></committees><actions><item><actionDate>2021-01-03</actionDate><text>Introduced in House</text>'
                  '<type>IntroReferral</type><actionCode>1000</actionCode></item></actions></bill></billStatus>')
        archive = self.zipped('BILLSTATUS-118-hr.zip', {'BILLSTATUS-118hr7.xml': xml, 'BILLSTATUS-117hr9.xml': legacy})
        records = self.build('govinfo_billstatus', [('BILLSTATUS-118-hr.zip', archive)])
        bill = 'congress:bill:118-hr-7'
        measures = {r['entity_id']: r for r in self.by(records, kind='entity')}
        self.assertEqual(measures[bill]['attributes']['measure_status'], 'enacted as public/private law')
        self.assertEqual(measures['congress:bill:117-hr-9']['attributes']['measure_status'], 'not enacted as of source update')
        self.assertIn('congress:committee:hszz00', [r['object'] for r in self.by(records, predicate='referred_to_committee', subject='congress:bill:117-hr-9')])
        self.assertEqual(len(self.by(records, predicate='cosponsored_measure')), 1)
        cosponsor = self.by(records, predicate='cosponsored_measure')[0]
        self.assertEqual((cosponsor['subject'], cosponsor['object'], cosponsor['valid_to']), ('bioguide:Z000002', bill, '2023-02-01'))
        self.assertEqual(self.by(records, predicate='became_law')[0]['valid_from'], '2023-03-01')
        self.assertEqual(self.by(records, predicate='related_measure')[0]['object'], 'congress:bill:118-s-9')
        actions = self.by(records, event_type='legislative_action')
        self.assertEqual(len([a for a in actions if bill in a['participants']]), 2)
        self.assertIn('congress:committee:hszz00', next(a for a in actions if a['occurred_at'] == '2023-01-09')['participants'])

    def test_congress_api_pages_join_bill_and_committee_ids(self):
        pages = [{'houseRollCallVotes': [{'congress': 119, 'sessionNumber': 1, 'rollCallNumber': 5, 'startDate': '2025-01-07T12:00:00-05:00',
                                          'legislationType': 'HR', 'legislationNumber': '7', 'result': 'Passed'}], 'pagination': {}},
                 {'nominations': [{'citation': 'PN1', 'congress': 119, 'receivedDate': '2025-01-20', 'organization': 'Department of Examples',
                                   'nominationType': {'isCivilian': True}}], 'pagination': {}},
                 {'committees': [{'systemCode': 'hszz01', 'name': 'Fixtures Subcommittee', 'parent': {'systemCode': 'hszz00'}}]},
                 {'nominations': [{'citation': 'PN1', 'congress': 119, 'receivedDate': '2025-01-20'}]}]
        path = self.write('pages.jsonl', ''.join(json.dumps(p) + '\n' for p in pages))
        records = self.build('congress_gov_api', [('pages.jsonl', path)])
        vote = self.by(records, event_type='house_roll_call_vote')[0]
        self.assertEqual(vote['participants'], ['us:congress:house', 'congress:bill:119-hr-7'])
        self.assertEqual(len(self.by(records, event_type='presidential_nomination_received')), 1)
        self.assertEqual(self.by(records, predicate='part_of')[0]['object'], 'congress:committee:hszz00')


class RegulationAndLobbyingTests(FullPipelineBase):
    def test_lda_filings_amounts_lobbyists_and_contributions(self):
        filing = {'url': 'https://lda.gov/api/v1/filings/f1/', 'filing_uuid': 'f1', 'filing_type': 'Q2', 'filing_year': 2026,
                  'filing_period': 'second_quarter', 'income': '50000.00', 'expenses': None, 'dt_posted': '2026-07-20T10:00:00-04:00',
                  'registrant': {'id': 1, 'name': 'EXAMPLE STRATEGIES', 'contact_telephone': '+1 555-0100'},
                  'client': {'id': 2, 'name': 'FICTIONAL WIDGETS', 'state': 'ZZ'},
                  'lobbying_activities': [{'general_issue_code': 'DEF', 'description': 'Appropriations',
                                           'government_entities': [{'id': 1, 'name': 'SENATE'}, {'id': 1, 'name': 'SENATE'}],
                                           'lobbyists': [{'lobbyist': {'id': 9, 'first_name': 'PAT', 'last_name': 'EXAMPLE'}, 'covered_position': None, 'new': True}]},
                                          {'general_issue_code': 'BUD', 'description': 'Budget', 'government_entities': [],
                                           'lobbyists': [{'lobbyist': {'id': 9, 'first_name': 'PAT', 'last_name': 'EXAMPLE'}, 'covered_position': 'Staff'}]}]}
        contribution = {'url': 'https://lda.gov/api/v1/contributions/c1/', 'filing_uuid': 'c1', 'filing_type': 'MM', 'filing_year': 2026,
                        'filing_period': 'mid_year', 'filer_type': 'lobbyist', 'registrant': {'id': 1, 'name': 'EXAMPLE STRATEGIES'},
                        'lobbyist': {'id': 9, 'first_name': 'PAT', 'last_name': 'EXAMPLE'}, 'address_1': '1 Private Road',
                        'contribution_items': [{'contribution_type': 'feca', 'contributor_name': 'SELF', 'payee_name': 'EXAMPLE FOR SENATE',
                                                'honoree_name': 'Lee Example', 'amount': '1000.00', 'date': '2026-03-01'}]}
        path = self.write('lda.jsonl', ''.join(json.dumps(r) + '\n' for r in (filing, contribution, filing)))
        records = self.build('lda_lobbying', [('lda.jsonl', path)])
        income = self.by(records, metric='lobbying_income')[0]
        self.assertEqual((income['value'], income['subject'], income['valid_from'], income['valid_to']),
                         (50000, 'lda:registrant:1', '2026-04-01', '2026-07-01'))
        self.assertEqual(len(self.by(records, predicate='contacted_government_entity')), 1)
        link = self.by(records, predicate='lobbied_for_client')[0]
        self.assertEqual((link['subject'], link['object'], link['attributes']['issue_codes']), ('lda:lobbyist:9', 'lda:client:2', ['BUD', 'DEF']))
        given = self.by(records, metric='lobbyist_contribution_amount')[0]
        self.assertEqual((given['subject'], given['value'], given['valid_from']), ('lda:lobbyist:9', 1000, '2026-03-01'))
        text = json.dumps(records)
        self.assertNotIn('555-0100', text)
        self.assertNotIn('Private Road', text)

    def test_federal_register_documents_and_duplicate_agencies(self):
        document = {'document_number': '2026-00001', 'type': 'Rule', 'title': 'Fictional rule', 'publication_date': '2026-01-02',
                    'effective_on': '2026-02-01', 'agencies': [{'id': 1, 'name': 'Example Department', 'parent_id': None},
                                                               {'id': 2, 'name': 'Example Bureau', 'parent_id': 1},
                                                               {'id': 2, 'name': 'Example Bureau', 'parent_id': 1}],
                    'cfr_references': [{'title': 99, 'part': '1'}], 'regulation_id_numbers': ['0000-AA00'], 'html_url': 'https://example.invalid'}
        notice = {'document_number': '2026-00002', 'type': 'Notice', 'title': 'Fictional notice', 'publication_date': '2026-01-03',
                  'agencies': [{'id': 2, 'name': 'Example Bureau', 'parent_id': 1}]}
        order = {'document_number': '2026-00003', 'type': 'Presidential Document', 'subtype': 'Executive Order', 'title': 'Fictional order',
                 'publication_date': '2026-01-04', 'executive_order_number': 99999, 'agencies': []}
        path = self.write('fr.jsonl', ''.join(json.dumps(r) + '\n' for r in (document, notice, order, notice)))
        records = self.build('federal_register_documents', [('fr.jsonl', path)])
        types = {r['entity_id']: r['entity_type'] for r in self.by(records, kind='entity')}
        self.assertEqual((types['federalregister:2026-00001'], types['federalregister:2026-00002'], types['federalregister:2026-00003']),
                         ('regulation', 'publication', 'law'))
        status = next(r['value'] for r in self.by(records, predicate='document_status') if r['subject'] == 'federalregister:2026-00002')
        self.assertEqual(status['effective_status'], 'unknown')
        self.assertEqual(len(self.by(records, predicate='issued_document_by')), 3)
        self.assertIn({'namespace': 'executive_order', 'value': '99999'}, [r['value'] for r in self.by(records, predicate='identifier_assignment')])


class ElectionsAndProcurementTests(FullPipelineBase):
    def test_mit_state_senate_and_county_returns(self):
        president = ('year,state,state_po,state_fips,state_cen,state_ic,office,candidate,party_detailed,writein,candidatevotes,totalvotes,version,notes,party_simplified\n'
                     '2024,EXAMPLE,ZZ,6,93,71,US PRESIDENT,"DOE, JANE",DEMOCRAT,False,100,250,20250101,,DEMOCRAT\n'
                     '2024,EXAMPLE,ZZ,6,93,71,US PRESIDENT,"ROE, RICK",REPUBLICAN,False,150,250,20250101,,REPUBLICAN\n')
        senate = ('year\tstate\tstate_po\tstate_fips\tstate_cen\tstate_ic\toffice\tdistrict\tstage\tspecial\tcandidate\tparty_detailed\twritein\tmode\tcandidatevotes\ttotalvotes\tunofficial\tversion\tparty_simplified\n'
                  '2021\t"EXAMPLE"\t"ZZ"\t6\t93\t71\t"US SENATE"\t"statewide"\t"gen"\t"True"\t"A PERSON"\t"INDEPENDENT"\t"False"\t"total"\t70.0\t"NA"\t"False"\t"20220101"\t"OTHER"\n'
                  '2021\t"EXAMPLE"\t"ZZ"\t6\t93\t71\t"US SENATE"\t"statewide"\t"gen"\t"True"\t"JOHN "JACK" DOE"\t""\t"False"\t"total"\t5.0\t"NA"\t"False"\t"20220101"\t"OTHER"\n')
        county = ('year\tstate\tstate_po\tcounty_name\tcounty_fips\toffice\tcandidate\tparty\tcandidatevotes\ttotalvotes\tversion\tmode\n'
                  '2020\tEXAMPLE\tZZ\tEXAMPLE COUNTY\t6001\tUS PRESIDENT\tJANE DOE\tDEMOCRAT\t10\t30\t20210101\tTOTAL\n')
        records = self.build('mit_election_returns', [('1976-2024-president.csv', self.write('p.csv', president)),
                                                      ('1976-2024-senate-state.tab', self.write('s.tab', senate)),
                                                      ('countypres_2000-2024.tab', self.write('c.tab', county))])
        votes = self.by(records, metric='votes_received')
        jane = next(r for r in votes if r['dimensions']['candidate'] == 'DOE, JANE')
        self.assertEqual((jane['subject'], jane['value'], jane['valid_from'], jane['valid_to']), ('geo:US:state:06', 100, '2024-11-05', '2024-11-06'))
        self.assertEqual(len([r for r in self.by(records, metric='total_votes_cast') if r['subject'] == 'geo:US:state:06']), 1)
        special = next(r for r in votes if r['dimensions']['office'] == 'US SENATE')
        self.assertEqual((special['valid_from'], special['dimensions']['special']), ('2021-01-01', True))
        jack = next(r for r in votes if r['dimensions']['candidate'] == 'JOHN "JACK" DOE')
        self.assertEqual((jack['value'], jack['dimensions']['party']), (5, None))
        county_votes = next(r for r in votes if r['subject'] == 'geo:US:county:06001')
        self.assertEqual((county_votes['value'], county_votes['valid_from']), (10, '2020-11-03'))

    def test_parlgov_parties_elections_and_cabinets(self):
        party = ('country_name_short,country_name,party_name_short,party_name_english,party_name,party_name_ascii,family_name_short,family_name,'
                 'left_right,state_market,liberty_authority,eu_anti_pro,cmp,euprofiler,ees,castles_mair,huber_inglehart,ray,benoit_laver,chess,'
                 'country_id,party_id,family_id\nZZZ,Exampleland,EP,Example Party,Beispielpartei,Example Party,soc,Social democracy,3.5,,,,123,,,,,,,,1,10,1\n')
        election = ('country_name_short,country_name,election_type,election_date,vote_share,seats,seats_total,party_name_short,party_name,'
                    'party_name_english,left_right,country_id,election_id,previous_parliament_election_id,previous_cabinet_id,party_id\n'
                    'ZZZ,Exampleland,parliament,2020-05-01,40.5,20,50,EP,Beispielpartei,Example Party,3.5,1,100,,,10\n')
        cabinet = ('country_name_short,country_name,election_date,start_date,cabinet_name,caretaker,cabinet_party,prime_minister,seats,'
                   'election_seats_total,party_name_short,party_name,party_name_english,left_right,country_id,election_id,cabinet_id,previous_cabinet_id,party_id\n'
                   'ZZZ,Exampleland,2020-05-01,2020-06-01,First,0,1,1,20,50,EP,Beispielpartei,Example Party,3.5,1,100,500,,10\n'
                   'ZZZ,Exampleland,2020-05-01,2022-01-01,Second,0,0,0,20,50,EP,Beispielpartei,Example Party,3.5,1,100,501,500,10\n'
                   'ZZZ,Exampleland,2020-05-01,2022-01-01,Second,0,0,0,3,50,none,sans etiquette,no party affiliation,,1,100,501,500,947\n'
                   'ZZZ,Exampleland,2020-05-01,2022-01-01,Second,0,0,0,11,50,none,sans etiquette,no party affiliation,,1,100,501,500,947\n')
        records = self.build('parlgov', [('view_party.csv', self.write('party.csv', party)),
                                         ('view_election.csv', self.write('election.csv', election)),
                                         ('view_cabinet.csv', self.write('cabinet.csv', cabinet))])
        share = self.by(records, metric='election_vote_share')[0]
        self.assertEqual((share['subject'], share['value'], share['valid_from']), ('parlgov:party:10', 40.5, '2020-05-01'))
        member = self.by(records, predicate='cabinet_member_party')
        self.assertEqual([(r['object'], r['valid_from'], r['valid_to']) for r in member], [('parlgov:cabinet:500', '2020-06-01', '2022-01-01')])
        self.assertEqual(self.by(records, predicate='registered_in')[0]['object'], 'iso3:ZZZ')
        independents = self.by(records, metric='parliament_seats_held', subject='parlgov:party:947')[0]
        self.assertEqual((independents['value'], independents['attributes']['source_rows_merged']), (14, 2))

    def test_usaspending_transactions_awards_and_recipients_stay_distinct(self):
        header = ['contract_transaction_unique_key', 'contract_award_unique_key', 'award_id_piid', 'modification_number',
                  'parent_award_agency_id', 'parent_award_id_piid', 'federal_action_obligation', 'total_dollars_obligated',
                  'current_total_value_of_award', 'potential_total_value_of_award', 'action_date', 'action_date_fiscal_year',
                  'awarding_agency_code', 'awarding_agency_name', 'awarding_sub_agency_code', 'awarding_sub_agency_name',
                  'funding_sub_agency_code', 'funding_sub_agency_name', 'recipient_uei', 'recipient_name', 'recipient_parent_uei',
                  'recipient_parent_name', 'primary_place_of_performance_country_code', 'primary_place_of_performance_state_code',
                  'prime_award_transaction_place_of_performance_county_fips_code', 'naics_code', 'product_or_service_code',
                  'action_type_code', 'highly_compensated_officer_1_name']
        rows = [['T1', 'CONT_AWD_A1_9700_B1_9700', 'A1', '0', '9700', 'B1', '100.00', '150.00', '200.00', '500.00', '2025-10-15', '2026',
                 '097', 'Department of Defense', '2100', 'Department of the Army', '2100', 'Department of the Army', 'UEI000000001',
                 'EXAMPLE CORP', 'UEI000000000', 'EXAMPLE HOLDINGS', 'USA', 'CA', '06001', '336411', '1510', 'A', 'SECRET PERSON'],
                ['T2', 'CONT_AWD_A1_9700_B1_9700', 'A1', 'P00001', '9700', 'B1', '50.00', '150.00', '200.00', '500.00', '2026-01-05', '2026',
                 '097', 'Department of Defense', '2100', 'Department of the Army', '2100', 'Department of the Army', 'UEI000000001',
                 'EXAMPLE CORP', 'UEI000000000', 'EXAMPLE HOLDINGS', 'USA', 'CA', '06001', '336411', '1510', 'C', 'SECRET PERSON']]
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\r\n')
        writer.writerow(header)
        writer.writerows(rows)
        archive = self.zipped('FY2026_097_Contracts_Full_20260906.zip', {'FY2026_097_Contracts_Full_20260907_1.csv': buffer.getvalue()})
        records = self.build('usaspending', [('FY2026_097_Contracts_Full_20260906.zip', archive)])
        award = 'usaspending:award:CONT_AWD_A1_9700_B1_9700'
        transactions = self.by(records, metric='federal_action_obligation')
        self.assertEqual(sorted((r['value'], r['valid_from']) for r in transactions), [(50, '2026-01-05'), (100, '2025-10-15')])
        self.assertEqual(len(self.by(records, kind='entity', entity_id=award)), 1)
        self.assertEqual(self.by(records, metric='award_total_obligated')[0]['valid_from'], '2026-09-06')
        self.assertEqual(self.by(records, predicate='awarded_to')[0]['object'], 'uei:UEI000000001')
        self.assertEqual(self.by(records, predicate='subsidiary_of')[0]['object'], 'uei:UEI000000000')
        self.assertEqual(self.by(records, predicate='place_of_performance')[0]['object'], 'geo:US:county:06001')
        self.assertEqual(self.by(records, predicate='order_under_idv')[0]['object'], 'usaspending:award:CONT_IDV_B1_9700')
        self.assertEqual(self.by(records, predicate='award_naics')[0]['object'], 'naics2022:336411')
        self.assertNotIn('SECRET PERSON', json.dumps(records))

    def test_usaspending_assistance_loans_listings_and_redacted_recipients(self):
        header = ['assistance_transaction_unique_key', 'assistance_award_unique_key', 'award_id_fain', 'modification_number',
                  'federal_action_obligation', 'total_obligated_amount', 'face_value_of_loan', 'original_loan_subsidy_cost',
                  'total_face_value_of_loan', 'total_loan_subsidy_cost', 'action_date', 'action_date_fiscal_year',
                  'awarding_agency_code', 'awarding_agency_name', 'awarding_sub_agency_code', 'awarding_sub_agency_name',
                  'recipient_uei', 'recipient_name', 'recipient_parent_uei', 'recipient_address_line_1',
                  'primary_place_of_performance_country_code', 'prime_award_transaction_place_of_performance_state_fips_code',
                  'prime_award_transaction_place_of_performance_county_fips_code', 'cfda_number', 'cfda_title',
                  'assistance_type_code', 'record_type_code', 'action_type_code', 'highly_compensated_officer_1_name']
        rows = [['AT1', 'ASST_NON_LOAN1_012', 'LOAN1', '0', '1000.00', '1500.00', '250000.00', '12000.00', '250000.00', '12000.00',
                 '2025-11-02', '2026', '012', 'Department of Agriculture', '12D2', 'Farm Service Agency', 'UEI000000009',
                 'EXAMPLE FARM LLC', 'UEI000000008', '1 SECRET LANE', 'USA', '28', '28163', '10.110', 'EXAMPLE PROGRAM', '07', '2', 'A',
                 'HIDDEN OFFICER'],
                ['AT2', 'ASST_NON_LOAN1_012', 'LOAN1', '1', '500.00', '1500.00', '0.00', '0.00', '250000.00', '12000.00',
                 '2026-01-10', '2026', '012', 'Department of Agriculture', '12D2', 'Farm Service Agency', 'UEI000000009',
                 'EXAMPLE FARM LLC', 'UEI000000008', '1 SECRET LANE', 'USA', '28', '28163', '10.110', 'EXAMPLE PROGRAM', '07', '2', 'B',
                 'HIDDEN OFFICER'],
                ['AT3', 'ASST_NON_PAY9_012', 'PAY9', '0', '46440.86', '46440.86', '0.00', '0.00', '0.00', '0.00', '2026-09-30', '2026',
                 '012', 'Department of Agriculture', '12D2', 'Farm Service Agency', '', 'REDACTED DUE TO PII', '', '', 'USA', '28', '28163',
                 '10.110', 'EXAMPLE PROGRAM', '06', '3', 'A', '']]
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\r\n')
        writer.writerow(header)
        writer.writerows(rows)
        archive = self.zipped('FY2026_All_Assistance_Full_20260906.zip', {'FY2026_All_Assistance_Full_20260906_1.csv': buffer.getvalue()})
        records = self.build('usaspending_assistance', [('FY2026_All_Assistance_Full_20260906.zip', archive)])
        loan = 'usaspending:award:ASST_NON_LOAN1_012'
        obligations = sorted((r['value'], r['valid_from']) for r in self.by(records, metric='federal_action_obligation', subject=loan))
        self.assertEqual(obligations, [(500, '2026-01-10'), (1000, '2025-11-02')])
        self.assertEqual([r['value'] for r in self.by(records, metric='loan_face_value')], [250000])
        self.assertEqual([r['value'] for r in self.by(records, metric='loan_subsidy_cost')], [12000])
        totals = {r['metric']: (r['value'], r['valid_from']) for r in records if r.get('subject') == loan and r.get('metric', '').startswith('award_total')}
        self.assertEqual(totals, {'award_total_obligated': (1500, '2026-09-06'), 'award_total_loan_face_value': (250000, '2026-09-06'),
                                  'award_total_loan_subsidy_cost': (12000, '2026-09-06')})
        self.assertEqual(len(self.by(records, kind='entity', entity_id=loan)), 1)
        self.assertEqual(self.by(records, predicate='awarded_to')[0]['object'], 'uei:UEI000000009')
        self.assertEqual(self.by(records, predicate='subsidiary_of')[0]['object'], 'uei:UEI000000008')
        self.assertEqual({r['object'] for r in self.by(records, predicate='award_assistance_listing')}, {'cfda:10.110'})
        self.assertEqual({r['object'] for r in self.by(records, predicate='place_of_performance')}, {'geo:US:county:28163'})
        redacted = self.by(records, predicate='recipient_basis')[0]
        self.assertEqual((redacted['subject'], redacted['value']), ('usaspending:award:ASST_NON_PAY9_012', 'individual recipient redacted for privacy'))
        text = json.dumps(records)
        for secret in ('SECRET LANE', 'HIDDEN OFFICER', 'REDACTED DUE TO PII'):
            self.assertNotIn(secret, text)



if __name__ == '__main__':
    unittest.main()
