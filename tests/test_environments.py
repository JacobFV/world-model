import unittest
from copy import deepcopy
from worldmodel.environments import Environment


def spec():
    return {'actions':{'supply':{'binding':'flow','port':'inflow','type':'number','unit':'barrel/second','minimum':0,'maximum':10}},
            'observations':{'stock':{'entity':'resource:a','variable':'inventory','unit':'barrel'}},
            'reward':{'terms':[{'selector':{'entity':'resource:a','variable':'inventory','unit':'barrel'},'mode':'delta','weight':1,'scale':2}]},
            'max_steps':3,'max_evaluations':5}


def evaluate(history,seed):
    stock=10+sum(item['inputs'][0]['value'] for item in history)
    return {'snapshots':[{'time':str(len(history)),'entity':'resource:a','variable':'inventory','value':stock,'unit':'barrel'},
                         {'time':str(len(history)),'entity':'resource:hidden','variable':'inventory','value':99,'unit':'barrel'}]}


class EnvironmentTests(unittest.TestCase):
    def test_action_input_observation_output_and_delta_reward(self):
        env=Environment(evaluate,spec());obs,info=env.reset(seed=7)
        self.assertEqual(obs,{'stock':10})
        obs,reward,terminated,truncated,info=env.step({'supply':4})
        self.assertEqual(obs,{'stock':14});self.assertEqual(reward,2);self.assertFalse(terminated or truncated)
        self.assertEqual(env.history[0]['inputs'][0]['port'],'inflow')
        self.assertEqual(env.reset(seed=7)[0],{'stock':10})

    def test_bad_actions_and_missing_reward_state_do_not_commit(self):
        env=Environment(evaluate,spec());env.reset()
        for action in ({'supply':11},{'supply':True},{'other':1}):
            with self.assertRaises(ValueError):env.step(action)
        self.assertEqual(env.history,[])
        def broken(history,seed):
            result=evaluate(history,seed)
            if history:result['snapshots'][0]['unit']='USD'
            return result
        env=Environment(broken,spec());env.reset()
        with self.assertRaises(ValueError):env.step({'supply':2})
        self.assertEqual(env.history,[])

    def test_horizon_termination_and_no_hidden_state_in_info(self):
        request=spec();request['max_steps']=1
        env=Environment(evaluate,request);env.reset()
        obs,_,_,truncated,info=env.step({'supply':1})
        self.assertTrue(truncated);self.assertNotIn('snapshots',info)
        with self.assertRaises(ValueError):env.step({'supply':1})
        request=spec();request['termination']={'selector':request['observations']['stock'],'operator':'gte','value':11}
        env=Environment(evaluate,request);env.reset()
        self.assertTrue(env.step({'supply':1})[2])

    def test_mutable_inputs_and_outputs_are_copied(self):
        request=spec();env=Environment(evaluate,request);request['actions'].clear()
        env.reset();env.step({'supply':2});h=env.history;h.clear()
        self.assertEqual(len(env.history),1)

class TemporalEnvironmentTests(unittest.TestCase):
    def test_replay_preserves_state_and_action_history(self):
        from tests import test_materialize
        from worldmodel.environments import TemporalEvaluator
        fixture=test_materialize.MaterializeTests();fixture.setUp()
        try:
            request=deepcopy(fixture.request);request['bindings'][0]['cadence_seconds']=2
            adapter=TemporalEvaluator(fixture.store,fixture.graph,request,2,fixture.registry)
            options={'actions':{'flow':{'binding':'a_flow','port':'flow','type':'number','unit':'unit/second','minimum':0,'maximum':10}},
                     'observations':{'stock':{'entity':'asset:a','variable':'stock','unit':'unit'}},
                     'reward':{'terms':[{'selector':{'entity':'asset:a','variable':'stock','unit':'unit'},'mode':'delta','weight':1,'scale':1}]},'max_steps':3}
            env=Environment(adapter,options);env.reset(seed=9)
            self.assertEqual(env.step({'flow':1})[:2],({'stock':12},2))
            self.assertEqual(env.step({'flow':3})[:2],({'stock':18},6))
            self.assertEqual([r['value'] for r in env.materialization['snapshots']],[10,12,18])
            env.reset(seed=9)
            self.assertEqual(env.step({'flow':1})[:2],({'stock':12},2))
            with self.assertRaisesRegex(ValueError,'cadence'):
                TemporalEvaluator(fixture.store,fixture.graph,request,3,fixture.registry)
        finally:fixture.tearDown()

class BudgetTests(unittest.TestCase):
    def test_budget_truncates_without_committing_unexecuted_action(self):
        from worldmodel.environments import BudgetExceeded
        def limited(history,seed):
            if len(history)>1:raise BudgetExceeded('work exhausted')
            return evaluate(history,seed)
        env=Environment(limited,spec());env.reset();env.step({'supply':1})
        observation,reward,terminated,truncated,info=env.step({'supply':2})
        self.assertEqual(observation,{'stock':11});self.assertEqual(reward,0)
        self.assertFalse(terminated);self.assertTrue(truncated);self.assertEqual(len(env.history),1)
        self.assertIn('truncation_reason',info)

    def test_reset_can_exhaust_evaluation_budget(self):
        request=spec();request['max_evaluations']=1
        env=Environment(evaluate,request)
        self.assertTrue(env.reset()[1]['truncated'])
