"""The OpenFIGI security bridge: what a CUSIP reaches, and everything it refuses to reach.

``docs/firm-panel.md`` measures the gap this closes: 87.36M 13F holding rows are keyed on a CUSIP
and no issuer CIK, and the only published route to an issuer reached 1.7% of them. These cases are
the route that changes that - a CUSIP answered with a FIGI, and that FIGI answered with a ticker at
a MIC that ``sec_issuer_reference`` also publishes - and the guards that keep it from merging
different things: a check digit that does not recompute, a filer's option pseudo-CUSIP, an exchange
line of a security mistaken for the security, a ticker with no venue, and a value that is not
one-to-one.

Fixtures only; no network.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_unify_scale import EVIDENCE, Fixture, entity

from worldmodel import unify as U
from worldmodel.graph import Graph
from worldmodel.resolution import bridges
from worldmodel.resolution import openfigi as O
from worldmodel.resolution.deterministic import MAPPING_SPECS, link_mapping

APPLE = '037833100'
APPLE_CALL = '037833900'          # a filer's option pseudo-CUSIP: 9 in the seventh position
MSFT = '594918104'
AAPL_US = 'BBG000B9XRY4'          # the United States composite
AAPL_XNAS = 'BBG000B9XSK7'        # one exchange's line of the same security
MSFT_US = 'BBG000BPH459'


def answer(key, figi, *, id_type='ID_CUSIP', id_value=APPLE, level='us_composite', ticker='AAPL',
           mic=None, exch='US', composite=None, share_class=None, matches=1, name='APPLE INC'):
    query = {'id_type': id_type, 'id_value': id_value}
    if mic:
        query['mic_code'] = mic
    return {'kind': 'assertion', 'id': 'of:' + key, 'subject': 'figi:' + figi,
            'predicate': 'openfigi_mapping', 'observed_at': '2026-09-19', 'evidence': EVIDENCE,
            'value': {'query': query, 'figi_level': level, 'match_rank': 0, 'matches_for_query': matches,
                      'published': {'figi': figi, 'name': name, 'ticker': ticker, 'exchCode': exch,
                                    'compositeFIGI': composite, 'shareClassFIGI': share_class,
                                    'securityType': 'Common Stock', 'securityType2': 'Common Stock',
                                    'marketSector': 'Equity', 'securityDescription': ticker}}}


def listing(key, cik, mic, symbol):
    return {'kind': 'assertion', 'id': 'sec:' + key, 'subject': 'sec:cik:' + cik,
            'predicate': 'issuer_listing', 'object': f'ticker:{mic}:{symbol}',
            'observed_at': '2026-09-15', 'evidence': EVIDENCE, 'attributes': {}}


class CusipValueTests(unittest.TestCase):
    """The CUSIP check digit, recomputed rather than trusted."""

    def test_the_check_digit_recomputes(self):
        for value in (APPLE, MSFT, '46625H100', '023135106', '02079K305', '78462F103'):
            self.assertEqual(O.cusip(value), value, value)
        self.assertEqual(O.cusip(' 46625h100 '), '46625H100')

    def test_an_option_pseudo_cusip_is_not_a_cusip(self):
        # 13F filers write a 9 into the seventh position for a put or a call. The value belongs to
        # no issue, and 46,642 of the 141,566 CUSIPs on these information tables are of this kind.
        for value in (APPLE_CALL, '037833950', '594918904', '78462F953', '30303M902'):
            self.assertIsNone(O.cusip(value), value)

    def test_the_all_zero_placeholder_passes_the_check_digit_and_is_still_refused(self):
        self.assertEqual(O.cusip_check_digit('00000000'), '0')
        self.assertIsNone(O.cusip('000000000'))
        self.assertIn('all_zero_cusip', O.REFUSED)

    def test_shapes_that_are_not_cusips(self):
        for value in ('03783310', '0378331000', '037833 10', '', None, 'US0378331005', '0378*3100'):
            self.assertIsNone(O.cusip(value), value)


class MappingStatementTests(unittest.TestCase):
    """Which rows of an answer become identity, and which stay statements."""

    @staticmethod
    def claim(**kwargs):
        return O.identity_claim(answer('x', AAPL_US, **kwargs)['value'])

    def test_a_us_composite_answer_is_the_security_the_cusip_names(self):
        self.assertEqual(self.claim(), ('cusip', APPLE, 'openfigi_cusip_figi'))

    def test_an_instrument_with_no_composite_is_one_instrument(self):
        # A bond, a muni or a preferred: OpenFIGI answers NOT LISTED or TRACE and no composite.
        self.assertEqual(self.claim(level='unlisted', exch='NOT LISTED', id_value='037833AK6')[0], 'cusip')

    def test_a_venue_line_of_a_security_is_never_identity(self):
        self.assertIsNone(self.claim(level='venue', exch='UW', composite=AAPL_US))
        self.assertIsNone(self.claim(level='composite', exch='GR', composite=AAPL_US))
        self.assertIn('venue_level_rows', O.REFUSED)
        self.assertIn('non_us_composite', O.REFUSED)

    def test_a_ticker_answer_is_never_read_as_identity(self):
        self.assertIsNone(self.claim(id_type='TICKER', id_value='AAPL'))
        self.assertIn('bare_ticker', O.REFUSED)

    def test_a_cusip_that_fails_its_check_digit_is_not_read_back_either(self):
        self.assertIsNone(self.claim(id_value=APPLE_CALL))
        self.assertIsNone(self.claim(id_value='000000000'))

    def test_a_row_that_is_not_a_mapping_statement_reads_as_nothing(self):
        for value in (None, 'text', {}, {'query': {}}, {'query': 'x', 'published': {}}):
            self.assertIsNone(O.identity_claim(value), value)

    def test_the_bridge_reads_the_claim_off_the_published_record(self):
        record = answer('1', AAPL_US)
        self.assertEqual(bridges.record_claims(record),
                         [('figi:' + AAPL_US, 'cusip', APPLE, None, 'openfigi_cusip_figi')])
        self.assertEqual(bridges.record_claims(answer('2', AAPL_XNAS, level='venue', exch='UW',
                                                      composite=AAPL_US)), [])

    def test_the_bridge_declares_a_one_to_one_mapping_specification(self):
        self.assertEqual(bridges.cardinality('openfigi_cusip_figi'), '1:1')
        self.assertIn('openfigi_cusip_figi', MAPPING_SPECS)
        self.assertEqual(MAPPING_SPECS['openfigi_cusip_figi']['relation'], 'same_as')

    def test_the_prefilter_keeps_the_records_the_bridge_needs(self):
        self.assertIn(b'"predicate":"openfigi_mapping"', bridges.BRIDGE_TAGS)


class ListingRowTests(unittest.TestCase):
    """A listing is a relationship scoped to a MIC, never an identity and never a bare symbol."""

    @staticmethod
    def row(**kwargs):
        record = answer('x', AAPL_XNAS, id_type='TICKER', id_value='AAPL', level='venue',
                        composite=AAPL_US, **kwargs)
        return O.listing_row(record['subject'], record['value'])

    def test_a_mic_scoped_answer_is_a_listing(self):
        self.assertEqual(self.row(mic='XNAS'), {'left': AAPL_XNAS, 'right': 'AAPL', 'scope': 'XNAS'})

    def test_an_answer_with_no_mic_is_not_a_listing(self):
        self.assertIsNone(self.row())
        self.assertIn('exchange_code_as_mic', O.REFUSED)

    def test_the_published_ticker_is_used_not_the_one_asked_for(self):
        record = answer('x', AAPL_XNAS, id_type='TICKER', id_value='BRK-B', level='venue',
                        composite=AAPL_US, mic='XNYS', ticker='BRK/B')
        self.assertEqual(O.listing_row(record['subject'], record['value'])['right'], 'BRK/B')

    def test_listings_meet_the_issuer_listing_edges_sec_publishes(self):
        rows = [{'left': AAPL_XNAS, 'right': 'AAPL', 'scope': 'XNAS'}]
        result = link_mapping('figi_ticker', rows, observed_at='2026-09-19', evidence=EVIDENCE)
        self.assertEqual(len(result['assertions']), 1)
        assertion = result['assertions'][0]
        self.assertEqual((assertion['subject'], assertion['predicate'], assertion['object']),
                         ('figi:' + AAPL_XNAS, 'listed_as', 'ticker:XNAS:AAPL'))
        # sec_issuer_reference publishes the same object for the issuer, which is what composes.
        self.assertEqual(listing('1', '0000320193', 'XNAS', 'AAPL')['object'], assertion['object'])

    def test_an_undated_listing_says_so(self):
        result = link_mapping('figi_ticker', [{'left': AAPL_XNAS, 'right': 'AAPL', 'scope': 'XNAS'}],
                              observed_at='2026-09-19', evidence=EVIDENCE)
        self.assertEqual(result['assertions'][0]['attributes']['temporal_validity'], 'unknown')

    def test_two_figis_at_one_ticker_and_mic_are_refused(self):
        rows = [{'left': AAPL_XNAS, 'right': 'AAPL', 'scope': 'XNAS'},
                {'left': 'BBG000OTHER1', 'right': 'AAPL', 'scope': 'XNAS'}]
        result = link_mapping('figi_ticker', rows, observed_at='2026-09-19', evidence=EVIDENCE)
        self.assertEqual(result['assertions'], [])
        self.assertEqual(len(result['conflicts']), 1)

    def test_one_figi_with_two_symbols_at_one_mic_is_refused(self):
        rows = [{'left': AAPL_XNAS, 'right': 'AAPL', 'scope': 'XNAS'},
                {'left': AAPL_XNAS, 'right': 'AAPL2', 'scope': 'XNAS'}]
        self.assertEqual(link_mapping('figi_ticker', rows, observed_at='2026-09-19',
                                      evidence=EVIDENCE)['assertions'], [])

    def test_the_same_symbol_at_two_mics_stays_two_listings(self):
        rows = [{'left': AAPL_XNAS, 'right': 'AAPL', 'scope': 'XNAS'},
                {'left': 'BBG000B9XVV8', 'right': 'AAPL', 'scope': 'XNYS'}]
        result = link_mapping('figi_ticker', rows, observed_at='2026-09-19', evidence=EVIDENCE)
        self.assertEqual(len(result['assertions']), 2)
        self.assertEqual(result['conflicts'], [])

    def test_a_listing_row_without_a_scope_is_refused_outright(self):
        with self.assertRaises(ValueError):
            link_mapping('figi_ticker', [{'left': AAPL_XNAS, 'right': 'AAPL'}],
                         observed_at='2026-09-19', evidence=EVIDENCE)


class BridgeResolutionTests(unittest.TestCase):
    """The bridge end to end, over a published fixture catalog."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.fixture = Fixture(root)
        self.index = root / 'index.sqlite'
        publish = self.fixture.publish
        publish('openfigi_mappings', [
            # the security a CUSIP names, and one exchange's line of it
            entity('figi:' + AAPL_US, 'APPLE INC', 'security'),
            answer('1', AAPL_US, share_class='BBG001S5N8V8'),
            entity('figi:' + AAPL_XNAS, 'APPLE INC', 'security'),
            answer('2', AAPL_XNAS, id_type='TICKER', id_value='AAPL', level='venue', exch='UW',
                   mic='XNAS', composite=AAPL_US, share_class='BBG001S5N8V8'),
            # a second issuer, reached the same way
            entity('figi:' + MSFT_US, 'MICROSOFT CORP', 'security'),
            answer('3', MSFT_US, id_value=MSFT, ticker='MSFT', name='MICROSOFT CORP'),
            # a CUSIP two composites answer: the 1:1 specification refuses both
            entity('figi:BBG000DOUBLE1', 'DOUBLE ONE', 'security'),
            answer('4', 'BBG000DOUBLE1', id_value='458140100', ticker='DBL1', name='DOUBLE ONE'),
            entity('figi:BBG000DOUBLE2', 'DOUBLE TWO', 'security'),
            answer('5', 'BBG000DOUBLE2', id_value='458140100', ticker='DBL2', name='DOUBLE TWO'),
            # one composite answering two CUSIPs: refused the other way round
            entity('figi:BBG000TWOCUS1', 'TWO CUSIPS', 'security'),
            answer('6', 'BBG000TWOCUS1', id_value='172967101', ticker='TWO', name='TWO CUSIPS'),
            answer('7', 'BBG000TWOCUS1', id_value='254687106', ticker='TWO', name='TWO CUSIPS'),
            # a venue line whose CUSIP answer is never read
            entity('figi:BBG000VENUE01', 'VENUE ONLY', 'security'),
            answer('8', 'BBG000VENUE01', id_value='742718109', level='venue', exch='GR',
                   composite='BBG000VENUE99', ticker='VEN', name='VENUE ONLY')])
        publish('sec_13f_history', [
            entity('cusip:' + APPLE, 'APPLE INC COM', 'security'),
            entity('cusip:' + MSFT, 'MICROSOFT CORP COM', 'security'),
            entity('cusip:458140100', 'INTEL CORP COM', 'security'),
            entity('cusip:172967101', 'CITIGROUP INC COM', 'security'),
            entity('cusip:254687106', 'DISNEY WALT CO COM', 'security'),
            entity('cusip:742718109', 'PROCTER & GAMBLE CO COM', 'security'),
            entity('cusip:' + APPLE_CALL, 'APPLE INC CALL', 'security')])
        publish('sec_issuer_reference', [
            entity('sec:cik:0000320193', 'Apple Inc.', 'business'),
            entity('ticker:XNAS:AAPL', 'AAPL', 'ticker_listing'),
            listing('1', '0000320193', 'XNAS', 'AAPL')])
        self.datasets = ['openfigi_mappings', 'sec_13f_history', 'sec_issuer_reference']

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, **kwargs):
        U.unify(self.fixture.catalog, self.fixture.store, index=self.index, datasets=self.datasets,
                publish=False, progress=None)
        report = U.resolve_identities(self.fixture.catalog, self.fixture.store, index=self.index,
                                      workdir=Path(self.tmp.name) / 'resolve', datasets=self.datasets,
                                      progress=None, **kwargs)
        graph = Graph(self.index)
        return report, lambda entity_id: graph.resolved_entity(entity_id)['members']

    def test_a_13f_cusip_reaches_the_security_openfigi_published(self):
        report, members = self.resolve()
        self.assertEqual(members('cusip:' + APPLE), ['cusip:' + APPLE, 'figi:' + AAPL_US])
        self.assertEqual(members('cusip:' + MSFT), ['cusip:' + MSFT, 'figi:' + MSFT_US])
        self.assertGreaterEqual(report['counts']['bridge_claims_openfigi_cusip_figi'], 2)

    def test_the_bridge_is_the_only_thing_joining_them(self):
        report, members = self.resolve(bridges=False)
        self.assertEqual(members('cusip:' + APPLE), ['cusip:' + APPLE])
        self.assertNotIn('openfigi_cusip_figi', report['bridges'])

    def test_a_venue_line_never_takes_the_cusip_with_it(self):
        _, members = self.resolve()
        self.assertEqual(members('figi:' + AAPL_XNAS), ['figi:' + AAPL_XNAS])
        self.assertEqual(members('cusip:742718109'), ['cusip:742718109'])

    def test_one_cusip_answered_by_two_securities_is_refused_not_merged(self):
        report, members = self.resolve()
        self.assertEqual(members('cusip:458140100'), ['cusip:458140100'])
        self.assertTrue([row for row in report['bridge_conflicts']
                         if row['bridge'] == 'openfigi_cusip_figi' and row.get('value') == '458140100'],
                        report['bridge_conflicts'])

    def test_one_security_answering_two_cusips_is_refused(self):
        report, members = self.resolve()
        self.assertEqual(members('cusip:172967101'), ['cusip:172967101'])
        self.assertEqual(members('cusip:254687106'), ['cusip:254687106'])
        self.assertTrue([row for row in report['bridge_conflicts']
                         if row['bridge'] == 'openfigi_cusip_figi'
                         and row.get('subject') == 'figi:BBG000TWOCUS1'], report['bridge_conflicts'])

    def test_an_option_pseudo_cusip_reaches_nothing(self):
        _, members = self.resolve()
        self.assertEqual(members('cusip:' + APPLE_CALL), ['cusip:' + APPLE_CALL])

    def test_a_figi_never_becomes_its_issuer(self):
        _, members = self.resolve()
        self.assertNotIn('sec:cik:0000320193', members('cusip:' + APPLE))
        self.assertNotIn('figi:' + AAPL_US, members('sec:cik:0000320193'))
        self.assertIn('issuer_identity', O.REFUSED)


