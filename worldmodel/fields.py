"""Explicit field supports and conservative kernels; graphs are bounded projections.

Nonnegative scalar quantities only. Cell measure defines integration. Conductance
has measure/second units; directed transport_rate is a fraction/second. Neither
geometry nor political sovereignty is inferred from an adjacency or a claim.
"""
from copy import deepcopy
import math
from .util import canonical, digest


def _finite(x, label, positive=False):
    if type(x) not in (int, float) or not math.isfinite(x) or x < 0 or (positive and not x):
        raise ValueError(f'{label} must be finite and {"positive" if positive else "nonnegative"}')
    return float(x)


def _bound(x, label, maximum):
    if type(x) is not int or not 1 <= x <= maximum:
        raise ValueError(f'{label} must be an integer in 1..{maximum}')
    return x


def _integral(values):
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise ValueError('Field values exceed the finite integral range') from exc
    if not math.isfinite(total):
        raise ValueError('Field values exceed the finite integral range')
    return total


def schema():
    return {'entity_types': {'field_cell': {'parent': 'location'}, 'territory_claim': {'parent': 'entity'}},
            'relations': {'field_connected_to': {'domain': 'field_cell', 'range': 'field_cell'},
                          'claims_field_cell': {'domain': 'territory_claim', 'range': 'field_cell'},
                          'claim_made_by': {'domain': 'territory_claim', 'range': 'agent'}},
            'variables': {'field_value': {'type': 'object', 'unit': 'field_value', 'domain': 'field_cell'}}}


