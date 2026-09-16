"""Offline fictional source-shape contracts, separate from acquired-data integration."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from worldmodel.normalizers import AVAILABLE,normalize_sample
from worldmodel.ontology import validate_typed_graph

class OfflineNormalizerContracts(unittest.TestCase):
    def test_every_original_adapter_has_a_fictional_closed_graph_fixture(self):
        fixtures=json.loads((Path(__file__).parent/'fixtures/normalizer_shapes.json').read_text())
        self.assertEqual(set(fixtures),set(AVAILABLE))
        for dataset,row in fixtures.items():
            with self.subTest(dataset=dataset),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'payload';path.write_text(json.dumps(row)+'\n')
                receipt={'retrieved_at':'2026-01-01','bytes':path.stat().st_size,'source':{'sampling':{'config':{'body':{'seriesid':['LNS14000000']}}}}}
                path.with_name('receipt.json').write_text(json.dumps(receipt))
                context=Mock(definition={'id':dataset},raw_inputs=[{'dataset':dataset,'artifact':'0'*64}])
                context.raw_path.return_value=path
                # Mirror worldmodel.pipeline.Context for a single-payload sample artifact.
                context.raw_receipt.return_value=receipt
                context.raw_coverage.return_value={'layout':'payload','sampled':True,'complete':False,'stop_reason':None,'shards':1,'bytes':receipt['bytes']}
                context.raw_evidence.return_value=[{'input':context.raw_inputs[0],'locator':'fictional-row:1'}]
                rows=list(normalize_sample(context));self.assertTrue(rows)
                validate_typed_graph(rows)
                self.assertTrue(all(r['evidence'] for r in rows))
                if dataset=='census_business':
                    self.assertIsNone(next(r['value'] for r in rows if r.get('metric')=='employment'))