class PipelineTests(unittest.TestCase):
    """The dataset pipeline's own guards: the FIGI level, and a positional answer that slipped."""

    @staticmethod
    def _pipeline():
        """pipeline.py loaded the way the runner's snapshot loads it."""
        import importlib.util
        root = Path(__file__).resolve().parent.parent / 'data' / 'openfigi_mappings'
        spec = importlib.util.spec_from_file_location('openfigi_mappings_pipeline', root / 'pipeline.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_the_level_of_a_row_is_read_off_the_published_fields(self):
        level = self._pipeline().level
        self.assertEqual(level({'figi': AAPL_US, 'compositeFIGI': AAPL_US, 'exchCode': 'US'}), 'us_composite')
        self.assertEqual(level({'figi': AAPL_XNAS, 'compositeFIGI': AAPL_US, 'exchCode': 'UW'}), 'venue')
        self.assertEqual(level({'figi': 'BBG000GERMAN1', 'compositeFIGI': 'BBG000GERMAN1',
                                'exchCode': 'GR'}), 'composite')
        self.assertEqual(level({'figi': 'BBG0000C9F72', 'compositeFIGI': None,
                                'exchCode': 'NOT LISTED'}), 'unlisted')
        self.assertEqual(level({'figi': 'BBG004LV6Q05', 'compositeFIGI': None, 'exchCode': None}), 'unlisted')

    def test_the_query_carries_the_scope_the_request_asked_for(self):
        query_of = self._pipeline().query_of
        self.assertEqual(query_of({'idType': 'ID_CUSIP', 'idValue': APPLE}),
                         {'id_type': 'ID_CUSIP', 'id_value': APPLE})
        self.assertEqual(query_of({'idType': 'TICKER', 'idValue': 'AAPL', 'micCode': 'XNAS'}),
                         {'id_type': 'TICKER', 'id_value': 'AAPL', 'mic_code': 'XNAS'})
        self.assertEqual(query_of({'idType': 'TICKER', 'idValue': 'AAPL', 'exchCode': 'US'})['exch_code'], 'US')

    def test_a_request_level_error_is_a_counted_failure_not_a_misalignment(self):
        # OpenFIGI answers a request it did not process with HTTP 200 and a one-element error
        # body. Six of the 11,246 requests came back this way, and all six answered correctly
        # when re-sent, so it is transient - but the runner cannot retry a 200.
        pipeline = self._pipeline()
        request = {'group': 'cusip', 'batch': 0, 'jobs': [{'idType': 'ID_CUSIP', 'idValue': APPLE}] * 10}
        error = {'error': 'There was an error while processing this request.'}
        self.assertTrue(pipeline.request_level_failure(request, 0, error))
        self.assertEqual(pipeline._check_complete({0: request}, {0: 1}, {0}),
                         {'failed_requests': [0], 'identifiers_not_mapped': 10})
        # not a request-level failure: a per-job warning, a later position, a single-job request
        self.assertFalse(pipeline.request_level_failure(request, 3, error))
        self.assertFalse(pipeline.request_level_failure(request, 0, {'warning': 'No identifier found.'}))
        self.assertFalse(pipeline.request_level_failure({**request, 'jobs': request['jobs'][:1]}, 0, error))
        # and a shard that carries an error *and* other results is still a misalignment
        with self.assertRaises(ValueError):
            pipeline._check_complete({0: request}, {0: 4}, {0})

    def test_an_answer_that_is_not_one_result_per_job_fails_the_build(self):
        # The endpoint does not echo the identifier it answered, so a short or long answer
        # re-aligns every following result with the wrong CUSIP.
        check = self._pipeline()._check_complete
        requests = {0: {'group': 'cusip', 'batch': 0, 'jobs': [{'idType': 'ID_CUSIP', 'idValue': APPLE}] * 10}}
        check(requests, {0: 10})
        for got in (9, 11, 0):
            with self.assertRaises(ValueError):
                check(requests, {0: got})


class DeclarationTests(unittest.TestCase):
    """The declaration says what will be sent, and nothing it sends breaks a published limit."""

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parent.parent / 'data' / 'openfigi_mappings' / 'dataset.json'
        cls.definition = json.loads(path.read_text())

    def test_every_request_stays_within_the_published_unkeyed_limits(self):
        acquisition = self.definition['acquisition']
        combinations = acquisition['combinations']
        self.assertTrue(combinations)
        # 25 requests a minute, 10 mapping jobs a request, measured against the service on
        # 2026-09-19: an eleventh job is answered with HTTP 413.
        self.assertLessEqual(acquisition['rate_limit']['requests_per_second'], 25 / 60)
        for combination in combinations:
            self.assertLessEqual(len(combination['jobs']), 10, combination['batch'])
            self.assertIn(combination['group'], ('cusip', 'ticker_mic', 'ticker_us'))

    def test_every_cusip_it_asks_about_passes_the_check_digit(self):
        for combination in self.definition['acquisition']['combinations']:
            if combination['group'] != 'cusip':
                continue
            for job in combination['jobs']:
                self.assertEqual(job['idType'], 'ID_CUSIP')
                self.assertIsNotNone(O.cusip(job['idValue']) or job['idValue'] == '000000000',
                                     job['idValue'])

    def test_a_mic_is_only_ever_an_iso_10383_code(self):
        for combination in self.definition['acquisition']['combinations']:
            for job in combination['jobs']:
                if 'micCode' in job:
                    self.assertIn(job['micCode'], ('XNAS', 'XNYS'), job)

    def test_the_declaration_counts_match_the_request_list(self):
        parameters = self.definition['parameters']
        counts = {}
        for combination in self.definition['acquisition']['combinations']:
            counts[combination['group']] = counts.get(combination['group'], 0) + 1
        self.assertEqual(counts, parameters['requests'])

    def test_the_terms_are_recorded_and_permit_redistribution(self):
        source = self.definition['source']
        self.assertTrue(source['redistribution'])
        self.assertEqual(source['terms_url'], 'https://www.openfigi.com/docs/terms-of-service')
        self.assertEqual(source['license_status'], 'public_domain_dedication')
        self.assertIn('public domain', source['license_notes'])


if __name__ == '__main__':
    unittest.main()
