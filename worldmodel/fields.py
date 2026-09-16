"""Explicit field supports and conservative kernels; graphs are bounded projections.

Nonnegative scalar quantities only. Cell measure defines integration. Conductance
has measure/second units; directed transport_rate is a fraction/second. Neither
geometry nor political sovereignty is inferred from an adjacency or a claim.

Size and work ceilings are named limits (worldmodel.limits: field_max_cells,
field_max_fields, field_max_values, field_max_edges, field_max_claims,
field_max_claim_memberships, field_max_substeps, field_max_work,
field_max_projection_records, field_max_input_bytes). Numerics run on the shared
array core (worldmodel.field_arrays); backend='python'|'numpy'|'auto' results are
bit-identical.

Estimation hook (field_diffusion_transport): a field may declare ``decay_rate``
(1/second, first-order loss) and ``source_rate`` (amount per measure per second).
Such open fields report ``external_input`` and check final = initial + external.
``calibration=`` binds ``edges[*].conductance``/``transport_rate`` and the fitted
decay/source terms (see :func:`calibrate_field_config`).
"""
from copy import deepcopy
import math
from .field_arrays import FieldArrays
from .backends import resolve_backend
from .limits import resolve_limits
from .util import canonical, digest


def _finite(x, label, positive=False):
    if type(x) not in (int, float) or not math.isfinite(x) or x < 0 or (positive and not x):
        raise ValueError(f'{label} must be finite and {"positive" if positive else "nonnegative"}')
    return float(x)


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


def calibrate_field_config(config, calibration, *, field=None):
    """Bind estimated edge coefficients and one field's decay/source terms; returns (config, bindings record).

    ``field`` names the field that receives ``fields.*.decay_rate``/``source_rate``;
    it defaults to the only field of a single-field world.
    """
    from .estimation.binding import apply_bindings, parameter_bindings
    names = list(config.get('fields', {})) if isinstance(config.get('fields'), dict) else []
    target = field if field is not None else (names[0] if len(names) == 1 else None)
    if target is not None and target not in names:
        raise ValueError('Calibration field must name an existing field')
    rename = {'fields.*.': f'fields.{target}.'} if target is not None else {}
    return apply_bindings(config, parameter_bindings(calibration), rename=rename,
                          only=lambda path: path.startswith('edges[*].') or (target is not None and path.startswith(f'fields.{target}.')))


def field_parameter_provenance(config, calibration=None):
    """Edge coefficients and field decay/source terms as estimated (with record ids) or assumed."""
    from .estimation.binding import parameter_provenance
    leaves = {}
    for edge in config.get('edges', []):
        for key in ('conductance', 'transport_rate'):
            leaves[f'edges[{edge["id"]}].{key}'] = edge.get(key, 0)
    for name, field in config.get('fields', {}).items():
        for key in ('decay_rate', 'source_rate'):
            leaves[f'fields.{name}.{key}'] = field.get(key, 0)
    return parameter_provenance(leaves, calibration)


