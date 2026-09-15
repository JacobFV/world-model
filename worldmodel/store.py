"""Filesystem source of truth. Published manifests and bytes are append-only."""
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
        for name in ('raw', 'processing', 'final', 'runs'):
            (base / name).mkdir(parents=True, exist_ok=True)
        return base

    @contextmanager
    def lock(self, dataset):
        base = self.initialize(dataset)
        with (base / '.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(f'Dataset already has an active writer: {dataset}') from error
            try:
                yield base
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def artifact_dir(self, ref):
        return self.dataset_dir(ref['dataset']) / 'raw' / hash_id(ref['artifact'])

    def version_dir(self, ref):
        return self.dataset_dir(ref['dataset']) / 'final' / hash_id(ref['version'])

    def import_file(self, dataset, path, source, *, update_latest=True):
        """Snapshot a local file. Different acquisition contexts get distinct refs."""
        path = Path(path)
        if not path.is_file():
            raise ValueError(f'Input is not a file: {path}')
        with self.lock(dataset) as base:
            staging = base / 'processing' / ('import-' + uuid.uuid4().hex)
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
                if update_latest:
                    atomic_json(base / 'raw-latest.json', ref)
                return ref
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def latest_raw(self, dataset):
        path = self.dataset_dir(dataset) / 'raw-latest.json'
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
        if file_hash(directory / 'payload') != receipt['sha256']:
            raise ValueError('Raw payload checksum mismatch')
        return receipt

    def manifest(self, ref):
        manifest = read_json(self.version_dir(ref) / 'manifest.json')
        identity = {k: v for k, v in manifest.items() if k != 'version'}
        if (manifest.get('version') != ref['version'] or manifest.get('dataset') != ref['dataset']
                or digest(identity) != ref['version']):
            raise ValueError('Final manifest hash mismatch')
        return manifest

    def latest(self, dataset):
        ref = read_json(self.dataset_dir(dataset) / 'latest.json')
        if ref.get('dataset') != dataset:
            raise ValueError('Version pointer dataset mismatch')
        self.verify(ref)
        return ref

    def verify(self, ref, recursive=True, _visited=None):
        visited = _visited if _visited is not None else set()
        key = (ref['dataset'], ref['version'])
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
            key = current['dataset'] + '@' + current['version']
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
