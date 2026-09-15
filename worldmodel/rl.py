"""Bounded offline RL helpers. Toy learning success is not causal validation."""
from copy import deepcopy
import json
import math
import random


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in {low}..{high}')
    return value


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite numeric')
    return value


def _seeds(values, name):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 10000 or any(type(v) is not int for v in values) or len(set(values)) != len(values):
        raise ValueError(f'{name} must contain 1..10000 unique integer seeds')
    return list(values)


def train_tabular(env_factory, actions, observation_encoder, *, training_seeds, evaluation_seeds,
                  max_steps=100, alpha=.2, gamma=.95, epsilon=.2, seed=0, baseline_action=0):
    """Train tabular Q-learning then evaluate a frozen policy on held-out seeds.

    actions is an explicit finite list of action dictionaries. The encoder maps
    visible observations to finite JSON data; serialized encodings are Q keys.
    Ties choose the first action. Evaluation and fixed-action baseline use the
    same held-out seeds and separate fresh environment instances. The trainer's
    max_steps cap treats its boundary as terminal (finite-horizon objective).
    No live model fitting, parallel workers, or implicit external dependencies.
    """
    train_seeds, eval_seeds = _seeds(training_seeds, 'training_seeds'), _seeds(evaluation_seeds, 'evaluation_seeds')
    if set(train_seeds) & set(eval_seeds): raise ValueError('Training and evaluation seeds must be disjoint')
    _integer(max_steps, 'max_steps', 1, 1000)
    _integer(seed, 'seed', -2**63, 2**63 - 1)
    if (len(train_seeds) + 2 * len(eval_seeds)) * max_steps > 1000000:
        raise ValueError('Training/evaluation transition budget exceeds 1000000')
    if not isinstance(actions, list) or not 1 <= len(actions) <= 100 or any(not isinstance(a, dict) for a in actions):
        raise ValueError('actions must contain 1..100 action dictionaries')
    try:
        encoded_actions = [json.dumps(a, sort_keys=True, allow_nan=False) for a in actions]
    except (ValueError, TypeError) as exc:
        raise ValueError('Actions must be finite JSON data') from exc
    if len(set(encoded_actions)) != len(actions) or sum(map(len, encoded_actions)) > 65536:
        raise ValueError('Actions must be unique and total at most 64 KiB')
    _integer(baseline_action, 'baseline_action', 0, len(actions) - 1)
    for name, value in [('alpha', alpha), ('gamma', gamma), ('epsilon', epsilon)]:
        if not 0 <= _finite(value, name) <= 1 or (name == 'alpha' and value == 0):
            raise ValueError(f'Invalid {name}')
    if not callable(env_factory) or not callable(observation_encoder): raise ValueError('Factory and encoder must be callable')
    actions = deepcopy(actions)
    rng = random.Random(seed)
    q = {}
    transitions = 0

    def key(observation):
        try:
            value = json.dumps(observation_encoder(deepcopy(observation)), sort_keys=True, allow_nan=False, separators=(',', ':'))
        except (ValueError, TypeError) as exc:
            raise ValueError('Observation encoder must return finite JSON data') from exc
        if len(value) > 4096: raise ValueError('Encoded observation exceeds 4 KiB')
        return value

    def row(state):
        if state not in q:
            if len(q) >= 10000: raise ValueError('Q table exceeds 10000 states')
            q[state] = [0.0] * len(actions)
        return q[state]

    def greedy(values): return max(range(len(actions)), key=lambda i: values[i])

    training_returns = []
    for episode_seed in train_seeds:
        env = env_factory()
        try:
            observation, info = env.reset(seed=episode_seed)
            total = 0.0
            for step in range(max_steps):
                if step == 0 and (info.get('terminated') or info.get('truncated')): break
                current = key(observation)
                values = row(current)
                action = rng.randrange(len(actions)) if rng.random() < epsilon else greedy(values)
                next_obs, reward, terminated, truncated, info = env.step(deepcopy(actions[action]))
                reward = _finite(reward, 'reward')
                done = bool(terminated or truncated or step + 1 == max_steps)
                target = reward if done else reward + gamma * max(row(key(next_obs)))
                values[action] += alpha * (target - values[action])
                _finite(values[action], 'Q value')
                total += reward
                transitions += 1
                observation = next_obs
                if done: break
            training_returns.append(_finite(total, 'return'))
        finally:
            if hasattr(env, 'close'): env.close()

    def evaluate(baseline=False):
        returns, capped = [], 0
        nonlocal transitions
        for episode_seed in eval_seeds:
            env = env_factory()
            try:
                observation, info = env.reset(seed=episode_seed)
                total = 0.0
                for step in range(max_steps):
                    if step == 0 and (info.get('terminated') or info.get('truncated')): break
                    action = baseline_action if baseline else greedy(q.get(key(observation), [0.0] * len(actions)))
                    observation, reward, terminated, truncated, info = env.step(deepcopy(actions[action]))
                    total += _finite(reward, 'reward')
                    transitions += 1
                    if terminated or truncated: break
                    if step + 1 == max_steps: capped += 1
                returns.append(_finite(total, 'return'))
            finally:
                if hasattr(env, 'close'): env.close()
        # Scale before summation: finite returns can overflow a naive sum.
        mean = _finite(math.fsum(value / len(returns) for value in returns), 'mean return')
        return {'episodes': len(returns), 'returns': returns, 'mean_return': mean, 'trainer_capped_episodes': capped}

    evaluation, baseline = evaluate(), evaluate(True)
    improvement = _finite(evaluation['mean_return'] - baseline['mean_return'], 'mean return improvement')
    return {'q_table': deepcopy(q), 'actions': actions, 'training_seeds': train_seeds, 'evaluation_seeds': eval_seeds,
            'training_returns': training_returns, 'evaluation': evaluation, 'baseline': baseline,
            'mean_return_improvement': improvement,
            'transitions': transitions, 'seed': seed, 'max_steps': max_steps,
            'epistemic_status': 'offline_learning_benchmark', 'causally_validated': False}


