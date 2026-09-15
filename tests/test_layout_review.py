"""Packaging regressions found by independent greenfield layout review."""
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

PROJECT=Path(__file__).resolve().parents[1]


class LayoutPackagingReviewTests(unittest.TestCase):
    def build_fixture(self,symlink=False):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        for name in ('setup.py','MANIFEST.in','pyproject.toml','LICENSE','DATA_RIGHTS.md'):
            shutil.copy2(PROJECT/name,root/name)
        (root/'worldmodel').mkdir();(root/'worldmodel/__init__.py').write_text('')
        dataset=root/'data/demo';dataset.mkdir(parents=True)
        (dataset/'dataset.json').write_text('{"id":"demo"}')
        (dataset/'pipeline.py').write_text('def run(context): return []\n')
        (dataset/'helper.json').write_text('{"factor":2}')
        for name in ('latest.json','raw-latest.json'):
            (dataset/name).write_text('{"legacy":"pointer"}')
        (dataset/'artifacts/raw').mkdir(parents=True)
        (dataset/'artifacts/raw/private.json').write_text('{"private":"acquired payload"}')
        if symlink:(dataset/'alias.json').symlink_to(dataset/'artifacts/raw/private.json')
        result=subprocess.run([sys.executable,'setup.py','build_py','--build-lib','built','sdist','--dist-dir','dist'],
                              cwd=root,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        return root

    def test_checkout_build_excludes_preserved_legacy_pointers(self):
        root=self.build_fixture();catalog=root/'built/worldmodel/_resources/catalog/demo'
        self.assertTrue((catalog/'dataset.json').exists())
        for name in ('latest.json','raw-latest.json'):
            self.assertFalse((catalog/name).exists(),'Legacy runtime pointer leaked into bundled catalog')

    def test_sdist_preserves_same_supported_local_json_resources(self):
        root=self.build_fixture()
        self.assertTrue((root/'built/worldmodel/_resources/catalog/demo/helper.json').exists())
        with tarfile.open(next((root/'dist').glob('*.tar.gz'))) as archive:
            self.assertTrue(any(name.endswith('/data/demo/helper.json') for name in archive.getnames()),
                            'Supported local JSON resource disappears when building through sdist')

    def test_payload_symlink_is_not_dereferenced_into_wheel(self):
        root=self.build_fixture(symlink=True)
        self.assertFalse((root/'built/worldmodel/_resources/catalog/demo/alias.json').exists(),
                         'Symlink copied an ignored acquired payload into bundled catalog')

    def test_sdist_excludes_payload_symlinks_and_legacy_pointers(self):
        root=self.build_fixture(symlink=True)
        with tarfile.open(next((root/'dist').glob('*.tar.gz'))) as archive:
            names=archive.getnames()
            for excluded in ('alias.json','latest.json','raw-latest.json','private.json'):
                self.assertFalse(any(name.endswith('/'+excluded) for name in names), excluded)


class LayoutStageConsumerReviewTests(unittest.TestCase):
    def test_graph_queries_preserve_named_stage_in_provenance(self):
        from worldmodel.graph import Graph
        from worldmodel.store import Store
        from worldmodel.util import atomic_json, canonical, digest, file_hash
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=Store(root/'data')
            source=root/'source.json';source.write_text('{"value":1}')
            raw=store.import_file('source',source,{'publisher':'test'})
            record={'kind':'observation','id':'obs:1','subject':'entity:a','metric':'count',
                    'value':1,'unit':'unit','dimensions':{},'observed_at':'2024-01-01T00:00:00Z',
                    'evidence':[{'input':raw,'locator':'line:1'}]}
            staging=store.scratch_dir('source')/'normalized';staging.mkdir()
            (staging/'records.jsonl').write_bytes(canonical(record)+b'\n')
            identity={'schema_version':2,'dataset':'source','stage':'normalized','inputs':[],
                      'raw_inputs':[raw],'outputs':{'records.jsonl':{'sha256':file_hash(staging/'records.jsonl'),
                        'bytes':(staging/'records.jsonl').stat().st_size}}}
            ref={'dataset':'source','stage':'normalized','version':digest(identity)}
            atomic_json(staging/'manifest.json',{**identity,'version':ref['version']})
            staging.rename(store.version_dir(ref))
            graph=Graph(root/'graph.sqlite');graph.build(store,[ref])
            provenance=graph.observations('count')[0]['_provenance']['input']
            self.assertEqual(provenance,ref)
            self.assertTrue(store.verify(provenance))

    def test_legacy_disposable_graph_index_requires_rebuild(self):
        import sqlite3
        from worldmodel.graph import Graph
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'graph.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
                db.execute("INSERT INTO metadata VALUES('schema_version','1')")
            with self.assertRaisesRegex(ValueError,'rebuild|graph-build'):
                Graph(path).observations('count')
