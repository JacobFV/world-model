from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from worldmodel.graph import Graph

class MultiStageGraphTests(unittest.TestCase):
    def test_independent_stages_of_one_dataset_retain_both_claims(self):
        store=SimpleNamespace(verify=lambda ref:True,records=lambda ref,verify=False:iter([{'id':'claim','kind':'observation','metric':'value','value':1,'observed_at':'2026-01-01'}]))
        refs=[{'dataset':'example','stage':stage,'version':'a'*64} for stage in ('normalized','adjusted')]
        with tempfile.TemporaryDirectory() as tmp:
            graph=Graph(Path(tmp)/'index.sqlite');self.assertEqual(graph.build(store,refs)['records'],2)
            self.assertEqual({r['_provenance']['input']['stage'] for r in graph.observations('value')['records']},{'normalized','adjusted'})
            with self.assertRaises(ValueError):graph.build(store,[refs[0],refs[0]])
