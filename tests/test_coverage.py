import unittest
from worldmodel.coverage import coverage_report

class CoverageTests(unittest.TestCase):
    def test_identity_evidence_and_validation_maps_are_independent(self):
        rows=[{'kind':'entity','id':'r:p','entity_id':'person:p','entity_type':'person'},
              {'kind':'entity','id':'r:o','entity_id':'org:o','entity_type':'organization','attributes':{'synthetic_reference':True}},
              {'kind':'observation','id':'r:1','metric':'cash','value':0},
              {'kind':'observation','id':'r:2','metric':'cash','value':None},
              {'kind':'observation','id':'r:3','metric':'cash','value':999,'epistemic_status':'forecast'},
              {'kind':'assertion','id':'r:4','subject':'person:p','predicate':'alias','value':'Pat'}]
        registry={'processes':[{'id':'p','validated':False}],'implementations':[{'id':'p.a'}]}
        report=coverage_report(rows,registry,[{'dataset':'one','status':'sampled'}],
                               [{'artifact':{'dataset':'fit'},'holdout':{'mae':1,'persistence_mae':2,'beats_persistence':True}}])
        self.assertEqual(report['identities']['entities'],2)
        self.assertEqual(report['identities']['source_scoped_references'],1)
        self.assertEqual(report['evidence']['nonmissing_observations'],1)
        self.assertEqual(report['excluded_scenario_records'],1)
        self.assertEqual(report['validation']['causally_validated_models'],[])
        self.assertIsNone(report['identities']['universe_size'])
        self.assertEqual(report['identities']['completeness'],'unknown')
        self.assertEqual(report['domains']['public_people']['entities'],1)
        self.assertIn('obligations',report['missing_domains'])
