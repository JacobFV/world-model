"""Bounded conservative graph fluxes and transactional support timelines.

Vectors are passive components in a caller-declared fixed global frame. Edge
coefficients are supplied, never inferred from coordinates. This is not a curved
surface, fluid velocity, or distributed mesh solver.

Numerics run on worldmodel.field_arrays (python reference or bit-identical numpy
backend). Size/work ceilings are named limits in worldmodel.limits.
"""
from copy import deepcopy
from datetime import timedelta
import math
import re

from .field_arrays import FieldArrays
from .backends import resolve_backend
from .limits import resolve_limits
from .model import identifier, instant
from .util import canonical, digest

HISTORY_MODES = ('full', 'every_n', 'summary')


def _finite(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(label + ' must be finite' + (' and positive' if positive else ''))
    return value


def _seconds(value, name):
    _finite(value, name, True)
    if value != round(value, 6):
        raise ValueError(name + ' requires microsecond precision or coarser')
    return value


def _sum(values):
    try:
        return _finite(math.fsum(values), 'integral')
    except OverflowError as error:
        raise ValueError('Integral exceeds finite range') from error


def _components(field, value):
    if field.get('value_type', 'scalar') == 'vector':
        if not isinstance(value, list):
            raise ValueError('Vector values must be lists')
        return list(value)
    return [value]


def _pack(field, values):
    return values if field.get('value_type', 'scalar') == 'vector' else values[0]


def _check_coordinates(coordinate_system):
    if (not isinstance(coordinate_system, dict) or set(coordinate_system) != {'kind', 'axes', 'unit'}
            or not isinstance(coordinate_system['unit'], str) or not coordinate_system['unit']):
        raise ValueError('Explicit coordinate kind, axes and unit required')
    if coordinate_system['kind'] == 'cartesian':
        if coordinate_system['axes'] != ['x', 'y']:
            raise ValueError('Cartesian coordinates require x,y axes')
    elif coordinate_system['kind'] == 'geodetic':
        if coordinate_system['axes'] != ['latitude', 'longitude'] or coordinate_system['unit'] != 'degree':
            raise ValueError('Geodetic point coordinates require latitude,longitude degrees')
    else:
        raise ValueError('Unsupported coordinate system')


def _check_frames(definitions, component_frames):
    """definitions: {name: (value_type, components)}; frames must match vector fields."""
    if not isinstance(component_frames, dict):
        raise ValueError('Explicit component_frames object required')
    vectors = set()
    for name, (value_type, components) in definitions.items():
        if value_type != 'vector':
            continue
        vectors.add(name)
        frame = component_frames.get(name)
        if (not isinstance(frame, dict) or set(frame) != {'kind', 'axes'} or frame['kind'] != 'fixed_global'
                or not isinstance(frame['axes'], list) or len(frame['axes']) != components
                or any(not isinstance(axis, str) or not axis for axis in frame['axes'])
                or len(set(frame['axes'])) != components):
            raise ValueError('Vector requires a matching fixed_global component frame')
    if set(component_frames) != vectors:
        raise ValueError('Component frames must exactly match vector fields')


def _prepare(world, coordinate_system, component_frames, limits=None, backend=None):
    """Validate a dict world and build its array core (amount arrays, topology)."""
    limits = resolve_limits(limits)
    if not isinstance(world, dict):
        raise ValueError('World must be an object')
    _check_coordinates(coordinate_system)
    cells = world.get('cells')
    if not isinstance(cells, list):
        raise ValueError('Cells must be a list')
    limits.integer('field_max_cells', len(cells), 'cells')
    measures, index = [], {}
    geodetic = coordinate_system['kind'] == 'geodetic'
    for cell in cells:
        identifier(cell['id'])
        if cell['id'] in index:
            raise ValueError('Duplicate support cell')
        index[cell['id']] = len(measures)
        measures.append(_finite(cell['measure'], 'measure', True))
        if 'coordinates' in cell:
            point = cell['coordinates']
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('Coordinates require two explicit values')
            for value in point:
                _finite(value, 'coordinate')
            if geodetic and (abs(point[0]) > 90 or abs(point[1]) > 180):
                raise ValueError('Latitude/longitude outside degree bounds')
    if not isinstance(world.get('measure_unit'), str) or not world['measure_unit']:
        raise ValueError('Explicit measure_unit required')
    fields = world.get('fields')
    if not isinstance(fields, dict):
        raise ValueError('Fields must be an object')
    limits.integer('field_max_fields', len(fields), 'fields')
    edges = world.get('edges', [])
    if not isinstance(edges, list):
        raise ValueError('Edge budget exceeded')
    limits.check('field_max_edges', len(edges), 'Edge budget exceeded')
    definitions, width = {}, 0
    for name, field in fields.items():
        if not isinstance(name, str) or not name or field['kind'] not in ('extensive', 'intensive'):
            raise ValueError('Field needs a name and extensive/intensive kind')
        if not isinstance(field.get('unit'), str) or not field['unit']:
            raise ValueError('Explicit field unit required')
        values = field.get('values')
        if not isinstance(values, dict) or values.keys() != index.keys():
            raise ValueError('Every field must explicitly cover every selected support')
        typ = field.get('value_type', 'scalar')
        if typ not in ('scalar', 'vector'):
            raise ValueError('Only signed scalar or vector fields supported')
        for key in ('decay_rate', 'source_rate'):
            if key in field:
                _finite(field[key], key)
        components = 1
        if typ == 'vector':
            first = next(iter(values.values()))
            if not isinstance(first, list):
                raise ValueError('Vector values must be lists')
            if type(len(first)) is not int or not 1 <= len(first) <= 32:
                raise ValueError('vector components limit must be in 1..32')
            components = len(first)
        definitions[name] = (typ, components)
        width += components
    _check_frames(definitions, component_frames)
    limits.check('field_max_values', width * len(measures), 'Field component storage budget exceeded')
    sources, targets, conductance, rates, seen = [], [], [], [], set()
    for edge in edges:
        if edge['id'] in seen:
            raise ValueError('Duplicate edge ID')
        seen.add(edge['id'])
        a, b = edge['source'], edge['target']
        if a not in index or b not in index or a == b:
            raise ValueError('Edges require distinct selected support endpoints')
        g = _finite(edge.get('conductance', 0), 'conductance')
        r = _finite(edge.get('transport_rate', 0), 'transport_rate')
        if g < 0 or r < 0:
            raise ValueError('Negative coefficients unsupported; reverse the explicit directed edge instead')
        sources.append(index[a]); targets.append(index[b]); conductance.append(g); rates.append(r)
    chosen = resolve_backend(backend, size=len(measures) + len(edges))
    core = FieldArrays(measures, sources, targets, conductance, rates, backend=chosen, validate=False)
    ids = list(index)
    for name, field in fields.items():
        typ, components = definitions[name]
        rows = []
        for key in ids:
            parts = _components(field, field['values'][key])
            if len(parts) != components:
                raise ValueError('Vector component count mismatch')
            for v in parts:
                _finite(v, 'field component')
            rows.append(parts if typ == 'vector' else parts[0])
        core.add_field(name, field['kind'], rows, vector=typ == 'vector', decay_rate=field.get('decay_rate', 0.0), source_rate=field.get('source_rate', 0.0))
    peak = core.peak()
    decay = core.decay_peak()
    if decay:
        peak = peak + decay
    if not math.isfinite(peak):
        raise ValueError('outgoing rate must be finite')
    return {'ids': ids, 'core': core, 'definitions': definitions, 'peak': peak}


def _check_request_units(request, measure_unit):
    if request.get('boundary') != 'closed':
        raise ValueError('Only explicit closed boundary dynamics supported')
    if request.get('edge_units') != {'conductance': measure_unit + '/second', 'transport_rate': '1/second'}:
        raise ValueError('Edge units must declare measure/second conductance and 1/second directed transport')


def _segment(core, duration, requested, max_substeps, max_work, peak):
    """Advance core in place; return execution and per-field conservation."""
    before = {name: core.statistics(name) for name in core.fields}
    plan = core.plan(duration, requested, max_substeps, max_work, peak=peak)
    core.reset_external()
    core.advance(plan['step_seconds'], plan['substeps'])
    conservation = {}
    for name, field in core.fields.items():
        spec = {'value_type': 'vector' if field['vector'] else 'scalar'}
        old, norm = before[name]; after, after_norm = core.statistics(name)
        if core.is_open(name):  # Open field: balance against the accumulated decay/source input; L1 may grow.
            external = field['external']
            tolerance = [1e-10 * max(1, v, n, abs(e)) for v, n, e in zip(norm, after_norm, external)]
            if any(abs(b - (a + e)) > t for a, b, e, t in zip(old, after, external, tolerance)):
                raise ValueError('Open-field balance check failed')
            conservation[name] = {'initial_integral': _pack(spec, old), 'final_integral': _pack(spec, after),
                                  'external_input': _pack(spec, list(external)),
                                  'difference': _pack(spec, [b - a - e for a, b, e in zip(old, after, external)]),
                                  'absolute_tolerance': _pack(spec, tolerance)}
            continue
        tolerance = [1e-10 * max(1, v) for v in norm]
        if any(abs(b - a) > t or n > o + t for a, b, o, n, t in zip(old, after, norm, after_norm, tolerance)):
            raise ValueError('Conservation or signed component stability check failed')
        conservation[name] = {'initial_integral': _pack(spec, old), 'final_integral': _pack(spec, after),
                              'difference': _pack(spec, [b - a for a, b in zip(old, after)]),
                              'absolute_tolerance': _pack(spec, tolerance)}
    execution = {'substeps': plan['substeps'], 'step_seconds': plan['step_seconds'], 'work': plan['work'],
                 'maximum_outgoing_fraction': plan['step_seconds'] * plan['peak']}
    return execution, conservation


def evolve_fields(world, request, *, coordinate_system, component_frames, limits=None, backend=None, calibration=None, calibration_field=None):
    """Apply stable simultaneous conservative fluxes; return a detached result.

    Fields may declare ``decay_rate``/``source_rate`` (open fields; conservation
    reports ``external_input``). ``calibration`` binds estimated edge coefficients and
    decay/source terms (worldmodel.fields.calibrate_field_config).
    """
    applied = None
    if calibration is not None:
        from .fields import calibrate_field_config
        world, applied = calibrate_field_config(world, calibration, field=calibration_field)
    if not isinstance(request, dict) or set(request) - {'duration_seconds', 'step_seconds', 'boundary', 'edge_units', 'max_substeps', 'max_work'}:
        raise ValueError('Unknown numerical request fields')
    limits = resolve_limits(limits)
    if not isinstance(world, dict):
        raise ValueError('World must be an object within the input byte limit')
    limits.check('field_max_input_bytes', len(canonical(world)), 'World input bytes')
    prepared = _prepare(world, coordinate_system, component_frames, limits, backend)
    _check_request_units(request, world['measure_unit'])
    duration = _finite(request['duration_seconds'], 'duration_seconds')
    if duration < 0:
        raise ValueError('duration_seconds must be nonnegative')
    requested = _seconds(request.get('step_seconds', duration or 1), 'step_seconds')
    maximum = limits.integer('field_max_substeps', request.get('max_substeps', 10000), 'max_substeps')
    budget = limits.integer('field_max_work', request.get('max_work', 1000000), 'max_work')
    core = prepared['core']
    execution, conservation = _segment(core, duration, requested, maximum, budget, prepared['peak'])
    state = {key: deepcopy(value) for key, value in world.items() if key != 'fields'}
    state['fields'] = {}
    for name, field in world['fields'].items():
        reported = core.reported(name)
        if core.backend == 'numpy':
            reported = reported.tolist()
        state['fields'][name] = {**{k: deepcopy(v) for k, v in field.items() if k != 'values'},
                                 'values': dict(zip(prepared['ids'], reported))}
    result = {'state': state, 'execution': execution,
            'conservation': conservation, 'coordinate_system': deepcopy(coordinate_system),
            'component_frames': deepcopy(component_frames), 'epistemic_status': 'synthetic_scenario',
            'calibrated': False, 'assumptions': [
                'Explicit closed graph; no external boundary flux.',
                'Conductance has support-measure/second units; directed transport is source fraction/second.',
                'Vectors are independent passive components in a fixed global frame; no rotation or velocity-derived flow.',
                'Coordinates do not determine coefficients; no general geometric or curved-surface PDE is claimed.']}
    if applied is not None:
        from .fields import field_parameter_provenance
        result['calibration'] = applied
        result['parameter_provenance'] = field_parameter_provenance(world, applied)
    return result


def _statistics(world):
    measures = {cell['id']: cell['measure'] for cell in world['cells']}
    result = {}
    for name, field in world['fields'].items():
        rows = [[_finite(v * (measures[key] if field['kind'] == 'intensive' else 1), 'integral component')
                 for v in _components(field, value)] for key, value in field['values'].items()]
        result[name] = ([_sum(row[i] for row in rows) for i in range(len(rows[0]))],
                        [_sum(abs(row[i]) for row in rows) for i in range(len(rows[0]))])
    return result


def _at(value):
    if not isinstance(value, str) or re.search(r'\.\d{7,}', value):
        raise ValueError('Timeline times require microsecond precision or coarser')
    return instant(value)


def _audit(store, report):
    from .spatial_store import _json
    last = store.db.execute('SELECT hash FROM lifecycle_audit ORDER BY revision DESC LIMIT 1').fetchone()
    report = {**report, 'previous_hash': last['hash'] if last else store._metadata()['source_hash']}
    checksum = digest(report)
    cursor = store.db.execute('INSERT INTO lifecycle_audit(payload,hash) VALUES(?,?)', (_json(report, store._limits().spatial_max_row_bytes), checksum))
    return {**report, 'revision': cursor.lastrowid, 'hash': checksum}


def _scope(domain, events, maximum):
    """Keep explicit lifecycle transfers within the selected numerical domain."""
    result = set(domain)
    for event in events:
        kind = event.get('type')
        if kind == 'birth':
            key = event['cell']['id']
            if key in result:
                raise ValueError('Birth support already exists in timeline domain')
            result.add(key)
        elif kind == 'death':
            key = event['cell']
            if key not in result or (event.get('transfer_to') is not None and event['transfer_to'] not in result):
                raise ValueError('Lifecycle transfer must stay in selected domain')
            result.remove(key)
        elif kind == 'merge':
            keys = event['cells']
            if not isinstance(keys, list) or not set(keys) <= result:
                raise ValueError('Merge sources must belong to selected domain')
            result.difference_update(keys); result.add(event['cell']['id'])
        elif kind == 'split':
            if event['cell'] not in result:
                raise ValueError('Split source must belong to selected domain')
            result.remove(event['cell']); result.update(c['id'] for c in event['children'])
            if any(e['source'] not in result or e['target'] not in result for e in event.get('edges', [])):
                raise ValueError('Replacement edges must stay in selected domain')
        else:
            raise ValueError('Unsupported timeline lifecycle event')
        if not result or len(result) > maximum:
            raise ValueError('Timeline cell limit exceeded or domain became empty')
    return result


def evolve_spatial_timeline(store, request, *, limits=None, backend=None):
    """Persist one bounded timeline atomically, including all dated lifecycle.

    ``history`` controls retention: ``full`` (default) keeps every frame state and
    snapshot row; ``every_n`` keeps full frames at every ``history_every``-th frame
    plus the final frame and summary frames otherwise; ``summary`` keeps only frame
    times, lineage, selection sizes and per-field integrals and returns no final
    state dict (``state_summary`` carries counts, integrals and an array digest).
    """
    limits = resolve_limits(limits if limits is not None else getattr(store, '_limit_overrides', None))
    allowed = {'start', 'end', 'sample_seconds', 'step_seconds', 'boundary', 'edge_units', 'component_frames',
               'cells', 'allow_boundary_cut', 'events', 'max_cells', 'max_frames', 'max_snapshots', 'max_work', 'max_substeps',
               'history', 'history_every'}
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError('Unknown timeline fields or request exceeds budget')
    limits.check('timeline_max_request_bytes', len(canonical(request)), 'Timeline request exceeds budget')
    start, end = _at(request['start']), _at(request['end'])
    if end < start:
        raise ValueError('Timeline end must not precede start')
    sample = _seconds(request['sample_seconds'], 'sample_seconds')
    step = _seconds(request.get('step_seconds', sample), 'step_seconds')
    caps = {name: limits.integer(limit, request.get(name, default), name) for name, default, limit in (
        ('max_cells', 1000, 'field_max_cells'), ('max_frames', 100, 'timeline_max_frames'),
        ('max_snapshots', 100000, 'timeline_max_snapshots'), ('max_work', 1000000, 'field_max_work'),
        ('max_substeps', 10000, 'field_max_substeps'))}
    history = request.get('history', 'full')
    if history not in HISTORY_MODES:
        raise ValueError('history must be full, every_n or summary')
    every = request.get('history_every', 1)
    if history == 'every_n' and (type(every) is not int or every < 1):
        raise ValueError('history_every must be a positive integer')
    if type(request.get('allow_boundary_cut', False)) is not bool:
        raise ValueError('allow_boundary_cut must be boolean')
    total_us = round((end - start).total_seconds() * 1000000)
    sample_us = round(sample * 1000000)
    count = (total_us + sample_us - 1) // sample_us + 1
    if count > caps['max_frames']:
        raise ValueError('Timeline frame budget exceeded')
    points = {min(i * sample_us, total_us) for i in range(count)}
    schedule = {}
    batches = request.get('events', [])
    if not isinstance(batches, list):
        raise ValueError('Timeline event batch budget exceeded')
    limits.check('spatial_max_lifecycle_batch', len(batches), 'Timeline event batch budget exceeded')
    operations = 0
    for batch in batches:
        if not isinstance(batch, dict) or set(batch) != {'time', 'events'} or not isinstance(batch['events'], list):
            raise ValueError('Dated batch requires time and events')
        at = _at(batch['time'])
        if not start <= at <= end or not batch['events']:
            raise ValueError('Event time or batch size outside timeline bounds')
        limits.check('spatial_max_lifecycle_batch', len(batch['events']), 'Timeline event batch size')
        operations += len(batch['events'])
        limits.check('timeline_max_lifecycle_operations', operations, 'Timeline lifecycle operation budget exceeded')
        offset = round((at - start).total_seconds() * 1000000)
        points.add(offset); schedule.setdefault(offset, []).append(batch['events'])
    if len(points) > caps['max_frames']:
        raise ValueError('Timeline event/sample frame budget exceeded')
    frames_meta = request.get('component_frames')
    ordered = sorted(points)
    with store._transaction(write=True):
        metadata = store._metadata()
        if 'simulation_time' in metadata and _at(metadata['simulation_time']) != start:
            raise ValueError('Timeline start must equal the stored continuation time')
        if 'dynamics_component_frames' in metadata and metadata['dynamics_component_frames'] != frames_meta:
            raise ValueError('Continuation component frames cannot change implicitly')
        coordinate_system = metadata['coordinate_system']
        _check_request_units(request, metadata['measure_unit'])

        def load(cells, check_boundary=True):
            loaded = store.load_arrays(cells=cells, limit=caps['max_cells'], backend=backend, limits=limits)
            _check_frames({n: (d['value_type'], d.get('components', 1)) for n, d in loaded['definitions'].items()}, frames_meta)
            if check_boundary and loaded['boundary_edges'] and not request.get('allow_boundary_cut', False):
                raise ValueError('Selected timeline cuts boundary edges; explicit acceptance required')
            return loaded

        loaded = load(request.get('cells'), check_boundary=False)
        domain = set(loaded['ids'])
        if history == 'summary':
            source = store._source(metadata)
        else:
            source = deepcopy(store.select(cells=sorted(domain), limit=caps['max_cells'])['source'])
        initial = {name: loaded['core'].statistics(name, reported=False) for name in loaded['core'].fields}
        external = {name: [0.0] * len(total) for name, (total, _) in initial.items()}
        frames, snapshots, event_audit, segments = [], [], [], []
        output_bytes = work = substeps = 0
        previous = 0
        dirty = False
        loaded = load(sorted(domain))
        final_state = None

        def flush():
            nonlocal dirty
            if dirty:
                store.put_value_arrays(loaded['ids'], loaded['core'])
                dirty = False

        for position, offset in enumerate(ordered):
            at = (start + timedelta(microseconds=offset)).isoformat()
            if offset > previous:
                if work >= caps['max_work'] or substeps >= caps['max_substeps']:
                    raise ValueError('Cumulative numerical budget exhausted')
                numerical = {'duration_seconds': (offset - previous) / 1000000, 'step_seconds': step,
                             'boundary': request.get('boundary'), 'edge_units': request.get('edge_units'),
                             'max_work': caps['max_work'] - work, 'max_substeps': caps['max_substeps'] - substeps}
                core = loaded['core']
                execution, conservation = _segment(core, numerical['duration_seconds'], step, numerical['max_substeps'],
                                                   numerical['max_work'], core.peak())
                work += execution['work']; substeps += execution['substeps']
                dirty = True
                segment = {'start': (start + timedelta(microseconds=previous)).isoformat(), 'end': at,
                           'execution': execution, 'conservation': conservation}
                _audit(store, {'events': [{'type': 'field_evolution', **segment}], 'requests': numerical})
                segments.append(segment)
            if offset in schedule:
                flush()
                for events in schedule[offset]:
                    domain = _scope(domain, events, caps['max_cells'])
                    report = store.apply(events)
                    for event in report['events']:
                        if 'external_input' in event:
                            for name, value in event['external_input'].items():
                                parts = value if isinstance(value, list) else [value]
                                external[name] = [_sum([a, b]) for a, b in zip(external[name], parts)]
                    dated = _audit(store, {'events': [{'type': 'dated_lifecycle', 'time': at,
                                                      'applied_revision': report['revision'], 'applied_hash': report['hash']}],
                                           'requests': {'time': at, 'events': events}})
                    event_audit.append({'time': at, 'application': report, 'date_record': dated})
                loaded = load(sorted(domain))
            last = position == len(ordered) - 1
            retained = history == 'full' or (history == 'every_n' and (position % every == 0 or last))
            phase = 'after_events' if offset in schedule else 'sample'
            if retained:
                flush()
                selected = store.select(cells=sorted(domain), limit=caps['max_cells'])
                frame = {'time': at, 'phase': phase, 'state': selected['state'], 'source': selected['source'],
                         'selection': selected['selection']}
                rows = [{'time': at, 'entity': key, 'variable': name, 'value': deepcopy(value), 'unit': field['unit'],
                         'origin': 'synthetic_scenario', 'evidence': []}
                        for name, field in selected['state']['fields'].items() for key, value in field['values'].items()]
                if len(snapshots) + len(rows) > caps['max_snapshots']:
                    raise ValueError('Timeline snapshot budget exceeded')
                output_bytes += len(canonical(frame)) + len(canonical(rows))
                if last:
                    final_state = selected['state']
            else:
                core = loaded['core']
                frame = {'time': at, 'phase': phase, 'state': None, 'source': store._source(),
                         'selection': {'cells': len(loaded['ids']), 'boundary_edges': loaded['boundary_edges'], 'truncated': False},
                         'integrals': {name: _pack({'value_type': 'vector' if f['vector'] else 'scalar'}, core.statistics(name)[0])
                                       for name, f in core.fields.items()}}
                rows = []
                output_bytes += len(canonical(frame))
            limits.check('timeline_max_output_bytes', output_bytes, 'Timeline output exceeds budget')
            frames.append(frame); snapshots.extend(rows); previous = offset
        flush()
        core = loaded['core']
        final = {name: core.statistics(name) for name in core.fields}
        conservation = {}
        for name, field in core.fields.items():
            spec = {'value_type': 'vector' if field['vector'] else 'scalar'}
            before, norm = initial[name]; after, _ = final[name]
            difference = [b - _sum([a, imported]) for a, b, imported in zip(before, after, external[name])]
            if any(abs(d) > 1e-9 * max(1, n, abs(imported)) for d, n, imported in zip(difference, norm, external[name])):
                raise ValueError('Timeline integral conservation failed')
            conservation[name] = {'initial_integral': _pack(spec, before), 'external_input': _pack(spec, external[name]),
                                  'final_integral': _pack(spec, after), 'difference': _pack(spec, difference)}
        from .spatial_store import _json
        store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('simulation_time', _json(end.isoformat())))
        store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('dynamics_component_frames', _json(frames_meta)))
        if final_state is not None:
            completion = {'type': 'timeline_complete', 'start': start.isoformat(), 'end': end.isoformat(),
                          'state_hash': digest(final_state)}
        else:
            completion = {'type': 'timeline_complete', 'start': start.isoformat(), 'end': end.isoformat(),
                          'state_hash': core.digest(loaded['ids']), 'state_hash_kind': 'arrays-sha256-v1'}
        completed = _audit(store, {'events': [completion], 'requests': request})
        if final_state is not None:
            final_source = store.select(cells=sorted(domain), limit=caps['max_cells'])['source']
        else:
            final_source = store._source()
        result = {'start': start.isoformat(), 'end': end.isoformat(), 'frames': frames, 'snapshots': snapshots,
                  'state': final_state, 'source': source, 'final_source': final_source,
                  'coordinate_system': coordinate_system, 'component_frames': deepcopy(frames_meta),
                  'conservation': conservation, 'events': event_audit, 'segments': segments, 'completion': completed,
                  'execution': {'work': work, 'substeps': substeps, 'frames': len(frames), 'lifecycle_operations': operations,
                                'history': history, 'backend': core.backend},
                  'epistemic_status': 'synthetic_scenario', 'calibrated': False,
                  'assumptions': ['Closed selected graph; explicitly accepted cut edges have zero boundary flux.',
                                  'Events occur before their timestamped frame; sampling/event boundaries also split integration steps.',
                                  'Passive fixed-frame components, explicit coefficients; no curved-surface PDE.']}
        if final_state is None:
            result['state_summary'] = {'cells': len(loaded['ids']), 'edges': core.m, 'state_hash': completion['state_hash'],
                                       'integrals': {name: value['final_integral'] for name, value in conservation.items()}}
        return result
