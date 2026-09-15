import copy
import unittest
from worldmodel.benchmarks import benchmark_series

def rows(values=None):
    return [{'series':'fixture','unit':'points','time':f'2026-01-{i+1:02d}','value':v,'evidence':[{'fixture':i}]} for i,v in enumerate(values or [1,2,3,4,5,6,7,8,9,10])]
class SeriesBenchmarkTests(unittest.TestCase):
    def test_final_targets_cannot_select_model(self):
        a=rows();b=copy.deepcopy(a)
        for r in b[7:]:r['value']*=1000
        x=benchmark_series(a,'2026-01-04','2026-01-07');y=benchmark_series(b,'2026-01-04','2026-01-07')
        self.assertEqual(x['selected_model'],y['selected_model']);self.assertEqual(x['parameters'],y['parameters'])
        self.assertTrue(x['predictions'][0]['input_evidence'])
        self.assertFalse(x['causally_calibrated'])
    def test_persistence_wins_constant_and_large_finite_is_stable(self):
        for value in (3,1e308):
            r=benchmark_series(rows([value]*10),'2026-01-04','2026-01-07')
            self.assertEqual(r['selected_model'],'persistence');self.assertEqual(r['test']['mae'],0)
    def test_invalid_identity_units_duplicates_and_future_information_reject(self):
        for field,value in [('unit','USD'),('series','other'),('time','2026-01-01'),('available_at','2026-02-01')]:
            data=rows();data[2][field]=value
            with self.assertRaises(ValueError):benchmark_series(data,'2026-01-04','2026-01-07')
    def test_missing_exclusion_and_insufficient_partitions(self):
        data=rows();data[4]['value']=None
        r=benchmark_series(data,'2026-01-04','2026-01-07')
        self.assertEqual(r['excluded_missing'][0]['time'],'2026-01-05')
        with self.assertRaises(ValueError):benchmark_series(data,'2026-01-02','2026-01-07')

    def test_validation_labels_must_be_available_before_model_selection(self):
        data=rows();data[6]['available_at']='2026-01-07T12:00:00Z'
        with self.assertRaisesRegex(ValueError,'Validation target'):
            benchmark_series(data,'2026-01-04','2026-01-07')
