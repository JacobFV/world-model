"""Bounded conservative graph fluxes and transactional support timelines.

Vectors are passive components in a caller-declared fixed global frame. Edge
coefficients are supplied, never inferred from coordinates. This is not a curved
surface, fluid velocity, or distributed mesh solver.
"""
from copy import deepcopy
from datetime import timedelta
import math
import re

from .model import identifier, instant
from .util import canonical, digest


def _finite(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(label + ' must be finite' + (' and positive' if positive else ''))
    return value


def _bound(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{name} limit must be in 1..{maximum}')
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


def _prepare(world, coordinate_system, component_frames):
    if not isinstance(world, dict) or len(canonical(world)) > 8 * 1024 * 1024:
        raise ValueError('World must be an object within the 8 MiB input budget')
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
    cells = world.get('cells')
    if not isinstance(cells, list):
        raise ValueError('Cells must be a list')
    _bound(len(cells), 'cells', 1000)
    measures = {}
    for cell in cells:
        identifier(cell['id'])
        if cell['id'] in measures:
            raise ValueError('Duplicate support cell')
        measures[cell['id']] = _finite(cell['measure'], 'measure', True)
        if 'coordinates' in cell:
            point = cell['coordinates']
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('Coordinates require two explicit values')
            for value in point:
                _finite(value, 'coordinate')
            if coordinate_system['kind'] == 'geodetic' and (abs(point[0]) > 90 or abs(point[1]) > 180):
                raise ValueError('Latitude/longitude outside degree bounds')
    if not isinstance(world.get('measure_unit'), str) or not world['measure_unit']:
        raise ValueError('Explicit measure_unit required')
    fields = world.get('fields')
    if not isinstance(fields, dict):
        raise ValueError('Fields must be an object')
    _bound(len(fields), 'fields', 100)
    amounts, width, vector_names = {}, 0, set()
    if not isinstance(component_frames, dict):
        raise ValueError('Explicit component_frames object required')
    for name, field in fields.items():
        if not isinstance(name, str) or not name or field['kind'] not in ('extensive', 'intensive'):
            raise ValueError('Field needs a name and extensive/intensive kind')
        if not isinstance(field.get('unit'), str) or not field['unit']:
            raise ValueError('Explicit field unit required')
        values = field.get('values')
        if not isinstance(values, dict) or set(values) != set(measures):
            raise ValueError('Every field must explicitly cover every selected support')
        typ = field.get('value_type', 'scalar')
        if typ not in ('scalar', 'vector'):
            raise ValueError('Only signed scalar or vector fields supported')
        components = 1
        if typ == 'vector':
            vector_names.add(name)
            first = next(iter(values.values()))
            if not isinstance(first, list):
                raise ValueError('Vector values must be lists')
            components = _bound(len(first), 'vector components', 32)
            frame = component_frames.get(name)
            if (not isinstance(frame, dict) or set(frame) != {'kind', 'axes'} or frame['kind'] != 'fixed_global'
                    or not isinstance(frame['axes'], list) or len(frame['axes']) != components
                    or any(not isinstance(axis, str) or not axis for axis in frame['axes'])
                    or len(set(frame['axes'])) != components):
                raise ValueError('Vector requires a matching fixed_global component frame')
        width += components
        amounts[name] = {}
        for key, value in values.items():
            parts = _components(field, value)
            if len(parts) != components:
                raise ValueError('Vector component count mismatch')
            factor = measures[key] if field['kind'] == 'intensive' else 1
            amounts[name][key] = [_finite(_finite(v, 'field component') * factor, 'extensive component') for v in parts]
    if set(component_frames) != vector_names:
        raise ValueError('Component frames must exactly match vector fields')
    if width * len(measures) > 100000:
        raise ValueError('Field component storage budget exceeded')
    edges = world.get('edges', [])
    if not isinstance(edges, list) or len(edges) > 10000:
        raise ValueError('Edge budget exceeded')
    outgoing = {key: 0.0 for key in measures}
    seen = set()
    for edge in edges:
        if edge['id'] in seen:
            raise ValueError('Duplicate edge ID')
        seen.add(edge['id'])
        a, b = edge['source'], edge['target']
        if a not in measures or b not in measures or a == b:
            raise ValueError('Edges require distinct selected support endpoints')
        conductance = _finite(edge.get('conductance', 0), 'conductance')
        rate = _finite(edge.get('transport_rate', 0), 'transport_rate')
        if conductance < 0 or rate < 0:
            raise ValueError('Negative coefficients unsupported; reverse the explicit directed edge instead')
        outgoing[a] = _finite(outgoing[a] + conductance / measures[a] + rate, 'outgoing rate')
        outgoing[b] = _finite(outgoing[b] + conductance / measures[b], 'outgoing rate')
    return measures, amounts, width, max(outgoing.values())


def _statistics(world):
    measures = {cell['id']: cell['measure'] for cell in world['cells']}
    result = {}
    for name, field in world['fields'].items():
        rows = [[_finite(v * (measures[key] if field['kind'] == 'intensive' else 1), 'integral component')
                 for v in _components(field, value)] for key, value in field['values'].items()]
        result[name] = ([_sum(row[i] for row in rows) for i in range(len(rows[0]))],
                        [_sum(abs(row[i]) for row in rows) for i in range(len(rows[0]))])
    return result


def evolve_fields(world, request, *, coordinate_system, component_frames):
    """Apply stable simultaneous conservative fluxes; return a detached result."""
    if not isinstance(request, dict) or set(request) - {'duration_seconds', 'step_seconds', 'boundary', 'edge_units', 'max_substeps', 'max_work'}:
        raise ValueError('Unknown numerical request fields')
    measures, amounts, width, peak = _prepare(world, coordinate_system, component_frames)
    if request.get('boundary') != 'closed':
        raise ValueError('Only explicit closed boundary dynamics supported')
    if request.get('edge_units') != {'conductance': world['measure_unit'] + '/second', 'transport_rate': '1/second'}:
        raise ValueError('Edge units must declare measure/second conductance and 1/second directed transport')
    duration = _finite(request['duration_seconds'], 'duration_seconds')
    if duration < 0:
        raise ValueError('duration_seconds must be nonnegative')
    requested = _seconds(request.get('step_seconds', duration or 1), 'step_seconds')
    maximum = _bound(request.get('max_substeps', 10000), 'max_substeps', 100000)
    budget = _bound(request.get('max_work', 1000000), 'max_work', 10000000)
    stable = min(requested, .9 / peak) if peak else requested
    if stable <= 0 or not math.isfinite(duration / stable):
        raise ValueError('Numerical stability is outside finite range')
    steps = math.ceil(duration / stable) if duration else 0
    if steps and duration / steps * peak > .9:
        steps += 1
    work = steps * (len(measures) + len(world.get('edges', []))) * width
    if steps > maximum or work > budget:
        raise ValueError(f'Numerical budget exceeded: {steps} substeps, {work} component updates')
    dt = duration / steps if steps else 0
    initial = _statistics(world)
    for _ in range(steps):
        for name, values in amounts.items():
            changes = {key: [0.0] * len(value) for key, value in values.items()}
            for edge in world.get('edges', []):
                a, b = edge['source'], edge['target']
                for i in range(len(values[a])):
                    transfer = _finite(dt * (edge.get('conductance', 0) *
                        (values[a][i] / measures[a] - values[b][i] / measures[b]) +
                        edge.get('transport_rate', 0) * values[a][i]), 'edge flux')
                    changes[a][i] = _finite(changes[a][i] - transfer, 'net flux')
                    changes[b][i] = _finite(changes[b][i] + transfer, 'net flux')
            for key in values:
                values[key] = [_finite(v + c, 'updated component') for v, c in zip(values[key], changes[key])]
    state = deepcopy(world)
    for name, values in amounts.items():
        field = state['fields'][name]
        field['values'] = {key: _pack(field, [_finite(v / (measures[key] if field['kind'] == 'intensive' else 1), 'reported component')
                                            for v in value]) for key, value in values.items()}
    final = _statistics(state)
    conservation = {}
    for name, field in state['fields'].items():
        before, norm = initial[name]; after, after_norm = final[name]
        tolerance = [1e-10 * max(1, v) for v in norm]
        if any(abs(b - a) > t or n > old + t for a, b, old, n, t in zip(before, after, norm, after_norm, tolerance)):
            raise ValueError('Conservation or signed component stability check failed')
        conservation[name] = {'initial_integral': _pack(field, before), 'final_integral': _pack(field, after),
                              'difference': _pack(field, [b - a for a, b in zip(before, after)]),
                              'absolute_tolerance': _pack(field, tolerance)}
    return {'state': state, 'execution': {'substeps': steps, 'step_seconds': dt, 'work': work,
                                         'maximum_outgoing_fraction': dt * peak},
            'conservation': conservation, 'coordinate_system': deepcopy(coordinate_system),
            'component_frames': deepcopy(component_frames), 'epistemic_status': 'synthetic_scenario',
            'calibrated': False, 'assumptions': [
                'Explicit closed graph; no external boundary flux.',
                'Conductance has support-measure/second units; directed transport is source fraction/second.',
                'Vectors are independent passive components in a fixed global frame; no rotation or velocity-derived flow.',
                'Coordinates do not determine coefficients; no general geometric or curved-surface PDE is claimed.']}


def _at(value):
    if not isinstance(value, str) or re.search(r'\.\d{7,}', value):
        raise ValueError('Timeline times require microsecond precision or coarser')
    return instant(value)


def _audit(store, report):
    from .spatial_store import _json
    last = store.db.execute('SELECT hash FROM lifecycle_audit ORDER BY revision DESC LIMIT 1').fetchone()
    report = {**report, 'previous_hash': last['hash'] if last else store._metadata()['source_hash']}
    checksum = digest(report)
    cursor = store.db.execute('INSERT INTO lifecycle_audit(payload,hash) VALUES(?,?)', (_json(report), checksum))
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


def evolve_spatial_timeline(store, request):
    """Persist one bounded timeline atomically, including all dated lifecycle."""
    allowed = {'start', 'end', 'sample_seconds', 'step_seconds', 'boundary', 'edge_units', 'component_frames',
               'cells', 'allow_boundary_cut', 'events', 'max_cells', 'max_frames', 'max_snapshots', 'max_work', 'max_substeps'}
    if not isinstance(request, dict) or set(request) - allowed or len(canonical(request)) > 1024 * 1024:
        raise ValueError('Unknown timeline fields or request exceeds 1 MiB budget')
    start, end = _at(request['start']), _at(request['end'])
    if end < start:
        raise ValueError('Timeline end must not precede start')
    sample = _seconds(request['sample_seconds'], 'sample_seconds')
    step = _seconds(request.get('step_seconds', sample), 'step_seconds')
    limits = {name: _bound(request.get(name, default), name, maximum) for name, default, maximum in (
        ('max_cells', 1000, 1000), ('max_frames', 100, 1000), ('max_snapshots', 100000, 100000),
        ('max_work', 1000000, 10000000), ('max_substeps', 10000, 100000))}
    if type(request.get('allow_boundary_cut', False)) is not bool:
        raise ValueError('allow_boundary_cut must be boolean')
    total_us = round((end - start).total_seconds() * 1000000)
    sample_us = round(sample * 1000000)
    count = (total_us + sample_us - 1) // sample_us + 1
    if count > limits['max_frames']:
        raise ValueError('Timeline frame budget exceeded')
    points = {min(i * sample_us, total_us) for i in range(count)}
    schedule = {}
    batches = request.get('events', [])
    if not isinstance(batches, list) or len(batches) > 100:
        raise ValueError('Timeline event batch budget exceeded')
    operations = 0
    for batch in batches:
        if not isinstance(batch, dict) or set(batch) != {'time', 'events'} or not isinstance(batch['events'], list):
            raise ValueError('Dated batch requires time and events')
        at = _at(batch['time'])
        if not start <= at <= end or not 1 <= len(batch['events']) <= 100:
            raise ValueError('Event time or batch size outside timeline bounds')
        operations += len(batch['events'])
        if operations > 1000:
            raise ValueError('Timeline lifecycle operation budget exceeded')
        offset = round((at - start).total_seconds() * 1000000)
        points.add(offset); schedule.setdefault(offset, []).append(batch['events'])
    if len(points) > limits['max_frames']:
        raise ValueError('Timeline event/sample frame budget exceeded')
    frames_meta = request.get('component_frames')
    with store._transaction(write=True):
        metadata = store._metadata()
        if 'simulation_time' in metadata and _at(metadata['simulation_time']) != start:
            raise ValueError('Timeline start must equal the stored continuation time')
        if 'dynamics_component_frames' in metadata and metadata['dynamics_component_frames'] != frames_meta:
            raise ValueError('Continuation component frames cannot change implicitly')
        selected = store.select(cells=request.get('cells'), limit=limits['max_cells'])
        domain = {cell['id'] for cell in selected['state']['cells']}
        coordinate_system = selected['coordinate_system']
        _prepare(selected['state'], coordinate_system, frames_meta)
        source = deepcopy(selected['source'])
        initial = _statistics(selected['state'])
        external = {name: [0.0] * len(total) for name, (total, _) in initial.items()}
        frames, snapshots, event_audit, segments = [], [], [], []
        output_bytes = work = substeps = 0
        previous = 0

        def choose():
            result = store.select(cells=sorted(domain), limit=limits['max_cells'])
            if result['selection']['boundary_edges'] and not request.get('allow_boundary_cut', False):
                raise ValueError('Selected timeline cuts boundary edges; explicit acceptance required')
            return result

        selected = choose()
        for offset in sorted(points):
            at = (start + timedelta(microseconds=offset)).isoformat()
            if offset > previous:
                if work >= limits['max_work'] or substeps >= limits['max_substeps']:
                    raise ValueError('Cumulative numerical budget exhausted')
                numerical = {'duration_seconds': (offset - previous) / 1000000, 'step_seconds': step,
                             'boundary': request.get('boundary'), 'edge_units': request.get('edge_units'),
                             'max_work': limits['max_work'] - work, 'max_substeps': limits['max_substeps'] - substeps}
                evolved = evolve_fields(selected['state'], numerical, coordinate_system=coordinate_system, component_frames=frames_meta)
                work += evolved['execution']['work']; substeps += evolved['execution']['substeps']
                for key in domain:
                    store._put_values(key, {name: field['values'][key] for name, field in evolved['state']['fields'].items()})
                segment = {'start': (start + timedelta(microseconds=previous)).isoformat(), 'end': at,
                           'execution': evolved['execution'], 'conservation': evolved['conservation']}
                _audit(store, {'events': [{'type': 'field_evolution', **segment}], 'requests': numerical})
                segments.append(segment)
            for events in schedule.get(offset, []):
                domain = _scope(domain, events, limits['max_cells'])
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
            selected = choose()
            _prepare(selected['state'], coordinate_system, frames_meta)
            frame = {'time': at, 'phase': 'after_events' if offset in schedule else 'sample',
                     'state': selected['state'], 'source': selected['source'], 'selection': selected['selection']}
            rows = [{'time': at, 'entity': key, 'variable': name, 'value': deepcopy(value), 'unit': field['unit'],
                     'origin': 'synthetic_scenario', 'evidence': []}
                    for name, field in selected['state']['fields'].items() for key, value in field['values'].items()]
            if len(snapshots) + len(rows) > limits['max_snapshots']:
                raise ValueError('Timeline snapshot budget exceeded')
            output_bytes += len(canonical(frame)) + len(canonical(rows))
            if output_bytes > 16 * 1024 * 1024:
                raise ValueError('Timeline output exceeds 16 MiB budget')
            frames.append(frame); snapshots.extend(rows); previous = offset
        # Validate units/boundary even for a zero-duration timeline.
        evolve_fields(selected['state'], {'duration_seconds': 0, 'step_seconds': step,
                      'boundary': request.get('boundary'), 'edge_units': request.get('edge_units')},
                      coordinate_system=coordinate_system, component_frames=frames_meta)
        final = _statistics(selected['state'])
        conservation = {}
        for name, field in selected['state']['fields'].items():
            before, norm = initial[name]; after, _ = final[name]
            difference = [b - _sum([a, imported]) for a, b, imported in zip(before, after, external[name])]
            if any(abs(d) > 1e-9 * max(1, n, abs(imported)) for d, n, imported in zip(difference, norm, external[name])):
                raise ValueError('Timeline integral conservation failed')
            conservation[name] = {'initial_integral': _pack(field, before), 'external_input': _pack(field, external[name]),
                                  'final_integral': _pack(field, after), 'difference': _pack(field, difference)}
        from .spatial_store import _json
        store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('simulation_time', _json(end.isoformat())))
        store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('dynamics_component_frames', _json(frames_meta)))
        completed = _audit(store, {'events': [{'type': 'timeline_complete', 'start': start.isoformat(), 'end': end.isoformat(),
                                             'state_hash': digest(selected['state'])}], 'requests': request})
        result = {'start': start.isoformat(), 'end': end.isoformat(), 'frames': frames, 'snapshots': snapshots,
                  'state': selected['state'], 'source': source, 'final_source': store.select(cells=sorted(domain))['source'],
                  'coordinate_system': coordinate_system, 'component_frames': deepcopy(frames_meta),
                  'conservation': conservation, 'events': event_audit, 'segments': segments, 'completion': completed,
                  'execution': {'work': work, 'substeps': substeps, 'frames': len(frames), 'lifecycle_operations': operations},
                  'epistemic_status': 'synthetic_scenario', 'calibrated': False,
                  'assumptions': ['Closed selected graph; explicitly accepted cut edges have zero boundary flux.',
                                  'Events occur before their timestamped frame; sampling/event boundaries also split integration steps.',
                                  'Passive fixed-frame components, explicit coefficients; no curved-surface PDE.']}
        if len(canonical(result)) > 16 * 1024 * 1024:
            raise ValueError('Timeline output exceeds 16 MiB budget')
        return result
