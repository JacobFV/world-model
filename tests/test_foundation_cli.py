import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.cli import main
from worldmodel.store import Store

PROJECT=Path(__file__).resolve().parents[1]

class FoundationCLITests(unittest.TestCase):
    def test_field_projection_and_lifecycle_publish_typed_verified_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands=[['field-view','--request',str(PROJECT/'examples/fields.json'),'--view',str(PROJECT/'examples/field-view.json'),'--evolve'],
                      ['lifecycle','--request',str(PROJECT/'examples/lifecycle.json'),'--at','2023-01-01','--known-at','2026-09-16']]
            for args in commands:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    status=main(['--data-root',tmp,*args])
                self.assertEqual(status,0)
                ref=json.loads(output.getvalue())['artifact']
                self.assertTrue(Store(tmp).verify(ref))
                self.assertTrue(list(Store(tmp).records(ref)))

    def test_actor_selection_and_immutable_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                status=main(['--data-root',tmp,'actors','--request',str(PROJECT/'examples/actors.json'),
                             '--actor','human','--implementation','human_behavior.utility','--at','2026-01-01','--known-at','2026-09-16'])
            self.assertEqual(status,0)
            result=json.loads(output.getvalue())
            self.assertEqual(result['implementation'],'human_behavior.utility')
            self.assertTrue(Store(tmp).verify(result['artifact']))