class FieldWorld:
    def __init__(self, config, *, limits=None, backend=None, calibration=None, calibration_field=None):
        from .model import identifier
        limits = resolve_limits(limits)
        self.calibration = None
        if calibration is not None:
            config, self.calibration = calibrate_field_config(config, calibration, field=calibration_field)
        self.limits, self.backend = limits, backend
        payload = canonical(config)
        if not isinstance(config, dict) or set(config) - {'cells', 'edges', 'fields', 'claims', 'measure_unit', 'description'}:
            raise ValueError('Unknown field world config fields')
        limits.check('field_max_input_bytes', len(payload), 'Field world input bytes')
        if not isinstance(config.get('cells'), list) or not config['cells']:
            raise ValueError('World must contain at least one explicit cell')
        limits.check('field_max_cells', len(config['cells']), 'World explicit cells')
        edges_in = config.get('edges', [])
        fields_in = config.get('fields')
        if isinstance(edges_in, list):
            limits.check('field_max_edges', len(edges_in), 'Field/topology storage budget exceeded: edges')
        if isinstance(fields_in, dict):
            limits.check('field_max_fields', len(fields_in), 'Field/topology storage budget exceeded: fields')
            limits.check('field_max_values', len(config['cells']) * len(fields_in), 'Field/topology storage budget exceeded: cell values')
        self.config = deepcopy(config)
        self.cells = {}
        for cell in self.config['cells']:
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
            self.cells[cell['id']] = cell
        self.ids = list(self.cells)
        index = {key: i for i, key in enumerate(self.ids)}
        self.edges, sources, targets = [], [], []
        seen = set()
        for edge in self.config.get('edges', []):
            if edge['id'] in seen or not isinstance(edge['id'], str) or not edge['id']:
                raise ValueError('Duplicate or invalid edge ID')
            seen.add(edge['id'])
            if edge['source'] not in self.cells or edge['target'] not in self.cells or edge['source'] == edge['target']:
                raise ValueError('Edges need two distinct existing cells')
            item = dict(edge)
            item['conductance'] = _finite(edge.get('conductance', 0), 'conductance')
            item['transport_rate'] = _finite(edge.get('transport_rate', 0), 'transport_rate')
            self.edges.append(item)
            sources.append(index[edge['source']]); targets.append(index[edge['target']])
        self._sources, self._targets = sources, targets
        self.fields = self.config['fields']
        if not isinstance(self.fields, dict) or not self.fields:
            raise ValueError('Field/topology storage budget exceeded: at least one field is required')
        for name, field in self.fields.items():
            if not isinstance(name, str) or not name or field['kind'] not in ('extensive', 'intensive') or not isinstance(field['unit'], str) or not field['unit']:
                raise ValueError('Fields require a name, extensive/intensive kind and explicit unit')
            if not isinstance(field['values'], dict) or field['values'].keys() != self.cells.keys():
                raise ValueError('Each field must explicitly cover every cell; no implicit zero imputation')
            for value in field['values'].values():
                _finite(value, 'field value')
            for key in ('decay_rate', 'source_rate'):
                if key in field and (type(field[key]) not in (int, float) or not math.isfinite(field[key])):
                    raise ValueError(f'{key} must be finite')
        claims = self.config.get('claims', [])
        if not isinstance(claims, list):
            raise ValueError('Claim budget exceeded')
        limits.check('field_max_claims', len(claims), 'Claim budget exceeded')
        seen = set()
        for claim in claims:
            identifier(claim['id']); identifier(claim['claimant'])
            if claim['id'] in seen or claim['id'] in self.cells or claim['claimant'] in self.cells:
                raise ValueError('Duplicate claim or conflicting entity ID')
            seen.add(claim['id'])
            if not isinstance(claim['cells'], list) or len(set(claim['cells'])) != len(claim['cells']) or any(c not in self.cells for c in claim['cells']):
                raise ValueError('Claim cells must be distinct existing cells')
        if set(c['claimant'] for c in claims) & seen:
            raise ValueError('Claimant and claim IDs must be distinct')
        limits.check('field_max_claim_memberships', sum(len(c['cells']) for c in claims), 'Territory membership budget exceeded')
        self._cores = {}

    def _core(self, backend=None):
        chosen = resolve_backend(backend if backend is not None else self.backend, size=len(self.cells) + len(self.edges))
        if chosen not in self._cores:
            self._cores[chosen] = FieldArrays([self.cells[k]['measure'] for k in self.ids], self._sources, self._targets,
                                              [e['conductance'] for e in self.edges], [e['transport_rate'] for e in self.edges],
                                              backend=chosen, validate=False)
        return self._cores[chosen]

    def peak_outgoing_rate(self, backend=None):
        return self._core(backend).peak()

    def evolve(self, request, *, backend=None):
        """Return a new state; never mutate source fields or create missing detail."""
        if set(request) - {'duration_seconds', 'step_seconds', 'max_substeps', 'max_work'}:
            raise ValueError('Unknown evolution request field')
        limits = self.limits
        duration = _finite(request['duration_seconds'], 'duration_seconds')
        requested_step = _finite(request.get('step_seconds', duration or 1), 'step_seconds', True)
        maximum = limits.integer('field_max_substeps', request.get('max_substeps', 10000), 'max_substeps')
        budget = limits.integer('field_max_work', request.get('max_work', 1000000), 'max_work')
        template = self._core(backend)
        core = FieldArrays.__new__(FieldArrays)
        core.__dict__.update({k: v for k, v in template.__dict__.items() if k not in ('fields', '_buffers')})
        core.fields, core._buffers = {}, None
        peak = core.peak()
        decay = max([f.get('decay_rate', 0) for f in self.fields.values() if f.get('decay_rate', 0) > 0] or [0])
        if decay:
            peak = peak + float(decay)
        stable_step = min(requested_step, .9 / peak) if peak else requested_step
        if not math.isfinite(peak) or stable_step <= 0 or not math.isfinite(duration / stable_step):
            raise ValueError('Dynamics are outside finite stability range')
        steps = math.ceil(duration / stable_step) if duration else 0
        work = steps * (len(self.cells) + len(self.edges)) * len(self.fields)
        if steps > maximum or work > budget:
            raise ValueError(f'Field evolution budget exceeded: {steps} substeps, {work} work units')
        for name, field in self.fields.items():
            values = field['values']
            try:
                core.add_field(name, field['kind'], [values[k] for k in self.ids], decay_rate=field.get('decay_rate', 0.0), source_rate=field.get('source_rate', 0.0))
            except ValueError as exc:
                raise ValueError('Field integral outside finite range') from exc
        initial = {name: _integral(core.fields[name]['amounts'][0].tolist() if core.backend == 'numpy' else core.fields[name]['amounts'][0])
                   for name in self.fields}
        dt = duration / steps if steps else 0
        core.advance(dt, steps, nonnegative=True, thresholds={name: -1e-10 * max(1, initial[name]) for name in self.fields})
        state = {key: deepcopy(value) for key, value in self.config.items() if key != 'fields'}
        state['fields'] = {}
        conservation = {}
        for name, field in self.fields.items():
            amounts = core.fields[name]['amounts'][0]
            final = _integral(amounts.tolist() if core.backend == 'numpy' else amounts)
            external = core.fields[name]['external'][0] if core.is_open(name) else None
            if external is None and not math.isclose(initial[name], final, abs_tol=1e-9, rel_tol=1e-10):
                raise ValueError('Field integral conservation violated')
            if external is not None and not math.isclose(initial[name] + external, final, abs_tol=1e-9, rel_tol=1e-9):
                raise ValueError('Open field balance violated (final != initial + external input)')
            reported = core.reported(name)
            state['fields'][name] = {**{k: deepcopy(v) for k, v in field.items() if k != 'values'},
                                     'values': dict(zip(self.ids, reported.tolist() if core.backend == 'numpy' else reported))}
            conservation[name] = {'initial_integral': initial[name], 'final_integral': final, 'difference': final - initial[name]}
            if external is not None:
                conservation[name].update(external_input=external, difference=final - initial[name] - external)
        result = {'state': state, 'execution': {'substeps': steps, 'step_seconds': dt, 'work': work},
                  'conservation': conservation, 'epistemic_status': 'synthetic_scenario', 'causally_calibrated': False,
                  'assumptions': ['Explicit topology; no spatial refinement or inferred geometry.',
                                  'Closed conservative nonnegative scalar fields; constant coefficients.',
                                  'Intensive integrals weight each value by its cell measure.',
                                  'Territory memberships are retained claims; overlap is not resolved into sovereignty.']}
        if any(core.is_open(name) for name in self.fields):
            result['assumptions'][1] = ('Nonnegative scalar fields with conservative edge fluxes; fields declaring decay_rate/source_rate '
                                        'are open (first-order loss, uniform source per measure); constant coefficients.')
        if self.calibration is not None:
            result['calibration'] = deepcopy(self.calibration)
            result['parameter_provenance'] = field_parameter_provenance(self.config, self.calibration)
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
        limit = self.limits.integer('field_max_projection_records', request.get('limit', 1000), 'limit')
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
        keys = request.get('cells', self.ids)
        if not isinstance(keys, list) or len(keys) != len(set(keys)) or any(k not in self.cells for k in keys):
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


