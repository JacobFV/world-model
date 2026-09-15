from pathlib import Path
import tempfile
import unittest
from worldmodel.artifacts import publish_report
from worldmodel.catalog import Catalog
from worldmodel.reference_build import build_reference
from worldmodel.store import Store

PROJECT=Path(__file__).resolve().parents[1]

class ReferenceBuildTests(unittest.TestCase):
    def prepare(self,root,conflict=False):
        store=Store(root);path=root/'fixture.txt';path.write_text('test fixture')
        raw=store.import_file('fixture',path,{'publisher':'test'},update_latest=False)
        common={'observed_at':'2026-01-01','evidence':[{'input':raw,'locator':'fixture'}]}
        rows=[{**common,'kind':'entity','id':'source:person','entity_type':'person','label':'Test'},
              {**common,'kind':'assertion','id':'source:alias','subject':'source:person','predicate':'alias','value':'Name'}]
        if conflict:
            rows.extend([{**common,'kind':'entity','id':'source:org','entity_type':'organization','label':'Org'},
                         {**common,'kind':'assertion','id':'source:equivalence','subject':'source:person','predicate':'same_as','object':'source:org'}])
        publish_report(store,'world_evidence',{}, {},raw_inputs=[raw],records=rows,entrypoint='worldmodel.reference_build:build_reference')
        return store

    def test_implicit_entity_id_survives_join_and_lineage_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=self.prepare(Path(tmp))
            result=build_reference(Catalog(PROJECT/'data'),store,PROJECT)
            rows=list(store.records(result['artifact']))
            self.assertEqual(next(r for r in rows if r['kind']=='entity')['entity_id'],'source:person')
            self.assertEqual(next(r for r in rows if r['kind']=='assertion')['subject'],'source:person')
            store.verify(result['artifact'])

    def test_identity_conflict_rejects_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=self.prepare(Path(tmp),True)
            with self.assertRaisesRegex(ValueError,'type conflict'):
                build_reference(Catalog(PROJECT/'data'),store,PROJECT)
            self.assertFalse((store.latest_path('reference_evidence')).exists())