class VectorEnvironment:
    """Sequential batching over distinct instances; no automatic reset.

    Each member's step is independent. A member failure may follow successful
    earlier members; the batch then requires reset (no cross-environment atomicity).
    """
    def __init__(self, factories):
        if not isinstance(factories, (list, tuple)) or not 1 <= len(factories) <= 100 or any(not callable(f) for f in factories):
            raise ValueError('Provide 1..100 environment factories')
        self._envs = [f() for f in factories]
        if len({id(env) for env in self._envs}) != len(self._envs): raise ValueError('Factories must create distinct environments')
        self._ready = False
        self._done = [False] * len(self._envs)

    def reset(self, seeds):
        if not isinstance(seeds, (list, tuple)) or len(seeds) != len(self._envs) or any(type(s) is not int for s in seeds):
            raise ValueError('Provide one integer seed per environment')
        self._ready = False
        results = [env.reset(seed=seed) for env, seed in zip(self._envs, seeds)]
        self._done = [bool(info.get('terminated') or info.get('truncated')) for _, info in results]
        self._ready = True
        return [r[0] for r in results], [r[1] for r in results]

    def step(self, actions):
        if not self._ready or any(self._done): raise ValueError('Batch reset required before stepping or after any member ends')
        if not isinstance(actions, (list, tuple)) or len(actions) != len(self._envs): raise ValueError('Provide one action per environment')
        self._ready = False
        results = [env.step(deepcopy(action)) for env, action in zip(self._envs, actions)]
        self._done = [bool(r[2] or r[3]) for r in results]
        self._ready = True
        return tuple([result[i] for result in results] for i in range(5))

    def close(self):
        for env in self._envs:
            if hasattr(env, 'close'): env.close()
        self._ready = False


