"""Offline full-acquisition pipeline tests for company, market and research datasets.

Each test publishes a tiny sharded raw artifact (same receipt shape as `wm acquire`) into a
temporary store and runs the real dataset-local pipeline through the Runner.
"""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[1]
RETRIEVED = '2026-09-15T18:00:00+00:00'


def zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def tsv(header, *rows):
    return '\t'.join(header) + '\n' + ''.join('\t'.join(r) + '\n' for r in rows)


def xlsx(cells):
    """Minimal workbook with inline strings: cells maps 'A1' -> text."""
    rows = {}
    for ref, value in cells.items():
        rows.setdefault(int(''.join(c for c in ref if c.isdigit())), []).append((ref, value))
    body = ''.join(f'<row r="{n}">' + ''.join(f'<c r="{r}" t="inlineStr"><is><t>{v}</t></is></c>' for r, v in sorted(cs)) + '</row>'
                   for n, cs in sorted(rows.items()))
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{body}</sheetData></worksheet>')
    return zip_bytes({'xl/worksheets/sheet1.xml': sheet})


class FullPipelineBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def run_full(self, dataset, shards, reader=None):
        """shards: list of (bytes, metadata dict)."""
        files = []
        for n, (content, meta) in enumerate(shards):
            path = self.root / f'shard{n}'
            path.write_bytes(content.encode() if isinstance(content, str) else content)
            files.append({'path': path, 'retrieved_at': RETRIEVED, **meta})
        source = {'publisher': 'fixture', 'acquisition': {'strategy': 'fixture', 'reader': reader}}
        ref = self.store.import_shards(dataset, files, source, complete=True, update_latest=False)
        out = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run(dataset, raw_refs={dataset: [ref]})
        return list(self.store.records(out))

    @staticmethod
    def by_id(records):
        return {r['id']: r for r in records}


class MarketReferenceTests(FullPipelineBase):
    def test_nasdaq_directories_emit_venue_scoped_listings(self):
        nasdaq = ('Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\r\n'
                  'AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\r\nZVZZT|Test|G|Y|N|100|N|N\r\n'
                  'File Creation Time: 0915202614:01|||||||\r\n')
        other = ('ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\r\n'
                 'BRK.A|Berkshire Hathaway Inc. Class A|N|BRK.A|N|1|N|BRK=A\r\nFile Creation Time: 0915202614:01||||||\r\n')
        rows = self.by_id(self.run_full('nasdaq_listings', [(nasdaq, {}), (other, {})], {'format': 'psv'}))
        self.assertEqual(rows['nasdaq_listings:XNAS:AAPL:entity']['entity_id'], 'ticker:XNAS:AAPL')
        self.assertEqual(rows['nasdaq_listings:XNYS:BRK.A:venue']['object'], 'mic:XNYS')
        self.assertTrue(rows['nasdaq_listings:XNAS:AAPL:entity']['attributes']['source_snapshot_time'].startswith('2026-09-15T14:01'))
        self.assertFalse(any('ZVZZT' in key for key in rows))
        self.assertFalse(any(r.get('entity_type') in ('security', 'business') for r in rows.values()))

    def test_mic_full_list(self):
        csv_text = ('"MIC","OPERATING MIC","OPRT/SGMT","MARKET NAME-INSTITUTION DESCRIPTION","LEGAL ENTITY NAME","LEI","MARKET CATEGORY CODE",'
                    '"ACRONYM","ISO COUNTRY CODE (ISO 3166)","CITY","WEBSITE","STATUS","CREATION DATE","LAST UPDATE DATE","LAST VALIDATION DATE","EXPIRY DATE","COMMENTS"\r\n'
                    '"XNAS","XNAS","OPRT","NASDAQ - ALL MARKETS","NASDAQ, INC.","549300L8X1Q78ERXFD06","NSPD","NASDAQ","US","NEW YORK","WWW.NASDAQ.COM","ACTIVE","20050601","20150613","20260901",,""\r\n'
                    '"XNGS","XNAS","SGMT","NASDAQ GLOBAL SELECT","","","NSPD","","US","NEW YORK","","ACTIVE","20060717","20060717","20260901",,""\r\n')
        records = self.run_full('iso_mic_venues', [(csv_text, {})], {'format': 'csv'})
        self.assertIn('mic:XNAS', {r.get('object') for r in records if r.get('predicate') == 'mic_operating_venue'})
        self.assertIn('lei:549300L8X1Q78ERXFD06', {r.get('object') for r in records})
        self.assertFalse(any('source_row' in r['attributes'] for r in records))

    def test_ndx_snapshot_membership_is_dated(self):
        payload = {'data': {'date': 'Sep 15, 2026 2:38 PM', 'data': {'rows': [
            {'symbol': 'AAPL', 'companyName': 'Apple Inc. Common Stock', 'marketCap': '4,819,873,886,800', 'lastSalePrice': '$330.29'}]}}}
        rows = self.by_id(self.run_full('nasdaq_index_reference', [(json.dumps(payload), {'request': {'url': 'https://api.nasdaq.com/x'}}),
                                                                   (b'%PDF-1.7 fixture', {'request': {'url': 'https://indexes.nasdaqomx.com/docs/Methodology_NDX.pdf'}})]))
        member = rows['nasdaq_index:2026-09-15:AAPL:member']
        self.assertEqual((member['valid_from'], member['valid_to'], member['object']), ('2026-09-15', '2026-09-16', 'index:nasdaq:NDX'))
        self.assertEqual(rows['nasdaq_index:2026-09-15:AAPL:market_capitalization']['value'], 4819873886800)
        self.assertEqual(rows['nasdaq_index:2026-09-15:AAPL:last_sale_price']['unit'], 'USD/share')

    def test_spdr_holdings_workbooks(self):
        cells = {'A1': 'Fund Name:', 'B1': 'Fixture S&amp;P 500 ETF', 'A2': 'Ticker Symbol:', 'B2': 'SPY', 'A3': 'Holdings:', 'B3': 'As of 14-Sep-2026'}
        for col, name in zip('ABCDEFGH', ['Name', 'Ticker', 'Identifier', 'SEDOL', 'Weight', 'Sector', 'Shares Held', 'Local Currency']):
            cells[f'{col}5'] = name
        for col, value in zip('ABCDEFGH', ['NVIDIA CORP', 'NVDA', '67066G104', '2379504', '7.7', '-', '2.95E8', 'USD']):
            cells[f'{col}6'] = value
        for col, value in zip('ABCDEFGH', ['S+P EMINI DEC26', 'ESZ6', 'ADI394XT0', '-', '-0.0003', '-', '64250.0', 'USD']):
            cells[f'{col}7'] = value
        reader = {'format': 'xlsx', 'header_row': 5, 'stop_when_blank': 'Ticker', 'context_cells': {'fund': 'B1', 'ticker': 'B2', 'date': 'B3'}}
        records = self.run_full('ssga_dia_holdings', [(xlsx(cells), {'request': {'params': {'fund': 'spy'}}})], reader)
        self.assertIn('cusip:67066G104', {r.get('entity_id') for r in records})
        weights = sorted(r['value'] for r in records if r.get('metric') == 'portfolio_weight')
        self.assertEqual(weights, [-0.0003, 7.7])
        self.assertIn('ssga:fund:SPY', {r.get('object') for r in records if r.get('predicate') == 'position_holder'})

    def test_dia_nav_workbook(self):
        cells = {'A1': 'Fund', 'B1': 'Fixture DIA', 'A2': 'Ticker', 'B2': 'DIA', 'A4': 'Date', 'B4': 'NAV', 'C4': 'Shares Outstanding',
                 'D4': 'Total Net Assets', 'A5': '14-Sep-2026', 'B5': '455.12', 'C5': '100', 'D5': '-'}
        reader = {'format': 'xlsx', 'header_row': 4, 'stop_when_blank': 'Date', 'context_cells': {'fund': 'B1', 'ticker': 'B2'}}
        rows = self.by_id(self.run_full('ssga_dia_nav', [(xlsx(cells), {})], reader))
        self.assertEqual(rows['ssga_nav:0:2026-09-14:fund_nav']['value'], 455.12)
        self.assertEqual(rows['ssga_nav:0:2026-09-14:fund_net_assets']['missing_reason'], 'source_blank')


