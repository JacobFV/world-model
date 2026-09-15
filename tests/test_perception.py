import copy
import unittest
from worldmodel.perception import ObservationWrapper


class HiddenEnvironment:
    def reset(self, seed=0):
        self.t = 0
        return {'visible': 0, 'masked': 10, 'secret': 900}, {'secret': 800}

    def step(self, action):
        self.t += 1
        return {'visible': self.t, 'masked': 10, 'secret': 900}, 0, False, self.t == 10, {'secret': 800}


class PerceptionTests(unittest.TestCase):
    def test_delay_mask_allowlist_and_visible_only_memory(self):
        env = ObservationWrapper(HiddenEnvironment(), ['visible', 'masked'], masked=['masked'], delay=2, memory_size=3)
        obs, info = env.reset(seed=4)
        self.assertEqual(obs, {'visible': 0, 'masked': None})
        self.assertNotIn('secret', info)
        self.assertEqual(env.step({})[0]['visible'], 0)
        self.assertEqual(env.step({})[0]['visible'], 0)
        self.assertEqual(env.step({})[0]['visible'], 1)
        self.assertEqual(env.belief_memory, [{'visible': 0, 'masked': None}, {'visible': 0, 'masked': None}, {'visible': 1, 'masked': None}])
        memory = env.belief_memory; memory[0]['visible'] = 999
        self.assertNotEqual(env.belief_memory[0]['visible'], 999)

    def test_noise_is_seeded_locally_and_reset_reproducible(self):
        env = ObservationWrapper(HiddenEnvironment(), ['visible'], noise_std=.2)
        first = [env.reset(seed=17)[0], env.step({})[0]]
        again = [env.reset(seed=17)[0], env.step({})[0]]
        self.assertEqual(first, again)
        self.assertNotEqual(first[0]['visible'], 0)
        self.assertNotEqual(first[0], env.reset(seed=18)[0])

    def test_explicit_allowlist_and_mask_validation(self):
        for kwargs in ({'allowlist': []}, {'allowlist': ['visible'], 'masked': ['secret']},
                       {'allowlist': ['visible'], 'delay': -1}, {'allowlist': ['visible'], 'noise_std': float('nan')}):
            with self.assertRaises(ValueError): ObservationWrapper(HiddenEnvironment(), **kwargs)
        env = ObservationWrapper(HiddenEnvironment(), ['absent'])
        with self.assertRaises(ValueError): env.reset()

    def test_reset_termination_flags_are_preserved_without_info_leak(self):
        class Ended(HiddenEnvironment):
            def reset(self, seed=0): return {'visible': 0}, {'terminated': True, 'secret': 12}
        env = ObservationWrapper(Ended(), ['visible'])
        _, info = env.reset()
        self.assertTrue(info['terminated'])
        self.assertNotIn('secret', info)
        with self.assertRaises(ValueError): env.step({})

    def test_delayed_memory_payload_is_bounded(self):
        class Large(HiddenEnvironment):
            def reset(self, seed=0): return {'visible': 'x' * 65537}, {}
        with self.assertRaisesRegex(ValueError, 'budget'):
            ObservationWrapper(Large(), ['visible']).reset()

    def test_report_failure_requires_reset_and_preserves_visible_memory(self):
        class Missing(HiddenEnvironment):
            calls = 0
            def step(self, action):
                self.calls += 1
                return {}, 0, True, False, {}
        base = Missing(); env = ObservationWrapper(base, ['visible'])
        env.reset(); before = env.belief_memory
        with self.assertRaises(ValueError): env.step({})
        self.assertEqual(env.belief_memory, before)
        with self.assertRaisesRegex(ValueError, 'Reset required'): env.step({})
        self.assertEqual(base.calls, 1)

    def test_failed_reset_invalidates_prior_episode(self):
        class FailingReset(HiddenEnvironment):
            fail = False
            def reset(self, seed=0):
                if self.fail: raise ValueError('Failed remote reset')
                return super().reset(seed=seed)
        base = FailingReset(); env = ObservationWrapper(base, ['visible'])
        env.reset(); base.fail = True
        with self.assertRaises(ValueError): env.reset()
        with self.assertRaisesRegex(ValueError, 'Reset required'): env.step({})

    def test_post_noise_report_and_cumulative_memory_are_bounded(self):
        from unittest.mock import patch
        import json
        class Vector(HiddenEnvironment):
            def reset(self, seed=0): return {'visible': [0] * 2000}, {}
            def step(self, action): return {'visible': [0] * 2000}, 0, False, False, {}
        env = ObservationWrapper(Vector(), ['visible'], noise_std=1, memory_size=1000)
        # Small test budget exercises the same cumulative guard without a large fixture.
        with patch('worldmodel.perception._HISTORY_BUDGET', 100000, create=True):
            env.reset(seed=1)
            env.step({})
            memory, queue = env.belief_memory, copy.deepcopy(list(env._queue))
            with self.assertRaisesRegex(ValueError, 'budget'): env.step({})
            self.assertEqual(env.belief_memory, memory)
            self.assertEqual(list(env._queue), queue)
            self.assertLess(sum(len(json.dumps(o).encode()) for o in memory + queue), 100000)
            with self.assertRaisesRegex(ValueError, 'Reset required'): env.step({})

    def test_noise_expansion_respects_per_observation_limit(self):
        class Vector(HiddenEnvironment):
            def reset(self, seed=0): return {'visible': [0] * 4000}, {}
        env = ObservationWrapper(Vector(), ['visible'], noise_std=1)
        with self.assertRaisesRegex(ValueError, 'budget'): env.reset()
        self.assertEqual(env.belief_memory, [])

    def test_close_forwards_and_invalidates_wrapper(self):
        class Closable(HiddenEnvironment):
            closed = False
            def close(self): self.closed = True
        base = Closable(); env = ObservationWrapper(base, ['visible'])
        env.reset(); env.close()
        self.assertTrue(base.closed)
        with self.assertRaisesRegex(ValueError, 'Reset required'): env.step({})
