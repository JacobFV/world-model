import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from worldmodel.store import Store
from worldmodel.util import atomic_json, digest, file_hash, read_json


class DatasetStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'data')

    def publish(self,stage=None,inputs=(),raw_inputs=()):
        identity={'schema_version':2,'dataset':'example','inputs':list(inputs),'raw_inputs':list(raw_inputs),
            'definition':{'id':'example','entrypoint':'test:run','code':{'sources':{'hidden.py':'hidden'},'files':{'hidden.py':'hash'}}},
            'code':{'files':{'test.py':'0'*64},'sources':{'test.py':'sensitive source body'*10000}},
            'rights':{'code_license':'MIT','sources':[{'license_id':'other'}]},
            'parameters':{'value':5},'outputs':{}}
        if stage is not None:identity['stage']=stage
        payload=b'{"value":1}\n'
        import hashlib
        identity['outputs']['records.jsonl']={'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        version=digest(identity);ref={'dataset':'example','version':version}
        if stage is not None:ref['stage']=stage
        folder=self.store.version_dir(ref);folder.mkdir()
        (folder/'records.jsonl').write_bytes(payload)
        atomic_json(folder/'manifest.json',{**identity,'version':version})
        return ref

    def test_new_paths_create_parents_and_never_read_legacy(self):
        base=self.store.initialize('example');h='a'*64
        self.assertEqual(self.store.artifact_dir({'dataset':'example','artifact':h}),base/'artifacts/raw'/h)
        self.assertEqual(self.store.version_dir({'dataset':'example','version':h}),base/'artifacts/final'/h)
        self.assertEqual(self.store.version_dir({'dataset':'example','stage':'normalize','version':h}),base/'artifacts/normalize'/h)
        self.assertTrue((base/'artifacts/normalize').is_dir())
        self.assertEqual(self.store.scratch_dir('example'),base/'scratch')
        self.assertEqual(self.store.runs_dir('example'),base/'scratch/runs')
        self.assertEqual(self.store.latest_path('example'),base/'manifests/latest.json')
        self.assertEqual(self.store.latest_path('example','normalize'),base/'manifests/stages/normalize/latest.json')
        self.assertEqual(self.store.raw_latest_path('example'),base/'manifests/raw-latest.json')
        self.assertEqual(self.store.samples_dir('example'),base/'artifacts/samples')
        self.assertEqual(self.store.sample_latest_path('example'),base/'manifests/samples/latest.json')
        atomic_json(base/'latest.json',{'dataset':'example','version':h})
        with self.assertRaises(FileNotFoundError):self.store.latest('example')
        self.assertTrue((base/'latest.json').exists())

    def test_import_exports_receipt_index_and_keeps_payload_untracked(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        source=self.root/'input.txt';source.write_text('test bytes')
        ref=self.store.import_file('example',source,{'publisher':'test','license_id':'MIT'})
        base=self.store.dataset_dir('example');h=ref['artifact']
        self.assertEqual(self.store.latest_raw('example'),ref)
        self.assertTrue((base/'artifacts/raw'/h/'payload').is_file())
        self.assertEqual(read_json(base/'manifests/raw'/f'{h}.json')['sha256'],file_hash(source))
        subprocess.run(['git','-C',str(self.root),'add','data'],check=True)
        tracked=subprocess.check_output(['git','-C',str(self.root),'ls-files'],text=True).splitlines()
        self.assertIn('data/example/.gitignore',tracked)
        self.assertIn(f'data/example/manifests/raw/{h}.json',tracked)
        self.assertNotIn(f'data/example/artifacts/raw/{h}/payload',tracked)
        (base/'artifacts/raw'/h/'payload').write_text('corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):self.store.artifact(ref)

    def test_indexes_strip_code_bodies_preserve_hashes_refs_rights_and_stage(self):
        self.store.initialize('example');ref=self.publish(stage='normalize')
        self.store.publish_index(ref)
        base=self.store.dataset_dir('example');index=read_json(base/'manifests/normalize'/f"{ref['version']}.json")
        self.assertEqual(index['code']['files'],{'test.py':'0'*64})
        self.assertNotIn('sources',index['code'])
        self.assertNotIn('sources',index['definition']['code'])
        self.assertEqual(index['rights']['sources'][0]['license_id'],'other')
        self.assertEqual(index['inputs'],[]);self.assertIn('sha256',index['outputs']['records.jsonl'])
        self.assertLess(len(json.dumps(index)),10000)
        self.assertEqual(self.store.latest('example',stage='normalize'),ref)
        self.assertFalse(self.store.latest_path('example').exists())
        (self.store.version_dir(ref)/'records.jsonl').write_text('corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):self.store.publish_index(ref)

    def test_initialize_preserves_legacy_files_and_custom_ignores(self):
        base=self.store.dataset_dir('example');(base/'raw').mkdir(parents=True)
        (base/'raw/preserved').write_text('old')
        (base/'.gitignore').write_text('/custom/\n')
        self.store.initialize('example');self.store.initialize('example')
        self.assertEqual((base/'raw/preserved').read_text(),'old')
        rules=(base/'.gitignore').read_text().splitlines()
        for rule in ('/custom/','/artifacts/','/scratch/','/raw/','/final/','/processing/','/runs/','/samples/','/latest.json','/raw-latest.json','/.lock'):
            self.assertEqual(rules.count(rule),1)

    def test_stage_path_traversal_and_mismatched_manifest_are_rejected(self):
        for stage in ('../raw','/tmp','x/y','..','','X',None):
            with self.assertRaises(ValueError):self.store.version_dir({'dataset':'example','stage':stage,'version':'a'*64})
        self.store.initialize('example');ref=self.publish()
        self.store.publish_index(ref)
        self.assertEqual(self.store.latest('example'),ref)
        self.assertEqual(self.store.latest('example',stage='final'),ref)
        import shutil
        wrong={**ref,'stage':'other'};destination=self.store.version_dir(wrong)
        shutil.copytree(self.store.version_dir(ref),destination)
        with self.assertRaisesRegex(ValueError,'stage'):self.store.verify(wrong)

    def test_recursive_verification_does_not_skip_same_hash_at_another_stage(self):
        import shutil
        self.store.initialize('example');child=self.publish()
        wrong={**child,'stage':'other'}
        shutil.copytree(self.store.version_dir(child),self.store.version_dir(wrong))
        parent=self.publish(stage='aggregate',inputs=[child,wrong])
        with self.assertRaisesRegex(ValueError,'stage'):self.store.verify(parent)
        valid=self.publish(stage='summary',inputs=[child])
        lineage=self.store.lineage(valid)
        self.assertEqual({m.get('stage','final') for m in lineage['versions']},{'final','summary'})

    def test_raw_import_without_latest_still_exports_index_and_checks_size(self):
        source=self.root/'input';source.write_bytes(b'abcd')
        ref=self.store.import_file('example',source,{},update_latest=False)
        self.assertFalse(self.store.raw_latest_path('example').exists())
        self.assertTrue((self.store.dataset_dir('example')/'manifests/raw'/f"{ref['artifact']}.json").exists())
        receipt=self.store.artifact(ref);receipt.pop('artifact');receipt['bytes']=0
        bad={**ref,'artifact':digest(receipt)}
        destination=self.store.artifact_dir(bad);destination.mkdir()
        (destination/'payload').write_bytes(b'abcd')
        atomic_json(destination/'receipt.json',{**receipt,'artifact':bad['artifact']})
        with self.assertRaisesRegex(ValueError,'checksum'):self.store.artifact(bad)