class PriceFeedTests(FullPipelineBase):
    def test_massive_grouped_daily_bars(self):
        payload = {'status': 'OK', 'adjusted': True, 'resultsCount': 1,
                   'results': [{'T': 'AAPL', 'o': 1.0, 'h': 2.0, 'l': 0.5, 'c': 1.5, 'v': 1000, 'vw': 1.4, 'n': 10, 't': 1}]}
        holiday = {'status': 'OK', 'adjusted': True, 'resultsCount': 0, 'queryCount': 0}
        rows = self.by_id(self.run_full('market_prices', [(json.dumps(payload), {'request': {'params': {'date': '2026-09-14'}}}),
                                                          (json.dumps(holiday), {'request': {'params': {'date': '2025-12-25'}}})],
                                        {'format': 'json', 'records_path': ['results']}))
        close = rows['massive:2026-09-14:AAPL:close']
        self.assertEqual((close['subject'], close['metric'], close['value'], close['unit']), ('ticker:US:AAPL', 'close_price_split_adjusted', 1.5, 'USD/share'))
        self.assertEqual((close['valid_from'], close['valid_to']), ('2026-09-14', '2026-09-15'))
        self.assertEqual(rows['massive:2026-09-14:AAPL:volume']['value'], 1000)

    def test_massive_reference_rows(self):
        lines = [{'execution_date': '2024-06-10', 'id': 'S1', 'split_from': 1, 'split_to': 10, 'ticker': 'NVDA'},
                 {'cash_amount': 0.25, 'currency': 'USD', 'dividend_type': 'CD', 'ex_dividend_date': '2026-08-11', 'id': 'D1',
                  'pay_date': '2026-08-14', 'record_date': '2026-08-11', 'ticker': 'AAPL', 'frequency': 4},
                 {'ticker': 'AAPL', 'name': 'Apple Inc.', 'market': 'stocks', 'locale': 'us', 'primary_exchange': 'XNAS', 'type': 'CS',
                  'active': True, 'cik': '0000320193', 'composite_figi': 'BBG000B9XRY4', 'share_class_figi': 'BBG001S5N8V8'},
                 {'ticker': 'LEH', 'name': 'Lehman', 'market': 'stocks', 'locale': 'us', 'primary_exchange': 'XNYS', 'active': False,
                  'delisted_utc': '2008-09-17T04:00:00Z'}]
        records = self.run_full('market_corporate_actions', [('\n'.join(json.dumps(l) for l in lines) + '\n', {})], {'format': 'jsonl'})
        split = next(r for r in records if r.get('event_type') == 'stock_split')
        self.assertEqual((split['occurred_at'], split['attributes']['split_to']), ('2024-06-10', 10))
        dividend = next(r for r in records if r.get('metric') == 'cash_dividend_per_share')
        self.assertEqual((dividend['value'], dividend['unit'], dividend['valid_from']), (0.25, 'USD/share', '2026-08-11'))
        issuer = next(r for r in records if r.get('predicate') == 'issuer_listing')
        self.assertEqual((issuer['subject'], issuer['object']), ('sec:cik:0000320193', 'ticker:XNAS:AAPL'))
        self.assertIn('figi:BBG000B9XRY4', {r.get('object') for r in records})
        delisted = next(r for r in records if r.get('subject') == 'ticker:XNYS:LEH' and r.get('predicate') == 'identifier_assignment')
        self.assertEqual(delisted['valid_to'], '2008-09-17T04:00:00Z')
        self.assertFalse(any(r.get('predicate') == 'primary_listing' and 'LEH' in r['object'] for r in records))

    def test_alpaca_pages(self):
        page = {'bars': {'AAPL': [{'t': '2016-01-04T05:00:00Z', 'o': 1, 'h': 2, 'l': 0.5, 'c': 1.25, 'v': 10, 'n': 3, 'vw': 1.2}]}, 'next_page_token': None}
        rows = self.by_id(self.run_full('alpaca_daily_bars', [(json.dumps(page) + '\n', {})], {'format': 'jsonl'}))
        close = rows['alpaca:AAPL:2016-01-04:close']
        self.assertEqual((close['metric'], close['value'], close['dimensions']['adjustment']),
                         ('close_price_total_return_adjusted', 1.25, 'split_and_dividend_adjusted'))


