"""Audited external dataset DAGs and immutable, named local pipeline stages."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from copy import deepcopy
import importlib
import importlib.abc
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import traceback
import uuid
import warnings
from .model import validate_record
from .provenance import capture_code, verify_code_snapshot
from .util import atomic_json, canonical, digest, file_hash, now, read_json


@dataclass
class Context:
    store: object
    definition: dict
    parameters: dict
    inputs: list
    raw_inputs: list
    stage_inputs: dict = field(default_factory=dict)

    def records(self, dataset):
        ref = next((ref for ref in self.inputs if ref['dataset'] == dataset), None)
        if ref is None: raise ValueError(f'Undeclared input: {dataset}')
        return self.store.records(ref)

    def input_ref(self, dataset):
        ref = next((ref for ref in self.inputs if ref['dataset'] == dataset), None)
        if ref is None: raise ValueError(f'Undeclared input: {dataset}')
        return deepcopy(ref)

    def stage_ref(self, stage):
        if stage not in self.stage_inputs: raise ValueError(f'Undeclared stage input: {stage}')
        return deepcopy(self.stage_inputs[stage])

    def stage_records(self, stage):
        return self.store.records(self.stage_ref(stage))

    def raw_path(self, index=0):
        return self.store.artifact_dir(self.raw_inputs[index]) / 'payload'

    def raw_evidence(self, locator, index=0):
        return [{'input': deepcopy(self.raw_inputs[index]), 'locator': locator}]


@contextmanager
def _local_transform(root, entrypoint, snapshot):
    """Execute snapshotted sources in an isolated package, including relative helpers.

    This is trusted local Python execution, not a sandbox. Relative imports are
    loaded from the captured source map, avoiding stale pyc/module-cache reuse.
    """
    prefix = '_worldmodel_dataset_' + uuid.uuid4().hex
    sources = snapshot['sources']
    root = Path(root).resolve()

    class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def path_for(self, fullname):
            if fullname == prefix: return 'pipeline.py', True
            if not fullname.startswith(prefix + '.'): return None
            relative = fullname[len(prefix) + 1:].replace('.', '/')
            if relative + '/__init__.py' in sources: return relative + '/__init__.py', True
            if relative + '.py' in sources: return relative + '.py', False
            raise ImportError('Local helper is not in dataset code snapshot: ' + relative)

        def find_spec(self, fullname, path=None, target=None):
            found = self.path_for(fullname)
            if found is None: return None
            filename, package = found
            return importlib.util.spec_from_loader(fullname, self, origin=str(root / filename), is_package=package)

        def create_module(self, spec): return None

        def exec_module(self, module):
            filename, package = self.path_for(module.__name__)
            module.__file__ = str(root / filename)
            if package: module.__path__ = [str((root / filename).parent)]
            exec(compile(sources[filename], module.__file__, 'exec'), module.__dict__)

    loader = Loader()
    sys.meta_path.insert(0, loader)
    try:
        module = importlib.import_module(prefix)
        function = getattr(module, entrypoint.split(':', 1)[1], None)
        if not callable(function): raise ValueError('Stage entrypoint function is missing or not callable')
        yield function
    finally:
        sys.meta_path.remove(loader)
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + '.'): del sys.modules[name]


@contextmanager
def _legacy_transform(project, entrypoint):
    module, function = entrypoint.split(':', 1)
    if not module.startswith('worldmodel.'):
        raise ValueError('Legacy entrypoint must live in worldmodel so its code is captured')
    implementation = importlib.import_module(module)
    if Path(implementation.__file__).resolve() != (Path(project) / (module.replace('.', '/') + '.py')).resolve():
        raise ValueError('Loaded code does not match captured project')
    yield getattr(implementation, function)


def _schema_row(record, schema):
    if not isinstance(record, dict): raise ValueError('Stage rows must be JSON objects')
    canonical(record)
    checks = {'number': lambda x: type(x) in (int, float), 'integer': lambda x: type(x) is int,
              'string': lambda x: isinstance(x, str), 'boolean': lambda x: type(x) is bool,
              'object': lambda x: isinstance(x, dict), 'array': lambda x: isinstance(x, list),
              'null': lambda x: x is None}
    for name, kind in schema.get('required', {}).items():
        if name not in record: raise ValueError('Missing required stage field: ' + name)
        if not checks[kind](record[name]): raise ValueError('Stage field type mismatch: ' + name)
    if schema['format'] == 'evidence_jsonl': validate_record(record)


class Runner:
    def __init__(self, catalog, store, project):
        self.catalog, self.store, self.project = catalog, store, Path(project)

    def run(self, target, parameters=None, raw_refs=None, input_refs=None, *, stage=None):
        """Build selected stage and ancestors; external dependencies use their outputs.

        Overrides apply to the target dataset only. Input pins stop traversal at
        exact immutable versions. Named stage references include dataset,stage,version.
        """
        order = self.catalog.plan(target)
        self.catalog.stage_plan(target, stage)
        raw_refs, pinned = raw_refs or {}, input_refs or {}
        if set(pinned) - set(order) or target in pinned: raise ValueError('Input pins must name dependencies of target')
        if set(raw_refs) - set(order): raise ValueError('Raw pins must name datasets in the plan')
        if parameters is not None and not isinstance(parameters, dict): raise ValueError('Parameters must be an object')
        results = {}

        def build(dataset):
            if dataset in results: return results[dataset]
            if dataset in pinned:
                ref = pinned[dataset]
                if ref['dataset'] != dataset: raise ValueError('Input pin dataset mismatch')
                self.store.verify(ref); results[dataset] = ref; return ref
            definition = self.catalog.get(dataset)
            if definition['schema_version'] == 1 and not definition.get('entrypoint'):
                raise ValueError(f'{dataset}: configure an entrypoint and mapping before running')
            inputs = [build(parent) for parent in definition.get('dependencies', [])]
            raw = []
            if definition['kind'] == 'source':
                raw = raw_refs.get(dataset)
                if raw is None: raw = [self.store.latest_raw(dataset)]
                if not isinstance(raw, list) or not raw: raise ValueError('Source dataset requires raw inputs')
                for ref in raw:
                    if ref['dataset'] != dataset: raise ValueError('Raw pin dataset mismatch')
                    self.store.artifact(ref)
            params = {**definition.get('parameters', {}), **((parameters or {}) if dataset == target else {})}
            canonical(params)
            if definition['schema_version'] == 1:
                result = self._execute(definition, params, inputs, raw)
            else:
                stages = {s['id']: s for s in definition['stages']}
                stage_results = {}
                for name in self.catalog.stage_plan(dataset, stage if dataset == target else None):
                    descriptor = stages[name]
                    parents = {p: stage_results[p] for p in descriptor.get('depends_on', [])}
                    result = self._execute(definition, params, inputs, raw, descriptor, parents)
                    stage_results[name] = result
            results[dataset] = result
            return result
        return build(target)

    def _execute(self, definition, parameters, inputs, raw_inputs, stage=None, stage_inputs=None):
        dataset = definition['id']; stage_inputs = stage_inputs or {}
        all_inputs = inputs + list(stage_inputs.values())
        named = stage is not None
        name = stage['id'] if named else 'final'
        entrypoint = stage['entrypoint'] if named else definition['entrypoint']
        schema = stage['schema'] if named else {'format': 'evidence_jsonl'}
        validation = stage.get('validation', {}) if named else {'allow_empty': definition.get('allow_empty', False)}
        root = self.catalog.root / dataset if named else None
        with self.store.lock(dataset):
            run_id = uuid.uuid4().hex
            staging = self.store.scratch_dir(dataset) / run_id
            staging.mkdir(parents=True)
            run_path = self.store.runs_dir(dataset) / (run_id + '.json')
            attempt = {'run_id': run_id, 'dataset': dataset, 'stage': name, 'started_at': now(),
                       'status': 'running', 'inputs': all_inputs, 'raw_inputs': raw_inputs, 'parameters': parameters}
            atomic_json(run_path, attempt)
            audit = None
            try:
                code = capture_code(self.project, entrypoint, dataset_root=root)
                for ref in all_inputs: self.store.verify(ref)
                for ref in raw_inputs: self.store.artifact(ref)
                operation = digest({'definition': definition, 'stage': stage, 'parameters': parameters,
                    'inputs': all_inputs, 'raw_inputs': raw_inputs, 'code_files': code['files'],
                    'dataset_code_files': code.get('dataset_code', {}).get('files', {}),
                    'python': code['python'], 'packages': code['packages']})
                cache_path = self.store.dataset_dir(dataset) / 'manifests' / 'operations' / name / (operation + '.json')
                if named and stage.get('cache', 'content') == 'content' and cache_path.exists():
                    ref = read_json(cache_path)
                    if ref.get('dataset') != dataset or ref.get('stage') != name:
                        raise ValueError('Stage operation cache mismatch')
                    evicted = stage.get('retention', 'retain') == 'rebuildable' and not self.store.version_dir(ref).exists()
                    if not evicted:
                        self.store.verify(ref)
                        if self.store.manifest(ref).get('operation_key') != operation:
                            raise ValueError('Stage operation cache mismatch')
                        verify_code_snapshot(self.project, code, root)
                        self._publish_pointers(definition, ref)
                        attempt.update(status='succeeded', output=ref, cached=True, completed_at=now())
                        atomic_json(run_path, attempt)
                        return ref
                audit = sqlite3.connect(staging / 'validation.sqlite')
                audit.executescript('CREATE TABLE ids (id TEXT PRIMARY KEY); CREATE TABLE inputs (ref TEXT,id TEXT,PRIMARY KEY(ref,id));')
                if schema['format'] == 'evidence_jsonl':
                    for ref in all_inputs:
                        for record in self.store.records(ref):
                            if isinstance(record.get('id'), str):
                                audit.execute('INSERT OR IGNORE INTO inputs VALUES (?,?)', (digest(ref), record['id']))
                context = Context(self.store, deepcopy(definition), deepcopy(parameters), deepcopy(inputs),
                                  deepcopy(raw_inputs), deepcopy(stage_inputs))
                allowed = {canonical(ref) for ref in all_inputs + raw_inputs}
                count = 0
                loader = _local_transform(root, entrypoint, code['dataset_code']) if named else _legacy_transform(self.project, entrypoint)
                with loader as transform, (staging / 'records.jsonl').open('wb') as stream:
                    for record in transform(context):
                        count += 1
                        if named and count > validation.get('max_rows', 100000): raise ValueError('Stage row limit exceeded')
                        _schema_row(record, schema)
                        if schema['format'] == 'evidence_jsonl':
                            try: audit.execute('INSERT INTO ids VALUES (?)', (record['id'],))
                            except sqlite3.IntegrityError as error: raise ValueError('Duplicate record ID: ' + record['id']) from error
                            for evidence in record['evidence']:
                                ref = evidence['input']
                                if canonical(ref) not in allowed: raise ValueError('Record evidence references undeclared input')
                                if 'version' in ref and not audit.execute('SELECT 1 FROM inputs WHERE ref=? AND id=?', (digest(ref), evidence['record_id'])).fetchone():
                                    raise ValueError('Evidence references missing input record')
                        stream.write(canonical(record) + b'\n')
                    stream.flush(); os.fsync(stream.fileno())
                audit.close(); audit = None
                (staging / 'validation.sqlite').unlink()
                if not count and not validation.get('allow_empty', False): raise ValueError('Empty output; set allow_empty explicitly if intentional')
                for ref in all_inputs: self.store.verify(ref)
                for ref in raw_inputs: self.store.artifact(ref)
                verify_code_snapshot(self.project, code, root)
                output = staging / 'records.jsonl'
                from .rights import inherited_rights
                identity = {'rights': inherited_rights(self.store, all_inputs, raw_inputs),
                            'schema_version': 2 if named else 1, 'dataset': dataset, 'definition': definition,
                            'code': code, 'parameters': parameters, 'inputs': all_inputs, 'raw_inputs': raw_inputs,
                            'outputs': {'records.jsonl': {'sha256': file_hash(output), 'bytes': output.stat().st_size, 'rows': count}}}
                if named:
                    identity.update(stage=name, stage_definition=stage, operation_key=operation,
                                    retention=stage.get('retention', 'retain'))
                version = digest(identity)
                ref = {'dataset': dataset, **({'stage': name} if named else {}), 'version': version}
                atomic_json(staging / 'manifest.json', {**identity, 'version': version})
                destination = self.store.version_dir(ref)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists(): self.store.verify(ref); reused = True
                else: os.rename(staging, destination); reused = False
                attempt.update(status='succeeded', output=ref, completed_at=now(), reused=reused, cached=False)
                try:
                    self._publish_pointers(definition, ref)
                    if named and stage.get('cache', 'content') == 'content': atomic_json(cache_path, ref)
                    atomic_json(run_path, attempt)
                except OSError as error:
                    attempt['bookkeeping_error'] = str(error)
                    try:
                        atomic_json(run_path, attempt)
                    except OSError:
                        pass
                    warnings.warn(f'Output published as {dataset}@{version}; bookkeeping failed: {error}', RuntimeWarning)
                return ref
            except Exception as error:
                attempt.update(status='failed', completed_at=now(), error=str(error), traceback=traceback.format_exc())
                atomic_json(run_path, attempt)
                raise
            finally:
                if audit is not None: audit.close()
                if staging.exists(): shutil.rmtree(staging)

    def _publish_pointers(self, definition, ref):
        self.store.publish_index(ref)
        if 'stage' not in ref or ref['stage'] == definition.get('output_stage'):
            atomic_json(self.store.latest_path(definition['id']), ref)
