"""Dataset-local storage: ignored artifacts/scratch and tracked compact manifests.

Legacy raw/final directories are never consulted, moved or removed. Immutable
full manifests and receipts stay beside their payloads under artifacts/.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import uuid
from .util import atomic_json, digest, file_hash, hash_id, now, read_json, slug


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def dataset_dir(self, dataset):
        return self.root / slug(dataset)

    def initialize(self, dataset):
        base = self.dataset_dir(dataset)
        for name in ('artifacts/raw', 'artifacts/final', 'artifacts/samples',
                     'scratch/runs', 'manifests/raw', 'manifests/final',
                     'manifests/stages', 'manifests/samples'):
            (base / name).mkdir(parents=True, exist_ok=True)
        ignore = base / '.gitignore'
        previous = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
        rules = ('/artifacts/', '/scratch/', '/raw/', '/final/', '/processing/',
                 '/runs/', '/samples/', '/latest.json', '/raw-latest.json', '/.lock')
        missing = [rule for rule in rules if rule not in previous.splitlines()]
        if missing:
            temporary = ignore.with_name('.gitignore.' + uuid.uuid4().hex)
            try:
                temporary.write_text(previous + ('\n' if previous and not previous.endswith('\n') else '')
                                     + '\n'.join(missing) + '\n', encoding='utf-8')
                os.replace(temporary, ignore)
            finally:
                temporary.unlink(missing_ok=True)
        return base

    def scratch_dir(self, dataset):
        path = self.dataset_dir(dataset) / 'scratch'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def runs_dir(self, dataset):
        path = self.scratch_dir(dataset) / 'runs'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def latest_path(self, dataset, stage=None):
        base = self.dataset_dir(dataset) / 'manifests'
        return base / 'latest.json' if stage is None else base / 'stages' / slug(stage) / 'latest.json'

    def raw_latest_path(self, dataset):
        return self.dataset_dir(dataset) / 'manifests' / 'raw-latest.json'

    def samples_dir(self, dataset):
        path = self.dataset_dir(dataset) / 'artifacts' / 'samples'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sample_latest_path(self, dataset):
        return self.dataset_dir(dataset) / 'manifests' / 'samples' / 'latest.json'

    @staticmethod
    def _stage(ref):
        return slug(ref['stage']) if 'stage' in ref else 'final'

    @contextmanager
    def lock(self, dataset):
        base = self.initialize(dataset)
        with (self.scratch_dir(dataset) / '.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(f'Dataset already has an active writer: {dataset}') from error
            try:
                yield base
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def artifact_dir(self, ref):
        if 'stage' in ref:
            self._stage(ref)
        path = self.dataset_dir(ref['dataset']) / 'artifacts' / 'raw' / hash_id(ref['artifact'])
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def version_dir(self, ref):
        path = self.dataset_dir(ref['dataset']) / 'artifacts' / self._stage(ref) / hash_id(ref['version'])
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def import_file(self, dataset, path, source, *, update_latest=True):
        """Snapshot a local file. Different acquisition contexts get distinct refs."""
        path = Path(path)
        if not path.is_file():
            raise ValueError(f'Input is not a file: {path}')
        with self.lock(dataset) as base:
            staging = self.scratch_dir(dataset) / ('import-' + uuid.uuid4().hex)
            staging.mkdir()
            try:
                shutil.copyfile(path, staging / 'payload')
                identity = {'schema_version': 1, 'dataset': dataset,
                            'sha256': file_hash(staging / 'payload'),
                            'bytes': (staging / 'payload').stat().st_size,
                            'source': source, 'original_name': path.name, 'retrieved_at': now()}
                artifact = digest(identity)
                receipt = {**identity, 'artifact': artifact}
                atomic_json(staging / 'receipt.json', receipt)
                ref = {'dataset': dataset, 'artifact': artifact}
                destination = self.artifact_dir(ref)
                if destination.exists():
                    self.artifact(ref)
                else:
                    os.rename(staging, destination)
                self.publish_index(ref, update_latest=update_latest)
                return ref
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def latest_raw(self, dataset):
        path = self.raw_latest_path(dataset)
        if not path.exists():
            raise ValueError(f'No raw input for {dataset}; import a local file or explicitly fetch first')
        ref = read_json(path)
        if ref.get('dataset') != dataset:
            raise ValueError('Raw pointer dataset mismatch')
        self.artifact(ref)
        return ref

    def artifact(self, ref):
        directory = self.artifact_dir(ref)
        receipt = read_json(directory / 'receipt.json')
        identity = {k: v for k, v in receipt.items() if k != 'artifact'}
        if (receipt.get('artifact') != ref['artifact'] or receipt.get('dataset') != ref['dataset']
                or digest(identity) != ref['artifact']):
            raise ValueError('Raw receipt manifest hash mismatch')
        if (file_hash(directory / 'payload') != receipt['sha256']
                or (directory / 'payload').stat().st_size != receipt['bytes']):
            raise ValueError('Raw payload checksum mismatch')
        return receipt

    def manifest(self, ref):
        manifest = read_json(self.version_dir(ref) / 'manifest.json')
        identity = {k: v for k, v in manifest.items() if k != 'version'}
        if (manifest.get('version') != ref['version'] or manifest.get('dataset') != ref['dataset']
                or digest(identity) != ref['version']):
            raise ValueError('Final manifest hash mismatch')
        if self._stage(manifest) != self._stage(ref):
            raise ValueError('Manifest stage does not match reference stage')
        return manifest

    def publish_index(self, ref, *, update_latest=True):
        """Export a compact, derived metadata index; full artifacts remain authoritative.

        Code source bodies are omitted recursively. Configuration digests point to
        the full immutable manifest instead of duplicating large parameter values.
        The returned Path is tracked metadata, not a replacement integrity root.
        """
        self.initialize(ref['dataset'])
        if ('artifact' in ref) == ('version' in ref):
            raise ValueError('Index requires exactly one raw artifact or computed version')

        def compact(value, in_code=False):
            if isinstance(value, dict):
                return {key: compact(item, in_code or key == 'code')
                        for key, item in value.items()
                        if not (in_code and key in ('source', 'sources'))}
            if isinstance(value, list):
                return [compact(item, in_code) for item in value]
            return value

        if 'artifact' in ref:
            full = self.artifact(ref)
            index = compact(full)
            index['reference'] = dict(ref)
            index['receipt_path'] = str(self.artifact_dir(ref).relative_to(self.dataset_dir(ref['dataset'])) / 'receipt.json')
            path = self.dataset_dir(ref['dataset']) / 'manifests' / 'raw' / (ref['artifact'] + '.json')
            atomic_json(path, index)
            if update_latest:
                atomic_json(self.raw_latest_path(ref['dataset']), ref)
        else:
            self.verify(ref)
            full = self.manifest(ref)
            retained = ('schema_version', 'dataset', 'version', 'stage', 'output_stage',
                        'definition', 'code', 'inputs', 'raw_inputs', 'outputs', 'registry',
                        'backend_identity', 'rights')
            index = {key: compact(full[key], key == 'code') for key in retained if key in full}
            index['reference'] = dict(ref)
            index['stage'] = self._stage(ref)
            index['manifest_path'] = str(self.version_dir(ref).relative_to(self.dataset_dir(ref['dataset'])) / 'manifest.json')
            if 'parameters' in full:
                index['parameters_sha256'] = digest(full['parameters'])
            path = self.dataset_dir(ref['dataset']) / 'manifests' / self._stage(ref) / (ref['version'] + '.json')
            atomic_json(path, index)
            if update_latest:
                atomic_json(self.latest_path(ref['dataset'], self._stage(ref)), ref)
                if 'stage' not in ref:
                    atomic_json(self.latest_path(ref['dataset']), ref)
        return path

    def latest(self, dataset, stage=None):
        ref = read_json(self.latest_path(dataset, stage))
        if ref.get('dataset') != dataset:
            raise ValueError('Version pointer dataset mismatch')
        if stage is not None and self._stage(ref) != slug(stage):
            raise ValueError('Version pointer stage mismatch')
        self.verify(ref)
        return ref

    def verify(self, ref, recursive=True, _visited=None):
        visited = _visited if _visited is not None else set()
        key = (ref['dataset'], self._stage(ref), ref['version'])
        if key in visited:
            return True
        visited.add(key)
        manifest = self.manifest(ref)
        for name, expected in manifest['outputs'].items():
            if Path(name).name != name:
                raise ValueError('Unsafe output filename')
            path = self.version_dir(ref) / name
            if file_hash(path) != expected['sha256'] or path.stat().st_size != expected['bytes']:
                raise ValueError(f'Output checksum mismatch: {ref["dataset"]}/{name}')
        if recursive:
            for raw in manifest['raw_inputs']:
                self.artifact(raw)
            for parent in manifest['inputs']:
                self.verify(parent, _visited=visited)
        return True

    def records(self, ref, verify=True):
        if verify:
            self.verify(ref)
        import json
        with (self.version_dir(ref) / 'records.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)

    def lineage(self, ref):
        self.verify(ref)
        versions, artifacts = {}, {}

        def visit(current):
            key = (current['dataset'], self._stage(current), current['version'])
            if key in versions:
                return
            manifest = self.manifest(current)
            versions[key] = manifest
            for raw in manifest['raw_inputs']:
                artifacts[raw['dataset'] + '@' + raw['artifact']] = self.artifact(raw)
            for parent in manifest['inputs']:
                visit(parent)

        visit(ref)
        return {'root': ref, 'versions': list(versions.values()), 'artifacts': list(artifacts.values())}
