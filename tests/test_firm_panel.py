"""Firm panel builder on fixtures shaped like the published normalized SEC, GLEIF and 13F records."""
import itertools
import json
import tempfile
import unittest

from worldmodel.panels import firm


def line(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':'))


class FixtureSource:
    def __init__(self, records):
        self.records = records

    def has(self, dataset):
        return dataset in self.records

    def ref(self, dataset):
        return {'dataset': dataset, 'stage': 'normalized', 'version': f'v-{dataset}'}

    def lines(self, dataset):
        for record in self.records[dataset]:
            yield line(record) + '\n'


def gleif_entity(lei, authority, entity_id, label='ACME CORP'):
    return {'kind': 'entity', 'id': f'gleif_lei:{lei}', 'entity_id': f'lei:{lei}', 'entity_type': 'business',
            'label': label, 'observed_at': '2026-09-15T18:43:45+00:00',
            'attributes': {'registration_authority': authority, 'registration_authority_entity_id': entity_id,
                           'entity_status': 'ACTIVE', 'registration_status': 'ISSUED', 'initial_registration': '2012-06-06'},
            'evidence': [{'input': {'dataset': 'sec_gleif', 'artifact': 'a'},
                          'locator': 'shard:0/member:20260915-1600-gleif-goldencopy-lei2-golden-copy.csv/line:2'}]}


def issuer_security(lei, isin):
    return {'kind': 'assertion', 'id': f'gleif_isin:{isin}:{lei}', 'subject': f'lei:{lei}', 'predicate': 'issuer_security',
            'object': f'isin:{isin}', 'observed_at': '2026-09-15T18:43:45+00:00', 'attributes': {},
            'evidence': [{'input': {'dataset': 'sec_gleif', 'artifact': 'a'},
                          'locator': 'shard:1/member:lei-isin-20260915T071509.csv/line:2'}]}


# US0378331005 is Apple's ISIN: CUSIP 037833100. US5949181045 is Microsoft's: CUSIP 594918104.
ACME_LEI, ACME_CIK, ACME_ISIN, ACME_CUSIP = 'HWUPKR0MPOU8FGXBT394', '320193', 'US0378331005', '037833100'
OTHER_ISIN, OTHER_CUSIP = 'US5949181045', '594918104'


def gleif_records():
    return [
        gleif_entity(ACME_LEI, 'RA000665', ACME_CIK),
        gleif_entity('FUNDSERIES0000000001', 'RA000665', 'S000005113', label='A FUND SERIES'),   # series id, not a CIK
        gleif_entity('STATEREG000000000001', 'RA000063', '320193', label='SAME DIGITS, STATE REGISTER'),  # not SEC
        gleif_entity('DUPA0000000000000001', 'RA000665', '777'),
        gleif_entity('DUPB0000000000000002', 'RA000665', '777'),   # two LEIs print one CIK -> refused
        issuer_security(ACME_LEI, ACME_ISIN),
        issuer_security(ACME_LEI, 'GB0002634946'),                   # not a CUSIP-bearing ISIN
        issuer_security('STATEREG000000000001', OTHER_ISIN),         # LEI without an SEC link
    ]


def sec_lei(cik, lei):
    return {'kind': 'assertion', 'id': f'secsub:{cik}:lei', 'subject': f'sec:cik:{cik.zfill(10)}',
            'predicate': 'identifier_assignment', 'value': {'namespace': 'lei', 'value': lei},
            'observed_at': '2026-09-15T20:11:01+00:00', 'attributes': {}}


def portfolio(accession, manager, period, filed, form='13F-HR', amendment=None, prefix='sec13fh'):
    return {'kind': 'observation', 'id': f'{prefix}:{accession}:portfolio_value', 'metric': 'reported_13f_portfolio_value',
            'subject': f'sec:cik:{manager}', 'valid_from': period, 'observed_at': filed, 'value': 1, 'unit': 'USD',
            'dimensions': {'accession': accession, 'form': form},
            'attributes': {'amendment_type': amendment, 'report_type': '13F HOLDINGS REPORT'}}


def holding(accession, manager, cusip, shares, value, period, filed, put_call='-', klass='SH', prefix='sec13fh'):
    return {'kind': 'assertion', 'id': f'{prefix}:{accession}:{cusip}:{put_call}:{klass}', 'predicate': 'reported_holding',
            'subject': f'sec:cik:{manager}', 'object': f'cusip:{cusip}', 'valid_from': period, 'observed_at': filed,
            'attributes': {'accession': accession, 'shares': shares, 'value_usd': value}}


