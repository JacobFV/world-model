"""Chunked JSON and binary array checkpoints: verified write + read throughput.

chunked_json streams canonical JSON into zlib chunks (sha256 per chunk and whole
document) and reads it back with full verification. array writes raw int64/float64
columns with sha256 per buffer. Units are uncompressed payload bytes written+read.
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

KERNEL = 'checkpoints'
SIZES = {
    'small': {'json_mb': 10, 'array_mb': 100},
    'medium': {'json_mb': 100, 'array_mb': 1000},
    'large': {'json_mb': 1000, 'array_mb': 4000},
}
BACKENDS = ('chunked_json', 'array')


def run(size, params, backend):
    from worldmodel.checkpoints import read_array_checkpoint, read_checkpoint, stream_digest, write_array_checkpoint, write_checkpoint
    started = time.perf_counter()
    root = Path(tempfile.mkdtemp(prefix='wm-checkpoint-bench-'))
    try:
        if backend == 'chunked_json':
            import random
            rng = random.Random(1)
            rows = max(1, params['json_mb'] * 1024 * 1024 // 2000)
            payload = {'history': [{'inputs': [{'value': rng.random(), 'unit': 'cent'}] * 4} for _ in range(rows // 4)],
                       'trace': [[rng.random() for _ in range(95)] for _ in range(rows)]}
            checkpoint = {'schema_version': 1, 'identity': 'a' * 64, 'payload': payload}
            setup = time.perf_counter() - started
            kernel_started = time.perf_counter()
            checkpoint['checksum'] = stream_digest(checkpoint)
            info = write_checkpoint(checkpoint, root / 'cp')
            written = time.perf_counter()
            restored = read_checkpoint(root / 'cp')
            kernel = time.perf_counter() - kernel_started
            assert restored['checksum'] == checkpoint['checksum']
            details = {'document_bytes': info['bytes'], 'compressed_bytes': info['compressed_bytes'], 'chunks': info['chunks'],
                       'write_seconds': written - kernel_started, 'read_seconds': kernel - (written - kernel_started)}
            units = info['bytes'] * 2
        else:
            import numpy as np
            count = params['array_mb'] * 1024 * 1024 // 16
            arrays = {'cents': np.arange(count, dtype=np.int64) * 7, 'values': np.linspace(0, 1, count)}
            setup = time.perf_counter() - started
            kernel_started = time.perf_counter()
            info = write_array_checkpoint(arrays, {'step': 1}, root / 'arrays')
            written = time.perf_counter()
            loaded, _ = read_array_checkpoint(root / 'arrays')
            kernel = time.perf_counter() - kernel_started
            assert np.array_equal(loaded['cents'], arrays['cents'])
            details = {'bytes': info['bytes'], 'write_seconds': written - kernel_started, 'read_seconds': kernel - (written - kernel_started)}
            units = info['bytes'] * 2
        return {'units': units, 'unit': 'bytes', 'kernel_seconds': kernel, 'setup_seconds': setup, 'details': details}
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    from harness import main
    main(globals())