class FieldWorld:
    def __init__(self, config):
        from .model import identifier
        canonical(config)
        if not isinstance(config, dict) or set(config) - {'cells', 'edges', 'fields', 'claims', 'measure_unit', 'description'}:
            raise ValueError('Unknown field world config fields')
        self.config = deepcopy(config)
        self.cells = {}
        for cell in config['cells']:
            identifier(cell['id'])
            if cell['id'] in self.cells:
                raise ValueError('Duplicate field cell')
            _finite(cell['measure'], 'measure', True)
            if 'coordinates' in cell:
                coordinates = cell['coordinates']
                if not isinstance(coordinates, list) or len(coordinates) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in coordinates):
                    raise ValueError('coordinates require two finite values in a caller-declared coordinate system')
            if not isinstance(cell.get('tags', []), list) or any(not isinstance(x, str) for x in cell.get('tags', [])):
                raise ValueError('Cell tags must be a string list')
            self.cells[cell['id']] = deepcopy(cell)
        if not 1 <= len(self.cells) <= 100000:
            raise ValueError('World must contain 1..100000 explicit cells')
        self.edges = []
        seen = set()
        for edge in config.get('edges', []):
            if edge['id'] in seen or not isinstance(edge['id'], str) or not edge['id']:
                raise ValueError('Duplicate or invalid edge ID')
            seen.add(edge['id'])
            if edge['source'] not in self.cells or edge['target'] not in self.cells or edge['source'] == edge['target']:
                raise ValueError('Edges need two distinct existing cells')
            item = deepcopy(edge)
            item['conductance'] = _finite(edge.get('conductance', 0), 'conductance')
            item['transport_rate'] = _finite(edge.get('transport_rate', 0), 'transport_rate')
            self.edges.append(item)
        self.fields = deepcopy(config['fields'])
        if not isinstance(self.fields, dict) or not 1 <= len(self.fields) <= 1000 or len(self.cells) * len(self.fields) > 1000000 or len(self.edges) > 100000:
            raise ValueError('Field/topology storage budget exceeded')
        for name, field in self.fields.items():
            if not isinstance(name, str) or not name or field['kind'] not in ('extensive', 'intensive') or not isinstance(field['unit'], str) or not field['unit']:
                raise ValueError('Fields require a name, extensive/intensive kind and explicit unit')
            if not isinstance(field['values'], dict) or set(field['values']) != set(self.cells):
                raise ValueError('Each field must explicitly cover every cell; no implicit zero imputation')
            for value in field['values'].values():
                _finite(value, 'field value')
        claims = config.get('claims', [])
        seen = set()
        if not isinstance(claims, list) or len(claims) > 100000:
            raise ValueError('Claim budget exceeded')
        for claim in claims:
            identifier(claim['id']); identifier(claim['claimant'])
            if claim['id'] in seen or claim['id'] in self.cells or claim['claimant'] in self.cells:
                raise ValueError('Duplicate claim or conflicting entity ID')
            seen.add(claim['id'])
            if not isinstance(claim['cells'], list) or len(set(claim['cells'])) != len(claim['cells']) or not set(claim['cells']) <= set(self.cells):
                raise ValueError('Claim cells must be distinct existing cells')
        if set(c['claimant'] for c in claims) & seen:
            raise ValueError('Claimant and claim IDs must be distinct')
        if sum(len(c['cells']) for c in claims) > 1000000:
            raise ValueError('Territory membership budget exceeded')

    def evolve(self, request):
        """Return a new state; never mutate source fields or create missing detail."""
        if set(request) - {'duration_seconds', 'step_seconds', 'max_substeps', 'max_work'}:
            raise ValueError('Unknown evolution request field')
        duration = _finite(request['duration_seconds'], 'duration_seconds')
        requested_step = _finite(request.get('step_seconds', duration or 1), 'step_seconds', True)
        maximum = _bound(request.get('max_substeps', 10000), 'max_substeps', 100000)
        budget = _bound(request.get('max_work', 1000000), 'max_work', 10000000)
        outgoing = {key: 0. for key in self.cells}
        for e in self.edges:
            outgoing[e['source']] += e['conductance'] / self.cells[e['source']]['measure'] + e['transport_rate']
            outgoing[e['target']] += e['conductance'] / self.cells[e['target']]['measure']
        peak = max(outgoing.values())
        stable_step = min(requested_step, .9 / peak) if peak else requested_step
        if not math.isfinite(peak) or stable_step <= 0 or not math.isfinite(duration / stable_step):
            raise ValueError('Dynamics are outside finite stability range')
        steps = math.ceil(duration / stable_step) if duration else 0
        work = steps * (len(self.cells) + len(self.edges)) * len(self.fields)
        if steps > maximum or work > budget:
            raise ValueError(f'Field evolution budget exceeded: {steps} substeps, {work} work units')
        amounts = {name: {key: value * (self.cells[key]['measure'] if field['kind'] == 'intensive' else 1)
                         for key, value in field['values'].items()} for name, field in self.fields.items()}
        if any(not math.isfinite(v) for values in amounts.values() for v in values.values()):
            raise ValueError('Field integral outside finite range')
        initial = {name: _integral(values.values()) for name, values in amounts.items()}
        dt = duration / steps if steps else 0
        for _ in range(steps):
            for name, values in amounts.items():
                changes = {key: 0. for key in self.cells}
                for e in self.edges:
                    a, b = e['source'], e['target']
                    transfer = dt * (e['conductance'] * (values[a] / self.cells[a]['measure'] - values[b] / self.cells[b]['measure']) + e['transport_rate'] * values[a])
                    changes[a] -= transfer
                    changes[b] += transfer
                for key, change in changes.items():
                    value = values[key] + change
                    if not math.isfinite(value) or value < -1e-10 * max(1, initial[name]):
                        raise ValueError('Positivity or finite-value condition violated')
                    values[key] = max(0., value)
        state = deepcopy(self.config)
        conservation = {}
        for name, values in amounts.items():
            final = _integral(values.values())
            if not math.isclose(initial[name], final, abs_tol=1e-9, rel_tol=1e-10):
                raise ValueError('Field integral conservation violated')
            state['fields'][name]['values'] = {key: value / (self.cells[key]['measure'] if self.fields[name]['kind'] == 'intensive' else 1) for key, value in values.items()}
            conservation[name] = {'initial_integral': initial[name], 'final_integral': final, 'difference': final - initial[name]}
        result = {'state': state, 'execution': {'substeps': steps, 'step_seconds': dt, 'work': work},
                  'conservation': conservation, 'epistemic_status': 'synthetic_scenario', 'causally_calibrated': False,
                  'assumptions': ['Explicit topology; no spatial refinement or inferred geometry.',
                                  'Closed conservative nonnegative scalar fields; constant coefficients.',
                                  'Intensive integrals weight each value by its cell measure.',
                                  'Territory memberships are retained claims; overlap is not resolved into sovereignty.']}
        canonical(result)
        return result

    def project(self, request):
        """Lazily yield typed evidence records for an explicitly bounded selection.

        Requires immutable source evidence and observed_at. limit is a strict total
        record limit: oversized selections fail before the first record is yielded.
        Bounds are inclusive [xmin,ymin,xmax,ymax] in supplied coordinate units.
        Numeric field values retain original units inside the field_value object.
        """
        from .model import instant, validate_record
        if set(request) - {'cells', 'bounds', 'fields', 'include_topology', 'include_claims', 'limit', 'evidence', 'observed_at', 'valid_from'}:
            raise ValueError('Unknown projection field; refinement is not supported')
        limit = _bound(request.get('limit', 1000), 'limit', 100000)
        evidence = request.get('evidence')
        if not isinstance(evidence, list) or not evidence or any(not isinstance(x, dict) for x in evidence):
            raise ValueError('Projection requires explicit immutable source evidence')
        observed = request['observed_at']; instant(observed)
        validate_record({'kind': 'event', 'id': 'field:projection-validation', 'event_type': 'projection',
                         'observed_at': observed, 'occurred_at': observed, 'participants': [], 'evidence': evidence})
        for claim in self.config.get('claims', []):
            if claim.get('evidence'):
                validate_record({'kind': 'event', 'id': 'field:claim-validation', 'event_type': 'claim',
                                 'observed_at': observed, 'occurred_at': observed, 'participants': [], 'evidence': claim['evidence']})
        if 'valid_from' in request:
            instant(request['valid_from'])
        keys = request.get('cells', list(self.cells))
        if not isinstance(keys, list) or len(keys) != len(set(keys)) or not set(keys) <= set(self.cells):
            raise ValueError('Projection cells must be unique existing IDs')
        if 'bounds' in request:
            bounds = request['bounds']
            if not isinstance(bounds, list) or len(bounds) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds) or bounds[0] > bounds[2] or bounds[1] > bounds[3]:
                raise ValueError('Bounds require ordered finite xmin,ymin,xmax,ymax')
            keys = [key for key in keys if 'coordinates' in self.cells[key] and bounds[0] <= self.cells[key]['coordinates'][0] <= bounds[2] and bounds[1] <= self.cells[key]['coordinates'][1] <= bounds[3]]
        fields = request.get('fields', list(self.fields))
        if not isinstance(fields, list) or len(fields) != len(set(fields)) or not set(fields) <= set(self.fields):
            raise ValueError('Projection fields must be unique existing field names')
        selected = set(keys)
        edges = [e for e in self.edges if e['source'] in selected and e['target'] in selected] if request.get('include_topology', True) else []
        claims = [(c, [key for key in c['cells'] if key in selected]) for c in self.config.get('claims', [])] if request.get('include_claims', True) else []
        claims = [(c, members) for c, members in claims if members]
        actors = set(c['claimant'] for c, _ in claims)
        count = len(keys) * (1 + len(fields)) + len(edges) + len(actors) + sum(2 + len(members) for _, members in claims)
        if count > limit:
            raise ValueError(f'Projection record limit exceeded: {count} > {limit}')
        # Only a digest of the source state is retained; no eager record graph.
        state_id = digest(self.config)

        def record(kind, key, **values):
            source_evidence = deepcopy(evidence) + deepcopy(values.pop('claim_evidence', []))
            return {'kind': kind, 'id': 'field-record:' + digest([state_id, key, source_evidence, observed, request.get('valid_from')]),
                    'observed_at': observed, **({'valid_from': request['valid_from']} if 'valid_from' in request else {}),
                    'evidence': source_evidence, 'epistemic_status': 'synthetic_scenario', **values}

        for key in keys:
            yield record('entity', ['cell', key], entity_id=key, entity_type='field_cell', label=key,
                         attributes={'measure': self.cells[key]['measure'], 'measure_unit': self.config.get('measure_unit', 'support_unit'),
                                     'coordinates': self.cells[key].get('coordinates'), 'tags': self.cells[key].get('tags', [])})
            for name in fields:
                f = self.fields[name]
                yield record('observation', ['field', key, name], subject=key, metric='field_value', unit='field_value',
                             value={'field': name, 'kind': f['kind'], 'unit': f['unit'], 'value': f['values'][key]}, dimensions={'cell': key, 'field': name})
        for edge in edges:
            yield record('assertion', ['edge', edge['id']], subject=edge['source'], predicate='field_connected_to', object=edge['target'],
                         attributes={'conductance': edge['conductance'], 'transport_rate': edge['transport_rate'], 'diffusion_bidirectional': True, 'transport_directed': True})
        for actor in sorted(actors):
            yield record('entity', ['actor', actor], entity_id=actor, entity_type='agent', label=actor)
        for claim, members in claims:
            yield record('entity', ['claim', claim['id']], entity_id=claim['id'], entity_type='territory_claim', label=claim.get('label', claim['id']), attributes={'sovereignty_established': False}, claim_evidence=claim.get('evidence', []))
            yield record('assertion', ['claimant', claim['id']], subject=claim['id'], predicate='claim_made_by', object=claim['claimant'], claim_evidence=claim.get('evidence', []))
            for key in members:
                yield record('assertion', ['membership', claim['id'], key], subject=claim['id'], predicate='claims_field_cell', object=key, attributes={'sovereignty_established': False}, claim_evidence=claim.get('evidence', []))