def thirteen_f():
    return [
        portfolio('A1', '0000000001', '2020-03-31', '2020-05-10'),
        holding('A1', '0000000001', ACME_CUSIP, 100, 1000, '2020-03-31', '2020-05-10'),
        holding('A1', '0000000001', ACME_CUSIP, 5, 50, '2020-03-31', '2020-05-10', put_call='Call'),
        holding('A1', '0000000001', OTHER_CUSIP, 7, 70, '2020-03-31', '2020-05-10'),
        # Manager 2 files, then restates: only the restatement counts.
        portfolio('B1', '0000000002', '2020-03-31', '2020-05-12'),
        holding('B1', '0000000002', ACME_CUSIP, 999, 9990, '2020-03-31', '2020-05-12'),
        portfolio('B2', '0000000002', '2020-03-31', '2020-06-30', form='13F-HR/A', amendment='RESTATEMENT'),
        holding('B2', '0000000002', ACME_CUSIP, 200, 2000, '2020-03-31', '2020-06-30'),
        # Manager 3 omits the position, then adds it by a NEW HOLDINGS amendment after the deadline.
        portfolio('C1', '0000000003', '2020-03-31', '2020-05-14'),
        holding('C1', '0000000003', OTHER_CUSIP, 1, 10, '2020-03-31', '2020-05-14'),
        portfolio('C2', '0000000003', '2020-03-31', '2020-07-01', form='13F-HR/A', amendment='NEW HOLDINGS'),
        holding('C2', '0000000003', ACME_CUSIP, 300, 3000, '2020-03-31', '2020-07-01'),
        holding('C2', '0000000003', ACME_CUSIP, 1, 10, '2020-03-31', '2020-07-01', klass='PRN'),
    ]


LINES = itertools.count(2)


def fsds(accession, metric, value, end, qtrs, filed, form='10-Q', fy=2020, fp='Q1', tag='Revenues', **dims):
    return {'kind': 'observation', 'id': f'fsds:{accession}:{tag}:{end}:{qtrs}:USD:L{next(LINES)}',
            'subject': f'sec:cik:{ACME_CIK.zfill(10)}', 'metric': metric, 'value': value, 'unit': 'USD',
            'observed_at': filed, 'valid_from': end,
            'dimensions': {'concept': f'us-gaap:{tag}', 'form': form, 'qtrs': qtrs, 'fiscal_year': fy, 'fiscal_period': fp,
                           'period_type': 'duration' if qtrs else 'instant', **dims},
            'attributes': {'accession': accession, 'period_end': end}}


def fsds_records():
    issuer = {'kind': 'entity', 'id': 'fsds:shard:0:issuer:sec:cik:0000320193', 'entity_id': 'sec:cik:0000320193',
              'entity_type': 'business', 'label': 'ACME CORP', 'observed_at': '2020-05-01', 'attributes': {'sic': '3571'}}
    q1 = 'q1-2020'
    k2020 = 'k-2020'
    return [
        issuer,
        fsds('k-2019', 'total_assets', 450, '2019-12-31', 0, '2020-02-01', form='10-K', fy=2019, fp='FY', tag='Assets'),
        # 2020 Q1 10-Q: balance sheet at 2020-03-31, quarter revenue, prior year-end comparative.
        fsds(q1, 'total_assets', 500, '2020-03-31', 0, '2020-05-01', tag='Assets'),
        fsds(q1, 'total_assets', 450, '2019-12-31', 0, '2020-05-01', tag='Assets'),
        fsds(q1, 'revenue', 90, '2020-03-31', 1, '2020-05-01', tag='Revenues'),
        fsds(q1, 'revenue', 80, '2020-03-31', 1, '2020-05-01', tag='RevenueFromContractWithCustomerExcludingAssessedTax'),
        fsds(q1, 'revenue', 30, '2020-03-31', 1, '2020-05-01', tag='Revenues', segments='ProductOrServiceAxis=X'),
        fsds(q1, 'shares_outstanding_cover', 1000, '2020-04-20', 0, '2020-05-01', tag='EntityCommonStockSharesOutstanding'),
        # 2020 10-K restates the 2019 year-end assets and repeats Q1 revenue unchanged.
        fsds(k2020, 'total_assets', 600, '2020-12-31', 0, '2021-02-01', form='10-K', fp='FY', tag='Assets'),
        fsds(k2020, 'total_assets', 460, '2019-12-31', 0, '2021-02-01', form='10-K', fp='FY', tag='Assets'),
        fsds(k2020, 'revenue', 400, '2020-12-31', 4, '2021-02-01', form='10-K', fp='FY'),
        fsds(k2020, 'revenue', 90, '2020-03-31', 1, '2021-02-01', form='10-K', fp='FY'),
        # A value at an instant that is nobody's fiscal period is dropped.
        fsds(k2020, 'total_liabilities', 5, '2020-07-15', 0, '2021-02-01', form='10-K', fp='FY', tag='Liabilities'),
    ]


