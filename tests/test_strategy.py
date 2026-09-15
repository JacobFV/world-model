import unittest
from worldmodel.strategy import evaluate_strategies, merge_overrides

class StrategyTests(unittest.TestCase):
    def test_overrides_merge_businesses_by_identity_without_dropping_state(self):
        base={'businesses':[{'id':'a','cash':10,'policy':{'target_days':1}}, {'id':'b','cash':20}]}
        result=merge_overrides(base,{'businesses':[{'id':'a','policy':{'target_days':5}}]})
        self.assertEqual(result['businesses'][0]['cash'],10)
        self.assertEqual(result['businesses'][0]['policy']['target_days'],5)
        self.assertEqual(result['businesses'][1]['cash'],20)
        self.assertEqual(base['businesses'][0]['policy']['target_days'],1)
        with self.assertRaisesRegex(ValueError,'Unknown'):
            merge_overrides(base,{'businesses':[{'id':'invented','cash':100}]})

    def config(self):
        from worldmodel.economy import example_economy
        base=example_economy()
        return {'base':base,'policies':[{'id':'a','overrides':{}},{'id':'b','overrides':{}}],
                'scenarios':[{'id':'baseline','probability':.5,'overrides':{}},
                             {'id':'same','probability':.5,'overrides':{}}],
                'objective':{'metric':'total_profit','direction':'maximize'},
                'constraints':[{'metric':'defaults','op':'<=','value':0}], 'max_runs':4}

    def test_identical_policies_tie_and_report_zero_regret(self):
        result=evaluate_strategies(self.config())
        self.assertEqual(result['runs'],4)
        self.assertEqual(result['ranking'][0]['expected_value'],result['ranking'][1]['expected_value'])
        self.assertEqual(result['ranking'][0]['max_regret'],0)
        self.assertEqual(len(result['outcomes']),4)

    def test_preflight_run_budget_probabilities_and_constraints(self):
        cfg=self.config();cfg['max_runs']=3
        with self.assertRaisesRegex(ValueError,'budget'):
            evaluate_strategies(cfg)
        cfg=self.config();cfg['scenarios'][0]['probability']=1
        with self.assertRaisesRegex(ValueError,'probabilit'):
            evaluate_strategies(cfg)
        cfg=self.config();cfg['constraints']=[{'metric':'total_cash','op':'<=','value':-1}]
        result=evaluate_strategies(cfg)
        self.assertIsNone(result['recommended_policy'])
        self.assertTrue(all(not row['feasible'] for row in result['ranking']))

    def test_policies_cannot_change_environment_or_initial_endowment(self):
        cfg=self.config();cfg['policies'][0]['overrides']={'shocks':[]}
        with self.assertRaisesRegex(ValueError,'Policy'):
            evaluate_strategies(cfg)
        cfg=self.config();cfg['policies'][0]['overrides']={'businesses':[{'id':cfg['base']['businesses'][0]['id'],'cash':1000000}]}
        with self.assertRaisesRegex(ValueError,'Policy'):
            evaluate_strategies(cfg)

    def test_work_budget_counts_each_firm(self):
        cfg=self.config();cfg['base']['days']=1
        cfg['base']['businesses'].append({**cfg['base']['businesses'][0],'id':'second'})
        cfg['max_steps']=4
        with self.assertRaisesRegex(ValueError,'budget'):
            evaluate_strategies(cfg)
