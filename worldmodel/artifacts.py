"""Content-addressed domain reports with exact evidence and implementation inputs."""
from pathlib import Path
import os
import shutil
import uuid
from .model import validate_record
from .provenance import capture_code
from .util import atomic_json, canonical, digest, file_hash, now, read_json

PROJECT = Path(__file__).resolve().parents[1]


def publish_report(store, dataset, report, parameters, *, inputs=(), raw_inputs=(), records=(), entrypoint):
    inputs, raw_inputs = tuple(inputs), tuple(raw_inputs)
    code = capture_code(PROJECT, entrypoint)
    for ref in inputs:
        store.verify(ref)
    for ref in raw_inputs:
        store.artifact(ref)
    allowed = {canonical(ref) for ref in (*inputs, *raw_inputs)}
    canonical(report)
    with store.lock(dataset) as base:
        run_id = uuid.uuid4().hex
        staging = store.scratch_dir(dataset)/run_id
        staging.mkdir()
        try:
            atomic_json(staging/'report.json', report)
            with (staging/'records.jsonl').open('wb') as stream:
                for record in records:
                    validate_record(record)
                    if any(canonical(item['input']) not in allowed for item in record['evidence']):
                        raise ValueError('Output evidence must reference a declared input')
                    stream.write(canonical(record)+b'\n')
            from .rights import inherited_rights
            identity = {'rights':inherited_rights(store,inputs,raw_inputs), 'schema_version':1,'dataset':dataset,
                        'definition':{'id':dataset,'kind':'derived','schema_version':1,'entrypoint':entrypoint},
                        'parameters':parameters,'inputs':list(inputs),'raw_inputs':list(raw_inputs),'code':code,
                        'outputs':{name:{'sha256':file_hash(staging/name),'bytes':(staging/name).stat().st_size}
                                   for name in ('report.json','records.jsonl')}}
            ref = {'dataset':dataset,'version':digest(identity)}
            atomic_json(staging/'manifest.json',{**identity,'version':ref['version']})
            for input_ref in inputs:
                store.verify(input_ref)
            for input_ref in raw_inputs:
                store.artifact(input_ref)
            if any(file_hash(PROJECT/name) != checksum for name,checksum in code['files'].items()):
                raise ValueError('Code changed during report publication')
            destination = store.version_dir(ref)
            if destination.exists():
                store.verify(ref)
            else:
                os.rename(staging,destination)
            store.publish_index(ref)
            atomic_json(store.runs_dir(dataset)/f'{run_id}.json',{'run_id':run_id,'status':'succeeded','output':ref,'completed_at':now()})
            return ref
        except Exception as error:
            atomic_json(store.runs_dir(dataset)/f'{run_id}.json',{'run_id':run_id,'status':'failed','error':str(error),'completed_at':now()})
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def load_report(store, ref):
    store.verify(ref)
    return read_json(store.version_dir(ref)/'report.json')
