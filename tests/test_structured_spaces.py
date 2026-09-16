import copy
import unittest
from worldmodel.structured_spaces import StructuredSpace, structured_gymnasium_adapter


class StructuredSpaceTests(unittest.TestCase):
    def schema(self):
        return {'type': 'object', 'properties': {
            'throttle': {'type': 'number', 'minimum': -1, 'maximum': 1, 'unit': 'fraction'},
            'active': {'type': 'boolean'}, 'mode': {'type': 'categorical', 'choices': ['eco', 'full']},
            'sensor': {'type': 'vector', 'length': 2, 'minimum': 0, 'maximum': 10, 'unit': 'm', 'nullable': True},
            'counts': {'type': 'array', 'length': 2, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 5}}}}

    def value(self): return {'throttle': .2, 'active': True, 'mode': 'eco', 'sensor': None, 'counts': [1, 2]}

    def test_nested_roundtrip_mask_and_stable_schema_hash(self):
        space = StructuredSpace(self.schema()); value = self.value()
        self.assertEqual(space.unflatten(space.flatten(value)), value)
        value['sensor'] = [3, 4]
        self.assertEqual(space.unflatten(space.flatten(value)), value)
        reversed_schema = self.schema(); reversed_schema['properties'] = dict(reversed(list(reversed_schema['properties'].items())))
        self.assertEqual(space.schema_hash, StructuredSpace(reversed_schema).schema_hash)
        declaration = space.declaration()
        self.assertTrue(any(item['kind'] == 'mask' for item in declaration['layout']))
        self.assertTrue(any(item.get('unit') == 'm' for item in declaration['layout']))
        declaration['schema']['properties'].clear()
        self.assertTrue(space.declaration()['schema']['properties'])

    def test_strict_types_dimensions_keys_categories_and_nonfinite(self):
        space = StructuredSpace(self.schema())
        for key, value in [('active', 1), ('counts', [True, 2]), ('sensor', [1]),
                           ('mode', 'unknown'), ('throttle', float('nan'))]:
            data = self.value(); data[key] = value
            with self.assertRaises(ValueError): space.flatten(data)
        data = self.value(); data['extra'] = 1
        with self.assertRaises(ValueError): space.validate(data)
        with self.assertRaises(ValueError): space.unflatten([0])
        with self.assertRaises(ValueError): StructuredSpace({'type': 'object'})
        with self.assertRaises(ValueError): StructuredSpace({'type': 'number', 'minimum': 0})

    def test_none_requires_declared_mask_and_masked_payload_is_unambiguous(self):
        plain = StructuredSpace({'type': 'number', 'minimum': 5, 'maximum': 10})
        with self.assertRaises(ValueError): plain.flatten(None)
        nullable = StructuredSpace({'type': 'number', 'minimum': 5, 'maximum': 10, 'nullable': True})
        self.assertEqual(nullable.flatten(None), [0, 0])
        self.assertEqual(nullable.unflatten([0, 0]), None)
        with self.assertRaises(ValueError): nullable.unflatten([0, 5])
        self.assertEqual(nullable.unflatten([1, 5]), 5)

    def test_optional_gym_nested_roundtrip(self):
        try: import gymnasium
        except ImportError:
            with self.assertRaisesRegex(ImportError, 'pip install'): StructuredSpace(self.schema()).gym_space()
        else:
            space = StructuredSpace(self.schema()); encoded = space.to_gym(self.value())
            self.assertTrue(space.gym_space().contains(encoded))
            self.assertEqual(space.from_gym(encoded), self.value())

    def test_expansion_budget_precedes_layout_allocation(self):
        from unittest.mock import patch
        schema = {'type': 'array', 'length': 1000, 'items': {
            'type': 'vector', 'length': 1000, 'minimum': 0, 'maximum': 1}}
        with patch('worldmodel.structured_spaces._layout', side_effect=AssertionError('allocated')):
            with self.assertRaisesRegex(ValueError, 'spaces_max_channels=4096'): StructuredSpace(schema, limits={'spaces_max_channels': 4096})
        with self.assertRaises(ValueError): StructuredSpace({'type': []})

    def test_optional_gym_checker(self):
        try:
            from gymnasium.utils.env_checker import check_env
        except ImportError:
            self.skipTest('optional Gymnasium not installed')
        class Toy:
            def reset(self, seed=0): return {'reading': None}, {}
            def step(self, action): return {'reading': [float(action['levels'][0])]}, 1.0, False, False, {}
        actions = {'type': 'object', 'properties': {'levels': {'type': 'array', 'length': 1,
            'items': {'type': 'integer', 'minimum': 0, 'maximum': 2}}}}
        observations = {'type': 'object', 'properties': {'reading': {
            'type': 'vector', 'length': 1, 'minimum': 0, 'maximum': 2, 'nullable': True}}}
        env = structured_gymnasium_adapter(Toy(), actions, observations)
        try: check_env(env, skip_render_check=True)
        finally: env.close()
