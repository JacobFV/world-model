import unittest
from worldmodel.rights import inherited_rights

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
