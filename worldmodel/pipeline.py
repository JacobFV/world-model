"""Deterministic DAG builds with audited, atomic publication."""
from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import sqlite3
import traceback
import uuid
import warnings
from .model import validate_record
from .provenance import capture_code
from .util import atomic_json, canonical, digest, file_hash, now


@dataclass
class Context:
    store: object
    definition: dict
    parameters: dict
    inputs: list
    raw_inputs: list

    def records(self, dataset):
        ref = next((ref for ref in self.inputs if ref['dataset'] == dataset), None)
        if ref is None:
            raise ValueError(f'Undeclared input: {dataset}')
        return self.store.records(ref)

    def input_ref(self, dataset):
        return next(ref for ref in self.inputs if ref['dataset'] == dataset)

    def raw_path(self, index=0):
        return self.store.artifact_dir(self.raw_inputs[index]) / 'payload'

    def raw_evidence(self, locator, index=0):
        return [{'input': self.raw_inputs[index], 'locator': locator}]


class Runner:
    def __init__(self, catalog, store, project):
        self.catalog, self.store, self.project = catalog, store, Path(project)

    def run(self, target, parameters=None, raw_refs=None, input_refs=None):
        """Overrides apply to target only. Pins stop traversal at exact input versions."""
        order = self.catalog.plan(target)
        raw_refs, pinned = raw_refs or {}, input_refs or {}
        if set(pinned) - set(order) or target in pinned:
            raise ValueError('Input pins must name dependencies of target')
        if set(raw_refs) - set(order):
            raise ValueError('Raw pins must name datasets in the plan')
        results = {}

        def build(dataset):
            if dataset in results:
                return results[dataset]
            if dataset in pinned:
                ref = pinned[dataset]
                if ref['dataset'] != dataset:
                    raise ValueError('Input pin dataset mismatch')
                self.store.verify(ref)
                results[dataset] = ref
                return ref
            definition = self.catalog.get(dataset)
            entrypoint = definition.get('entrypoint')
            if not entrypoint:
                raise ValueError(f'{dataset}: configure an entrypoint and mapping before running')
            inputs = [build(parent) for parent in definition.get('dependencies', [])]
            raw = []
            if definition['kind'] == 'source':
                refs = raw_refs.get(dataset)
                raw = refs if refs is not None else [self.store.latest_raw(dataset)]
                if not raw:
                    raise ValueError('Source dataset requires raw inputs')
                for ref in raw:
                    if ref['dataset'] != dataset:
                        raise ValueError('Raw pin dataset mismatch')
                    self.store.artifact(ref)
            params = {**definition.get('parameters', {}),
                      **(parameters or {} if dataset == target else {})}
            result = self._execute(definition, params, inputs, raw)
            results[dataset] = result
            return result

        return build(target)

    def _execute(self, definition, parameters, inputs, raw_inputs):
        dataset = definition['id']
        with self.store.lock(dataset) as base:
            run_id = uuid.uuid4().hex
            staging = base / 'processing' / run_id
            staging.mkdir()
            run_path = base / 'runs' / (run_id + '.json')
            attempt = {'run_id': run_id, 'dataset': dataset, 'started_at': now(),
                       'status': 'running', 'inputs': inputs, 'raw_inputs': raw_inputs,
                       'parameters': parameters}
            atomic_json(run_path, attempt)
            audit = None
            try:
                code = capture_code(self.project, definition['entrypoint'])
                module, function = definition['entrypoint'].split(':', 1)
                # Plugins must be inside the snapshotted project package.
                if not module.startswith('worldmodel.'):
                    raise ValueError('Entrypoint must live in worldmodel so its code is captured')
                implementation = importlib.import_module(module)
                expected_path = (self.project / (module.replace('.', '/') + '.py')).resolve()
                if Path(implementation.__file__).resolve() != expected_path:
                    raise ValueError('Loaded code does not match captured project')
                transform = getattr(implementation, function)
                audit = sqlite3.connect(staging / 'validation.sqlite')
                audit.executescript('CREATE TABLE ids (id TEXT PRIMARY KEY); '
                                    'CREATE TABLE inputs (ref TEXT, id TEXT, PRIMARY KEY(ref,id));')
                for ref in inputs:
                    for record in self.store.records(ref):
                        audit.execute('INSERT INTO inputs VALUES (?,?)', (digest(ref), record['id']))
                context = Context(self.store, definition, parameters, inputs, raw_inputs)
                allowed = {canonical(ref) for ref in inputs + raw_inputs}
                count = 0
                with (staging / 'records.jsonl').open('wb') as stream:
                    for record in transform(context):
                        validate_record(record)
                        try:
                            audit.execute('INSERT INTO ids VALUES (?)', (record['id'],))
                        except sqlite3.IntegrityError as error:
                            raise ValueError(f'Duplicate record ID: {record["id"]}') from error
                        for evidence in record['evidence']:
                            ref = evidence['input']
                            if canonical(ref) not in allowed:
                                raise ValueError('Record evidence references undeclared input')
                            if 'version' in ref and not audit.execute(
                                    'SELECT 1 FROM inputs WHERE ref=? AND id=?',
                                    (digest(ref), evidence['record_id'])).fetchone():
                                raise ValueError('Evidence references missing input record')
                        stream.write(canonical(record) + b'\n')
                        count += 1
                    stream.flush()
                    os.fsync(stream.fileno())
                audit.close()
                audit = None
                (staging / 'validation.sqlite').unlink()
                if not count and not definition.get('allow_empty', False):
                    raise ValueError('Empty output; set allow_empty explicitly if intentional')
                # Detect concurrent modification of inputs or project during transformation.
                for ref in inputs:
                    self.store.verify(ref)
                for ref in raw_inputs:
                    self.store.artifact(ref)
                for name, expected in code['files'].items():
                    if file_hash(self.project / name) != expected:
                        raise ValueError('Code changed during execution; rerun with stable code')
                output = staging / 'records.jsonl'
                identity = {'schema_version': 1, 'dataset': dataset, 'definition': definition,
                            'code': code, 'parameters': parameters, 'inputs': inputs,
                            'raw_inputs': raw_inputs,
                            'outputs': {'records.jsonl': {'sha256': file_hash(output),
                                                        'bytes': output.stat().st_size, 'rows': count}}}
                version = digest(identity)
                ref = {'dataset': dataset, 'version': version}
                atomic_json(staging / 'manifest.json', {**identity, 'version': version})
                destination = self.store.version_dir(ref)
                if destination.exists():
                    self.store.verify(ref)
                    import shutil
                    shutil.rmtree(staging)
                    attempt['reused'] = True
                else:
                    os.rename(staging, destination)
                    attempt['reused'] = False
                # Rename is the publication commit point. Pointers and run logs
                # are advisory: a later I/O failure cannot unpublish valid output.
                attempt.update(status='succeeded', output=ref, completed_at=now())
                try:
                    atomic_json(base / 'latest.json', ref)
                    atomic_json(run_path, attempt)
                except OSError as error:
                    attempt['bookkeeping_error'] = str(error)
                    try:
                        atomic_json(run_path, attempt)
                    except OSError:
                        pass
                    warnings.warn(f'Output published as {dataset}@{version}; bookkeeping failed: {error}',
                                  RuntimeWarning)
                return ref
            except Exception as error:
                attempt.update(status='failed', completed_at=now(),
                               error=str(error), traceback=traceback.format_exc())
                atomic_json(run_path, attempt)
                raise
            finally:
                if audit is not None:
                    audit.close()
