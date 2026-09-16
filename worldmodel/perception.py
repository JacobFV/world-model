"""Agent-visible perception over an environment; privileged info is discarded."""
from collections import deque
from copy import deepcopy
import math
import json
import random

_HISTORY_BUDGET = 8 * 1024 * 1024
_OBSERVATION_BUDGET = 65536


class ObservationWrapper:
    """Top-level allowlist, masks, reporting delay and local Gaussian sensor noise.

    Delay is measured in successful environment transitions. Before sufficient
    history exists, reports use the reset observation. Numeric leaves receive
    independent noise when reported; masked values are always None. Belief memory
    is a bounded copy of delivered observations, not latent simulation state.
    A failed reset, step, or report requires reset because an underlying
    environment transition may already have occurred. Visible buffers commit
    only after raw and noisy JSON payloads satisfy their size budgets.
    This is an interface boundary, not a security sandbox for arbitrary Python.
    """
    def __init__(self, env, allowlist, *, masked=(), delay=0, noise_std=0, memory_size=1):
        if not isinstance(allowlist, (list, tuple)) or not 1 <= len(allowlist) <= 100 or any(not isinstance(k, str) or not k for k in allowlist) or len(set(allowlist)) != len(allowlist):
            raise ValueError('A nonempty unique observation allowlist is required')
        if not isinstance(masked, (list, tuple, set)) or any(not isinstance(k, str) for k in masked) or set(masked) - set(allowlist):
            raise ValueError('Masks must be included in the allowlist')
        if type(delay) is not int or not 0 <= delay <= 1000:
            raise ValueError('delay must be in 0..1000')
        if type(memory_size) is not int or not 1 <= memory_size <= 1000:
            raise ValueError('memory_size must be in 1..1000')
        if type(noise_std) not in (int, float) or not math.isfinite(noise_std) or noise_std < 0:
            raise ValueError('noise_std must be finite and nonnegative')
        self._env = env
        self.allowlist = tuple(allowlist)
        self.masked = frozenset(masked)
        self.delay = delay
        self.noise_std = noise_std
        self._queue = deque(maxlen=delay + 1)
        self._memory = deque(maxlen=memory_size)
        self._queue_sizes = deque(maxlen=delay + 1)
        self._memory_sizes = deque(maxlen=memory_size)
        self._rng = random.Random(0)
        self._step = 0
        self._ready = False
        self._terminated = self._truncated = False

    @property
    def spec(self):
        """Expose action declarations and visible selectors, without hidden ports."""
        underlying = getattr(self._env, 'spec', None)
        if not isinstance(underlying, dict) or not isinstance(underlying.get('actions'), dict) or not isinstance(underlying.get('observations'), dict):
            raise ValueError('Underlying environment must declare actions and observations')
        if set(self.allowlist) - set(underlying['observations']):
            raise ValueError('Allowlisted observation is not declared in environment spec')
        selectors = {key: deepcopy(underlying['observations'][key]) for key in self.allowlist}
        for key in self.masked:
            selectors[key]['masked'] = True
        return {'actions': deepcopy(underlying['actions']), 'observations': selectors}

    @property
    def belief_memory(self):
        return deepcopy(list(self._memory))

    def _filter(self, observation):
        if not isinstance(observation, dict) or set(self.allowlist) - set(observation):
            raise ValueError('Observation is missing an allowlisted field')
        filtered = {key: None if key in self.masked else deepcopy(observation[key]) for key in self.allowlist}
        return filtered

    def _size(self, observation):
        try:
            size = len(json.dumps(observation, allow_nan=False).encode('utf-8'))
        except (ValueError, TypeError) as exc:
            raise ValueError('Visible observations must be finite JSON') from exc
        if size > _OBSERVATION_BUDGET:
            raise ValueError('Perception observation/history budget exceeded')
        return size

    def _noise(self, value):
        if type(value) in (int, float):
            if not math.isfinite(value): raise ValueError('Observed numbers must be finite')
            result = value + self._rng.gauss(0, self.noise_std) if self.noise_std else value
            if not math.isfinite(result): raise ValueError('Sensor noise produced a nonfinite value')
            return result
        if isinstance(value, dict): return {k: self._noise(v) for k, v in value.items()}
        if isinstance(value, list): return [self._noise(v) for v in value]
        return deepcopy(value)

    def _report(self, observation, *, reset=False):
        filtered = self._filter(observation)
        raw_size = self._size(filtered)
        queue = deque(() if reset else self._queue, maxlen=self._queue.maxlen)
        queue_sizes = deque(() if reset else self._queue_sizes, maxlen=self._queue_sizes.maxlen)
        memory = deque(() if reset else self._memory, maxlen=self._memory.maxlen)
        memory_sizes = deque(() if reset else self._memory_sizes, maxlen=self._memory_sizes.maxlen)
        queue.append(filtered)
        queue_sizes.append(raw_size)
        visible = self._noise(queue[0])
        noisy_size = self._size(visible)
        memory_sizes.append(noisy_size)
        if sum(queue_sizes) + sum(memory_sizes) > _HISTORY_BUDGET:
            raise ValueError('Perception observation/history budget exceeded')
        memory.append(deepcopy(visible))
        self._queue, self._queue_sizes = queue, queue_sizes
        self._memory, self._memory_sizes = memory, memory_sizes
        return visible

    def _info(self):
        return {'step': self._step, 'terminated': self._terminated, 'truncated': self._truncated, 'perception': {'delay_steps': self.delay, 'noise_std': self.noise_std,
                'masked': sorted(self.masked)}, 'belief_memory': self.belief_memory}

    def reset(self, seed=0):
        self._ready = False
        if type(seed) is not int: raise ValueError('seed must be integer')
        observation, info = self._env.reset(seed=seed)
        self._rng = random.Random(seed)
        visible = self._report(observation, reset=True)
        self._step = 0
        self._terminated = bool(info.get('terminated', False))
        self._truncated = bool(info.get('truncated', False))
        report_info = self._info()
        self._ready = not (self._terminated or self._truncated)
        return visible, report_info

    def step(self, action):
        if not self._ready: raise ValueError('Reset required before stepping or after episode end')
        self._ready = False
        observation, reward, terminated, truncated, info = self._env.step(action)
        visible = self._report(observation)
        self._step += 1
        self._terminated, self._truncated = bool(terminated), bool(truncated)
        report_info = self._info()
        self._ready = not (terminated or truncated)
        return visible, reward, terminated, truncated, report_info

    def close(self):
        self._ready = False
        if hasattr(self._env, 'close'):
            self._env.close()


def split_observations(observation, allowlists):
    """Per-actor private views: each actor receives copies of only its allowlisted keys.

    ``allowlists`` maps actor ID to a list of observation keys. Unknown keys fail
    rather than silently yielding None, so private information cannot leak by typo.
    """
    if not isinstance(observation, dict) or not isinstance(allowlists, dict) or not allowlists:
        raise ValueError('Observation and nonempty actor allowlists are required')
    views = {}
    for actor, keys in allowlists.items():
        if not isinstance(actor, str) or not actor or not isinstance(keys, (list, tuple)) or len(set(keys)) != len(keys):
            raise ValueError('Each actor needs a unique list of observation keys')
        missing = [k for k in keys if k not in observation]
        if missing:
            raise ValueError(f'Actor {actor} allowlists undeclared observations: {missing}')
        views[actor] = {k: deepcopy(observation[k]) for k in keys}
    return views
