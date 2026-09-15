from copy import deepcopy
import unittest

import test_materialize as fixtures
from worldmodel.materialize import materialize


class InterventionTests(unittest.TestCase):
    setUp = fixtures.MaterializeTests.setUp
    tearDown = fixtures.MaterializeTests.tearDown
    publish = fixtures.MaterializeTests.publish

    def intervention(self, second=4, **changes):
        return {'time': f'2024-01-01T00:00:{second:02d}Z', 'binding': 'a_flow',
                'port': 'flow', 'value': 5, 'unit': 'unit/second', **changes}

    def test_changes_apply_before_calls_and_persist_between_actions(self):
        request = {**self.request, 'interventions': [self.intervention(6, value=1),
                                                   self.intervention(0, value=3),
                                                   self.intervention(4)]}
        original = deepcopy(request)
        result = materialize(self.store, self.graph, request, self.registry)
        self.assertEqual([row['value'] for row in result['snapshots']], [10, 27, 36])
        self.assertEqual([row['inputs']['flow']['value'] for row in result['trace']],
                         [3, 3, 5, 1, 1])
        self.assertEqual([row['value'] for row in result['input_interventions']], [3, 5, 1])
        self.assertEqual(request, original)

    def test_all_invalid_interventions_rejected_before_any_handler(self):
        calls = []
        original = self.registry._handlers['flow.det']
        def handler(inputs, parameters, context):
            calls.append(context['time'])
            return original(inputs, parameters, context)
        self.registry._handlers['flow.det'] = handler
        invalid = [self.intervention(5), self.intervention(10),
                   self.intervention(time='2023-12-31T23:59:58Z'),
                   self.intervention(time='2024-01-01T00:00:04.0000001Z'),
                   self.intervention(binding='missing'), self.intervention(port='missing'),
                   self.intervention(value='wrong'), self.intervention(unit='wrong')]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    materialize(self.store, self.graph,
                                {**self.request, 'interventions': [self.intervention(0), item]}, self.registry)
                self.assertEqual(calls, [])

    def test_duplicates_normalize_timezones_and_observed_rejects_changes(self):
        duplicate = self.intervention(time='2023-12-31T16:00:04-08:00')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            materialize(self.store, self.graph,
                        {**self.request, 'interventions': [self.intervention(), duplicate]}, self.registry)
        with self.assertRaisesRegex(ValueError, 'Observed'):
            materialize(self.store, self.graph,
                        {**self.request, 'mode': 'observed', 'bindings': [],
                         'interventions': [self.intervention()]}, self.registry)

    def test_variable_reference_cannot_be_replaced(self):
        request = deepcopy(self.request)
        request['bindings'][0]['inputs']['flow'] = {'entity': 'asset:a', 'variable': 'stock'}
        request['interventions'] = [self.intervention()]
        with self.assertRaisesRegex(ValueError, 'literal'):
            materialize(self.store, self.graph, request, self.registry)

    def test_fractional_cadence_applies_at_due_time_between_samples(self):
        request = deepcopy(self.request)
        request['end'] = '2024-01-01T00:00:01Z'
        request['step_seconds'] = 1
        request['bindings'][0]['cadence_seconds'] = .25
        request['interventions'] = [self.intervention(time='2024-01-01T00:00:00.250000Z', value=4)]
        result = materialize(self.store, self.graph, request, self.registry)
        self.assertEqual(result['snapshots'][-1]['value'], 13.5)
        self.assertEqual([row['inputs']['flow']['value'] for row in result['trace']], [2, 4, 4, 4])

    def field_request(self):
        from worldmodel.fields import register_field_processes
        register_field_processes(self.registry)
        config = {'cells': [{'id': 'cell:a', 'measure': 1}, {'id': 'cell:b', 'measure': 1}],
                  'edges': [], 'fields': {'mass': {'kind': 'extensive', 'unit': 'kg',
                                                  'values': {'cell:a': 4, 'cell:b': 0}}}}
        target = {'entity': 'asset:a', 'variable': 'fields_state'}
        return {**self.request, 'end': '2024-01-04', 'step_seconds': 86400, 'targets': [target],
                'initial_state': [{**target, 'value': config, 'unit': None}],
                'bindings': [{'id': 'field', 'process_id': 'field_dynamics',
                              'inputs': {'fields_state': {'value': config, 'unit': None}},
                              'outputs': {'fields_state': target}}]}

    def test_changed_field_work_rejected_before_any_handler(self):
        request = self.field_request()
        config = deepcopy(request['initial_state'][0]['value'])
        config['edges'] = [{'id': 'edge:ab', 'source': 'cell:a', 'target': 'cell:b', 'conductance': 1}]
        request['interventions'] = [{'time': '2024-01-02', 'binding': 'field',
                                     'port': 'fields_state', 'value': config, 'unit': None}]
        calls = []
        original = self.registry._handlers['field_dynamics.deterministic']
        def handler(inputs, parameters, context):
            calls.append(context['time'])
            return original(inputs, parameters, context)
        self.registry._handlers['field_dynamics.deterministic'] = handler
        with self.assertRaisesRegex(ValueError, 'work'):
            materialize(self.store, self.graph, request, self.registry)
        self.assertEqual(calls, [])

    def test_changed_field_work_records_full_prefix_estimate(self):
        request = self.field_request()
        config = deepcopy(request['initial_state'][0]['value'])
        config['fields']['mass']['values']['cell:a'] = 8
        request['interventions'] = [{'time': '2024-01-02', 'binding': 'field',
                                     'port': 'fields_state', 'value': config, 'unit': None}]
        result = materialize(self.store, self.graph, request, self.registry)
        self.assertEqual(result['plan']['work'][1]['intervention_time'], '2024-01-02T00:00:00+00:00')
        self.assertEqual(result['plan']['work'][1]['cell_edge_updates'], 6)

    def test_economy_replay_config_changes_are_not_dynamic_controls(self):
        from worldmodel.economy import example_economy
        from worldmodel.economy_processes import register_economy_processes
        register_economy_processes(self.registry)
        state = {'config': example_economy(), 'step': 0}
        target = {'entity': 'asset:a', 'variable': 'economy_state'}
        request = {**self.request, 'end': '2024-01-03', 'step_seconds': 86400,
                   'targets': [target], 'initial_state': [{**target, 'value': state, 'unit': None}],
                   'bindings': [{'id': 'economy', 'process_id': 'bank_energy_business',
                                 'inputs': {'economy_state': {'value': state, 'unit': None}},
                                 'outputs': {'economy_state': target}}],
                   'interventions': [{'time': '2024-01-02', 'binding': 'economy',
                                      'port': 'economy_state', 'value': state, 'unit': None}]}
        with self.assertRaisesRegex(ValueError, 'replay.*history'):
            materialize(self.store, self.graph, request, self.registry)
