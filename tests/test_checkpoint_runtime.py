import json
import unittest
from copy import deepcopy
from tests.test_materialize import MaterializeTests, constant_rate
from worldmodel.materialize import materialize


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MaterializeTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def evaluator(self, step=2, **kw):
        from worldmodel.checkpoints import CheckpointEvaluator
        f=self.fixture
        return CheckpointEvaluator(f.store, f.graph, f.request, step, f.registry, **kw)

    def test_incremental_calls_and_portable_restore_match_replay(self):
        f=self.fixture; a=self.evaluator(); a([],7); history=[]
        for flow in (1,3):
            history.append({'inputs':[{'binding':'a_flow','port':'flow','value':flow,'unit':'unit/second'}]})
            result=a(history,7)
        self.assertEqual(result['execution']['calls'],2)
        self.assertEqual([x['value'] for x in result['snapshots']],[10,12,18])
        checkpoint=json.loads(json.dumps(a.checkpoint()))
        b=self.evaluator(); b.restore(checkpoint)
        history.append({'inputs':[]})
        self.assertEqual(a(history,7),b(history,7))
        self.assertEqual(a.total_calls,3)
        bad=deepcopy(checkpoint);bad['payload']['elapsed']=999
        with self.assertRaisesRegex(ValueError,'checkpoint|Checkpoint'):b.restore(bad)
        with self.assertRaises(ValueError):self.evaluator(step=1).restore(checkpoint)

    def test_non_aligned_observations_preserve_due_times(self):
        f=self.fixture;a=self.evaluator(step=1);a([],7)
        for n in range(1,11):result=a([{'inputs':[]}]*n,7)
        replay=materialize(f.store,f.graph,{**f.request,'step_seconds':1},f.registry)
        self.assertEqual(result['snapshots'],replay['snapshots'])
        self.assertEqual(result['execution']['calls'],5)

    def test_budget_before_handler_and_transactional_failure(self):
        from worldmodel.environments import BudgetExceeded
        a=self.evaluator(max_total_calls=1);a([],7);a([{'inputs':[]}],7)
        before=a.checkpoint()
        with self.assertRaises(BudgetExceeded):a([{'inputs':[]}]*2,7)
        self.assertEqual(a.checkpoint(),before)
        f=self.fixture;f.request['bindings'][0]['inputs']['flow']['value']=1e308
        a=self.evaluator();a([],7);before=a.checkpoint()
        with self.assertRaises(ValueError):a([{'inputs':[]}],7)
        self.assertEqual(a.checkpoint(),before)

    def test_stochastic_state_survives_restore(self):
        f=self.fixture
        def stochastic(inputs,parameters,context):
            return constant_rate({'flow':{'value':context['rng'].random()}},parameters,context)
        f.registry.register_implementation({'id':'flow.random','process_id':'flow','fidelity':'stochastic',
            'max_step_seconds':2,'cost_per_call':1,'description':'random test'},stochastic)
        f.request['bindings'][0]['fidelity']='stochastic'
        a=self.evaluator();a([],5);a([{'inputs':[]}],5)
        b=self.evaluator();b.restore(json.loads(json.dumps(a.checkpoint())))
        for n in range(2,6):result=b([{'inputs':[]}]*n,5)
        replay=materialize(f.store,f.graph,{**f.request,'seed':5,'step_seconds':2},f.registry)
        self.assertEqual(result['snapshots'],replay['snapshots'])

    def test_end_of_step_prediction_waits_until_cadence(self):
        f=self.fixture
        def discrete(inputs,parameters,context):
            return {'pressures':[{'port':'stock','mode':'set','value':context['state']['stock']+context['dt_seconds'],
                'unit':'unit','strength':1,'confidence':1}]}
        f.registry.register_implementation({'id':'flow.discrete','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'test','output_timing':'end_of_step'},discrete)
        f.request['bindings'][0]['implementation_id']='flow.discrete'
        a=self.evaluator(step=1);a([],0)
        values=[]
        for n in range(1,5):values.append(a([{'inputs':[]}]*n,0)['snapshots'][-1]['value'])
        self.assertEqual(values,[10,12,12,14]);self.assertEqual(a.total_calls,2)

    def test_environment_selector_failure_rolls_back_evaluator(self):
        from worldmodel.environments import Environment
        a=self.evaluator()
        selector={'entity':'asset:a','variable':'stock','unit':'unit'}
        env=Environment(a,{'actions':{},'observations':{'stock':selector},'reward':{'terms':[]},'max_steps':4})
        env.reset();before=a.checkpoint()
        env.spec['observations']['stock']['unit']='wrong'
        with self.assertRaises(ValueError):env.step({})
        self.assertEqual(a.checkpoint(),before)
        env.spec['observations']['stock']['unit']='unit'
        self.assertEqual(env.step({})[0],{'stock':14})

    def test_live_failure_is_audited_and_never_silently_repeated(self):
        f=self.fixture
        class Backend:
            identity={'provider':'test','model':'explicit'}
            def __init__(self):self.calls=0
            def predict(self, *args, **kwargs):return None
        backend=Backend()
        def live(inputs,parameters,context):
            context['agent_backend'].calls+=1
            context['memory']['count']=context['memory'].get('count',0)+1
            return {'pressures':[{'port':'stock','mode':'rate','value':1e308,
                'unit':'unit','strength':1,'confidence':1}], 'memory':context['memory']}
        f.registry.register_implementation({'id':'flow.live','process_id':'flow','fidelity':'agent',
            'max_step_seconds':2,'cost_per_call':1,'description':'test'},live)
        f.request['bindings'][0]['fidelity']='agent'
        a=self.evaluator(agent_backend=backend);a([],0);before=a.checkpoint()
        with self.assertRaises(ValueError):a([{'inputs':[]}],0)
        self.assertEqual(a.checkpoint(),before);self.assertEqual(backend.calls,1)
        self.assertEqual(a.audit[0]['status'],'returned')
        with self.assertRaisesRegex(ValueError,'Backend transition failed'):a([{'inputs':[]}],0)
        self.assertEqual(backend.calls,1)
        with self.assertRaisesRegex(ValueError,'exactly-once'):a.restore(before)

    def test_live_memory_persists_without_prefix_replay(self):
        f=self.fixture
        class Backend:
            identity={'provider':'test','model':'explicit'}
            def __init__(self):self.calls=0
            def predict(self, *args, **kwargs):return None
        backend=Backend()
        def live(inputs,parameters,context):
            context['agent_backend'].calls+=1
            memory={'count':context['memory'].get('count',0)+1}
            prediction=constant_rate({'flow':{'value':memory['count']}},parameters,context)
            return {**prediction,'memory':memory}
        f.registry.register_implementation({'id':'flow.live','process_id':'flow','fidelity':'agent',
            'max_step_seconds':2,'cost_per_call':1,'description':'test'},live)
        f.request['bindings'][0]['fidelity']='agent'
        a=self.evaluator(agent_backend=backend);a([],0)
        a([{'inputs':[]}],0);result=a([{'inputs':[]}]*2,0)
        self.assertEqual(backend.calls,2)
        self.assertEqual(result['execution']['agent_memories'],{'asset:a':{'count':2}})
        self.assertEqual(result['snapshots'][-1]['value'],16)

    def test_short_final_discrete_step_is_applied_in_reference(self):
        f=self.fixture
        def discrete(inputs,parameters,context):
            return {'pressures':[{'port':'stock','mode':'set','value':context['state']['stock']+context['dt_seconds'],
                'unit':'unit','strength':1,'confidence':1}]}
        f.registry.register_implementation({'id':'flow.discrete','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'test','output_timing':'end_of_step'},discrete)
        f.request['bindings'][0]['implementation_id']='flow.discrete'
        f.request['end']='2024-01-01T00:00:03Z'
        result=materialize(f.store,f.graph,{**f.request,'step_seconds':1},f.registry)
        self.assertEqual([r['value'] for r in result['snapshots']],[10,10,12,13])
        a=self.evaluator(step=1);a([],7)
        for n in range(1,4):incremental=a([{'inputs':[]}]*n,7)
        self.assertEqual(incremental['snapshots'],result['snapshots'])

    def test_restore_revalidates_literal_types_and_trace_accounting(self):
        from worldmodel.util import digest
        a=self.evaluator();a([],0);a([{'inputs':[]}],0)
        for field in ('literal','trace'):
            checkpoint=a.checkpoint()
            if field=='literal':checkpoint['payload']['literals']['a_flow']['flow']['unit']='wrong'
            else:checkpoint['payload']['trace']=[]
            checkpoint.pop('checksum');checkpoint['checksum']=digest(checkpoint)
            with self.assertRaisesRegex(ValueError,'Checkpoint'):self.evaluator().restore(checkpoint)

    def test_checkpoint_inputs_reject_python_only_nested_values_before_execution(self):
        f=self.fixture
        f.registry.register_process({'id':'object_flow','inputs':{'flow':{'type':'object','unit':'unit/second'}},
            'outputs':{'stock':{'type':'number','unit':'unit'}},'description':'object flow','topology':'stock'})
        def object_flow(inputs,parameters,context):
            return constant_rate({'flow':{'value':inputs['flow']['value'].get(1,0)}},parameters,context)
        f.registry.register_implementation({'id':'object.det','process_id':'object_flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'object test'},object_flow)
        f.request['bindings'][0]['process_id']='object_flow'
        for invalid in ({1:7},{'nested':(1,2)},{'nested':[{False:3}]}):
            f.request['bindings'][0]['inputs']['flow']['value']=invalid
            with self.assertRaisesRegex(ValueError,'JSON-native'):self.evaluator()
        f.request['bindings'][0]['inputs']['flow']['value']={}
        a=self.evaluator();a([],0);before=a.checkpoint()
        for invalid in ({1:7},{'nested':(1,2)}):
            history=[{'inputs':[{'binding':'a_flow','port':'flow','unit':'unit/second','value':invalid}]}]
            with self.assertRaisesRegex(ValueError,'JSON-native'):a(history,0)
            self.assertEqual(a.checkpoint(),before);self.assertEqual(a.attempted_calls,0)

    def test_prediction_and_restore_reject_python_only_nested_values(self):
        f=self.fixture
        def invalid_prediction(inputs,parameters,context):
            return {**constant_rate(inputs,parameters,context),'diagnostics':{'nested':(1,2)}}
        f.registry.register_implementation({'id':'flow.invalid','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'invalid test'},invalid_prediction)
        f.request['bindings'][0]['implementation_id']='flow.invalid'
        a=self.evaluator();a([],0);before=a.checkpoint()
        with self.assertRaisesRegex(ValueError,'JSON-native'):a([{'inputs':[]}],0)
        self.assertEqual(a.checkpoint(),before)
        checkpoint=a.checkpoint();checkpoint['payload']['history']=()
        with self.assertRaisesRegex(ValueError,'JSON-native'):a.restore(checkpoint)

    def test_end_of_step_rejects_continuous_pressures_in_both_evaluators(self):
        f=self.fixture
        f.registry.register_implementation({'id':'flow.end','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'invalid timing','output_timing':'end_of_step'},constant_rate)
        f.request['bindings'][0]['implementation_id']='flow.end'
        a=self.evaluator(step=1);a([],0);before=a.checkpoint()
        with self.assertRaisesRegex(ValueError,'end_of_step.*set'):a([{'inputs':[]}],0)
        self.assertEqual(a.checkpoint(),before)
        with self.assertRaisesRegex(ValueError,'end_of_step.*set'):
            materialize(f.store,f.graph,f.request,f.registry)

    def test_simultaneous_pending_cost_is_reserved_identically(self):
        f=self.fixture
        def budget_rate(inputs,parameters,context):
            return constant_rate({'flow':{'value':context['remaining_budget']}},parameters,context)
        f.registry.register_implementation({'id':'flow.budget','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'budget aware'},budget_rate)
        f.request['end']='2024-01-01T00:00:02Z'
        f.request['bindings'][0]['implementation_id']='flow.budget'
        second=deepcopy(f.request['bindings'][0]);second['id']='b_flow';second['entity_id']='asset:b'
        second['outputs']['stock']['entity']='asset:b';f.request['bindings'].append(second)
        f.request['targets'].append({'entity':'asset:b','variable':'stock'})
        a=self.evaluator();a([],7);result=a([{'inputs':[]}],7)
        replay=materialize(f.store,f.graph,{**f.request,'step_seconds':2},f.registry)
        self.assertEqual(result['snapshots'],replay['snapshots'])
        self.assertEqual(result['snapshots'][-1]['value'],218)

    def test_restore_revalidates_held_pressure_contract_and_due_schedule(self):
        from worldmodel.util import digest
        a=self.evaluator(step=1);a([],0);a([{'inputs':[]}],0);before=a.checkpoint()
        for field,value in (('unit','invalid'),('port','missing'),('value','bad'),('mode','missing')):
            checkpoint=a.checkpoint();checkpoint['payload']['held']['a_flow'][0][field]=value
            checkpoint['payload']['trace'][-1]['prediction']['pressures'][0][field]=value
            checkpoint.pop('checksum');checkpoint['checksum']=digest(checkpoint)
            with self.assertRaises(ValueError):a.restore(checkpoint)
            self.assertEqual(a.checkpoint(),before)
        checkpoint=a.checkpoint();checkpoint['payload']['next_due']['a_flow']=3
        checkpoint.pop('checksum');checkpoint['checksum']=digest(checkpoint)
        with self.assertRaises(ValueError):a.restore(checkpoint)
        self.assertEqual(a.checkpoint(),before)