class CompanyFilingsTests(FullPipelineBase):
    def test_companyfacts_point_in_time_dedupe(self):
        facts = {'cik': 320193, 'entityName': 'Apple Inc.', 'facts': {'us-gaap': {
            'Assets': {'units': {'USD': [
                {'end': '2024-09-28', 'val': 100, 'accn': 'A-1', 'fy': 2024, 'fp': 'FY', 'form': '10-K', 'filed': '2024-11-01'},
                {'end': '2024-09-28', 'val': 100, 'accn': 'A-2', 'fy': 2025, 'fp': 'Q1', 'form': '10-Q', 'filed': '2025-01-31'},
                {'end': '2024-09-28', 'val': 90, 'accn': 'A-3', 'fy': 2025, 'fp': 'FY', 'form': '10-K/A', 'filed': '2025-03-01'},
                {'end': '2024-09-28', 'val': 1, 'accn': 'S-1', 'form': 'S-1', 'filed': '2024-01-01'}]}},
            'NetIncomeLoss': {'units': {'USD': [{'start': '2023-10-01', 'end': '2024-09-28', 'val': 5, 'accn': 'A-1', 'fy': 2024,
                                                 'fp': 'FY', 'form': '10-K', 'filed': '2024-11-01', 'frame': 'CY2024'}]}},
            'CommonStockSharesAuthorized': {'units': {'shares': [{'end': '2024-09-28', 'val': 5, 'accn': 'A-1', 'form': '10-K', 'filed': '2024-11-01'}]}}}}}
        records = self.run_full('sec_company_assets', [(zip_bytes({'CIK0000320193.json': json.dumps(facts)}), {})],
                                {'format': 'json', 'members': ['*.json']})
        assets = sorted((r for r in records if r.get('metric') == 'total_assets'), key=lambda r: r['observed_at'])
        self.assertEqual([(a['value'], a['observed_at'], a['attributes']['revision']) for a in assets],
                         [(100, '2024-11-01', 'first_report'), (90, '2025-03-01', 'restated')])
        income = next(r for r in records if r.get('metric') == 'net_income')
        self.assertEqual((income['valid_from'], income['valid_to'], income['dimensions']['period_type']), ('2023-10-01', '2024-09-29', 'duration'))
        self.assertFalse(any('CommonStockSharesAuthorized' in r['id'] for r in records))

    def test_submissions_issuer_reference(self):
        doc = {'cik': '320193', 'entityType': 'operating', 'sic': '3571', 'sicDescription': 'Electronic Computers', 'name': 'Apple Inc.',
               'tickers': ['AAPL'], 'exchanges': ['Nasdaq'], 'ein': '942404110', 'lei': None, 'stateOfIncorporation': 'CA', 'fiscalYearEnd': '0926',
               'formerNames': [{'name': 'APPLE COMPUTER INC', 'from': '1994-01-26T00:00:00.000Z', 'to': '2007-01-10T00:00:00.000Z'}],
               'filings': {'recent': {'accessionNumber': ['0000320193-26-000001'], 'filingDate': ['2026-08-01'], 'reportDate': ['2026-06-27'],
                                      'acceptanceDateTime': ['2026-08-01T16:30:00.000Z'], 'form': ['10-Q'], 'items': [''],
                                      'primaryDocument': ['aapl.htm'], 'isXBRL': [1], 'fileNumber': ['001-36743']}}}
        rows = self.by_id(self.run_full('sec_issuer_reference', [(json.dumps(doc), {})], {'format': 'json'}))
        self.assertEqual(rows['secsub:0000320193:listing:0']['object'], 'ticker:XNAS:AAPL')
        self.assertEqual(rows['secsub:0000320193:former_name:0']['valid_to'], '2007-01-10T00:00:00.000Z')
        self.assertEqual(rows['secsub:0000320193:name']['valid_from'], '2007-01-10T00:00:00.000Z')
        self.assertEqual(rows['secsub:0000320193:incorporated']['object'], 'geo:US:state:06')
        filing = rows['secsub:0000320193:filing:0000320193-26-000001']
        self.assertEqual((filing['event_type'], filing['attributes']['form']), ('sec_filing', '10-Q'))

    def test_ownership_datasets(self):
        form13f = zip_bytes({
            'SUBMISSION.tsv': tsv(['ACCESSION_NUMBER', 'FILING_DATE', 'SUBMISSIONTYPE', 'CIK', 'PERIODOFREPORT'],
                                  ['0001-26-1', '31-JUL-2026', '13F-HR', '0001067983', '30-JUN-2026'], ['0001-26-2', '01-AUG-2026', '13F-NT', '0000000007', '30-JUN-2026']),
            'COVERPAGE.tsv': tsv(['ACCESSION_NUMBER', 'FILINGMANAGER_NAME', 'AMENDMENTTYPE', 'REPORTTYPE'], ['0001-26-1', 'Fixture Capital', '', '13F HOLDINGS REPORT']),
            'SUMMARYPAGE.tsv': tsv(['ACCESSION_NUMBER', 'TABLEENTRYTOTAL', 'TABLEVALUETOTAL'], ['0001-26-1', '2', '3000']),
            'INFOTABLE.tsv': tsv(['ACCESSION_NUMBER', 'NAMEOFISSUER', 'TITLEOFCLASS', 'CUSIP', 'FIGI', 'VALUE', 'SSHPRNAMT', 'SSHPRNAMTTYPE', 'PUTCALL', 'INVESTMENTDISCRETION'],
                                 ['0001-26-1', 'APPLE INC', 'COM', '037833100', '', '1000', '10', 'SH', '', 'SOLE'],
                                 ['0001-26-1', 'APPLE INC', 'COM', '037833100', '', '2000', '20', 'SH', '', 'DFND'])})
        form345 = zip_bytes({
            'SUBMISSION.tsv': tsv(['ACCESSION_NUMBER', 'FILING_DATE', 'PERIOD_OF_REPORT', 'DOCUMENT_TYPE', 'ISSUERCIK', 'ISSUERNAME', 'ISSUERTRADINGSYMBOL'],
                                  ['0002-26-1', '03-SEP-2026', '01-SEP-2026', '4', '0000320193', 'Apple Inc.', 'AAPL']),
            'REPORTINGOWNER.tsv': tsv(['ACCESSION_NUMBER', 'RPTOWNERCIK', 'RPTOWNERNAME', 'RPTOWNER_RELATIONSHIP', 'RPTOWNER_TITLE'],
                                      ['0002-26-1', '0001214156', 'Cook Timothy', 'Director,Officer', 'CEO']),
            'NONDERIV_TRANS.tsv': tsv(['ACCESSION_NUMBER', 'SECURITY_TITLE', 'TRANS_DATE', 'TRANS_FORM_TYPE', 'TRANS_CODE', 'TRANS_SHARES',
                                       'TRANS_PRICEPERSHARE', 'TRANS_ACQUIRED_DISP_CD', 'SHRS_OWND_FOLWNG_TRANS', 'DIRECT_INDIRECT_OWNERSHIP'],
                                      ['0002-26-1', 'Common Stock', '01-SEP-2026', '4', 'S', '100.0', '230.5', 'D', '3000.0', 'D']),
            'DERIV_TRANS.tsv': tsv(['ACCESSION_NUMBER', 'SECURITY_TITLE', 'TRANS_DATE', 'TRANS_CODE', 'TRANS_SHARES'], ['0002-26-1', 'RSU', '', 'M', '5']),
            'NONDERIV_HOLDING.tsv': tsv(['ACCESSION_NUMBER', 'SECURITY_TITLE', 'SHRS_OWND_FOLWNG_TRANS', 'DIRECT_INDIRECT_OWNERSHIP'],
                                        ['0002-26-1', 'Common Stock', '500.0', 'I'])})
        later13f = zip_bytes({name: text.replace('0001-26-1', '0001-26-9').replace('31-JUL-2026', '14-NOV-2026').replace('30-JUN-2026', '30-SEP-2026')
                              for name, text in (('SUBMISSION.tsv', tsv(['ACCESSION_NUMBER', 'FILING_DATE', 'SUBMISSIONTYPE', 'CIK', 'PERIODOFREPORT'],
                                                                         ['0001-26-1', '31-JUL-2026', '13F-HR', '0001067983', '30-JUN-2026'])),
                                                 ('COVERPAGE.tsv', tsv(['ACCESSION_NUMBER', 'FILINGMANAGER_NAME'], ['0001-26-1', 'Fixture Capital'])),
                                                 ('SUMMARYPAGE.tsv', tsv(['ACCESSION_NUMBER', 'TABLEVALUETOTAL'], ['0001-26-1', '10'])),
                                                 ('INFOTABLE.tsv', tsv(['ACCESSION_NUMBER', 'NAMEOFISSUER', 'TITLEOFCLASS', 'CUSIP', 'VALUE', 'SSHPRNAMT', 'SSHPRNAMTTYPE'],
                                                                       ['0001-26-1', 'APPLE INC', 'COM', '037833100', '10', '1', 'SH'])))})
        records = self.run_full('sec_ownership_datasets', [(form13f, {}), (later13f, {}), (form345, {})], {'format': 'tsv', 'members': ['*.tsv'], 'strict': False})
        self.assertEqual(sorted(r['valid_from'] for r in records if r.get('predicate') == 'reported_holding'), ['2026-06-30', '2026-09-30'])
        holding = next(r for r in records if r.get('predicate') == 'reported_holding' and r['valid_from'] == '2026-06-30')
        self.assertEqual((holding['subject'], holding['object'], holding['attributes']['shares'], holding['attributes']['value_usd']),
                         ('sec:cik:0001067983', 'cusip:037833100', 30, 3000))
        self.assertEqual((holding['valid_from'], holding['observed_at'], holding['attributes']['investment_discretion']), ('2026-06-30', '2026-07-31', 'MIXED'))
        insider = next(r for r in records if r.get('predicate') == 'insider_of')
        self.assertEqual((insider['subject'], insider['object'], insider['attributes']['relationship']),
                         ('sec:cik:0001214156', 'sec:cik:0000320193', ['Director', 'Officer']))
        trade = next(r for r in records if r.get('event_type') == 'insider_transaction')
        self.assertEqual((trade['occurred_at'], trade['attributes']['shares'], trade['observed_at']), ('2026-09-01', 100, '2026-09-03'))
        held = next(r for r in records if r.get('metric') == 'insider_shares_owned')
        self.assertEqual((held['value'], held['dimensions']['ownership']), (500, 'I'))
        self.assertFalse(any(r.get('event_type') == 'insider_derivative_transaction' for r in records))  # blank TRANS_DATE skipped

    def test_gleif_level1_and_mappings(self):
        lei_csv = ('LEI,Entity.LegalName,Entity.LegalJurisdiction,Entity.EntityCategory,Entity.EntityStatus,Registration.RegistrationStatus,'
                   'Entity.SuccessorEntity.1.SuccessorLEI\n'
                   'HWUPKR0MPOU8FGXBT394,Apple Inc.,US-CA,GENERAL,ACTIVE,ISSUED,\n'
                   '001GPB6A9XPE8XJICC14,Fixture Fund,LU,FUND,ACTIVE,LAPSED,HWUPKR0MPOU8FGXBT394\n')
        records = self.run_full('sec_gleif', [(zip_bytes({'lei2.csv': lei_csv}), {}),
                                              (zip_bytes({'isin.csv': 'LEI,ISIN\nHWUPKR0MPOU8FGXBT394,US0378331005\nHWUPKR0MPOU8FGXBT394,DE000A0XYZ12\n'}), {}),
                                              (zip_bytes({'bic.csv': 'LEI,BIC\nHWUPKR0MPOU8FGXBT394,APLEUS66XXX\n'}), {})], {'format': 'csv', 'members': ['*.csv']})
        rows = self.by_id(records)
        self.assertEqual(rows['gleif_lei:HWUPKR0MPOU8FGXBT394:registered_in']['object'], 'geo:US:state:06')
        self.assertEqual(rows['gleif_lei:001GPB6A9XPE8XJICC14']['entity_type'], 'investment_fund')
        self.assertEqual(rows['gleif_isin:US0378331005:HWUPKR0MPOU8FGXBT394']['object'], 'isin:US0378331005')
        self.assertNotIn('gleif_isin:DE000A0XYZ12:HWUPKR0MPOU8FGXBT394', rows)
        self.assertEqual(rows['gleif_bic:HWUPKR0MPOU8FGXBT394:APLEUS66XXX']['value'], {'namespace': 'bic', 'value': 'APLEUS66XXX'})
        self.assertEqual(rows['gleif_lei:001GPB6A9XPE8XJICC14:successor:1']['object'], 'lei:HWUPKR0MPOU8FGXBT394')

    def test_gleif_level2_relationships_and_exceptions(self):
        rr = ('Relationship.StartNode.NodeID,Relationship.StartNode.NodeIDType,Relationship.EndNode.NodeID,Relationship.EndNode.NodeIDType,'
              'Relationship.RelationshipType,Relationship.RelationshipStatus,Relationship.Period.1.startDate,Relationship.Period.1.endDate,'
              'Relationship.Period.1.periodType,Relationship.Period.2.startDate,Relationship.Period.2.endDate,Relationship.Period.2.periodType,'
              'Registration.RegistrationStatus\n'
              '5493001KJTIIGC8Y1R12,LEI,549300B56MD0ZC402L06,LEI,IS_DIRECTLY_CONSOLIDATED_BY,ACTIVE,2016-01-01T00:00:00.000Z,2016-12-31T00:00:00.000Z,'
              'ACCOUNTING_PERIOD,2007-06-05T00:00:00.000Z,,RELATIONSHIP_PERIOD,PUBLISHED\n')
        repex = ('LEI,Exception.Category,Exception.Reason.1,Exception.Reason.2,Exception.Reason.3,Exception.Reason.4,Exception.Reason.5,'
                 'Exception.Reference.1,Exception.Reference.2,Exception.Reference.3,Exception.Reference.4,Exception.Reference.5\n'
                 '549300B56MD0ZC402L06,ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT,NATURAL_PERSONS,,,,,,,,,\n'
                 '549300B56MD0ZC402L06,DIRECT_ACCOUNTING_CONSOLIDATION_PARENT,NATURAL_PERSONS,,,,,,,,,\n'
                 '5493001KJTIIGC8Y1R12,ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT,NON_CONSOLIDATING,,,,,,,,,\n')
        rows = self.by_id(self.run_full('gleif_parent_relationships', [(zip_bytes({'rr.csv': rr}), {}), (zip_bytes({'repex.csv': repex}), {})],
                                        {'format': 'csv', 'members': ['*.csv']}))
        edge = rows['gleif_rr:lei:5493001KJTIIGC8Y1R12:directly_consolidated_by:lei:549300B56MD0ZC402L06']
        self.assertEqual(edge['valid_from'], '2007-06-05')
        self.assertEqual(edge['attributes']['accounting_period'], {'start': '2016-01-01', 'end': '2016-12-31'})
        self.assertIn('gleif_repex:549300B56MD0ZC402L06:ultimate', rows)
        self.assertNotIn('gleif_repex:5493001KJTIIGC8Y1R12:ultimate', rows)
        self.assertEqual(rows['gleif_repex:count:DIRECT_ACCOUNTING_CONSOLIDATION_PARENT:NATURAL_PERSONS']['value'], 1)

    def test_fdic_endpoints(self):
        financials = ('"CERT","REPDTE","RSSDHCR","ASSET","DEP","NETINC","RBC1AAJ","NUMEMP"\n'
                      '628,"20240930","1039502",100,80,3,9.5,10\n628,"20241231","1039502",110,85,4,9.6,11\n')
        institutions = ('"CERT","NAME","FED_RSSD","ACTIVE","ESTYMD","ENDEFYMD","STALP","STCNTY","LATITUDE","LONGITUDE"\n'
                        '628,"JPMorgan Chase Bank","852218",1,"01/01/1824","12/31/9999","OH","39049",40.0,-83.0\n')
        sod = ('"YEAR","CERT","UNINUMBR","NAMEBR","STCNTYBR","DEPSUMBR","SIMS_LATITUDE","SIMS_LONGITUDE","BKMO"\n'
               '2025,628,123,"Main Office","39049",5000,40.0,-83.0,1\n')
        meta = lambda endpoint: {'request': {'params': {'endpoint': endpoint}}}
        rows = self.by_id(self.run_full('fdic_bank_financials', [(financials, meta('financials')), (institutions, meta('institutions')), (sod, meta('sod'))],
                                        {'format': 'csv'}))
        assets = rows['fdic:628:20241231:ASSET']
        self.assertEqual((assets['value'], assets['valid_from'], assets['valid_to']), (110000, '2024-12-31', '2025-01-01'))
        self.assertEqual(rows['fdic:628:20241231:NETINC']['valid_from'], '2024-01-01')
        self.assertEqual(rows['fdic:628:20241231:RBC1AAJ']['unit'], 'percent')
        holder = rows['fdic:holder:628:1039502:2024-09-30']
        self.assertEqual((holder['object'], holder['valid_to']), ('rssd:1039502', '2025-01-01'))
        self.assertEqual(rows['fdic:institution:628:rssd']['value'], {'namespace': 'rssd', 'value': '852218'})
        self.assertNotIn('valid_to', rows['fdic:institution:628:charter'])
        self.assertEqual(rows['fdic:sod:2025:123:deposits']['value'], 5000000)

    def test_13f_history_thousand_dollar_values(self):
        form13f = zip_bytes({
            'SUBMISSION.tsv': tsv(['ACCESSION_NUMBER', 'FILING_DATE', 'SUBMISSIONTYPE', 'CIK', 'PERIODOFREPORT'],
                                  ['0001-22-1', '14-NOV-2022', '13F-HR', '0001067983', '30-SEP-2022'],
                                  ['0001-23-1', '14-FEB-2023', '13F-HR', '0001067983', '31-DEC-2022']),
            'COVERPAGE.tsv': tsv(['ACCESSION_NUMBER', 'FILINGMANAGER_NAME'], ['0001-22-1', 'Fixture Capital']),
            'SUMMARYPAGE.tsv': tsv(['ACCESSION_NUMBER', 'TABLEENTRYTOTAL', 'TABLEVALUETOTAL'], ['0001-22-1', '1', '5'], ['0001-23-1', '1', '5000']),
            'INFOTABLE.tsv': tsv(['ACCESSION_NUMBER', 'NAMEOFISSUER', 'TITLEOFCLASS', 'CUSIP', 'VALUE', 'SSHPRNAMT', 'SSHPRNAMTTYPE', 'PUTCALL', 'INVESTMENTDISCRETION'],
                                 ['0001-22-1', 'APPLE INC', 'COM', '037833100', '5', '40', 'SH', '', 'SOLE'],
                                 ['0001-23-1', 'APPLE INC', 'COM', '037833100', '5000', '40', 'SH', '', 'SOLE'])})
        rows = self.by_id(self.run_full('sec_13f_history', [(form13f, {})], {'format': 'tsv', 'members': ['*.tsv'], 'strict': False}))
        old, new = rows['sec13fh:0001-22-1:037833100:-:SH'], rows['sec13fh:0001-23-1:037833100:-:SH']
        self.assertEqual((old['attributes']['value_usd'], old['attributes']['value_source_unit']), (5000, 'thousand_USD'))
        self.assertEqual((new['attributes']['value_usd'], new['valid_from'], new['observed_at']), (5000, '2022-12-31', '2023-02-14'))
        self.assertNotIn('value_source_unit', new['attributes'])
        self.assertEqual(rows['sec13fh:0001-22-1:portfolio_value']['value'], 5000)

    def test_financial_statement_data_sets(self):
        sub = tsv(['adsh', 'cik', 'name', 'sic', 'countryba', 'stprinc', 'fye', 'form', 'period', 'fy', 'fp', 'filed', 'prevrpt'],
                  ['0000320193-26-000010', '320193', 'APPLE INC', '3571', 'US', 'CA', '0926', '10-Q', '20260630', '2026', 'Q3', '20260801', '0'],
                  ['0000000001-26-000001', '1', 'EIGHT K CO', '1000', 'US', 'DE', '1231', '8-K', '20260630', '2026', 'Q2', '20260801', '0'])
        num = tsv(['adsh', 'tag', 'version', 'ddate', 'qtrs', 'uom', 'segments', 'coreg', 'value', 'footnote'],
                  ['0000320193-26-000010', 'Revenues', 'us-gaap/2025', '20260630', '1', 'USD', '', '', '94000000000', ''],
                  ['0000320193-26-000010', 'Revenues', 'us-gaap/2025', '20260630', '1', 'USD', 'ProductOrService=iPhone;', '', '44000000000', ''],
                  ['0000320193-26-000010', 'Assets', 'us-gaap/2025', '20260630', '0', 'USD', '', '', '331000000000', ''],
                  ['0000320193-26-000010', 'CustomThing', '0000320193-26-000010', '20260630', '0', 'USD', '', '', '1', ''],
                  ['0000000001-26-000001', 'Assets', 'us-gaap/2025', '20260630', '0', 'USD', '', '', '5', ''])
        pre = tsv(['adsh', 'report', 'line', 'stmt', 'inpth', 'rfile', 'tag', 'version', 'plabel', 'negating'],
                  ['0000320193-26-000010', '4', '1', 'IS', '0', 'H', 'Revenues', 'us-gaap/2025', 'Total net sales', '0'])
        records = self.run_full('sec_financial_statements', [(zip_bytes({'sub.txt': sub, 'num.txt': num, 'pre.txt': pre, 'tag.txt': 'tag\tversion\n'}), {})],
                                {'format': 'tsv', 'members': ['*.txt'], 'strict': False})
        obs = [r for r in records if r['kind'] == 'observation']
        self.assertEqual(len(obs), 3)
        total = next(r for r in obs if r['metric'] == 'revenue' and 'segments' not in r['dimensions'])
        self.assertEqual((total['value'], total['valid_from'], total['valid_to'], total['observed_at']), (94000000000, '2026-04-01', '2026-07-01', '2026-08-01'))
        self.assertEqual((total['dimensions']['statement'], total['attributes']['as_filed_label']), ('IS', 'Total net sales'))
        segment = next(r for r in obs if r['dimensions'].get('segments'))
        self.assertEqual(segment['dimensions']['segments'], 'ProductOrService=iPhone;')
        assets = next(r for r in obs if r['metric'] == 'total_assets')
        self.assertEqual((assets['valid_from'], assets['dimensions']['period_type'], assets['subject']), ('2026-06-30', 'instant', 'sec:cik:0000320193'))

