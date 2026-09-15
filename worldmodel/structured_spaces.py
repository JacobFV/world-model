"""Declared, bounded structured spaces; stdlib core and optional Gymnasium mapping."""
from copy import deepcopy
import math
from .util import canonical, digest


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value): raise ValueError('Expected finite numeric value')
    return value


def _normalize(schema, depth=0):
    if not isinstance(schema, dict) or depth > 12: raise ValueError('Schema must be an object with depth at most 12')
    kind = schema.get('type')
    if not isinstance(kind, str): raise ValueError('Schema type must be a string')
    fields = {'type', 'unit', 'nullable'}
    fields |= {'number': {'minimum', 'maximum'}, 'integer': {'minimum', 'maximum'},
               'boolean': set(), 'categorical': {'choices'}, 'object': {'properties'},
               'array': {'length', 'items'}, 'vector': {'length', 'minimum', 'maximum'}}.get(kind, {'invalid'})
    if kind not in ('number', 'integer', 'boolean', 'categorical', 'object', 'array', 'vector') or set(schema) - fields:
        raise ValueError('Unknown schema type or fields')
    if schema.get('unit') is not None and not isinstance(schema['unit'], str): raise ValueError('Unit must be a string or null')
    if type(schema.get('nullable', False)) is not bool: raise ValueError('nullable must be boolean')
    node = {'type': kind, 'unit': schema.get('unit'), 'nullable': schema.get('nullable', False)}
    if kind in ('number', 'integer', 'vector'):
        low, high = _finite(schema.get('minimum')), _finite(schema.get('maximum'))
        if low > high: raise ValueError('Reversed numeric bounds')
        if kind == 'integer' and (type(low) is not int or type(high) is not int or max(abs(low), abs(high)) > 2**53 - 1):
            raise ValueError('Integer bounds require exactly representable bounded integers')
        if kind != 'vector': node.update(minimum=low, maximum=high)
    if kind == 'categorical':
        choices = schema.get('choices')
        if not isinstance(choices, list) or not 1 <= len(choices) <= 1000 or any(type(c) not in (str, int, float, bool) for c in choices):
            raise ValueError('Categorical choices require 1..1000 explicit scalar values')
        encoded = [canonical(c) for c in choices]
        if len(set(encoded)) != len(encoded): raise ValueError('Duplicate categorical choices')
        node['choices'] = deepcopy(choices)
    if kind == 'object':
        props = schema.get('properties')
        if not isinstance(props, dict) or not 1 <= len(props) <= 100 or any(not isinstance(k, str) or not k for k in props):
            raise ValueError('Object schema requires explicit nonempty properties')
        node['properties'] = {k: _normalize(props[k], depth + 1) for k in sorted(props)}
    if kind in ('array', 'vector'):
        length = schema.get('length')
        if type(length) is not int or not 1 <= length <= 1000: raise ValueError('Array/vector requires length in 1..1000')
        node.update(length=length, items=_normalize(schema.get('items') if kind == 'array' else
                    {'type': 'number', 'minimum': low, 'maximum': high, 'unit': schema.get('unit')}, depth + 1))
    return node


def _size(node):
    kind = node['type']
    if kind == 'object': count = sum(_size(child) for child in node['properties'].values())
    elif kind in ('array', 'vector'): count = node['length'] * _size(node['items'])
    else: count = 1
    count += int(node['nullable'])
    if count > 4096: raise ValueError('Structured space exceeds 4096 flattened channels')
    return count


def _layout(node, path=()):
    rows = []
    if node['nullable']: rows.append({'path': list(path), 'kind': 'mask', 'unit': None, 'minimum': 0, 'maximum': 1})
    kind = node['type']
    if kind == 'object':
        for key, child in node['properties'].items(): rows += _layout(child, path + (key,))
    elif kind in ('array', 'vector'):
        for i in range(node['length']): rows += _layout(node['items'], path + (i,))
    else:
        bounds = (node['minimum'], node['maximum']) if kind in ('number', 'integer') else (0, 1 if kind == 'boolean' else len(node['choices']) - 1)
        rows.append({'path': list(path), 'kind': kind, 'unit': node['unit'], 'minimum': bounds[0], 'maximum': bounds[1]})
    if len(rows) > 4096: raise ValueError('Structured space exceeds 4096 flattened channels')
    return rows


