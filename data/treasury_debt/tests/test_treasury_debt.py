"""Offline test: fiscaldata page shards (debt, DTS, MTS, average rates) through the Runner."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
DATASET = 'treasury_debt'
BASE = 'https://api.fiscaldata.treasury.gov/services/api/fiscal_service/'
PAGES = [
    ('v2', 'od', 'debt_to_penny',
     [{'record_date': '2025-01-02', 'tot_pub_debt_out_amt': '100.50', 'debt_held_public_amt': '80.25',
       'intragov_hold_amt': '20.25', 'src_line_nbr': '1'},
      {'record_date': '1993-04-01', 'tot_pub_debt_out_amt': '4.1', 'debt_held_public_amt': 'null',
       'intragov_hold_amt': 'null', 'src_line_nbr': '1'}],
     {'tot_pub_debt_out_amt': '$10.20', 'debt_held_public_amt': '$10.20', 'intragov_hold_amt': '$10.20'}),
    ('v1', 'dts', 'deposits_withdrawals_operating_cash',
     [{'record_date': '2010-06-01', 'account_type': 'Federal Reserve Account', 'transaction_type': 'Deposits',
       'transaction_catg': 'Deposits by States', 'transaction_catg_desc': 'null', 'transaction_today_amt': '1',
       'table_nbr': 'II', 'src_line_nbr': '5'},
      {'record_date': '2010-06-01', 'account_type': 'Federal Reserve Account', 'transaction_type': 'Deposits',
       'transaction_catg': 'Deposits by States', 'transaction_catg_desc': 'null', 'transaction_today_amt': '37',
       'table_nbr': 'II', 'src_line_nbr': '5'},
      {'record_date': '2010-06-01', 'account_type': 'Federal Reserve Account', 'transaction_type': 'Withdrawals',
       'transaction_catg': 'Total Withdrawals (excluding transfers)', 'transaction_catg_desc': 'null',
       'transaction_today_amt': '7', 'table_nbr': 'II', 'src_line_nbr': '60'}],
     {'transaction_today_amt': '$1,000,000'}),
    ('v1', 'mts', 'mts_table_5',
     [{'record_date': '2025-08-31', 'parent_id': 'null', 'classification_id': '1', 'classification_desc': 'Legislative Branch:',
       'current_month_gross_outly_amt': 'null', 'current_month_app_rcpt_amt': 'null', 'current_month_net_outly_amt': 'null',
       'current_fytd_net_outly_amt': 'null', 'table_nbr': '5', 'src_line_nbr': '1', 'line_code_nbr': '10',
       'data_type_cd': 'S', 'record_type_cd': 'SL', 'sequence_level_nbr': '1', 'sequence_number_cd': '1'},
      {'record_date': '2025-08-31', 'parent_id': '1', 'classification_id': '2', 'classification_desc': 'Senate',
       'current_month_gross_outly_amt': '10.5', 'current_month_app_rcpt_amt': 'null', 'current_month_net_outly_amt': '10.5',
       'current_fytd_net_outly_amt': '100', 'table_nbr': '5', 'src_line_nbr': '2', 'line_code_nbr': '20',
       'data_type_cd': 'D', 'record_type_cd': 'SL', 'sequence_level_nbr': '2', 'sequence_number_cd': '1.1'}],
     {'current_month_gross_outly_amt': '$10.20', 'current_month_net_outly_amt': '$10.20',
      'current_fytd_net_outly_amt': '$10.20'}),
    ('v2', 'od', 'avg_interest_rates',
     [{'record_date': '2026-08-31', 'security_type_desc': 'Marketable', 'security_desc': 'Treasury Bills',
       'avg_interest_rate_amt': '3.788', 'src_line_nbr': '1'}],
     {'avg_interest_rate_amt': '10.2%'}),
]


class TreasuryPipelineTest(unittest.TestCase):
    def test_fiscaldata_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            shards = []
            for index, (version, group, table, rows, formats) in enumerate(PAGES):
                path = temp / f'{index}.json'
                path.write_text(json.dumps({'data': rows, 'meta': {'dataFormats': formats, 'total-pages': 1}}))
                params = {'version': version, 'group': group, 'table': table, 'fields': 'x'}
                shards.append({'path': path, 'request': {'url': f'{BASE}{version}/accounting/{group}/{table}?format=json',
                                                         'params': params}})
            store = Store(temp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run(DATASET)
            records = [json.loads(line) for line in gzip.open(store.version_dir(ref) / 'records.jsonl.gz', 'rt')]
        obs = [r for r in records if r['kind'] == 'observation']
        debt = {(r['metric'], r['valid_from']): r for r in obs if r['dimensions']['table'] == 'debt_to_penny'}
        self.assertEqual(sorted(debt), [('debt_held_by_public', '2025-01-02'), ('intragovernmental_debt', '2025-01-02'),
                                        ('public_debt', '1993-04-01'), ('public_debt', '2025-01-02')])
        self.assertEqual((debt[('public_debt', '2025-01-02')]['value'], debt[('public_debt', '2025-01-02')]['valid_to']),
                         (100.5, '2025-01-03'))
        cash = [r for r in obs if r['dimensions']['table'] == 'deposits_withdrawals_operating_cash']
        self.assertEqual(sorted((r['metric'], r['value']) for r in cash),
                         [('treasury_cash_deposits', 1_000_000), ('treasury_cash_deposits', 37_000_000),
                          ('treasury_cash_withdrawals', 7_000_000)])
        self.assertTrue(next(r for r in cash if r['metric'] == 'treasury_cash_withdrawals')['attributes']['aggregate'])
        mts = {(r['metric'], r['dimensions']['period']): r for r in obs if r['dimensions']['table'] == 'mts_table_5'}
        self.assertEqual(sorted(mts), [('federal_outlays_gross', 'month'), ('federal_outlays_net', 'fytd'),
                                       ('federal_outlays_net', 'month')])
        self.assertEqual((mts[('federal_outlays_net', 'month')]['valid_from'], mts[('federal_outlays_net', 'month')]['valid_to']),
                         ('2025-08-01', '2025-09-01'))
        self.assertEqual((mts[('federal_outlays_net', 'fytd')]['valid_from'], mts[('federal_outlays_net', 'fytd')]['valid_to']),
                         ('2024-10-01', '2025-09-01'))
        rate = next(r for r in obs if r['metric'] == 'average_interest_rate')
        self.assertEqual((rate['subject'], rate['value'], rate['unit']), ('treasury:security_type:bills', 3.788, 'percent'))
        entities = {r['entity_id'] for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities, {'us:agency:treasury', 'treasury:security_type:bills'})
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records))


if __name__ == '__main__':
    unittest.main()