def companyfacts_records():
    return [
        {'kind': 'observation', 'id': 'secfacts:0000320193:us-gaap:Assets:USD::2011-12-31:old', 'subject': 'sec:cik:0000320193',
         'metric': 'total_assets', 'value': 300, 'unit': 'USD', 'observed_at': '2012-02-01', 'valid_from': '2011-12-31',
         'dimensions': {'concept': 'us-gaap:Assets', 'form': '10-K', 'fiscal_year': 2011, 'fiscal_period': 'FY',
                        'period_type': 'instant'},
         'attributes': {'accession': 'old', 'period_end': '2011-12-31', 'revision': 'first_report'}},
        # Filed after the FSDS cutoff: FSDS owns it, so companyfacts is ignored.
        {'kind': 'observation', 'id': 'secfacts:0000320193:us-gaap:Assets:USD::2020-03-31:q1', 'subject': 'sec:cik:0000320193',
         'metric': 'total_assets', 'value': 500, 'unit': 'USD', 'observed_at': '2020-05-01', 'valid_from': '2020-03-31',
         'dimensions': {'concept': 'us-gaap:Assets', 'form': '10-Q', 'fiscal_year': 2020, 'fiscal_period': 'Q1',
                        'period_type': 'instant'},
         'attributes': {'accession': 'q1-2020', 'period_end': '2020-03-31', 'revision': 'first_report'}},
    ]


def source():
    return FixtureSource({
        'sec_gleif': gleif_records(),
        'sec_issuer_reference': [sec_lei(ACME_CIK, ACME_LEI), sec_lei('999', 'SECONLY000000000000X')],
        'sec_13f_history': thirteen_f(),
        'sec_financial_statements': fsds_records(),
        'sec_company_assets': companyfacts_records(),
    })


class LinksTest(unittest.TestCase):
    def setUp(self):
        self.rows = list(firm.build_links(source()))
        self.by_id = {row['id']: row for row in self.rows}

    def test_cik_lei_and_cusip_from_published_identifiers_only(self):
        acme = self.by_id['firm_panel:link:0000320193']
        self.assertEqual(acme['lei']['lei'], ACME_LEI)
        self.assertEqual(acme['lei']['bases'], ['gleif_registration_authority_RA000665', 'sec_submissions_lei'])
        self.assertEqual(acme['cusips'], [[ACME_CUSIP, ACME_ISIN]])
        self.assertEqual(acme['available_at'], '2026-09-15')
        self.assertIn('firm_panel:link:0000000999', self.by_id)   # the SEC-printed LEI alone links

    def test_series_ids_state_registers_and_duplicates_are_not_links(self):
        construction = self.rows[-1]
        self.assertEqual(construction['construction']['gleif_ra000665_entities'], 4)
        self.assertEqual(construction['construction']['gleif_ra000665_cik_shaped'], 3)
        self.assertNotIn('firm_panel:link:0000000777', self.by_id)
        self.assertEqual(construction['cik_conflicts'][0]['cik'], '0000000777')
        linked_cusips = {c for row in self.rows if row.get('cik') for c, _ in row['cusips']}
        self.assertNotIn(OTHER_CUSIP, linked_cusips)
        self.assertEqual(construction['construction']['gleif_issuer_security_non_cusip_isin'], 1)


class OwnershipTest(unittest.TestCase):
    def test_restatements_replace_new_holdings_add_and_options_are_excluded(self):
        links = [row for row in firm.build_links(source()) if row.get('cik')]
        rows = list(firm.build_ownership(source(), links))
        acme = next(row for row in rows if row.get('cik') == '0000320193')
        self.assertEqual(acme['quarter_end'], '2020-03-31')
        self.assertEqual(acme['managers'], 3)
        self.assertEqual(acme['shares'], 100 + 200 + 300)
        self.assertEqual(acme['value_usd'], 1000 + 2000 + 3000)
        self.assertEqual(acme['available_at'], '2020-07-01')
        self.assertEqual(acme['due_date'], '2020-05-15')
        self.assertEqual(acme['value_share_filed_by_due_date'], round(1000 / 6000, 4))
        construction = rows[-1]['construction']
        self.assertEqual(construction['holdings_superseded'], 1)
        self.assertEqual(construction['holdings_options'], 1)
        self.assertEqual(construction['holdings_other_class'], 1)
        self.assertEqual(construction['holdings_unlinked_cusip'], 2)
        self.assertEqual(construction['restatements_applied'], 1)

    def test_resolution_keeps_latest_base_and_later_new_holdings(self):
        kept, counts = firm.resolve_13f_filings({
            'a': ('m', '2020-03-31', '2020-05-01', '13F-HR', None),
            'b': ('m', '2020-03-31', '2020-05-20', '13F-HR/A', 'NEW HOLDINGS'),
            'c': ('m', '2020-03-31', '2020-06-01', '13F-HR/A', 'RESTATEMENT'),
            'd': ('m', '2020-03-31', '2020-06-15', '13F-HR/A', 'NEW HOLDINGS')})
        self.assertEqual(kept, {'c': 'base', 'd': 'new_holdings'})
        self.assertEqual(counts['superseded_filings'], 2)


