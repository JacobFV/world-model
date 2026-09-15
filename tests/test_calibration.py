import unittest
from worldmodel.calibration import fit_ar1

class CalibrationTests(unittest.TestCase):
    def rows(self):
        return [{'time':f'2024-01-{i+1:02d}', 'value':2*i+3,'unit':'USD',
                 'evidence':[{'record_id':f'row:{i}'}]} for i in range(10)]

    def test_fit_uses_only_training_pairs_and_scores_future_one_step(self):
        result=fit_ar1(self.rows(),'2024-01-06')
        self.assertAlmostEqual(result['parameters']['intercept'],2)
        self.assertAlmostEqual(result['parameters']['coefficient'],1)
        self.assertAlmostEqual(result['holdout']['mae'],0)
        self.assertEqual(result['holdout']['persistence_mae'],2)
        self.assertEqual(result['training']['pairs'],5)
        self.assertEqual(result['holdout']['count'],4)
        changed=self.rows(); changed[-1]['value']=9999
        self.assertEqual(fit_ar1(changed,'2024-01-06')['parameters'],result['parameters'])

    def test_missing_and_duplicate_times_are_explicit(self):
        rows=self.rows();rows[2]['value']=None
        self.assertEqual(fit_ar1(rows,'2024-01-06')['excluded_missing'],1)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            fit_ar1(self.rows()+[self.rows()[0]],'2024-01-06')
        with self.assertRaisesRegex(ValueError,'unit'):
            rows=self.rows();rows[-1]['unit']='EUR';fit_ar1(rows,'2024-01-06')

    def test_training_variation_and_holdout_are_required(self):
        with self.assertRaisesRegex(ValueError,'holdout'):
            fit_ar1(self.rows(),'2024-01-20')
        with self.assertRaisesRegex(ValueError,'variation'):
            fit_ar1([{**r,'value':1} for r in self.rows()],'2024-01-06')

    def test_interleaved_series_cannot_be_fitted_together(self):
        rows=[{**row,'subject':'series:a' if i%2 else 'series:b'} for i,row in enumerate(self.rows())]
        with self.assertRaisesRegex(ValueError,'series'):
            fit_ar1(rows,'2024-01-06')