def numerical_space_spec(env, observation_bounds):
    """Return stdlib-only finite scalar space declarations for explicit bounds."""
    spec = getattr(env, 'spec', None)
    if not isinstance(spec, dict) or not isinstance(spec.get('actions'), dict) or not isinstance(spec.get('observations'), dict):
        raise ValueError('Environment must declare actions and observations in spec')
    if not isinstance(observation_bounds, dict) or set(observation_bounds) != set(spec['observations']):
        raise ValueError('Explicit bounds must match every observation')
    if any(descriptor.get('masked', False) for descriptor in spec['observations'].values()):
        raise ValueError('Numerical adapter cannot represent masked observations containing None')
    spaces = {'actions': {}, 'observations': {}}
    for name, descriptor in spec['actions'].items():
        if descriptor.get('type') != 'number' or 'choices' in descriptor:
            raise ValueError('Numerical adapter supports continuous scalar actions without choices only')
        low, high = _finite(descriptor.get('minimum'), 'minimum'), _finite(descriptor.get('maximum'), 'maximum')
        if low > high: raise ValueError('Reversed action bounds')
        spaces['actions'][name] = {'low': low, 'high': high, 'shape': []}
    for name, bounds in observation_bounds.items():
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2: raise ValueError('Observation bounds require [low, high]')
        low, high = _finite(bounds[0], 'low'), _finite(bounds[1], 'high')
        if low > high: raise ValueError('Reversed observation bounds')
        spaces['observations'][name] = {'low': low, 'high': high, 'shape': []}
    return spaces


def gymnasium_adapter(env, observation_bounds):
    """Optional Gymnasium scalar Dict/Box adapter; core runtime remains stdlib."""
    declaration = numerical_space_spec(env, observation_bounds)
    try:
        import gymnasium as gym
        import numpy as np
    except ImportError as exc:
        raise ImportError('Optional adapter requires: pip install gymnasium numpy') from exc

    class Adapter(gym.Env):
        metadata = {'render_modes': []}

        def __init__(self):
            super().__init__()
            self.action_space = gym.spaces.Dict({name: gym.spaces.Box(low=d['low'], high=d['high'], shape=(), dtype=np.float64) for name, d in declaration['actions'].items()})
            self.observation_space = gym.spaces.Dict({name: gym.spaces.Box(low=d['low'], high=d['high'], shape=(), dtype=np.float64) for name, d in declaration['observations'].items()})

        def _observation(self, observation):
            if not isinstance(observation, dict) or set(observation) != set(declaration['observations']): raise ValueError('Observation names do not match space')
            result = {name: np.asarray(_finite(value, 'observation'), dtype=np.float64) for name, value in observation.items()}
            if not self.observation_space.contains(result): raise ValueError('Observation is outside declared bounds')
            return result

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if options: raise ValueError('Reset options are unsupported')
            observation, info = env.reset(seed=int(seed) if seed is not None else int(self.np_random.integers(0, 2**31)))
            return self._observation(observation), info

        def step(self, action):
            if not isinstance(action, dict) or set(action) != set(declaration['actions']): raise ValueError('Action names do not match space')
            converted = {}
            for name, value in action.items():
                scalar = np.asarray(value)
                if scalar.shape != () or scalar.dtype.kind not in 'iuf': raise ValueError('Action must be numeric scalar')
                converted[name] = _finite(float(scalar), 'action')
                d = declaration['actions'][name]
                if not d['low'] <= converted[name] <= d['high']: raise ValueError('Action outside declared bounds')
            observation, reward, terminated, truncated, info = env.step(converted)
            return self._observation(observation), _finite(reward, 'reward'), bool(terminated), bool(truncated), info

        def close(self):
            if hasattr(env, 'close'): env.close()

    return Adapter()


def toy_learning_benchmark():
    """Run 280 one-step transitions of a two-context choice toy, offline.

    Reward is one if the chosen bit equals the observed context. This checks
    that training and evaluation work; it is not a realistic economic task.
    """
    class ChoiceToy:
        def reset(self, seed=0):
            self.context = seed % 2
            return {'context': self.context}, {}

        def step(self, action):
            return {'context': self.context}, float(action['choice'] == self.context), True, False, {}

    return train_tabular(ChoiceToy, [{'choice': 0}, {'choice': 1}], lambda o: o['context'],
                         training_seeds=list(range(200)), evaluation_seeds=list(range(1000, 1040)),
                         max_steps=1, seed=3)
