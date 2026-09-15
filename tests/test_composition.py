import copy
import json
import unittest
from worldmodel.composition import CompositionEvaluator, materialize_composition, register_composition_process


def config():
    return {'start':'2026-01-01T00:00:00Z','step_seconds':1,'coordinate_system':{'kind':'cartesian','axes':['x','y'],'unit':'m'},
            'world':{'measure_unit':'m2','cells':[{'id':'cell:depot','measure':2},{'id':'cell:buyer','measure':1}],
                     'fields':{'resource':{'kind':'extensive','unit':'kg','values':{'cell:depot':10,'cell:buyer':0}}}},
            'accounts':{'government':1000,'buyer':0,'producer':0},'roles':{'government':'government','buyer':'buyer','producer':'producer'},
            'policy_observations':[{'value':100,'unit':'cent','information_time':'2026-01-01T00:00:00Z','valid_time':'2026-01-01T00:00:00Z'}],
            'policy_contract':{'unit':'cent','lag_seconds':0,'max_age_seconds':10,'missing':'error'},'price_cents_per_kg':20,
            'steps':[{'source':'cell:depot','target':'cell:buyer','demand_kg':2,'events':[{'type':'split','cell':'cell:depot','children':[{'id':'cell:d1','measure':1},{'id':'cell:d2','measure':1}],'edges':[]}]},
                     {'source':'cell:d1','target':'cell:buyer','demand_kg':2,'events':[]}],
            'observations':{'accounts':['buyer'],'cells':['cell:buyer']}}


class CompositionTests(unittest.TestCase):
    def test_crossdomain_conservation_lifecycle_restricted_observation_and_restore(self):
        request=config();before=copy.deepcopy(request)
        with CompositionEvaluator(request) as evaluator:
            first=evaluator.step();cp=json.loads(json.dumps(evaluator.checkpoint()))
            self.assertEqual(first['observation']['accounts'],{'buyer':60})
            self.assertNotIn('government',first['observation']['accounts'])
            self.assertEqual(first['observation']['resources'],{'cell:buyer':2})
            final=evaluator.step();expected=evaluator.result()
            evaluator.restore(cp);evaluator.step();self.assertEqual(evaluator.result(),expected)
            self.assertEqual(final['conservation']['money_cents'],1000)
            self.assertEqual(final['conservation']['resource_kg'],10)
            self.assertNotIn('cell:depot',evaluator.result()['state']['fields']['resource']['values'])
        self.assertEqual(request,before)
        self.assertEqual(materialize_composition(request),expected)

    def test_failed_lifecycle_rolls_back_entire_policy_purchase_step(self):
        request=config();request['steps'][0]['events']=[{'type':'death','cell':'cell:depot'}]
        with CompositionEvaluator(request) as evaluator:
            before=evaluator.result()
            with self.assertRaises(ValueError):evaluator.step()
            self.assertEqual(evaluator.result(),before)

    def test_missing_policy_does_not_become_zero_and_fidelity_agrees(self):
        request=config();request['policy_observations']=[]
        with CompositionEvaluator(request) as evaluator:
            with self.assertRaises(ValueError):evaluator.step()
        simple=materialize_composition(config(),fidelity='aggregate');fine=materialize_composition(config(),fidelity='unit')
        self.assertEqual(simple['accounts'],fine['accounts'])
        self.assertEqual(simple['state'],fine['state'])
        self.assertGreater(fine['execution']['transfers'],simple['execution']['transfers'])

    def test_wrapper_honors_outer_information_cutoff(self):
        from worldmodel.processes import ProcessRegistry
        registry=ProcessRegistry();register_composition_process(registry)
        model=config();model['policy_observations'].append({'value':500,'unit':'cent',
            'information_time':'2026-01-01T00:00:01Z','valid_time':'2026-01-01T00:00:01Z'})
        prediction=registry.predict('cross_domain_composition.reference',{'composition_config':{'value':model,'unit':'composition-config'}},{},
            {'dt_seconds':1,'time':'2026-01-01T00:00:01Z','known_at':'2026-01-01T00:00:00Z','state':{'composition_state':{'completed_steps':1}}})
        self.assertEqual(prediction['pressures'][0]['value']['frames'][-1]['policy_input']['value'],100)

    def test_process_wrapper_rejects_hidden_internal_clock_mismatch(self):
        from worldmodel.processes import ProcessRegistry
        registry=ProcessRegistry();register_composition_process(registry)
        inputs={'composition_config':{'value':config(),'unit':'composition-config'}}
        for time,dt in [('2026-01-01T00:00:01Z',1),('2026-01-01T00:00:00Z',2)]:
            with self.assertRaisesRegex(ValueError,'clock|cadence'):
                registry.predict('cross_domain_composition.reference',inputs,{},
                    {'dt_seconds':dt,'time':time,'state':{'composition_state':{'completed_steps':0}}})

    def test_nonfinite_total_is_rejected_before_opening_store(self):
        from unittest.mock import patch
        request=config();request['world']['fields']['resource']['values']={'cell:depot':1e308,'cell:buyer':1e308}
        with patch('worldmodel.composition.SpatialStore') as store:
            with self.assertRaises(ValueError):CompositionEvaluator(request)
            store.assert_not_called()

    def test_budget_preflight_and_checkpoint_corruption(self):
        request=config();request['steps'][0]['demand_kg']=100001
        with self.assertRaises(ValueError):CompositionEvaluator(request,fidelity='unit')
        with CompositionEvaluator(config()) as evaluator:
            checkpoint=evaluator.checkpoint();checkpoint['completed_steps']=1
            with self.assertRaises(ValueError):evaluator.restore(checkpoint)


