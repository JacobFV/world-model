import unittest
from worldmodel.reconciliation import reconcile_claims

class ReconciliationTests(unittest.TestCase):
    def test_conflicts_preserved_and_decision_audited(self):
        rows=[{'id':'obs:a','kind':'observation','subject':'e:a','metric':'cash','value':1,'unit':'USD','observed_at':'2025-01-01'},
              {'id':'obs:b','kind':'observation','subject':'e:a','metric':'cash','value':2,'unit':'USD','observed_at':'2025-01-02'}]
        args=(rows,{'entity':'e:a','variable':'cash'},'2025-01-03','2025-01-03')
        self.assertEqual(reconcile_claims(*args)['status'],'conflicting')
        chosen=reconcile_claims(*args,policy='latest')
        self.assertEqual(chosen['selected_record_ids'],['obs:b'])
        self.assertEqual(chosen['rejected_record_ids'],['obs:a'])
        self.assertEqual(rows[0]['value'],1)

    def test_selector_units_dimensions_and_unknown_fields(self):
        rows=[{'id':'obs:usd','kind':'observation','subject':'e:a','metric':'cash','value':1,'unit':'USD','dimensions':{'segment':'retail'},'observed_at':'2025-01-01'},
              {'id':'obs:eur','kind':'observation','subject':'e:a','metric':'cash','value':2,'unit':'EUR','dimensions':{'segment':'wholesale'},'observed_at':'2025-01-02'}]
        selector={'entity':'e:a','variable':'cash','unit':'USD','dimensions':{'segment':'retail'}}
        chosen=reconcile_claims(rows,selector,'2025-01-03','2025-01-03',policy='latest')
        self.assertEqual(chosen['selected_record_ids'],['obs:usd'])
        with self.assertRaises(ValueError):reconcile_claims(rows,{**selector,'typo':1},'2025-01-03','2025-01-03')