class CompaniesHouseTests(FullPipelineBase):
    def test_basic_company_data_csv(self):
        csv_text = ('CompanyName, CompanyNumber,RegAddress.PostTown,CompanyCategory,CompanyStatus,CountryOfOrigin,DissolutionDate,'
                    'IncorporationDate,SICCode.SicText_1,PreviousName_1.CONDATE, PreviousName_1.CompanyName\n'
                    'FIXTURE LTD,01234567,LONDON,Private Limited Company,Active,United Kingdom,,02/03/2001,'
                    '62020 - Information technology consultancy activities,04/05/2010,OLD FIXTURE LTD\n')
        rows = self.by_id(self.run_full('companies_house_uk', [(zip_bytes({'BasicCompanyData.csv': csv_text}), {})], {'format': 'csv', 'members': ['*.csv']}))
        self.assertEqual(rows['ch:01234567']['attributes']['incorporated'], '2001-03-02')
        self.assertEqual(rows['ch:01234567:sic:1']['object'], 'uksic2007:62020')
        self.assertEqual((rows['ch:01234567:previous_name:1']['value'], rows['ch:01234567:previous_name:1']['valid_to']), ('OLD FIXTURE LTD', '2010-05-04'))

    def test_psc_snapshot_control_assertions(self):
        lines = [
            {'company_number': '01234567', 'data': {'kind': 'individual-person-with-significant-control', 'name': 'Ms Fixture Person',
             'nationality': 'British', 'country_of_residence': 'England', 'date_of_birth': {'month': 1, 'year': 1970},
             'natures_of_control': ['ownership-of-shares-25-to-50-percent', 'voting-rights-25-to-50-percent'], 'notified_on': '2016-04-06',
             'ceased_on': '2020-01-01', 'links': {'self': '/company/01234567/persons-with-significant-control/individual/abcDEF123'}}},
            {'company_number': '01234567', 'data': {'kind': 'corporate-entity-person-with-significant-control', 'name': 'PARENT LTD',
             'identification': {'legal_authority': 'Companies Act 2006', 'place_registered': 'Companies House', 'country_registered': 'England',
                                'registration_number': '7654321'}, 'natures_of_control': ['ownership-of-shares-75-to-100-percent'],
             'notified_on': '2020-01-01', 'links': {'self': '/company/01234567/persons-with-significant-control/corporate-entity/XYZ9'}}},
            {'company_number': '07654321', 'data': {'kind': 'persons-with-significant-control-statement',
             'statement': 'no-individual-or-entity-with-signficant-control', 'notified_on': '2017-01-01',
             'links': {'self': '/company/07654321/persons-with-significant-control-statements/S1'}}},
            {'data': {'kind': 'totals#persons-of-significant-control-snapshot', 'persons_of_significant_control_count': 3}}]
        rows = self.by_id(self.run_full('companies_house_uk', [(zip_bytes({'psc-snapshot.txt': ''.join(json.dumps(l) + '\n' for l in lines)}), {})]))
        control = rows['chpsc:01234567:abcDEF123:control']
        self.assertEqual((control['subject'], control['object'], control['valid_from'], control['valid_to']),
                         ('gb:psc:abcDEF123', 'gb:companies_house:01234567', '2016-04-06', '2020-01-01'))
        self.assertEqual(control['attributes']['share_bands'], {'ownership_of_shares': [0.25, 0.5], 'voting_rights': [0.25, 0.5]})
        person = rows['chpsc:01234567:abcDEF123:entity']
        self.assertEqual(person['entity_type'], 'person')
        self.assertNotIn('date_of_birth', json.dumps(person))
        self.assertEqual(rows['chpsc:01234567:XYZ9:registered_as']['object'], 'gb:companies_house:07654321')
        self.assertEqual(rows['chpsc:07654321:S1:statement']['value'], 'no-individual-or-entity-with-signficant-control')

