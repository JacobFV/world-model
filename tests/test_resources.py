from pathlib import Path
import tempfile
import unittest
from worldmodel.resources import resource_roots

class ResourceTests(unittest.TestCase):
    def test_installed_layout_uses_bundled_catalog_and_writable_data(self):
        with tempfile.TemporaryDirectory() as root:
            package=Path(root)/'site-packages/worldmodel';package.mkdir(parents=True)
            bundled=package/'_resources';(bundled/'catalog').mkdir(parents=True);(bundled/'fixtures').mkdir()
            roots=resource_roots(package,{'XDG_DATA_HOME':root+'/state'})
            self.assertEqual(roots['catalog'],bundled/'catalog')
            self.assertEqual(roots['data'],Path(root)/'state/worldmodel')
            self.assertNotIn('site-packages',str(roots['data']))

    def test_checkout_preserves_existing_layout(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);(root/'worldmodel').mkdir();(root/'data').mkdir();(root/'pyproject.toml').touch()
            roots=resource_roots(root/'worldmodel',{})
            self.assertEqual(roots['catalog'],root/'data')
            self.assertEqual(roots['data'],root/'data')

    def test_bundled_local_sampling_paths(self):
        from worldmodel.resources import local_fixture
        roots={'fixtures':Path('/package/_resources/fixtures')}
        self.assertEqual(local_fixture('tests/fixtures/countries.csv','/site-packages',roots),Path('/package/_resources/fixtures/countries.csv'))
        self.assertEqual(local_fixture('/tmp/custom.csv','/project',roots),Path('/tmp/custom.csv'))
        with self.assertRaises(ValueError):local_fixture('tests/fixtures/../outside.csv','/project',roots)
