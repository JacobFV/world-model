import copy
import unittest
from worldmodel.banking import simulate_banking, register_banking_processes
from worldmodel.processes import ProcessRegistry


class BankingTests(unittest.TestCase):
    def config(self, transactions=None):
        return {'currency': 'USD', 'banks': [
            {'id': 'a', 'reserves': 100, 'loans': {}, 'equity': 20, 'accounts': {'alice': 80}},
            {'id': 'b', 'reserves': 50, 'loans': {}, 'equity': 10, 'accounts': {'bob': 40}}],
            'transactions': transactions or []}

    def test_origination_creates_deposit_without_reserve_outflow(self):
        cfg = self.config([{'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': 30}])
        result = simulate_banking(cfg)
        bank = result['banks'][0]
        self.assertEqual((bank['reserves'], bank['loans']['alice'], bank['accounts']['alice']), (100, 30, 110))
        self.assertEqual(result['accounting']['deposit_change'], 30)
        self.assertEqual(cfg['banks'][0]['loans'], {})

    def test_repayment_extinguishes_credit_money(self):
        result = simulate_banking(self.config([
            {'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': 30},
            {'kind': 'repay', 'bank': 'a', 'borrower': 'alice', 'amount': 20}]))
        self.assertEqual(result['banks'][0]['loans']['alice'], 10)
        self.assertEqual(result['banks'][0]['accounts']['alice'], 90)
        self.assertEqual(result['accounting']['deposit_change'], 10)

    def test_interbank_settlement_and_balanced_journal(self):
        result = simulate_banking(self.config([{'kind': 'transfer', 'bank': 'a', 'from_account': 'alice',
                                      'to_bank': 'b', 'to_account': 'bob', 'amount': 25}]))
        a, b = result['banks']
        self.assertEqual((a['reserves'], b['reserves']), (75, 75))
        self.assertEqual((a['accounts']['alice'], b['accounts']['bob']), (55, 65))
        self.assertTrue(result['accounting']['balanced'])
        self.assertEqual(result['accounting']['reserve_change'], 0)
        for entry in result['journal']:
            self.assertEqual(sum(p['debit'] for p in entry['postings']), sum(p['credit'] for p in entry['postings']))

    def test_intrabank_transfer_moves_no_reserves(self):
        cfg = self.config([{'kind': 'transfer', 'bank': 'a', 'from_account': 'alice', 'to_bank': 'a', 'to_account': 'new', 'amount': 25}])
        result = simulate_banking(cfg)
        self.assertEqual(result['banks'][0]['reserves'], 100)
        self.assertEqual(result['banks'][0]['accounts']['new'], 25)

    def test_default_absorbed_by_equity_can_record_insolvency(self):
        result = simulate_banking(self.config([
            {'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': 30},
            {'kind': 'default', 'bank': 'a', 'borrower': 'alice', 'amount': 30}]))
        self.assertEqual(result['banks'][0]['equity'], -10)
        self.assertEqual(result['banks'][0]['accounts']['alice'], 110)
        self.assertEqual(result['banks'][0]['status'], 'insolvent')
        self.assertTrue(any(e['kind'] == 'bank_insolvent' for e in result['events']))
        self.assertTrue(result['accounting']['balanced'])

    def test_reject_insufficient_deposits_reserves_and_bad_identity(self):
        for amount in [81, -1, float('nan'), .001]:
            with self.assertRaises(ValueError):
                simulate_banking(self.config([{'kind': 'transfer', 'bank': 'a', 'from_account': 'alice', 'to_bank': 'b', 'to_account': 'bob', 'amount': amount}]))
        cfg = self.config(); cfg['banks'][0].update(reserves=5, loans={'alice': 95})
        cfg['transactions'] = [{'kind': 'transfer', 'bank': 'a', 'from_account': 'alice', 'to_bank': 'b', 'to_account': 'bob', 'amount': 10}]
        with self.assertRaises(ValueError): simulate_banking(cfg)
        cfg = self.config(); cfg['banks'][0]['equity'] = 21
        with self.assertRaises(ValueError): simulate_banking(cfg)
        with self.assertRaises(ValueError): simulate_banking(self.config([{'kind': 'originate', 'bank': 'unknown', 'borrower': 'x', 'amount': 1}]))

    def test_insolvent_continuation_and_failed_batch_preserves_input(self):
        cfg = self.config([
            {'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': 30},
            {'kind': 'default', 'bank': 'a', 'borrower': 'alice', 'amount': 30}])
        result = simulate_banking(cfg)
        continuation = {'banks': result['banks'], 'transactions': []}
        self.assertEqual(simulate_banking(continuation)['banks'][0]['equity'], -10)
        continuation['transactions'] = [{'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': 1}]
        original = copy.deepcopy(continuation)
        with self.assertRaises(ValueError): simulate_banking(continuation)
        self.assertEqual(continuation, original)
        cfg = self.config(); cfg['banks'][0].pop('reserves'); cfg['banks'][0]['status'] = 'solvent'
        with self.assertRaises(ValueError): simulate_banking(cfg)

    def test_exact_cents_and_budget(self):
        result = simulate_banking(self.config([{'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': .1}] * 100))
        self.assertEqual(result['banks'][0]['loans']['alice'], 10)
        cfg = self.config(); cfg['transactions'] = [{}] * 10001
        with self.assertRaises(ValueError): simulate_banking(cfg)

    def test_daily_adapter_clears_executed_transactions(self):
        registry = register_banking_processes(ProcessRegistry())
        cfg = self.config([{'kind': 'originate', 'bank': 'a', 'borrower': 'alice', 'amount': 30}])
        result = registry.predict('banking_ledger.deterministic', {'banking_state': {'value': cfg, 'unit': None}}, {}, {'dt_seconds': 86400})
        state = result['pressures'][0]['value']
        self.assertEqual(state['transactions'], [])
        self.assertEqual(state['banks'][0]['loans']['alice'], 30)
        repeated = registry.predict('banking_ledger.deterministic', {'banking_state': {'value': state, 'unit': None}}, {}, {'dt_seconds': 86400})
        self.assertEqual(repeated['pressures'][0]['value']['banks'][0]['loans']['alice'], 30)
