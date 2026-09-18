import unittest
from worldmodel.rights import (COMMERCIAL_USE_ENV,commercial_use,inherited_rights,person_level_rule,
                               retain_identified_persons)

class RightsTests(unittest.TestCase):
    def test_multiple_source_terms_survive_derived_chain_without_guessing_license(self):
        class Store:
            def artifact(self,ref):return {'source':{'publisher':ref['dataset'],'license':'MIT' if ref['dataset']=='a' else None}}
            def manifest(self,ref):return {'raw_inputs':[{'dataset':'a','artifact':'1'},{'dataset':'b','artifact':'2'}],'inputs':[]}
        report=inherited_rights(Store(),[{'dataset':'derived','version':'3'}],[])
        self.assertEqual(report['code_license'],'MIT')
        self.assertEqual(len(report['sources']),2)
        self.assertEqual(report['sources'][0]['metadata']['license'],'MIT')
        self.assertTrue(report['redistribution_review_required'])
        self.assertEqual(report['computation_policy'],'metadata_only_no_local_execution_gate')

    def test_derived_sample_follows_parent_rights(self):
        class Store:
            def artifact(self,ref):
                return {'source':{'sampling':{'input_version':{'dataset':'parent','version':'v'}}}} if ref['dataset']=='excerpt' else {'source':{'license':'CC-BY-4.0','attribution':'Original author'}}
            def manifest(self,ref):return {'inputs':[],'raw_inputs':[{'dataset':'original','artifact':'raw'}]}
        report=inherited_rights(Store(),raw_inputs=[{'dataset':'excerpt','artifact':'sample'}])
        self.assertIn({'license':'CC-BY-4.0','attribution':'Original author'},[r['metadata'] for r in report['sources']])


class CommercialUseFlagTests(unittest.TestCase):
    CONDITIONAL={'person_level_records':{'policy':'conditional','condition':'non_commercial_use'}}

    def test_unset_flag_defaults_to_commercial_so_a_fresh_clone_filters(self):
        self.assertTrue(commercial_use({}))
        allowed,decision=retain_identified_persons(self.CONDITIONAL,{})
        self.assertFalse(allowed)
        self.assertEqual(decision['declared_purpose'],'commercial')

    def test_declared_non_commercial_purpose_unlocks_a_conditional_source(self):
        for value in ('0','false','no','off','non_commercial'):
            allowed,decision=retain_identified_persons(self.CONDITIONAL,{COMMERCIAL_USE_ENV:value})
            self.assertTrue(allowed,value)
            self.assertTrue(decision['identified_persons_retained'])

    def test_a_prohibited_source_stays_filtered_under_either_purpose(self):
        source={'person_level_records':{'policy':'prohibited'}}
        for value in ('0','1'):
            allowed,_=retain_identified_persons(source,{COMMERCIAL_USE_ENV:value})
            self.assertFalse(allowed,value)

    def test_a_permitted_source_stays_open_under_either_purpose(self):
        source={'person_level_records':{'policy':'permitted'}}
        for value in ('0','1'):
            allowed,_=retain_identified_persons(source,{COMMERCIAL_USE_ENV:value})
            self.assertTrue(allowed,value)

    def test_an_undeclared_source_is_treated_as_prohibited(self):
        # Every dataset that predates this rule keeps the aggregate-only behaviour it had.
        allowed,decision=retain_identified_persons({'publisher':'Somebody'},{COMMERCIAL_USE_ENV:'0'})
        self.assertFalse(allowed)
        self.assertEqual(decision['policy'],'prohibited')

    def test_unparseable_flag_is_an_error_rather_than_a_silent_default(self):
        with self.assertRaises(ValueError):commercial_use({COMMERCIAL_USE_ENV:'maybe'})

    def test_unknown_policy_and_unknown_condition_are_rejected(self):
        with self.assertRaises(ValueError):person_level_rule({'person_level_records':{'policy':'sometimes'}})
        with self.assertRaises(ValueError):person_level_rule({'person_level_records':{'policy':'conditional','condition':'tuesday'}})
