"""Chunked JSON and binary array checkpoints keep integrity and atomicity guarantees."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldmodel.backends import numpy_available
from worldmodel.checkpoints import read_array_checkpoint, read_checkpoint, stream_digest, write_array_checkpoint, write_checkpoint
from worldmodel.util import canonical, digest


def envelope(size=2000):
    payload = {'history': [{'inputs': [{'value': i, 'unit': 'x', 'text': 'ü' * (i % 7)}]} for i in range(size)],
               'state': [{'key': ['a', 'b'], 'item': {'value': [0.1 * i for i in range(50)]}}], 'nested': {'z': {'y': {'x': {'w': [1, 2, {'v': None}]}}}}}
    value = {'schema_version': 1, 'identity': 'a' * 64, 'payload': payload}
    return {**value, 'checksum': digest(value)}


class ChunkedCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_stream_digest_equals_canonical_digest(self):
        wide = {'rows': [[i * .5, {'k': 'ü', 'z': [i] * 300}] for i in range(700)], 'flat': list(range(1000))}
        for value in (envelope(10), wide, {'b': 1, 'a': [1.5, 'é', {'d': {}, 'c': []}]}, [], {}, 3, 'x'):
            self.assertEqual(stream_digest(value), digest(value))

    def test_roundtrip_equals_json_checkpoint(self):
        checkpoint = envelope()
        info = write_checkpoint(checkpoint, self.root / 'cp', chunk_bytes=4096)
        self.assertGreater(info['chunks'], 5)
        self.assertEqual(info['bytes'], len(canonical(checkpoint)))
        self.assertEqual(info['document_sha256'], digest(checkpoint))
        self.assertEqual(read_checkpoint(self.root / 'cp'), checkpoint)
        with self.assertRaisesRegex(ValueError, 'exists'):
            write_checkpoint(checkpoint, self.root / 'cp')
        changed = json.loads(json.dumps(checkpoint)); changed['payload']['nested'] = 1
        write_checkpoint(changed, self.root / 'cp', overwrite=True)
        self.assertEqual(read_checkpoint(self.root / 'cp')['payload']['nested'], 1)

    def test_corruption_missing_chunk_and_manifest_tampering_rejected(self):
        checkpoint = envelope()
        write_checkpoint(checkpoint, self.root / 'cp', chunk_bytes=4096)
        chunk = sorted((self.root / 'cp').glob('*.jsonz'))[1]
        original = chunk.read_bytes()
        chunk.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            read_checkpoint(self.root / 'cp')
        chunk.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            read_checkpoint(self.root / 'cp')
        chunk.write_bytes(original)
        manifest = json.loads((self.root / 'cp' / 'manifest.json').read_text())
        manifest['document_bytes'] += 1
        (self.root / 'cp' / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'manifest integrity'):
            read_checkpoint(self.root / 'cp')

    def test_failed_writer_publishes_nothing_and_keeps_previous_checkpoint(self):
        checkpoint = envelope()
        write_checkpoint(checkpoint, self.root / 'cp', chunk_bytes=4096)
        import worldmodel.checkpoints as module
        with patch.object(module.zlib, 'compress', side_effect=[b'x', OSError('disk full')]):
            with self.assertRaises(OSError):
                write_checkpoint(envelope(3000), self.root / 'cp', chunk_bytes=4096, overwrite=True)
        self.assertEqual(read_checkpoint(self.root / 'cp'), checkpoint)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['cp'])

    def test_array_checkpoint_lists_roundtrip_and_reject_corruption(self):
        arrays = {'ints': [1, -2, 2 ** 62], 'floats': [0.1, -3.5e300, 0.0], 'flags': [True, False]}
        write_array_checkpoint(arrays, {'note': 'lists'}, self.root / 'arrays')
        loaded, metadata = read_array_checkpoint(self.root / 'arrays')
        self.assertEqual(metadata, {'note': 'lists'})
        self.assertEqual({k: v.tolist() if hasattr(v, 'tolist') else v for k, v in loaded.items()}, arrays)
        with self.assertRaises(ValueError):
            write_array_checkpoint({'bad': [1, 'x']}, {}, self.root / 'bad')
        with self.assertRaises(ValueError):
            write_array_checkpoint({'ints': [1]}, {'t': (1, 2)}, self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())
        target = next((self.root / 'arrays').glob('*.bin'))
        target.write_bytes(target.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, 'integrity'):
            read_array_checkpoint(self.root / 'arrays')

    @unittest.skipUnless(numpy_available(), 'numpy optional')
    def test_numpy_array_checkpoint_is_exact(self):
        import numpy as np
        rng = np.random.default_rng(3)
        arrays = {'cents': rng.integers(-2 ** 62, 2 ** 62, 10000, dtype=np.int64), 'values': rng.standard_normal((100, 7)),
                  'mask': rng.random(333) < .5, 'small': rng.integers(0, 9, 10, dtype=np.int32)}
        info = write_array_checkpoint(arrays, {'step': 5}, self.root / 'np')
        self.assertEqual(info['arrays'], 4)
        loaded, metadata = read_array_checkpoint(self.root / 'np')
        self.assertEqual(metadata, {'step': 5})
        for name, array in arrays.items():
            self.assertEqual(loaded[name].dtype, array.dtype)
            self.assertTrue(np.array_equal(loaded[name], array))


class EvaluatorChunkedCheckpointTests(unittest.TestCase):
    def setUp(self):
        from tests.test_materialize import MaterializeTests
        self.fixture = MaterializeTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def evaluator(self):
        from worldmodel.checkpoints import CheckpointEvaluator
        f = self.fixture
        return CheckpointEvaluator(f.store, f.graph, f.request, 2, f.registry)

    def test_chunked_evaluator_checkpoint_restores_like_json(self):
        a = self.evaluator(); a([], 7); history = []
        for flow in (1, 3):
            history.append({'inputs': [{'binding': 'a_flow', 'port': 'flow', 'value': flow, 'unit': 'unit/second'}]})
            a(history, 7)
        target = self.fixture.root / 'chunked'
        info = a.checkpoint(format='chunked', directory=target, chunk_bytes=1024)
        self.assertEqual(info['checksum'], a.checkpoint()['checksum'])
        self.assertEqual(read_checkpoint(target), a.checkpoint())
        b = self.evaluator(); b.restore(target)
        history.append({'inputs': []})
        self.assertEqual(a(history, 7), b(history, 7))
        with self.assertRaises(ValueError):
            a.checkpoint(format='chunked')
        c = self.evaluator(); c([], 7); before = c.checkpoint()
        chunk = next(target.glob('*.jsonz')); chunk.write_bytes(chunk.read_bytes()[:-2])
        with self.assertRaises(ValueError):
            c.restore(target)
        self.assertEqual(c.checkpoint(), before)
        from worldmodel.limits import LimitExceeded, use_limits
        with use_limits(checkpoint_max_json_bytes=100):
            with self.assertRaisesRegex(LimitExceeded, 'checkpoint_max_json_bytes=100'):
                self.evaluator().restore(json.loads(json.dumps(a.checkpoint())))


if __name__ == '__main__':
    unittest.main()
