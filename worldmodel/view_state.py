"""Evidence selection and explicit scenario state for requested materializations."""
from copy import deepcopy
from .model import identifier, instant
from .util import canonical


def variable_ref(value):
    if not isinstance(value, dict) or set(value) != {'entity', 'variable'}:
        raise ValueError('Variable reference must contain exactly entity and variable')
    identifier(value['entity'])
    if not isinstance(value['variable'], str) or not value['variable']:
        raise ValueError('Variable name must be nonempty')
    return value['entity'], value['variable']


def value_type(value):
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'vector' if value and all(type(x) in (int, float) for x in value) else 'array'
    raise ValueError('State must have a concrete typed value; null is unknown')


def active_at(record, at, known_at):
    return (instant(record['observed_at']) <= known_at
            and (not record.get('valid_from') or instant(record['valid_from']) <= at)
            and (not record.get('valid_to') or at < instant(record['valid_to'])))


class EvidenceState:
    def __init__(self, store, graph_ref, keys):
        self.graph_ref = graph_ref
        self.candidates = {key: [] for key in keys}
        self.entities = {}
        entity_ids = {key[0] for key in keys}
        store.verify(graph_ref)
        for record in store.records(graph_ref, verify=False):
            if record['kind'] == 'entity':
                entity = record.get('entity_id', record['id'])
                if entity in entity_ids:
                    self.entities.setdefault(entity, []).append(record)
            elif (record['kind'] in ('observation', 'assertion') and 'value' in record
                  and record.get('epistemic_status', 'observed') == 'observed'):
                key = (record.get('subject'), record.get('metric', record.get('predicate')))
                if key in self.candidates:
                    self.candidates[key].append(record)
        missing = entity_ids - set(self.entities)
        if missing:
            raise ValueError(f'Unknown entities in requested state: {sorted(missing)}')

    def select(self, at, known_at, initial_state=(), policy='error'):
        if policy not in ('error', 'latest_observation'):
            raise ValueError('Unsupported reconciliation policy')
        overrides = {}
        for item in initial_state:
            key = variable_ref({name: item[name] for name in ('entity', 'variable')})
            if key not in self.candidates or key in overrides:
                raise ValueError('Initial state must uniquely name a reachable variable')
            inferred = value_type(item.get('value'))
            declared = item.get('type', inferred)
            if declared != inferred and not (declared == 'array' and inferred == 'vector'):
                raise ValueError(f'Scenario override type conflicts with value for {key}')
            if item.get('unit') is not None and not isinstance(item['unit'], str):
                raise ValueError('Scenario override unit must be string or null')
            canonical(item)
            overrides[key] = item
        state, decisions = {}, []
        for key, candidates in self.candidates.items():
            entities = [record for record in self.entities[key[0]] if active_at(record, at, known_at)]
            if not entities:
                raise ValueError(f'Entity {key[0]} has no description available at requested times')
            available = [record for record in candidates
                         if record.get('value') is not None and active_at(record, at, known_at)]
            evidence = []
            if key in overrides:
                item = overrides[key]
                value, unit = item['value'], item.get('unit')
                units = {record.get('unit') for record in available}
                if units and unit not in units:
                    raise ValueError(f'Scenario override unit conflicts with observed state for {key}')
                evidence = [{'input': self.graph_ref, 'record_id': record['id']} for record in entities]
                decisions.append({'entity': key[0], 'variable': key[1], 'policy': 'scenario_override',
                                  'override': item, 'observed_candidates': [r['id'] for r in available]})
                origin = 'scenario_assumption'
            elif not available:
                raise ValueError(f'Missing initial state at {at.isoformat()}: {key}')
            else:
                alternatives = {canonical([r['value'], r.get('unit')]) for r in available}
                selected = available
                if len(alternatives) > 1:
                    if policy == 'error':
                        raise ValueError(f'Conflicting evidence for {key}; choose a reconciliation policy or scenario override')
                    latest = max(instant(r['observed_at']) for r in available)
                    selected = [r for r in available if instant(r['observed_at']) == latest]
                    if len({canonical([r['value'], r.get('unit')]) for r in selected}) > 1:
                        raise ValueError(f'Conflicting equally recent evidence for {key}')
                    decisions.append({'entity': key[0], 'variable': key[1], 'policy': policy,
                                      'selected': [r['id'] for r in selected],
                                      'rejected': [r['id'] for r in available if r not in selected]})
                value, unit = selected[0]['value'], selected[0].get('unit')
                evidence = [{'input': self.graph_ref, 'record_id': r['id']} for r in selected]
                origin = 'observed'
            state[key] = {'value': deepcopy(value), 'unit': unit,
                          'type': overrides[key].get('type', value_type(value)) if key in overrides else value_type(value),
                          'evidence': evidence, 'origin': origin}
        return state, decisions


def collect_evidence(state):
    unique = {}
    for item in state.values():
        for evidence in item['evidence']:
            unique[canonical(evidence)] = evidence
    return list(unique.values())