def simulate_fields(config, *, limits=None, backend=None, calibration=None, calibration_field=None):
    if set(config) != {'world', 'evolution'}:
        raise ValueError('simulate_fields requires world and evolution')
    return FieldWorld(config['world'], limits=limits, backend=backend, calibration=calibration,
                      calibration_field=calibration_field).evolve(config['evolution'])


def estimate_process_work(state, calls, *, limits=None, parameters=None):
    """Bound cumulative daily cell/edge updates without executing dynamics.

    Each daily call must also fit the adapter's per-call ``max_substeps`` (default
    10000) and ``max_work`` (default 1000000) parameters, so unfit work fails in
    preflight before any handler runs; pass the binding parameters when raised.
    """
    if type(calls) is not int or calls < 0:
        raise ValueError('calls must be a nonnegative integer')
    parameters = parameters or {}
    world = FieldWorld(state, limits=limits, calibration=parameters.get('calibration'), calibration_field=parameters.get('calibration_field'))
    peak = world.peak_outgoing_rate() + max([f.get('decay_rate', 0) for f in world.fields.values() if f.get('decay_rate', 0) > 0] or [0])
    if not math.isfinite(peak) or not math.isfinite(86400 * peak / .9):
        raise ValueError('Field stability requirement outside finite work range')
    substeps = max(1, math.ceil(86400 * peak / .9))
    per_call = substeps * (len(world.cells) + len(world.edges)) * len(world.fields)
    work = calls * per_call
    call_substeps = world.limits.integer('field_max_substeps', parameters.get('max_substeps', 10000), 'max_substeps')
    call_work = world.limits.integer('field_max_work', parameters.get('max_work', 1000000), 'max_work')
    if substeps > call_substeps or per_call > call_work:
        raise ValueError(f'Field daily work exceeds per-call budget: {substeps} substeps, {per_call} work units '
                         f'(max_substeps={call_substeps}, max_work={call_work} parameters)')
    world.limits.check('field_max_substeps', substeps, 'Field daily substeps')
    world.limits.check('field_max_work', work, 'Field cumulative work (cell/edge updates)')
    return {'cell_edge_updates': work, 'daily_substeps': substeps, 'max_cell_edge_updates': world.limits.field_max_work}


