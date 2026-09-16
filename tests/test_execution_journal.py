import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'journal.sqlite'

    def journal(self,**kw):
        from worldmodel.execution_journal import ExecutionJournal
        return ExecutionJournal(self.path,**kw)

    def test_append_reopen_pagination_and_idempotency(self):
        with self.journal() as j:
            self.assertEqual(j.append('episode',{'a':1},key='first'),1)
            self.assertEqual(j.append('episode',{'a':1},key='first'),1)
            with self.assertRaises(ValueError):j.append('episode',{'a':2},key='first')
            for n in range(2,6):j.append('episode',{'a':n})
        with self.journal() as j:
            page=j.history('episode',limit=2)
            self.assertEqual([r['value']['a'] for r in page['items']],[1,2])
            self.assertTrue(page['has_more'])
            page=j.history('episode',after=page['next_after'],limit=3)
            self.assertEqual([r['sequence'] for r in page['items']],[3,4,5])
            self.assertFalse(page['has_more'])

    def test_shared_quota_is_monotonic_under_concurrent_reservations(self):
        from worldmodel.execution_journal import QuotaExceeded
        with self.journal() as j:j.configure_quota('calls',7)
        def reserve(n):
            with self.journal() as j:
                try:j.reserve('calls',1,'attempt:'+str(n));return True
                except QuotaExceeded:return False
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(reserve,range(20)))
        self.assertEqual(sum(results),7)
        with self.journal() as j:
            self.assertEqual(j.quota('calls'),{'name':'calls','limit':7,'used':7,'remaining':0})
            with self.assertRaises(ValueError):j.configure_quota('calls',8)
            reservation=j.reservations('calls',limit=1)['items'][0]
            j.reserve('calls',1,reservation['key'])
            self.assertEqual(j.quota('calls')['used'],7)

    def test_pending_and_uncertain_effects_never_authorize_retry(self):
        from worldmodel.execution_journal import EffectUnresolved
        with self.journal() as j:
            j.configure_quota('calls',2)
            first=j.begin_effect('request:1',{'prompt':'test'},quota='calls')
            self.assertTrue(first['execute']);self.assertEqual(first['status'],'pending')
        with self.journal() as j:
            with self.assertRaises(EffectUnresolved):j.begin_effect('request:1',{'prompt':'test'},quota='calls')
            j.mark_uncertain('request:1','connection lost')
            with self.assertRaises(EffectUnresolved):j.begin_effect('request:1',{'prompt':'test'},quota='calls')
            with self.assertRaises(ValueError):j.complete_effect('request:1',{'answer':'yes'})
            j.complete_effect('request:1',{'answer':'yes'},provider_receipt={'id':'provider:1'})
            repeated=j.begin_effect('request:1',{'prompt':'test'},quota='calls')
            self.assertFalse(repeated['execute']);self.assertEqual(repeated['result'],{'answer':'yes'})
            self.assertEqual(j.quota('calls')['used'],1)
            with self.assertRaises(ValueError):j.begin_effect('request:1',{'prompt':'changed'},quota='calls')
            with self.assertRaises(ValueError):j.complete_effect('request:1',{'answer':'different'})

    def test_effect_budget_failure_is_atomic(self):
        from worldmodel.execution_journal import QuotaExceeded
        with self.journal() as j:
            j.configure_quota('calls',1);j.reserve('calls',1,'existing')
            with self.assertRaises(QuotaExceeded):j.begin_effect('blocked',{},quota='calls')
            self.assertEqual(j.effects()['items'],[])
            self.assertEqual(len(j.reservations('calls')['items']),1)

    def checkpoint(self):
        from worldmodel.util import digest
        value={'schema_version':1,'identity':'a'*64,'payload':{'state':{'n':1}}}
        return {**value,'checksum':digest(value)}

    def test_checkpoint_history_commit_rollback_and_corruption(self):
        with self.journal() as j:
            cp=self.checkpoint()
            saved=j.save_checkpoint('episode',cp,history_events=[{'action':1}])
            self.assertEqual(saved['sequence'],1)
            with self.assertRaises(ValueError):j.save_checkpoint('episode',cp,history_events=[{'bad':(1,2)}])
            self.assertEqual(len(j.history('episode')['items']),1)
        with self.journal() as j:
            self.assertEqual(j.load_checkpoint('episode'),cp)
            with self.assertRaises(ValueError):j.load_checkpoint('episode',expected_identity='b'*64)
            j.db.execute("UPDATE checkpoints SET payload='{}' WHERE session='episode'")
            with self.assertRaisesRegex(ValueError,'integrity'):j.load_checkpoint('episode')

    def test_payload_and_logical_storage_limits_prevent_partial_writes(self):
        limits={'max_payload_bytes':100,'max_checkpoint_bytes':1000,'max_storage_bytes':220,'max_records':100}
        with self.journal(limits=limits) as j:
            with self.assertRaises(ValueError):j.append('s',{'text':'x'*100})
            self.assertEqual(j.history('s')['items'],[])
            for n in range(3):j.append('s',{'text':'x'*50})
            with self.assertRaises(ValueError):j.append('s',{'text':'x'*50})
            self.assertEqual(len(j.history('s')['items']),3)
            from worldmodel.limits import LimitExceeded, use_limits
            self.assertEqual(len(j.history('s',limit=101)['items']),3)
            with use_limits(journal_max_page_items=100):
                with self.assertRaisesRegex(LimitExceeded,'journal_max_page_items=100'):j.history('s',limit=101)
            with self.assertRaises(ValueError):j.append('s',{1:'changed by JSON'})

    def test_atomic_checkpoint_rolls_back_if_history_exhausts_storage(self):
        from worldmodel.util import canonical
        cp=self.checkpoint();size=len(canonical(cp))
        limits={'max_storage_bytes':size+5}
        with self.journal(limits=limits) as j:
            with self.assertRaises(ValueError):j.save_checkpoint('s',cp,history_events=[{'extra':True}])
            with self.assertRaises(ValueError):j.load_checkpoint('s')
            self.assertEqual(j.history('s')['items'],[]);self.assertEqual(j.usage(),{'bytes':0,'records':0})

    def test_idempotent_keys_preserve_json_scalar_types(self):
        with self.journal() as j:
            j.append('s',True,key='key')
            with self.assertRaises(ValueError):j.append('s',1,key='key')
            j.begin_effect('key',True)
            j.complete_effect('key',True)
            with self.assertRaises(ValueError):j.begin_effect('key',1)
            with self.assertRaises(ValueError):j.complete_effect('key',1)

    def test_concurrent_effect_claim_only_authorizes_one_caller(self):
        from worldmodel.execution_journal import EffectUnresolved
        with self.journal() as j:j.configure_quota('calls',5)
        def claim(_):
            with self.journal() as j:
                try:return j.begin_effect('same',{'a':1},quota='calls')['execute']
                except EffectUnresolved:return False
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(claim,range(8)))
        self.assertEqual(sum(results),1)
        with self.journal() as j:
            self.assertEqual(j.quota('calls')['used'],1)
            self.assertEqual(j.history('effect:same')['items'][0]['value']['status'],'pending')

    def test_page_byte_limit_and_stale_checkpoint_writer(self):
        with self.journal() as j:
            for n in range(3):j.append('s',{'n':n})
            page=j.history('s',max_bytes=7)
            self.assertEqual(len(page['items']),1);self.assertTrue(page['has_more'])
            j.save_checkpoint('s',self.checkpoint(),expected_sequence=0)
            with self.assertRaises(ValueError):j.save_checkpoint('s',self.checkpoint(),expected_sequence=0)
            self.assertEqual(j.save_checkpoint('s',self.checkpoint(),expected_sequence=1)['sequence'],2)

    def test_checkpoint_evaluator_reopens_without_resetting_shared_quota(self):
        from tests import test_materialize
        from worldmodel.checkpoints import CheckpointEvaluator
        fixture=test_materialize.MaterializeTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        def evaluator():
            return CheckpointEvaluator(fixture.store,fixture.graph,fixture.request,2,fixture.registry)
        first=evaluator();first([],7);first([{'inputs':[]}],7)
        with self.journal() as j:
            j.configure_quota('process',3);j.reserve('process',1,'step:1')
            j.save_evaluator('episode',first,history_events=[{'inputs':[]}],expected_sequence=0)
        second=evaluator()
        with self.journal() as j:
            restored=j.load_evaluator('episode',second)
            self.assertEqual(restored['snapshots'],first.result()['snapshots'])
            j.reserve('process',1,'step:2')
            result=second([{'inputs':[]}]*2,7)
            self.assertEqual(result['snapshots'][-1]['value'],18)
            j.load_evaluator('episode',second)
            self.assertEqual(j.quota('process')['used'],2)
            self.assertEqual(second.total_calls,1)

    def test_effect_audit_namespace_and_keys_remain_accessible(self):
        with self.journal() as j:
            with self.assertRaises(ValueError):j.begin_effect('x'*194,{})
            with self.assertRaises(ValueError):j.append('effect:reserved',{'status':'completed'})
            key='x'*193;j.begin_effect(key,{})
            self.assertEqual(j.history('effect:'+key)['items'][0]['value']['status'],'pending')

    def test_verified_backup_authentication_and_safe_retention(self):
        from worldmodel.execution_journal import ExecutionJournal
        key=b'x'*32
        with ExecutionJournal(self.path,signing_key=key) as j:
            j.configure_quota('calls',3);j.reserve('calls',1,'a')
            j.begin_effect('external',{});j.mark_uncertain('external','timeout')
            for n in range(4):j.append('history',{'n':n});j.save_checkpoint('cp',self.checkpoint())
            j.prune('history',keep_last=1,limit=10)
            j.prune('cp',kind='checkpoints',keep_last=1,limit=10)
            self.assertEqual(j.history('history')['items'][0]['sequence'],4)
            self.assertEqual(j.append('history',{'n':4}),5)
            with self.assertRaises(ValueError):j.prune('effect:external',keep_last=1)
            self.assertEqual(j.quota('calls')['used'],1)
            j.verify();usage=j.storage_usage();self.assertIn('database_bytes',usage)
            exported=j.backup(self.path.with_name('export.sqlite'))
        with ExecutionJournal.from_backup(exported['manifest'],signing_key=key) as j:
            self.assertEqual(j.quota('calls')['used'],1)
            self.assertEqual(j.effects()['items'][0]['status'],'uncertain')
        with self.assertRaises(ValueError):ExecutionJournal.from_backup(exported['manifest'],signing_key=b'y'*32)
        with open(exported['database'],'ab') as f:f.write(b'changed')
        with self.assertRaises(ValueError):ExecutionJournal.from_backup(exported['manifest'],signing_key=key)

    def test_recomputed_payload_hash_does_not_bypass_hmac(self):
        from worldmodel.execution_journal import ExecutionJournal
        from worldmodel.util import canonical,digest
        with ExecutionJournal(self.path,signing_key=b'x'*32) as j:
            j.append('history',{'x':1})
            corrupt={'x':2};text=canonical(corrupt).decode()
            j.db.execute('UPDATE entries SET payload=?,checksum=?,bytes=?',(text,digest(corrupt),len(text)))
            with self.assertRaises(ValueError):j.history('history')