def simulate_fields(config):
    if set(config) != {'world', 'evolution'}:
        raise ValueError('simulate_fields requires world and evolution')
    return FieldWorld(config['world']).evolve(config['evolution'])


def estimate_process_work(state, calls):
    """Bound cumulative daily cell/edge updates without executing dynamics."""
    if type(calls) is not int or calls < 0:
        raise ValueError('calls must be a nonnegative integer')
    world = FieldWorld(state)
    outgoing = {key: 0. for key in world.cells}
    for edge in world.edges:
        outgoing[edge['source']] += edge['conductance'] / world.cells[edge['source']]['measure'] + edge['transport_rate']
        outgoing[edge['target']] += edge['conductance'] / world.cells[edge['target']]['measure']
    peak = max(outgoing.values())
    if not math.isfinite(peak) or not math.isfinite(86400 * peak / .9):
        raise ValueError('Field stability requirement outside finite work range')
    substeps = max(1, math.ceil(86400 * peak / .9))
    work = calls * substeps * (len(world.cells) + len(world.edges)) * len(world.fields)
    if work > 100000 or substeps > 10000:
        raise ValueError('Field cumulative work exceeds 100000 cell/edge updates or 10000 daily substeps')
    return {'cell_edge_updates': work, 'daily_substeps': substeps, 'max_cell_edge_updates': 100000}