class PanelTest(unittest.TestCase):
    def build(self):
        src = source()
        links = [row for row in firm.build_links(src) if row.get('cik')]
        ownership = [row for row in firm.build_ownership(src, links) if row.get('cik')]
        with tempfile.TemporaryDirectory() as work:
            rows = list(firm.build_panel(src, links, ownership, workdir=work,
                                         links_ref={'dataset': 'firm_panel', 'stage': 'links', 'version': 'l'},
                                         ownership_ref={'dataset': 'firm_panel', 'stage': 'ownership', 'version': 'o'}))
        return {row['id']: row for row in rows}, rows[-1]

    def test_restated_values_are_later_vintages(self):
        rows, _ = self.build()
        prior = rows['firm_panel:0000320193:2019-12-31']
        vintages = prior['instants']['total_assets']
        self.assertEqual([(v['value'], v['available_at'], v['revision']) for v in vintages],
                         [(450, '2020-02-01', 'first_report'), (460, '2021-02-01', 'restated')])
        self.assertEqual(vintages[0]['confirmations'], 1)   # the 10-Q comparative repeats it unchanged
        self.assertEqual(firm.value_as_of(vintages, '2020-12-31')['value'], 450)
        self.assertEqual(firm.value_as_of(vintages, '2021-02-01')['value'], 460)
        self.assertIsNone(firm.value_as_of(vintages, '2020-01-31'))
        self.assertEqual((prior['fiscal_year'], prior['fiscal_period'], prior['form']), (2019, 'FY', '10-K'))

    def test_one_tag_per_filing_no_segments_and_confirmations(self):
        rows, _ = self.build()
        q1 = rows['firm_panel:0000320193:2020-03-31']
        revenue = q1['durations']['revenue']['1']
        self.assertEqual(len(revenue), 1)
        self.assertEqual(revenue[0]['value'], 90)
        self.assertEqual(revenue[0]['concept'], 'us-gaap:Revenues')
        self.assertEqual(revenue[0]['confirmations'], 1)
        self.assertEqual((q1['fiscal_year'], q1['fiscal_period'], q1['form'], q1['first_filed']), (2020, 'Q1', '10-Q', '2020-05-01'))
        cover = q1['instants']['shares_outstanding_cover'][0]
        self.assertEqual((cover['value'], cover['as_of']), (1000, '2020-04-20'))

    def test_identity_ownership_and_contracts_blocks(self):
        rows, construction = self.build()
        q1 = rows['firm_panel:0000320193:2020-03-31']
        self.assertEqual(q1['lei']['lei'], ACME_LEI)
        self.assertEqual(q1['institutional_ownership']['managers'], 3)
        self.assertIsNone(q1['federal_contracts'])
        self.assertEqual(q1['linked'], {'financials': True, 'lei': True, 'institutional_ownership': True,
                                        'federal_contracts': False})
        self.assertFalse(construction['federal_contracts']['linked'])
        annual = rows['firm_panel:0000320193:2020-12-31']
        self.assertIsNone(annual['institutional_ownership'])
        self.assertEqual(annual['durations']['revenue']['4'][0]['value'], 400)

    def test_companyfacts_only_before_the_fsds_cutoff_and_off_period_values_dropped(self):
        rows, construction = self.build()
        old = rows['firm_panel:0000320193:2011-12-31']
        self.assertEqual(old['instants']['total_assets'][0]['source'], 'sec_company_assets')
        self.assertEqual(construction['construction']['sec_company_assets_values_loaded'], 1)
        self.assertEqual(construction['construction']['values_not_at_a_fiscal_period'], 1)
        self.assertEqual(construction['construction']['fsds_dimensional_or_coregistrant_values_skipped'], 1)
        self.assertNotIn('firm_panel:0000320193:2020-07-15', rows)

    def test_coverage(self):
        rows, _ = self.build()
        report = firm.coverage(rows.values())
        self.assertEqual(report['issuers'], 1)
        self.assertEqual(report['rows'], 4)
        self.assertEqual(report['issuer_share']['lei'], 1.0)
        self.assertEqual(report['issuer_share']['federal_contracts'], 0.0)
        self.assertEqual(report['rows_with_a_restated_value'], 1)

    def test_quarter_end(self):
        self.assertEqual(firm.quarter_end('2023-09-24'), '2023-09-30')
        self.assertEqual(firm.quarter_end('2023-12-31'), '2023-12-31')
        self.assertEqual(firm.quarter_end('2024-01-01'), '2024-03-31')


if __name__ == '__main__':
    unittest.main()
