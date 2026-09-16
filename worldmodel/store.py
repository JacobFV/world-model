"""Dataset-local storage: ignored artifacts/scratch and tracked compact manifests.

Legacy raw/final directories are never consulted, moved or removed. Immutable
full manifests and receipts stay beside their payloads under artifacts/.

Raw artifacts have two layouts:

- single payload (schema 1): ``artifacts/raw/<artifact>/payload`` + ``receipt.json``
- sharded (schema 2, ``layout: "shards"``): ``artifacts/raw/<artifact>/shards/<n>``
  + ``receipt.json`` listing each shard's sha256/bytes/request/retrieved_at/complete.
  The receipt ``sha256`` is ``digest([[shard_sha256, shard_bytes], ...])``.

Verification modes: ``full`` (default) hashes every payload/shard; files of at
least ``HASH_MEMO_MIN_BYTES`` are re-hashed only when their (size, mtime, ctime,
inode) stat identity changes within one Store instance. ``size`` checks the
receipt digest and file sizes only; opt in with ``Store(root, raw_verify='size')``
or ``WORLD_MODEL_RAW_VERIFY=size`` for long iterative pipelines on trusted disks.
"""
from contextlib import contextmanager
import fcntl
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from .util import atomic_json, digest, file_hash, hash_id, now, read_json, slug

HASH_MEMO_MIN_BYTES = 64 * 1024 * 1024


def _place(source, destination, method):
    """Copy, hardlink (falls back to copy) or move (falls back to copy+unlink)."""
    if method == 'copy':
        shutil.copyfile(source, destination)
    elif method == 'link':
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)
    elif method == 'move':
        try:
            os.rename(source, destination)
        except OSError:
            shutil.copyfile(source, destination)
            os.unlink(source)
    else:
        raise ValueError('Import method must be copy, link or move')