class CompositionMaterializationTests(unittest.TestCase):
    def test_registered_composition_matches_reference_and_checkpoint(self):
        from tests.test_materialize import MaterializeTests
        from worldmodel.processes import ProcessRegistry
        from worldmodel.materialize import materialize
        from worldmodel.checkpoints import CheckpointEvaluator
        fixture=MaterializeTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        registry=ProcessRegistry();register_composition_process(registry)
        model=config();model['start']='2024-01-01T00:00:00Z'
        model['policy_observations'][0].update(information_time=model['start'],valid_time=model['start'])
        target={'entity':'asset:a','variable':'composition_state'}
        request={'start':model['start'],'end':'2024-01-01T00:00:02Z','known_at':model['start'],'step_seconds':1,
                 'targets':[target],'initial_state':[{**target,'value':{'completed_steps':0},'unit':'composition-state'}],
                 'bindings':[{'id':'composition','process_id':'cross_domain_composition','cadence_seconds':1,
                              'inputs':{'composition_config':{'value':model,'unit':'composition-config'}},'outputs':{'composition_state':target}}]}
        replay=materialize(fixture.store,fixture.graph,request,registry)
        self.assertEqual(replay['snapshots'][-1]['value'],materialize_composition(model,known_at=request['known_at']))
        evaluator=CheckpointEvaluator(fixture.store,fixture.graph,request,1,registry)
        evaluator.reset();evaluator([{'inputs':[]}],0);saved=evaluator.checkpoint()
        expected=evaluator([{'inputs':[]}]*2,0)
        evaluator.restore(saved);restored=evaluator([{'inputs':[]}]*2,0)
        self.assertEqual(expected['snapshots'],restored['snapshots'])
        self.assertEqual(expected['snapshots'],replay['snapshots'])
        self.assertIn('approximation',replay['plan']['fidelity_selection'][0])
        from worldmodel.environments import Environment
        request['initial_state'][0]['value']=materialize_composition(model,steps=0,known_at=request['known_at'])
        limited=CheckpointEvaluator(fixture.store,fixture.graph,request,1,registry)
        selector={**target,'unit':'composition-state','path':['accounts','buyer']}
        env=Environment(limited,{'actions':{},'observations':{'buyer_cash':selector},'reward':{'terms':[]},'max_steps':2})
        self.assertEqual(env.reset()[0],{'buyer_cash':0})
        observation,_,_,_,info=env.step({})
        self.assertEqual(observation,{'buyer_cash':60})
        self.assertNotIn('accounts',info)