class ResearchTests(FullPipelineBase):
    def test_crossref_full_rows(self):
        work = {'DOI': '10.1000/XYZ', 'title': ['Fixture'], 'type': 'journal-article', 'issued': {'date-parts': [[2024, 5]]},
                'is-referenced-by-count': 3,
                'author': [{'given': 'A', 'family': 'B', 'ORCID': 'https://orcid.org/0000-0002-1825-0097',
                            'affiliation': [{'name': 'Fixture Lab', 'id': [{'id': 'https://ror.org/05h992307', 'id-type': 'ROR'}]}, {'name': 'Unnamed Org'}]}],
                'funder': [{'DOI': '10.13039/100000005', 'name': 'U.S. Department of Defense', 'award': ['W911NF']}]}
        records = self.run_full('crossref_research', [(json.dumps(work) + '\n' + json.dumps(work) + '\n', {}), ('{"page": 1}\n', {'role': 'page_log'})],
                                {'format': 'jsonl'})
        rows = self.by_id(records)
        self.assertEqual(rows['crossref:10.1000/xyz:metadata']['value']['issued'], '2024-05')
        self.assertEqual(rows['crossref:10.1000/xyz:affiliation:0:0']['object'], 'ror:05h992307')
        self.assertEqual(rows['crossref:10.1000/xyz:affiliation:0:1']['value'], 'Unnamed Org')
        self.assertEqual(rows['crossref:10.1000/xyz:funder:0']['attributes']['awards'], ['W911NF'])
        self.assertEqual(sum(1 for r in records if r.get('entity_type') == 'research_paper'), 1)

    def test_openalex_full_rows(self):
        institution = {'id': 'https://openalex.org/I1', 'display_name': 'Lab', 'type': 'facility', 'country_code': 'US', 'ror': 'https://ror.org/05h992307',
                       'ids': {'grid': 'grid.1'}, 'lineage': ['https://openalex.org/I1', 'https://openalex.org/I2', 'https://openalex.org/I2'],
                       'works_count': 5, 'cited_by_count': 7, 'updated_date': '2026-09-01T00:00:00.123'}
        author = {'id': 'https://openalex.org/A9', 'display_name': 'Person', 'orcid': 'https://orcid.org/0000-0002-1825-0097', 'works_count': 120,
                  'last_known_institutions': [{'id': 'https://openalex.org/I3', 'display_name': 'Company', 'type': 'company'}], 'updated_date': '2026-09-02'}
        rows = self.by_id(self.run_full('openalex_people', [(json.dumps(institution) + '\n' + json.dumps(author) + '\n', {})], {'format': 'jsonl'}))
        self.assertEqual(rows['openalex_people:I1:lineage:I2']['object'], 'openalex:I2')
        self.assertEqual(rows['openalex_people:A9:last_known:I3']['object'], 'openalex:I3')
        self.assertNotIn('valid_from', rows['openalex_people:A9:last_known:I3'])
        self.assertEqual(rows['openalex_people:A9:institution:I3']['entity_type'], 'business')
        self.assertEqual(rows['openalex_people:I1:works_count']['valid_from'], '2026-09-01')

    def test_nasa_paged_rss(self):
        rss = ('<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>Post</title>'
               '<link>https://www.nasa.gov/p/</link><guid>g1</guid><pubDate>Mon, 14 Sep 2026 10:00:00 +0000</pubDate>'
               '<dc:creator>NASA</dc:creator><category>Artemis</category></item></channel></rss>')
        records = self.run_full('nasa_publications', [(rss, {}), (rss, {})])
        self.assertEqual(sum(1 for r in records if r.get('entity_type') == 'post'), 1)
        with self.assertRaises(Exception):
            self.run_full('nasa_publications', [('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>', {})])


if __name__ == '__main__':
    unittest.main()
