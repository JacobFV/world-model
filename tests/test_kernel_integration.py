from copy import deepcopy
import unittest

from tests import test_materialize as fixtures
from worldmodel.materialize import materialize


class KernelIntegrationTests(unittest.TestCase):
    setUp = fixtures.MaterializeTests.setUp
    tearDown = fixtures.MaterializeTests.tearDown
    publish = fixtures.MaterializeTests.publish

    def test_initial_override_type_is_enforced(self):
        request = {**self.request, 'initial_state': [
            {'entity': 'asset:a', 'variable': 'stock', 'value': 4, 'unit': 'unit', 'type': 'string'}]}
        with self.assertRaisesRegex(ValueError, 'type'):
            materialize(self.store, self.graph, request, self.registry)

    def test_computed_records_cannot_be_promoted_to_observed_evidence(self):
        for status in ('forecast', 'scenario_assumption', 'persistence_assumption', 'derived_aggregate'):
            rows = deepcopy(self.rows)
            rows[1]['epistemic_status'] = status
            graph = self.publish(rows)
            with self.assertRaisesRegex(ValueError, 'Missing initial state'):
                materialize(self.store, graph, self.request, self.registry)
            request = dict(self.request, initial_state=[
                {'entity': 'asset:a', 'variable': 'stock', 'value': 4, 'unit': 'unit', 'type': 'number'}])
            result = materialize(self.store, graph, request, self.registry)
            self.assertEqual(result['snapshots'][0]['origin'], 'scenario_assumption')
            self.assertEqual(result['snapshots'][0]['value'], 4)
        rows = deepcopy(self.rows)
        rows[1]['epistemic_status'] = 'observed'
        result = materialize(self.store, self.publish(rows), self.request, self.registry)
        self.assertEqual(result['snapshots'][0]['origin'], 'observed')

    def population_request(self, fidelity):
        return {'start': '2024-01-01', 'end': '2024-01-03', 'step_seconds': 86400,
                'known_at': '2024-01-01', 'budget': 100, 'seed': 7,
                'targets': [{'entity': 'asset:a', 'variable': 'population'},
                            {'entity': 'asset:b', 'variable': 'population'}],
                'initial_state': [{'entity': 'asset:' + entity, 'variable': 'population',
                                   'value': value, 'unit': 'people', 'type': 'number'}
                                  for entity, value in [('a', 100), ('b', 200)]],
                'bindings': [{'id': entity, 'process_id': 'population_growth', 'entity_id': 'asset:' + entity,
                              'fidelity': fidelity,
                              'inputs': {'population': {'entity': 'asset:' + entity, 'variable': 'population'}},
                              'outputs': {'population': {'entity': 'asset:' + entity, 'variable': 'population'}},
                              'parameters': {'growth_rate_per_year': .1, 'goals': ['grow']}}
                             for entity in ['a', 'b']]}

    def test_stochastic_materialization_replays_seed_and_artifact(self):
        request = self.population_request('stochastic')
        first = materialize(self.store, self.graph, request)
        replay = materialize(self.store, self.graph, request)
        self.assertEqual(first['snapshots'], replay['snapshots'])
        self.assertEqual(first['artifact'], replay['artifact'])
        changed = materialize(self.store, self.graph, dict(request, seed=8))
        self.assertNotEqual(first['snapshots'][-1]['value'], changed['snapshots'][-1]['value'])

    def test_agent_memory_is_per_entity_and_transcripts_retain_actual_responses(self):
        class Backend:
            identity = {'provider': 'local-test', 'model': 'scripted', 'version': '1'}
            def __init__(self):
                self.requests = []
            def predict(self, request):
                self.requests.append(deepcopy(request))
                return {'pressures': [{'port': 'population', 'mode': 'rate', 'value': 1 / 86400,
                                       'unit': 'people', 'strength': 1, 'confidence': 1}],
                        'memory': {'count': request['memory'].get('count', 0) + 1}}
        backend = Backend()
        result = materialize(self.store, self.graph, self.population_request('agent'), agent_backend=backend)
        self.assertEqual(result['execution']['agent_memories'], {'asset:a': {'count': 2}, 'asset:b': {'count': 2}})
        self.assertEqual([r['memory'] for r in backend.requests], [{}, {}, {'count': 1}, {'count': 1}])
        self.assertEqual(backend.requests[0]['goals'], ['grow'])
        self.assertGreater(backend.requests[0]['budget'], 0)
        self.assertEqual(result['trace'][-1]['prediction']['diagnostics']['agent_audit']['response']['memory'], {'count': 2})
        self.assertEqual(result['backend_identity'], backend.identity)
        mixed_request = self.population_request('agent')
        mixed_request['bindings'].append(dict(mixed_request['bindings'][0], id='z_deterministic', fidelity='deterministic'))
        mixed = materialize(self.store, self.graph, mixed_request, agent_backend=Backend())
        self.assertEqual(mixed['execution']['agent_memories']['asset:a'], {'count': 2})


if __name__ == '__main__':
    unittest.main()