def _predict(inputs, parameters, context):
    if context['dt_seconds'] != 86400:
        raise ValueError('Field adapter requires exact daily cadence')
    if set(parameters) - {'max_substeps', 'max_work'}:
        raise ValueError('Field adapter accepts only max_substeps and max_work; daily stable timestep is automatic')
    estimate_process_work(inputs['fields_state']['value'], 1)
    result = FieldWorld(inputs['fields_state']['value']).evolve({'duration_seconds': context['dt_seconds'],
        'step_seconds': context['dt_seconds'], 'max_substeps': parameters.get('max_substeps', 10000), 'max_work': parameters.get('max_work', 1000000)})
    return {'pressures': [{'port': 'fields_state', 'mode': 'set', 'value': result['state'], 'unit': None, 'strength': 1, 'confidence': 1}],
            'diagnostics': {'execution': result['execution'], 'conservation': result['conservation'], 'causally_calibrated': False}}


def register_field_processes(registry):
    port = {'type': 'object', 'unit': None}
    registry.register_process({'id': 'field_dynamics', 'inputs': {'fields_state': port}, 'outputs': {'fields_state': port},
        'topology': 'Explicit measured support and conservative adjacency fluxes.', 'description': 'Nonnegative scalar transport/diffusion; uncalibrated coefficients.'})
    registry.register_implementation({'id': 'field_dynamics.deterministic', 'process_id': 'field_dynamics', 'fidelity': 'deterministic',
        'min_step_seconds': 86400, 'max_step_seconds': 86400, 'output_timing': 'end_of_step', 'cost_per_call': 1,
        'work_estimator': 'worldmodel.fields:estimate_process_work', 'work_input': 'fields_state', 'work_unit': 'cell_edge_updates',
        'description': 'Daily update with explicit bounded positivity-preserving numerical substeps.'}, _predict)
    return registry
