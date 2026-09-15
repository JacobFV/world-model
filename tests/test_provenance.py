from pathlib import Path
import unittest
from unittest.mock import patch
from worldmodel.provenance import capture_code

class ProvenancePortabilityTests(unittest.TestCase):
    def test_source_capture_works_without_git_executable(self):
        with patch('worldmodel.provenance.subprocess.run',side_effect=FileNotFoundError('git')):
            code=capture_code(Path(__file__).resolve().parents[1],'worldmodel.provenance:capture_code')
        self.assertIsNone(code['git_commit'])
        self.assertIsNone(code['git_dirty'])
        self.assertIn('worldmodel/provenance.py',code['files'])
        self.assertIn('worldmodel/provenance.py',code['sources'])

    def test_installed_resources_ignore_compiled_bytecode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            root=Path(root).resolve();package=root/'worldmodel';resources=package/'_resources'
            cache=resources/'examples/__pycache__';cache.mkdir(parents=True)
            (cache/'example.cpython-314.pyc').write_bytes(b'\xff\x00')
            (resources/'config.json').write_text('{}')
            with patch('worldmodel.provenance._PACKAGE_ROOT',package), patch('worldmodel.provenance._LOADED_SOURCE_HASHES',{}):
                code=capture_code(root,'worldmodel.example:run')
            self.assertEqual(list(code['sources']),['worldmodel/_resources/config.json'])
