import tempfile
import unittest
from pathlib import Path
from worldmodel.artifacts import publish_report, load_report
from worldmodel.store import Store

class ArtifactTests(unittest.TestCase):
    def test_report_roundtrip_idempotence_and_raw_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'data');path=Path(temp)/'input.json';path.write_text('{"x":1}')
            raw=store.import_file('fixture',path,{'publisher':'fictional'})
            kwargs={'raw_inputs':[raw],'entrypoint':'worldmodel.calibration:fit_ar1'}
            ref=publish_report(store,'report',{'value':2},{'factor':2},**kwargs)
            self.assertEqual(load_report(store,ref),{'value':2})
            self.assertEqual(ref,publish_report(store,'report',{'value':2},{'factor':2},**kwargs))
            store.artifact_dir(raw).joinpath('payload').write_text('tamper')
            with self.assertRaisesRegex(ValueError,'checksum'):
                load_report(store,ref)

    def test_generator_input_refs_are_retained_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'data');path=Path(temp)/'input.json';path.write_text('{}')
            raw=store.import_file('fixture',path,{'publisher':'fixture'})
            ref=publish_report(store,'report',{}, {},raw_inputs=(r for r in [raw]),entrypoint='worldmodel.calibration:fit_ar1')
            self.assertEqual(store.manifest(ref)['raw_inputs'],[raw])