class Store:
    def __init__(self, root, raw_verify=None):
        self.root = Path(root).resolve()
        self.raw_verify = raw_verify or os.environ.get('WORLD_MODEL_RAW_VERIFY') or 'full'
        if self.raw_verify not in ('full', 'size'):
            raise ValueError('raw_verify must be full or size')
        self._hash_memo = {}

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

    def _hash(self, path):
        stat = path.stat()
        if stat.st_size < HASH_MEMO_MIN_BYTES:
            return file_hash(path)
        identity = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev)
        memo = self._hash_memo.get(str(path))
        if memo and memo[0] == identity:
            return memo[1]
        checksum = file_hash(path)
        self._hash_memo[str(path)] = (identity, checksum)
        return checksum

    def _commit_raw(self, dataset, staging, identity, update_latest):
        artifact = digest(identity)
        atomic_json(staging / 'receipt.json', {**identity, 'artifact': artifact})
        ref = {'dataset': dataset, 'artifact': artifact}
        destination = self.artifact_dir(ref)
        if destination.exists():
            self.artifact(ref)
        else:
            os.rename(staging, destination)
        self.publish_index(ref, update_latest=update_latest)
        return ref

    def import_file(self, dataset, path, source, *, update_latest=True, method='copy'):
        """Snapshot a local file. Different acquisition contexts get distinct refs.

        ``method='link'`` hardlinks (no second copy; falls back to copy across
        filesystems) and ``method='move'`` renames the input into the store.
        """
        path = Path(path)
        if not path.is_file():
            raise ValueError(f'Input is not a file: {path}')
        with self.lock(dataset):
            staging = self.scratch_dir(dataset) / ('import-' + uuid.uuid4().hex)
            staging.mkdir()
            try:
                _place(path, staging / 'payload', method)
                identity = {'schema_version': 1, 'dataset': dataset,
                            'sha256': file_hash(staging / 'payload'),
                            'bytes': (staging / 'payload').stat().st_size,
                            'source': source, 'original_name': path.name, 'retrieved_at': now()}
                return self._commit_raw(dataset, staging, identity, update_latest)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def import_shards(self, dataset, shards, source, *, complete, stop_reason=None, update_latest=True,
                      method='link', trusted_hashes=False):
        """Publish a multi-shard raw artifact.

        ``shards`` is an ordered list of dicts with ``path`` plus metadata such as
        ``url``/``request``/``retrieved_at``/``complete``/``role`` (``data`` by
        default). Optional ``sha256``/``bytes`` are checked; with
        ``trusted_hashes=True`` a writer-computed ``sha256`` is accepted after a
        size check instead of re-hashing. A per-shard ``_method`` overrides ``method``.
        """
        if not isinstance(shards, list) or not shards:
            raise ValueError('Sharded import requires at least one shard')
        if type(complete) is not bool:
            raise ValueError('Sharded import requires an explicit complete flag')
        if not complete and not stop_reason:
            raise ValueError('Incomplete raw artifacts require a stop_reason')
        with self.lock(dataset):
            staging = self.scratch_dir(dataset) / ('import-' + uuid.uuid4().hex)
            (staging / 'shards').mkdir(parents=True)
            try:
                entries = []
                for index, shard in enumerate(shards):
                    source_path = Path(shard['path'])
                    if not source_path.is_file():
                        raise ValueError(f'Shard is not a file: {source_path}')
                    destination = staging / 'shards' / str(index)
                    _place(source_path, destination, shard.get('_method', method))
                    size = destination.stat().st_size
                    if shard.get('bytes') is not None and shard['bytes'] != size:
                        raise ValueError(f'Shard {index} size does not match declared bytes')
                    checksum = shard.get('sha256') if trusted_hashes and shard.get('sha256') else file_hash(destination)
                    if shard.get('sha256') and checksum != shard['sha256']:
                        raise ValueError(f'Shard {index} checksum does not match declared sha256')
                    metadata = {k: v for k, v in shard.items() if k not in ('path', 'sha256', 'bytes', '_method', 'index')}
                    entries.append({**metadata, 'index': index, 'path': f'shards/{index}', 'sha256': checksum,
                                    'bytes': size, 'role': shard.get('role', 'data'),
                                    'complete': shard.get('complete', True)})
                identity = {'schema_version': 2, 'layout': 'shards', 'dataset': dataset,
                            'sha256': digest([[e['sha256'], e['bytes']] for e in entries]),
                            'bytes': sum(e['bytes'] for e in entries), 'shard_count': len(entries),
                            'shards': entries, 'complete': complete, 'stop_reason': stop_reason,
                            'source': source, 'retrieved_at': now()}
                return self._commit_raw(dataset, staging, identity, update_latest)
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

    def receipt(self, ref):
        """Read a raw receipt and check its content address (no payload hashing)."""
        receipt = read_json(self.artifact_dir(ref) / 'receipt.json')
        identity = {k: v for k, v in receipt.items() if k != 'artifact'}
        if (receipt.get('artifact') != ref['artifact'] or receipt.get('dataset') != ref['dataset']
                or digest(identity) != ref['artifact']):
            raise ValueError('Raw receipt manifest hash mismatch')
        return receipt

    def artifact(self, ref, verify=None):
        mode = verify or self.raw_verify
        if mode not in ('full', 'size'):
            raise ValueError('verify must be full or size')
        directory = self.artifact_dir(ref)
        receipt = self.receipt(ref)
        if receipt.get('layout') == 'shards':
            shards = receipt.get('shards')
            if (not isinstance(shards, list) or len(shards) != receipt.get('shard_count')
                    or digest([[s['sha256'], s['bytes']] for s in shards]) != receipt['sha256']
                    or sum(s['bytes'] for s in shards) != receipt['bytes']):
                raise ValueError('Raw shard list mismatch')
            for index, shard in enumerate(shards):
                if shard.get('index') != index or shard.get('path') != f'shards/{index}':
                    raise ValueError('Unsafe raw shard path')
                path = directory / shard['path']
                if path.stat().st_size != shard['bytes'] or (mode == 'full' and self._hash(path) != shard['sha256']):
                    raise ValueError(f'Raw payload checksum mismatch (shard {index})')
            return receipt
        payload = directory / 'payload'
        if payload.stat().st_size != receipt['bytes'] or (mode == 'full' and self._hash(payload) != receipt['sha256']):
            raise ValueError('Raw payload checksum mismatch')
        return receipt

    def raw_shards(self, ref, role=None):
        """Ordered shard descriptors with absolute ``path``; single payloads appear as shard 0."""
        directory = self.artifact_dir(ref)
        receipt = self.receipt(ref)
        if receipt.get('layout') == 'shards':
            shards = []
            for index, shard in enumerate(receipt['shards']):
                if shard.get('index') != index or shard.get('path') != f'shards/{index}':
                    raise ValueError('Unsafe raw shard path')
                shards.append({**shard, 'path': directory / shard['path']})
        else:
            sampled = 'sampling' in (receipt.get('source') or {})
            shards = [{'index': 0, 'path': directory / 'payload', 'sha256': receipt['sha256'],
                       'bytes': receipt['bytes'], 'role': 'data', 'complete': not sampled,
                       'retrieved_at': receipt.get('retrieved_at')}]
        return [shard for shard in shards if role is None or shard.get('role', 'data') == role]

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
        Sharded raw receipts are summarized (count, roles, digest of the shard list).
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
            if 'shards' in index:
                shards = index.pop('shards')
                index['shards_summary'] = {'count': len(shards), 'shards_sha256': digest(shards),
                                           'roles': sorted({s.get('role', 'data') for s in shards}),
                                           'incomplete_shards': sum(not s.get('complete', True) for s in shards)}
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
            if path.stat().st_size != expected['bytes'] or self._hash(path) != expected['sha256']:
                raise ValueError(f'Output checksum mismatch: {ref["dataset"]}/{name}')
        if recursive:
            for raw in manifest['raw_inputs']:
                self.artifact(raw)
            for parent in manifest['inputs']:
                self.verify(parent, _visited=visited)
        return True

    def records(self, ref, verify=True):
        """Stream records from ``records.jsonl`` or gzip-compressed ``records.jsonl.gz``."""
        if verify:
            self.verify(ref)
        directory = self.version_dir(ref)
        plain, compressed = directory / 'records.jsonl', directory / 'records.jsonl.gz'
        if plain.exists():
            stream = plain.open(encoding='utf-8')
        elif compressed.exists():
            stream = gzip.open(compressed, 'rt', encoding='utf-8')
        else:
            raise FileNotFoundError(f'No records output for {ref["dataset"]}@{ref["version"]}')
        with stream:
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