def _predict(inputs, parameters, context):
    if context['dt_seconds'] != 86400:
        raise ValueError('Field adapter requires exact daily cadence')
    if set(parameters) - {'max_substeps', 'max_work', 'calibration', 'calibration_field'}:
        raise ValueError('Field adapter accepts only max_substeps, max_work, calibration and calibration_field; daily stable timestep is automatic')
    estimate_process_work(inputs['fields_state']['value'], 1, parameters=parameters)
    world = FieldWorld(inputs['fields_state']['value'], calibration=parameters.get('calibration'), calibration_field=parameters.get('calibration_field'))
    result = world.evolve({'duration_seconds': context['dt_seconds'],
        'step_seconds': context['dt_seconds'], 'max_substeps': parameters.get('max_substeps', 10000), 'max_work': parameters.get('max_work', 1000000)})
    diagnostics = {'execution': result['execution'], 'conservation': result['conservation'], 'causally_calibrated': False,
                   'parameter_provenance': field_parameter_provenance(world.config, world.calibration)}
    if world.calibration is not None:
        diagnostics['calibration'] = world.calibration
    return {'pressures': [{'port': 'fields_state', 'mode': 'set', 'value': result['state'], 'unit': None, 'strength': 1, 'confidence': 1}],
            'diagnostics': diagnostics}


def register_field_processes(registry):
    port = {'type': 'object', 'unit': None}
    registry.register_process({'id': 'field_dynamics', 'inputs': {'fields_state': port}, 'outputs': {'fields_state': port},
        'topology': 'Explicit measured support and conservative adjacency fluxes.', 'description': 'Nonnegative scalar transport/diffusion; uncalibrated coefficients.'})
    registry.register_implementation({'id': 'field_dynamics.deterministic', 'process_id': 'field_dynamics', 'fidelity': 'deterministic',
        'min_step_seconds': 86400, 'max_step_seconds': 86400, 'output_timing': 'end_of_step', 'cost_per_call': 1,
        'work_estimator': 'worldmodel.fields:estimate_process_work', 'work_input': 'fields_state', 'work_unit': 'cell_edge_updates',
        'description': 'Daily update with explicit bounded positivity-preserving numerical substeps.'}, _predict)
    return registry
