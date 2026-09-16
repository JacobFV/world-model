"""Typed, replaceable illustrative processes and stable pressure integration."""
from copy import deepcopy
import json
import hashlib
import inspect
import marshal
import math

TYPES = {'number', 'string', 'boolean', 'object', 'array', 'vector'}
FIDELITIES = {'deterministic', 'stochastic', 'agent'}


def _number(value, label, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be finite numeric')
    if nonnegative and value < 0:
        raise ValueError(f'{label} must be nonnegative')
    return value


def _json(value):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Value must be finite JSON data') from exc


def _typed(value, value_type):
    if value_type == 'number':
        _number(value, 'value')
    elif value_type == 'vector':
        if not isinstance(value, list) or not value:
            raise ValueError('vector must be a nonempty numeric array')
        for item in value:
            _number(item, 'vector component')
    elif value_type in {'string', 'boolean', 'object', 'array'}:
        expected = {'string': str, 'boolean': bool, 'object': dict, 'array': list}[value_type]
        if not isinstance(value, expected):
            raise ValueError(f'value must have type {value_type}')
        _json(value)
    else:
        raise ValueError(f'Unknown value type: {value_type}')


def _pressure(p, value_type):
    if not isinstance(p, dict) or p.get('mode') not in {'rate', 'target', 'set'}:
        raise ValueError('Pressure must specify mode rate, target or set')
    if 'unit' not in p or (p['unit'] is not None and not isinstance(p['unit'], str)):
        raise ValueError('Pressure must declare unit')
    _typed(p.get('value'), value_type)
    _number(p.get('strength'), 'strength', True)
    _number(p.get('confidence'), 'confidence', True)
    _number(p['strength'] * p['confidence'], 'effective strength', True)
    if p['mode'] != 'set' and value_type not in {'number', 'vector'}:
        raise ValueError('Only numeric values support rate and target dynamics')


def combine_pressures(current, pressures, dt_seconds, value_type='number'):
    """Integrate constant rates and relaxations exactly; resolve exclusive sets."""
    _number(dt_seconds, 'dt_seconds', True)
    _typed(current, value_type)
    for p in pressures:
        _pressure(p, value_type)
        if value_type == 'vector' and len(p['value']) != len(current):
            raise ValueError('Pressure vector dimensions disagree with current state')
    if len({p['unit'] for p in pressures}) > 1:
        raise ValueError('Pressure units must match')
    sets = [p for p in pressures if p['mode'] == 'set']
    if sets:
        if len(sets) != len(pressures):
            raise ValueError('Set pressures are exclusive with rate/target dynamics')
        weight = max(p['strength'] * p['confidence'] for p in sets)
        winners = [p['value'] for p in sets if p['strength'] * p['confidence'] == weight]
        if any(value != winners[0] for value in winners):
            raise ValueError('Conflicting set targets have equal strength/confidence')
        return deepcopy(winners[0] if weight > 0 and dt_seconds > 0 else current)
    if not pressures or dt_seconds == 0:
        return deepcopy(current)
    if value_type == 'vector':
        return [combine_pressures(value, [dict(p, value=p['value'][i]) for p in pressures], dt_seconds)
                for i, value in enumerate(current)]
    rates = sum(p['value'] * p['strength'] * p['confidence'] for p in pressures if p['mode'] == 'rate')
    k = sum(p['strength'] * p['confidence'] for p in pressures if p['mode'] == 'target')
    _number(k, 'combined relaxation strength', True)
    _number(rates, 'combined rate')
    if k:
        target = sum(p['value'] * ((p['strength'] * p['confidence']) / k)
                     for p in pressures if p['mode'] == 'target')
        decay = math.exp(-k * dt_seconds)
        fraction = -math.expm1(-k * dt_seconds)
        result = current * decay + target * fraction + rates * (fraction / k)
    else:
        result = current + rates * dt_seconds
    return _number(result, 'combined result')


class ProcessRegistry:
    """Registry descriptors are defensive JSON copies; fidelity never falls back."""
    def __init__(self):
        self._processes = {}
        self._implementations = {}
        self._handlers = {}

    def register_process(self, spec):
        _json(spec)
        if not isinstance(spec.get('id'), str) or not spec['id'] or spec['id'] in self._processes:
            raise ValueError('Process id must be unique and nonempty')
        for key in ('inputs', 'outputs'):
            if not isinstance(spec.get(key), dict):
                raise ValueError(f'Process {key} must be a port mapping')
            for name, port in spec[key].items():
                if not isinstance(name, str) or not isinstance(port, dict) or port.get('type') not in TYPES:
                    raise ValueError('Invalid port descriptor')
                if port.get('unit') is not None and not isinstance(port['unit'], str):
                    raise ValueError('Port unit must be string or null')
                if 'temporal' in port:
                    temporal=port['temporal']
                    if not isinstance(temporal,dict) or set(temporal)!={'lag_seconds','max_age_seconds','missing'} or temporal['missing'] not in ('error','omit'):
                        raise ValueError('Input temporal contract requires lag, maximum age and missing policy')
                    _number(temporal['lag_seconds'],'input lag',True);_number(temporal['max_age_seconds'],'input maximum age',True)
                    if key!='inputs':raise ValueError('Temporal contracts apply only to input ports')
                if not isinstance(port.get('required', True), bool):
                    raise ValueError('Port required must be boolean')
        for key in ('topology', 'description'):
            if key not in spec:
                raise ValueError(f'Process requires {key}')
        if spec.get('validated', False) is not False or 'calibration' in spec:
            raise ValueError('validated is derived from attached calibration records; register processes with validated false')
        from .process_contracts import validate_conserved_outputs
        validate_conserved_outputs(spec)
        self._processes[spec['id']] = deepcopy(spec)

    def attach_calibration(self, record, *, process_id=None, required_components=None):
        """Attach a calibration record (see ``worldmodel.estimation.registry.calibration_record``).

        ``validated`` becomes true only when every required component has a record
        whose explicit, nonempty acceptance criteria all passed. An empty
        requirement list can never validate a process.
        """
        _json(record)
        from .util import digest
        if not isinstance(record, dict) or record.get('schema') != 'worldmodel.calibration/1':
            raise ValueError('Unsupported calibration record')
        if record.get('record_id') != digest({k: v for k, v in record.items() if k != 'record_id'}):
            raise ValueError('Calibration record content does not match record_id')
        process_id = process_id or record.get('process_id')
        if process_id not in self._processes:
            raise ValueError(f'Unknown process: {process_id}')
        component = record.get('component')
        if not isinstance(component, str) or not component:
            raise ValueError('Calibration record requires a component')
        acceptance = record.get('acceptance') or {}
        results = acceptance.get('results')
        passed = (isinstance(results, list) and bool(results) and all(r.get('passed') is True for r in results)
                  and acceptance.get('passed') is True and bool(record.get('criteria')))
        if record.get('validated') is not passed:
            raise ValueError('validated must equal the outcome of explicit acceptance criteria')
        if required_components is None:
            from .estimation.registry import required_components as lookup
            required_components = lookup(process_id)
        required = sorted(set(required_components))
        if component not in required and required:
            from .estimation.families import components_for
            if component not in components_for(process_id, include_optional=True):
                raise ValueError(f'Component {component} is not declared for process {process_id}')
        descriptor = self._processes[process_id]
        calibration = descriptor.setdefault('calibration', {'components': {}})
        calibration['components'][component] = deepcopy(record)
        calibration['required_components'] = required
        components = calibration['components']
        calibration['missing_components'] = [c for c in required if c not in components]
        calibration['failing_components'] = [c for c in required if c in components and components[c].get('validated') is not True]
        descriptor['validated'] = bool(required) and not calibration['missing_components'] and not calibration['failing_components']
        return deepcopy(calibration)

    def calibration(self, process_id):
        if process_id not in self._processes:
            raise ValueError(f'Unknown process: {process_id}')
        return deepcopy(self._processes[process_id].get('calibration'))

    def calibrated_parameters(self, process_id, *, require_validated=True):
        """Merged process-parameter paths from attached component estimates."""
        if process_id not in self._processes:
            raise ValueError(f'Unknown process: {process_id}')
        descriptor = self._processes[process_id]
        if require_validated and descriptor.get('validated') is not True:
            raise ValueError(f'Process {process_id} is not validated; pass require_validated=False to inspect estimates')
        merged = {}
        for component, record in sorted(descriptor.get('calibration', {}).get('components', {}).items()):
            if require_validated and record.get('validated') is not True:
                continue
            for path, value in record.get('process_parameters', {}).items():
                if path in merged and merged[path]['value'] != value:
                    raise ValueError(f'Conflicting calibrated values for {path}')
                merged[path] = {'value': value, 'component': component, 'estimate_id': record['estimate_id']}
        return merged

    def register_implementation(self, spec, handler):
        _json(spec)
        if not isinstance(spec.get('id'), str) or not spec['id'] or spec['id'] in self._implementations:
            raise ValueError('Implementation id must be unique and nonempty')
        if spec.get('process_id') not in self._processes or spec.get('fidelity') not in FIDELITIES:
            raise ValueError('Unknown process or fidelity')
        if _number(spec.get('max_step_seconds'), 'max_step_seconds', True) == 0:
            raise ValueError('max_step_seconds must be positive')
        _number(spec.get('cost_per_call'), 'cost_per_call', True)
        if not callable(handler):
            raise ValueError('Implementation handler must be callable')
        for key in ('min_step_seconds', 'min_regime', 'max_regime'):
            if key in spec:
                _number(spec[key], key, True)
        if 'approximation' in spec and (not isinstance(spec['approximation'], list) or any(not isinstance(v,str) for v in spec['approximation'])):
            raise ValueError('Implementation approximation must be explicit strings')
        descriptor = deepcopy(spec)
        descriptor.setdefault('approximation', ['No approximation statement supplied by this implementation.'])
        identity = {'module': getattr(handler, '__module__', type(handler).__module__),
                    'qualname': getattr(handler, '__qualname__', type(handler).__qualname__)}
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):
            source = None
        if source is not None:
            identity['source'] = source
            identity['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
        elif hasattr(handler, '__code__'):
            identity['code_sha256'] = hashlib.sha256(marshal.dumps(handler.__code__)).hexdigest()
        else:
            identity['source_unavailable'] = True
        closure = getattr(handler, '__closure__', None)
        if closure:
            captured = {}
            for name, cell in zip(handler.__code__.co_freevars, closure):
                try:
                    value = cell.cell_contents
                    _json(value)
                    captured[name] = deepcopy(value)
                except (ValueError, TypeError):
                    captured[name] = {'unserializable_type': type(cell.cell_contents).__qualname__}
            identity['closure'] = captured
        descriptor['handler_identity'] = identity
        self._implementations[spec['id']] = descriptor
        self._handlers[spec['id']] = handler

    def describe(self):
        return {'processes': [deepcopy(self._processes[k]) for k in sorted(self._processes)],
                'implementations': [deepcopy(self._implementations[k]) for k in sorted(self._implementations)]}

    def select(self, process_id, fidelity, step_seconds, remaining_budget, backend_available=False):
        if process_id not in self._processes:
            raise ValueError(f'Unknown process: {process_id}')
        _number(step_seconds, 'step_seconds', True)
        _number(remaining_budget, 'remaining_budget', True)
        if step_seconds == 0 or fidelity not in FIDELITIES:
            raise ValueError('Positive step and explicit supported fidelity required')
        choices = [s for s in self._implementations.values() if s['process_id'] == process_id
                   and s['fidelity'] == fidelity and step_seconds <= s['max_step_seconds']
                   and step_seconds >= s.get('min_step_seconds', s.get('min_regime', 0))
                   and step_seconds <= s.get('max_regime', s['max_step_seconds'])
                   and s['cost_per_call'] <= remaining_budget
                   and (backend_available or not (s.get('requires_backend') or fidelity == 'agent'))]
        if not choices:
            raise ValueError(f'No compatible implementation for {process_id}: fidelity={fidelity}, step={step_seconds}, budget={remaining_budget}, backend={backend_available}')
        return deepcopy(min(choices, key=lambda s: (s['cost_per_call'], s['id'])))

    def predict(self, implementation_id, inputs, parameters, context):
        if implementation_id not in self._implementations:
            raise ValueError(f'Unknown implementation: {implementation_id}')
        impl = self._implementations[implementation_id]
        process = self._processes[impl['process_id']]
        dt = _number(context.get('dt_seconds'), 'dt_seconds', True)
        if dt <= 0 or dt > impl['max_step_seconds'] or dt < impl.get('min_step_seconds', impl.get('min_regime', 0)) or dt > impl.get('max_regime', impl['max_step_seconds']):
            raise ValueError('Prediction duration outside implementation regime')
        if (impl['fidelity'] == 'agent' or impl.get('requires_backend')) and not callable(getattr(context.get('agent_backend'), 'predict', None)):
            raise ValueError('Agent implementation requires explicit backend.predict')
        if not isinstance(inputs, dict) or set(inputs) - set(process['inputs']):
            raise ValueError('Unknown input ports')
        inputs = deepcopy(inputs)
        for name, port in process['inputs'].items():
            if name in inputs and port.get('temporal'):
                from .process_contracts import select_timed_input
                item = inputs[name]
                if not isinstance(item,dict) or item.get('unit') != port.get('unit'):
                    raise ValueError('Timed input unit mismatch')
                selected = select_timed_input(item.get('value'), {'unit':port.get('unit'), **port['temporal']}, at=context['time'], known_at=context.get('known_at',context['time']))
                if selected is None:
                    inputs.pop(name)
                else:
                    inputs[name] = selected
            if name not in inputs:
                if port.get('required', True):
                    raise ValueError(f'Missing required input: {name}')
                continue
            item = inputs[name]
            if not isinstance(item, dict) or 'value' not in item or 'unit' not in item or item.get('unit') != port.get('unit'):
                raise ValueError(f'Input unit/value mismatch: {name}')
            _typed(item['value'], port['type'])
        _json(parameters)
        result = self._handlers[implementation_id](deepcopy(inputs), deepcopy(parameters), context)
        if not isinstance(result, dict):
            raise ValueError('Prediction must return a dictionary')
        result = deepcopy(result)
        if 'calibration' in process and isinstance(result.get('diagnostics', {}), dict):
            diagnostics = result.setdefault('diagnostics', {})
            diagnostics['validated'] = process.get('validated') is True
            diagnostics['calibration'] = {'validated': process.get('validated') is True,
                'components': {c: {k: r.get(k) for k in ('validated', 'estimate_id', 'report_id', 'cutoff')}
                               for c, r in sorted(process['calibration']['components'].items())}}
        if any(p.get('temporal') for p in process['inputs'].values()):
            result['input_receipts']={name:deepcopy(inputs.get(name)) for name,p in process['inputs'].items() if p.get('temporal')}
        for key, default in [('pressures', []), ('events', []), ('memory', {}), ('diagnostics', {})]:
            result.setdefault(key, default)
            if not isinstance(result[key], type(default)):
                raise ValueError(f'Prediction {key} has invalid type')
        for p in result['pressures']:
            if not isinstance(p, dict) or p.get('port') not in process['outputs']:
                raise ValueError('Unknown pressure output port')
            port = process['outputs'][p['port']]
            _pressure(p, port['type'])
            if impl.get('output_timing') == 'end_of_step' and p['mode'] != 'set':
                raise ValueError('end_of_step implementations may emit only set pressures')
            if p['unit'] != port.get('unit'):
                raise ValueError(f'Output unit mismatch: {p["port"]}')
            state = context.get('state', {})
            if p['port'] in state and port['type'] == 'vector' and len(p['value']) != len(state[p['port']]):
                raise ValueError('Output vector dimension mismatch')
        _json(result)
        return result
