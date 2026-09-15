import unittest
from worldmodel.validation import assess_holdout, parameter_sweep

class ValidationTests(unittest.TestCase):
    def test_scores_recomputed_and_temporal_leakage_rejected(self):
        report={'training':{'through':'2025-01-01'},'predictions':[
            {'time':'2025-01-02','input_time':'2025-01-01','actual':2,'prediction':2,'persistence':1},
            {'time':'2025-01-03','input_time':'2025-01-02','actual':3,'prediction':3,'persistence':2}]}
        result=assess_holdout(report,min_count=2)
        self.assertTrue(result['passes']);self.assertFalse(result['causally_validated'])
        report['predictions'][0]['input_time']='2025-01-03'
        with self.assertRaises(ValueError):assess_holdout(report,min_count=2)

    def test_sensitivity_is_bounded_and_does_not_change_baseline(self):
        baseline={'x':2}
        r=parameter_sweep(lambda c:{'y':c['x']**2},baseline,[{'path':['x'],'values':[1,2,3],'unit':'unit'}],
                          {'score':{'path':['y'],'unit':'unit_squared'}},max_runs=3)
        self.assertEqual([r['metrics']['score'] for r in r['runs']],[1,4,9])
        self.assertEqual(baseline,{'x':2})
        with self.assertRaises(ValueError):parameter_sweep(lambda c:c,baseline,[{'path':['x'],'values':[1,2]}],{},max_runs=1)

    def test_nonfinite_errors_are_rejected(self):
        report={'training':{'through':'2025-01-01'},'predictions':[
            {'time':'2025-01-02','input_time':'2025-01-01','actual':1e308,'prediction':-1e308,'persistence':0}]}
        with self.assertRaises(ValueError):assess_holdout(report,min_count=2)
