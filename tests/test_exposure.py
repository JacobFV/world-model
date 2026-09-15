import unittest
from worldmodel.exposure import stress_exposures


def config(cash=10):
    return {'as_of':'2026-01-01', 'end':'2027-01-01', 'currency':'USD', 'shock_bps':100,
            'entities':[{'id':'a','cash':cash},{'id':'b','cash':5},{'id':'c','cash':0}],
            'obligations':[{'id':'ab','borrower':'a','lender':'b','principal':10,'annual_rate':0,
                            'rate_type':'fixed','maturity':'2027-01-01'},
                           {'id':'bc','borrower':'b','lender':'c','principal':10,'annual_rate':0,
                            'rate_type':'floating','maturity':'2027-01-01'}]}


class ExposureTests(unittest.TestCase):
    def test_cascade_conserves_cash_and_fixed_rate_is_unaffected(self):
        result=stress_exposures(config(0))
        self.assertAlmostEqual(sum(r['ending_cash'] for r in result['baseline']['entities']),5)
        self.assertEqual(result['baseline']['total_shortfall'],15)
        self.assertEqual(result['stressed']['obligations'][0]['due'],10)
        self.assertAlmostEqual(result['stressed']['obligations'][1]['due'],10.1)
        self.assertFalse(result['causally_calibrated'])

    def test_settlement_pays_chain_without_creating_money(self):
        result=stress_exposures(config())['baseline']
        self.assertEqual(result['total_shortfall'],0)
        self.assertEqual([r['ending_cash'] for r in result['entities']],[0,5,10])

    def test_invalid_units_missing_parties_and_overdue_are_rejected(self):
        for change in ('currency','party','maturity','nan'):
            request=config()
            if change=='currency':request['currency']='EUR'
            if change=='party':request['obligations'][0]['lender']='missing'
            if change=='maturity':request['obligations'][0]['maturity']='2025-01-01'
            if change=='nan':request['shock_bps']=float('nan')
            with self.assertRaises(ValueError):stress_exposures(request)

class ExposureEvidenceTests(unittest.TestCase):
    def test_pinned_obligations_require_correct_dates_units_and_subjects(self):
        from worldmodel.exposure import exposure_from_evidence
        from copy import deepcopy
        class Store:
            def records(self, ref):return iter(self.rows)
        store=Store()
        common={'observed_at':'2026-01-02','valid_from':'2026-01-01','valid_to':'2026-02-01','epistemic_status':'observed'}
        store.rows=[{'kind':'entity','id':'ea','entity_id':'a','entity_type':'business'},
                    {'kind':'entity','id':'eb','entity_id':'b','entity_type':'business'},
                    {'kind':'entity','id':'el','entity_id':'loan','entity_type':'financial_obligation'},
                    {**common,'kind':'observation','id':'ca','subject':'a','metric':'cash','unit':'USD','value':10},
                    {**common,'kind':'observation','id':'cb','subject':'b','metric':'cash','unit':'USD','value':0},
                    {**common,'kind':'assertion','id':'terms','subject':'loan','predicate':'obligation_terms','unit':None,
                     'value':{'borrower':'a','lender':'b','principal':10,'annual_rate':0,'currency':'USD','rate_type':'fixed','maturity':'2027-01-01'}}]
        request={'as_of':'2026-01-15','known_at':'2026-01-16','end':'2027-01-01','currency':'USD','shock_bps':100,
                 'entities':[{'id':'a','cash_record':'ca'},{'id':'b','cash_record':'cb'}],
                 'obligations':[{'id':'loan','terms_record':'terms'}]}
        original=deepcopy(store.rows)
        result=exposure_from_evidence(store,{'dataset':'test','version':'0'*64},request)
        self.assertEqual(result['baseline']['total_shortfall'],0)
        self.assertEqual(len(result['initial_state_evidence']),3)
        for key,value in [('unit','EUR'),('valid_to','2026-01-10'),('observed_at','2026-02-01'),('subject','b'),('epistemic_status','forecast')]:
            store.rows=deepcopy(original);store.rows[3][key]=value
            with self.assertRaises(ValueError):exposure_from_evidence(store,{},request)