def _validate(node, value):
    if value is None and node['nullable']: return
    kind = node['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(node['properties']): raise ValueError('Object keys must match declared properties')
        for k, child in node['properties'].items(): _validate(child, value[k])
    elif kind in ('array', 'vector'):
        if not isinstance(value, list) or len(value) != node['length']: raise ValueError('Array/vector dimensions mismatch')
        for item in value: _validate(node['items'], item)
    elif kind in ('number', 'integer'):
        if kind == 'integer' and type(value) is not int: raise ValueError('Integer value required')
        if not node['minimum'] <= _finite(value) <= node['maximum']: raise ValueError('Numeric value outside bounds')
    elif kind == 'boolean':
        if type(value) is not bool: raise ValueError('Boolean value required')
    elif kind == 'categorical':
        if canonical(value) not in [canonical(c) for c in node['choices']]: raise ValueError('Invalid categorical value')


def _flatten(node, value):
    if node['nullable']:
        if value is None: return [0] * len(_layout(node))
        return [1] + _flatten({**node, 'nullable': False}, value)
    kind = node['type']
    if kind == 'object': return [x for k, child in node['properties'].items() for x in _flatten(child, value[k])]
    if kind in ('array', 'vector'): return [x for item in value for x in _flatten(node['items'], item)]
    if kind == 'boolean': return [int(value)]
    if kind == 'categorical': return [[canonical(c) for c in node['choices']].index(canonical(value))]
    return [value]


def _unflatten(node, values, offset):
    if node['nullable']:
        mask = values[offset]
        if mask not in (0, 1): raise ValueError('Mask must be zero or one')
        if mask == 0:
            end = offset + len(_layout(node))
            if any(value != 0 for value in values[offset + 1:end]): raise ValueError('Masked payload must be canonical zeros')
            return None, end
        return _unflatten({**node, 'nullable': False}, values, offset + 1)
    kind = node['type']
    if kind == 'object':
        result = {}
        for k, child in node['properties'].items(): result[k], offset = _unflatten(child, values, offset)
        return result, offset
    if kind in ('array', 'vector'):
        result = []
        for _ in range(node['length']):
            item, offset = _unflatten(node['items'], values, offset); result.append(item)
        return result, offset
    value = values[offset]
    if kind in ('integer', 'boolean', 'categorical'):
        if int(value) != value: raise ValueError('Discrete channel must encode an integer')
        value = int(value)
    if kind == 'boolean':
        if value not in (0, 1): raise ValueError('Boolean channel must be zero or one')
        value = bool(value)
    if kind == 'categorical':
        if not 0 <= value < len(node['choices']): raise ValueError('Categorical channel outside choices')
        value = deepcopy(node['choices'][value])
    return value, offset + 1


def _deps():
    try:
        import gymnasium as gym
        import numpy as np
    except ImportError as exc: raise ImportError('Optional structured adapter requires: pip install gymnasium numpy') from exc
    return gym, np


def _default(node):
    if node['nullable']: return None
    kind = node['type']
    if kind == 'object': return {k: _default(v) for k, v in node['properties'].items()}
    if kind in ('array', 'vector'): return [_default(node['items']) for _ in range(node['length'])]
    if kind == 'boolean': return False
    if kind == 'categorical': return deepcopy(node['choices'][0])
    return node['minimum']


def _gym_space(node, gym, np):
    if node['nullable']:
        return gym.spaces.Dict({'mask': gym.spaces.Discrete(2), 'value': _gym_space({**node, 'nullable': False}, gym, np)})
    kind = node['type']
    if kind == 'object': return gym.spaces.Dict({k: _gym_space(v, gym, np) for k, v in node['properties'].items()})
    if kind in ('array', 'vector'): return gym.spaces.Tuple(tuple(_gym_space(node['items'], gym, np) for _ in range(node['length'])))
    if kind == 'number': return gym.spaces.Box(node['minimum'], node['maximum'], shape=(), dtype=np.float64)
    if kind == 'integer': return gym.spaces.Discrete(node['maximum'] - node['minimum'] + 1, start=node['minimum'])
    return gym.spaces.Discrete(2 if kind == 'boolean' else len(node['choices']))


def _gym_value(node, value, np, encode):
    if node['nullable']:
        base = {**node, 'nullable': False}
        if encode: return {'mask': int(value is not None), 'value': _gym_value(base, value if value is not None else _default(base), np, True)}
        return None if int(value['mask']) == 0 else _gym_value(base, value['value'], np, False)
    kind = node['type']
    if kind == 'object': return {k: _gym_value(v, value[k], np, encode) for k, v in node['properties'].items()}
    if kind in ('array', 'vector'):
        rows = [_gym_value(node['items'], item, np, encode) for item in value]
        return tuple(rows) if encode else rows
    if encode:
        if kind == 'number': return np.asarray(value, dtype=np.float64)
        if kind == 'categorical': return [canonical(c) for c in node['choices']].index(canonical(value))
        return int(value)
    if kind == 'number': return float(value)
    if kind == 'boolean': return bool(value)
    if kind == 'categorical': return deepcopy(node['choices'][int(value)])
    return int(value)


class StructuredSpace:
    def __init__(self, schema):
        if len(canonical(schema)) > 1048576: raise ValueError('Schema exceeds 1 MiB')
        self._schema = _normalize(schema)
        _size(self._schema)
        self._layout = _layout(self._schema)
        self.schema_hash = digest(self._schema)

    def declaration(self):
        return deepcopy({'schema': self._schema, 'schema_hash': self.schema_hash, 'layout': self._layout,
                         'size': len(self._layout), 'mask_encoding': '0=missing with zero flat payload; 1=present'})

    def validate(self, value):
        canonical(value); _validate(self._schema, value); return deepcopy(value)

    def flatten(self, value): return _flatten(self._schema, self.validate(value))

    def unflatten(self, values):
        if not isinstance(values, list) or len(values) != len(self._layout): raise ValueError('Flattened dimension mismatch')
        for value in values: _finite(value)
        result, _ = _unflatten(self._schema, values, 0)
        return self.validate(result)

    def gym_space(self):
        gym, np = _deps(); return _gym_space(self._schema, gym, np)

    def to_gym(self, value):
        _, np = _deps(); return _gym_value(self._schema, self.validate(value), np, True)

    def from_gym(self, value):
        _, np = _deps()
        if not self.gym_space().contains(value): raise ValueError('Value does not belong to declared Gymnasium space')
        return self.validate(_gym_value(self._schema, value, np, False))


def structured_gymnasium_adapter(env, action_schema, observation_schema):
    """Optional adapter for explicit schemas, including categorical/masked values."""
    actions, observations = StructuredSpace(action_schema), StructuredSpace(observation_schema)
    gym, _ = _deps()
    class Adapter(gym.Env):
        metadata = {'render_modes': []}
        def __init__(self):
            self.action_space = actions.gym_space(); self.observation_space = observations.gym_space(); self._ready = False
        def reset(self, *, seed=None, options=None):
            self._ready = False
            super().reset(seed=seed)
            if options: raise ValueError('Reset options unsupported')
            observation, info = env.reset(seed=int(seed) if seed is not None else int(self.np_random.integers(0, 2**31)))
            result = observations.to_gym(observation)
            self._ready = not (info.get('terminated') or info.get('truncated'))
            return result, info
        def step(self, action):
            if not self._ready: raise ValueError('Reset required before step')
            self._ready = False
            observation, reward, terminated, truncated, info = env.step(actions.from_gym(action))
            result = observations.to_gym(observation); _finite(reward)
            self._ready = not (terminated or truncated)
            return result, float(reward), bool(terminated), bool(truncated), info
        def close(self):
            self._ready = False
            if hasattr(env, 'close'): env.close()
    return Adapter()
