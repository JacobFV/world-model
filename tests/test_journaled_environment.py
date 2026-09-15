from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests import test_materialize


class JournaledEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.f=test_materialize.MaterializeTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        from worldmodel.execution_journal import ExecutionJournal
        self.j=ExecutionJournal(self.f.root/'journal.sqlite');self.addCleanup(self.j.close)
        self.spec={'actions':{'flow':{'binding':'a_flow','port':'flow','type':'number','unit':'unit/second','minimum':0,'maximum':10}},
            'observations':{'stock':{'entity':'asset:a','variable':'stock','unit':'unit'}},
            'reward':{'terms':[]},'max_steps':5}

    def make(self,**kwargs):
        from worldmodel.checkpoints import CheckpointEvaluator
        from worldmodel.environments import Environment
        from worldmodel.journaled_environment import JournaledEnvironment
        evaluator=CheckpointEvaluator(self.f.store,self.f.graph,self.f.request,2,self.f.registry)
        return JournaledEnvironment(Environment(evaluator,self.spec),self.j,'episode',seed=7,**kwargs)

    def test_resume_deduplicates_actions_and_binds_configuration(self):
        first=self.make();out=first.step({'flow':3},action_id='one')
        self.assertEqual(out[0],{'stock':16});self.assertEqual(first.step({'flow':3},action_id='one'),out)
        resumed=self.make(resume=True)
        self.assertEqual(resumed.step({'flow':3},action_id='one'),out)
        self.assertEqual(resumed.evaluate.total_calls,1)
        self.assertEqual(resumed.step({'flow':1},action_id='two')[0],{'stock':18})
        with self.assertRaises(ValueError):resumed.step({'flow':2},action_id='one')
        self.spec['max_steps']=4
        with self.assertRaises(ValueError):self.make(resume=True)

    def test_failed_persistence_rolls_back_and_retry_consumes_new_quota(self):
        env=self.make();before=env.evaluate.checkpoint()
        with patch.object(env,'_commit',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):env.step({'flow':3},action_id='one')
        self.assertEqual(env.evaluate.checkpoint(),before);self.assertEqual(env.history,[])
        self.assertEqual(self.j.quota(env.quota_name)['used'],1)
        self.assertEqual(env.step({'flow':3},action_id='one')[0],{'stock':16})
        self.assertEqual(self.j.quota(env.quota_name)['used'],2)

    def test_lost_commit_response_and_quota_exhaustion(self):
        from worldmodel.execution_journal import QuotaExceeded
        env=self.make(max_process_calls=1);commit=env._commit
        def lost(*args,**kwargs):commit(*args,**kwargs);raise OSError('lost response')
        with patch.object(env,'_commit',side_effect=lost):
            with self.assertRaises(OSError):env.step({'flow':1},action_id='one')
        resumed=self.make(resume=True,max_process_calls=1)
        self.assertEqual(resumed.step({'flow':1},action_id='one')[0],{'stock':12})
        with self.assertRaises(QuotaExceeded):resumed.step({'flow':1},action_id='two')
        self.assertEqual(resumed.evaluate.total_calls,1)

    def test_crashed_claim_requires_explicit_resume_and_rejects_bad_environment_state(self):
        env=self.make();env._claim('one',{'flow':2},1)
        with self.assertRaises(ValueError):env.step({'flow':2},action_id='one')
        resumed=self.make(resume=True)
        self.assertEqual(resumed.step({'flow':2},action_id='one')[0],{'stock':14})
        cp=self.j.load_checkpoint(resumed.stream);cp['payload']['environment']['done']='false'
        from worldmodel.util import digest
        cp['checksum']=digest({k:v for k,v in cp.items() if k!='checksum'})
        self.j.save_checkpoint(resumed.stream,cp)
        with self.assertRaises(ValueError):self.make(resume=True)

    def test_journaled_live_backend_resume_and_restore_capability(self):
        from worldmodel.journaled_environment import JournaledEnvironment,JournaledBackend
        from worldmodel.environments import Environment
        from worldmodel.checkpoints import CheckpointEvaluator
        class Provider:
            identity={'provider':'test','model':'literal'};supports_idempotency=True
            def __init__(self):self.keys=[]
            def predict(self,request,*,idempotency_key):
                self.keys.append(idempotency_key)
                return test_materialize.constant_rate({'flow':{'value':2}},None,None)
        provider=Provider()
        def live(inputs,parameters,context):return context['agent_backend'].predict({'time':context['time'],'inputs':inputs})
        self.f.registry.register_implementation({'id':'flow.live','process_id':'flow','fidelity':'agent',
            'max_step_seconds':2,'cost_per_call':1,'description':'journal live'},live)
        self.f.request['bindings'][0]['fidelity']='agent'
        def create(resume=False):
            backend=JournaledBackend(provider,self.j,'live')
            evaluator=CheckpointEvaluator(self.f.store,self.f.graph,self.f.request,2,self.f.registry,agent_backend=backend)
            return JournaledEnvironment(Environment(evaluator,self.spec),self.j,'live',resume=resume)
        env=create()
        with patch.object(env,'_commit',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):env.step({'flow':2},action_id='one')
        self.assertEqual(len(provider.keys),1)
        env.step({'flow':2},action_id='one');cp=env.evaluate.checkpoint()
        with self.assertRaises(ValueError):env.evaluate.restore(cp)
        with self.assertRaises(ValueError):env.evaluate.restore(cp,journal_capability=object())
        resumed=create(True)
        with self.assertRaises(ValueError):resumed.evaluate.restore(cp,journal_capability=env._capability)
        resumed.step({'flow':2},action_id='one')
        self.assertEqual(len(provider.keys),1)
        resumed.step({'flow':2},action_id='two');self.assertEqual(len(provider.keys),2)

    def test_provider_uncertainty_blocks_resume_without_repeating(self):
        from worldmodel.journaled_environment import JournaledBackend
        from worldmodel.execution_journal import EffectUnresolved
        class Provider:
            identity={'provider':'test'};supports_idempotency=True
            def __init__(self):self.calls=0
            def predict(self,request,*,idempotency_key):self.calls+=1;raise TimeoutError('ambiguous')
        provider=Provider();backend=JournaledBackend(provider,self.j,'external');backend.begin_action('a')
        with self.assertRaises(TimeoutError):backend.predict({'x':1})
        backend.begin_action('a')
        with self.assertRaises(EffectUnresolved):backend.predict({'x':1})
        self.assertEqual(provider.calls,1);self.assertTrue(backend.unresolved())

    def test_episode_publishes_verified_immutable_journal_snapshot(self):
        env=self.make();env.step({'flow':1},action_id='one')
        ref=env.publish(self.f.store,'journal_episode',request={'explicit':'scenario'})
        self.assertTrue(self.f.store.verify(ref))
        from worldmodel.artifacts import load_report
        report=load_report(self.f.store,ref)
        raw=report['journal_snapshot'];self.f.store.artifact(raw)
        self.assertIn(raw,self.f.store.manifest(ref)['raw_inputs'])
        self.assertEqual(report['request'],{'explicit':'scenario'})
        self.assertEqual(report['frames'][-1]['outcome'][0],{'stock':12})

    def test_cli_requires_explicit_journal_session_and_resume_flags(self):
        from worldmodel.cli import parser
        args=parser().parse_args(['environment','graph','--request','request.json',
                                 '--journal','runtime.sqlite','--session','episode','--resume'])
        self.assertEqual(args.session,'episode');self.assertTrue(args.resume)

    def test_stored_outcome_and_evaluation_counter_are_validated_before_resume(self):
        from worldmodel.util import digest
        env=self.make();env.step({'flow':1},action_id='one')
        original=self.j.load_checkpoint(env.stream)
        for kind in ('counter','observation'):
            cp=deepcopy(original)
            if kind=='counter':cp['payload']['environment']['evaluations']=1
            else:cp['payload']['last_outcome'][0]['stock']=999
            cp['checksum']=digest({k:v for k,v in cp.items() if k!='checksum'})
            self.j.save_checkpoint(env.stream,cp)
            with self.assertRaises(ValueError):self.make(resume=True)

    def test_cli_resume_reuses_committed_actions_and_publishes_audit(self):
        import json
        from types import SimpleNamespace
        from worldmodel.environment_cli import execute
        request={'materialization':self.f.request,'environment':self.spec,'step_seconds':2,
                 'seed':7,'actions':[{'flow':1}]}
        path=self.f.root/'episode.json';path.write_text(json.dumps(request))
        args=SimpleNamespace(command='environment',reference='graph',backend='checkpoint',dataset='episode_cli',
            request=path,journal=self.f.root/'cli.sqlite',session='cli',resume=False)
        with patch('worldmodel.checkpoints.default_registry',return_value=self.f.registry):
            first=execute(args,None,self.f.store,None,lambda *args:self.f.graph)
            args.resume=True
            second=execute(args,None,self.f.store,None,lambda *args:self.f.graph)
        self.assertEqual(first['quota']['used'],1);self.assertEqual(second['quota']['used'],1)
        self.assertEqual(first['frames'],second['frames'])
        self.assertTrue(self.f.store.verify(second['artifact']))

    def test_resume_reset_reports_current_state_without_rewinding(self):
        env=self.make();env.step({'flow':3},action_id='one')
        resumed=self.make(resume=True)
        observation,info=resumed.reset(seed=7)
        self.assertEqual(observation,{'stock':16});self.assertEqual(info['step'],1)
        self.assertEqual(len(resumed.history),1)

    def test_partial_provider_success_blocks_takeover_until_reconciled_then_reuses_results(self):
        from worldmodel.journaled_environment import JournaledEnvironment,JournaledBackend
        from worldmodel.environments import Environment
        from worldmodel.checkpoints import CheckpointEvaluator
        from worldmodel.execution_journal import ExecutionJournal,EffectUnresolved
        class Provider:
            identity={'provider':'partial-test'};supports_idempotency=True
            def __init__(self):self.calls=[]
            def predict(self,request,*,idempotency_key):
                self.calls.append(idempotency_key)
                if request['phase']==2:raise TimeoutError('completion unknown')
                return test_materialize.constant_rate({'flow':{'value':2}},None,None)
        provider=Provider()
        def live(inputs,parameters,context):
            context['agent_backend'].predict({'phase':1,'time':context['time']})
            return context['agent_backend'].predict({'phase':2,'time':context['time']})
        self.f.registry.register_implementation({'id':'flow.live','process_id':'flow','fidelity':'agent',
            'max_step_seconds':2,'cost_per_call':1,'description':'partial effects'},live)
        self.f.request['bindings'][0]['fidelity']='agent'
        def create(journal,resume=False):
            backend=JournaledBackend(provider,journal,'partial')
            evaluator=CheckpointEvaluator(self.f.store,self.f.graph,self.f.request,2,self.f.registry,agent_backend=backend)
            return JournaledEnvironment(Environment(evaluator,self.spec),journal,'partial',resume=resume)
        env=create(self.j)
        self.assertEqual(provider.calls,[]);self.assertEqual(self.j.quota(env.quota_name)['used'],0)
        # A second live instance cannot take over a provider attempt still pending.
        pending=self.j.begin_effect(env.evaluate.backend.prefix+'in-flight',{'phase':'pending'})
        with ExecutionJournal(self.f.root/'journal.sqlite') as other:
            with self.assertRaises(EffectUnresolved):create(other,True)
        self.assertEqual(provider.calls,[])
        self.j.complete_effect(env.evaluate.backend.prefix+'in-flight',{'confirmed':'no invocation'},
                               provider_receipt={'operator':'test boundary'})
        with self.assertRaises(TimeoutError):env.step({'flow':2},action_id='one')
        with ExecutionJournal(self.f.root/'journal.sqlite') as other:
            with self.assertRaises(EffectUnresolved):create(other,True)
            effects=other.effects()['items'];self.assertEqual([e['status'] for e in effects],['completed','completed','uncertain'])
            other.complete_effect(effects[2]['key'],test_materialize.constant_rate({'flow':{'value':2}},None,None),
                                  provider_receipt={'confirmed':'provider-result'})
            resumed=create(other,True)
            self.assertEqual(resumed.step({'flow':2},action_id='one')[0],{'stock':14})
            self.assertEqual(len(provider.calls),2)
            self.assertEqual(other.quota(resumed.quota_name)['used'],2)

    def test_unusable_session_cannot_publish_a_potentially_stale_report(self):
        env=self.make();env._usable=False
        with self.assertRaisesRegex(RuntimeError,'resume'):
            env.publish(self.f.store,'episode_report')

    def test_superseded_live_writer_cannot_start_provider_effect(self):
        from worldmodel.journaled_environment import JournaledEnvironment, JournaledBackend
        from worldmodel.environments import Environment
        from worldmodel.checkpoints import CheckpointEvaluator
        from worldmodel.execution_journal import EffectUnresolved
        class Provider:
            identity = {'provider': 'fenced-test'}
            def __init__(self): self.calls = 0
            def predict(self, request):
                self.calls += 1
                return test_materialize.constant_rate({'flow': {'value': 2}}, None, None)
        provider = Provider()
        def live(inputs, parameters, context):
            return context['agent_backend'].predict({'time': context['time']})
        self.f.registry.register_implementation({'id': 'flow.live', 'process_id': 'flow', 'fidelity': 'agent',
            'max_step_seconds': 2, 'cost_per_call': 1, 'description': 'fenced'}, live)
        self.f.request['bindings'][0]['fidelity'] = 'agent'
        def create(resume=False):
            backend = JournaledBackend(provider, self.j, 'fenced')
            evaluator = CheckpointEvaluator(self.f.store, self.f.graph, self.f.request, 2, self.f.registry, agent_backend=backend)
            return JournaledEnvironment(Environment(evaluator, self.spec), self.j, 'fenced', resume=resume)
        old = create()
        original_step = old.env.step
        resumed = []
        def takeover_then_continue(actions):
            # Pause after old durable claim, before its first effect reservation.
            resumed.append(create(resume=True))
            # Reclaim the same action ID with a newer durable attempt.
            resumed[0]._claim('one', actions, resumed[0]._work())
            return original_step(actions)
        with patch.object(old.env, 'step', side_effect=takeover_then_continue):
            with self.assertRaises(EffectUnresolved): old.step({'flow': 2}, action_id='one')
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.j.effects()['items'], [])
        recovered = create(resume=True)
        self.assertEqual(recovered.step({'flow': 2}, action_id='one')[0], {'stock': 14})
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self.j.quota(old.quota_name)['used'], 3)
        restore = JournaledEnvironment._restore
        def effect_between_restore_and_takeover(instance, checkpoint):
            restore(instance, checkpoint)
            self.j.begin_effect(instance.evaluate.backend.prefix + 'race', {'pending': True})
        with patch.object(JournaledEnvironment, '_restore', effect_between_restore_and_takeover):
            with self.assertRaises(EffectUnresolved): create(resume=True)
        self.assertEqual(provider.calls, 1)
