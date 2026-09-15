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
