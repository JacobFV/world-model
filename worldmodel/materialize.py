"""Dependency-driven temporal views and explicitly assumed forecast scenarios."""
from copy import deepcopy
from datetime import timedelta
import math
import os
from pathlib import Path
import random
import re
import shutil
import uuid

from .model import instant, identifier, validate_record
from .provenance import capture_code
from .util import atomic_json, canonical, digest, file_hash, now, read_json, slug
from .view_state import EvidenceState, variable_ref, value_type, collect_evidence

PROJECT = Path(__file__).resolve().parents[1]


def positive(value, name, zero=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f'{name} must be finite and {"nonnegative" if zero else "positive"}')
    return value


def seconds(value, name):
    positive(value, name)
    if value != round(value, 6):
        raise ValueError(f'{name} must have microsecond precision or coarser')
    return value


def _request(request):
    allowed = {'start', 'end', 'step_seconds', 'known_at', 'targets', 'initial_state', 'seed', 'budget',
               'max_points', 'max_calls', 'bindings', 'abstraction', 'groups', 'reducers', 'mode',
               'reconciliation', 'dataset', 'fidelity', 'lifecycle', 'interventions'}
    unknown = set(request) - allowed
    if unknown:
        raise ValueError(f'Unsupported view dimensions/options: {sorted(unknown)}')
    request = deepcopy(request)
    canonical(request)
    for name in ('start', 'end', 'known_at'):
        if re.search(r'\.\d{7,}', request.get(name, '')):
            raise ValueError('Timestamps must have microsecond precision or coarser')
    start, end = instant(request['start']), instant(request['end'])
    if end < start:
        raise ValueError('end must be at or after start')
    step = seconds(request['step_seconds'], 'step_seconds')
    duration = (end-start).total_seconds()
    request.setdefault('known_at', request['start'])
    instant(request['known_at'])
    request.setdefault('mode', 'forecast' if request.get('bindings') else 'observed')
    if request['mode'] not in ('observed', 'forecast'):
        raise ValueError('mode must be observed or forecast')
    if request['mode'] == 'observed' and (request.get('bindings') or request.get('initial_state') or request.get('interventions')):
        raise ValueError('Observed views cannot contain process bindings or scenario overrides')
    request.setdefault('budget', 10000)
    positive(request['budget'], 'budget', zero=True)
    for key, default in [('max_points', 10000), ('max_calls', 10000)]:
        request.setdefault(key, default)
        if type(request[key]) is not int or not 1 <= request[key] <= 100000:
            raise ValueError(f'{key} must be an integer in 1..100000')
    request.setdefault('seed', 0)
    if type(request['seed']) is not int:
        raise ValueError('seed must be an integer')
    targets = [variable_ref(target) for target in request['targets']]
    if not targets or len(set(targets)) != len(targets):
        raise ValueError('Targets must be nonempty and unique')
    count = (round(duration*1000000) + round(step*1000000)-1)//round(step*1000000) + 1
    if count * len(targets) > request['max_points']:
        raise ValueError('Requested snapshots exceed max_points budget')
    times = [min(round(i*step, 6), duration) for i in range(count)]
    return request, start, end, times, targets


def _plan(registry, request, targets, duration, backend):
    descriptions = registry.describe()
    specs = {spec['id']: spec for spec in descriptions['processes']}
    bindings, producers = {}, {}
    for binding in request.get('bindings', []):
        name = binding['id']
        if not isinstance(name, str) or not name or name in bindings:
            raise ValueError('Process binding IDs must be nonempty and unique')
        if binding['process_id'] not in specs:
            raise ValueError(f'Unknown process: {binding["process_id"]}')
        spec = specs[binding['process_id']]
        if set(binding['outputs']) != set(spec['outputs']):
            raise ValueError(f'Binding {name} must bind every declared output port')
        if set(binding['inputs']) - set(spec['inputs']):
            raise ValueError(f'Binding {name} has undeclared input ports')
        for port, port_spec in spec['inputs'].items():
            if port_spec.get('required', True) and port not in binding['inputs']:
                raise ValueError(f'Binding {name} missing required input {port}')
        outputs = {port: variable_ref(value) for port, value in binding['outputs'].items()}
        if len(set(outputs.values())) != len(outputs):
            raise ValueError('One binding cannot map multiple output ports onto the same variable')
        for key in outputs.values():
            producers.setdefault(key, []).append(name)
        inputs = {}
        for port, item in binding['inputs'].items():
            if 'entity' in item:
                inputs[port] = variable_ref(item)
            elif 'value' in item and set(item) <= {'value', 'unit'}:
                value_type(item['value'])
                inputs[port] = deepcopy(item)
            else:
                raise ValueError('Input binding requires a variable reference or typed literal')
        bindings[name] = {**binding, 'inputs_resolved': inputs, 'outputs_resolved': outputs}
    keys, active = set(targets), set()
    pending = list(targets)
    while pending:
        key = pending.pop()
        for name in producers.get(key, []):
            if name in active:
                continue
            active.add(name)
            binding = bindings[name]
            dependencies = list(binding['outputs_resolved'].values())
            dependencies += [v for v in binding['inputs_resolved'].values() if isinstance(v, tuple)]
            for dependency in dependencies:
                if dependency not in keys:
                    keys.add(dependency)
                    pending.append(dependency)
    selected, total_cost, total_calls, agents = [], 0, 0, set()
    for name in sorted(active):
        binding = bindings[name]
        fidelity = binding.get('fidelity', request.get('fidelity', 'deterministic'))
        # Output interval may be coarse. Choose fidelity, then schedule at its own safe step.
        choices = []
        for candidate in descriptions['implementations']:
            if binding.get('implementation_id') and candidate['id'] != binding['implementation_id']:
                continue
            if candidate['process_id'] != binding['process_id'] or candidate['fidelity'] != fidelity:
                continue
            if (candidate.get('requires_backend') or fidelity == 'agent') and backend is None:
                continue
            maximum = min(candidate['max_step_seconds'], candidate.get('max_regime', math.inf))
            minimum = candidate.get('min_step_seconds', candidate.get('min_regime', 0))
            cadence = seconds(binding.get('cadence_seconds', maximum), 'cadence_seconds')
            tail = (round(duration*1000000) % round(cadence*1000000))/1000000
            if cadence > maximum or cadence < minimum or (tail > 1e-9 and tail < minimum):
                continue
            calls = (round(duration*1000000)+round(cadence*1000000)-1)//round(cadence*1000000) if duration else 0
            choices.append((calls*candidate['cost_per_call'], candidate['id'], candidate, cadence))
        if not choices:
            raise ValueError(f'No compatible implementation for {name}: fidelity={fidelity}, cadence/regime or backend unavailable')
        _, _, implementation, cadence = min(choices, key=lambda item: item[:2])
        calls = (round(duration*1000000)+round(cadence*1000000)-1)//round(cadence*1000000) if duration else 0
        total_calls += calls
        total_cost += calls * implementation['cost_per_call']
        entity = binding.get('entity_id', next(iter(binding['outputs_resolved'].values()))[0])
        identifier(entity)
        if fidelity == 'agent':
            if entity in agents:
                raise ValueError('Bind at most one agent kernel per entity to avoid conflicting memories')
            agents.add(entity)
        selected.append({**binding, 'implementation': implementation, 'cadence_seconds': cadence,
                         'entity_id': entity})
    if total_cost > request['budget'] or total_calls > request['max_calls']:
        raise ValueError(f'Materialization budget exceeded: {total_calls} calls, cost {total_cost}')
    if agents and (not isinstance(getattr(backend, 'identity', None), dict) or not backend.identity):
        raise ValueError('Agent backend requires an explicit identity dictionary for provenance')
    return selected, sorted(keys), {'bindings': [
        {k:v for k,v in binding.items() if not k.endswith('_resolved')} for binding in selected],
        'variables': [{'entity': e, 'variable': v} for e,v in sorted(keys)],
        'estimated_calls': total_calls, 'estimated_cost': total_cost,
        'dependency_policy': 'all producers of reachable variables; simultaneous feedback'}, specs


def _compatible(state, bindings, specs):
    for binding in bindings:
        spec = specs[binding['process_id']]
        for port, key in binding['outputs_resolved'].items():
            item, expected = state[key], spec['outputs'][port]
            if item['unit'] != expected.get('unit'):
                raise ValueError(f'Output unit mismatch for {key}')
            kind = item['type']
            if kind != expected['type'] and not (kind == 'vector' and expected['type'] == 'array'):
                raise ValueError(f'Output type mismatch for {key}')


def _interventions(request, start, end, bindings, specs):
    """Preflight literal changes without invoking any process handlers."""
    from .processes import _typed
    items = request.get('interventions', [])
    if not isinstance(items, list) or len(items) > 100000:
        raise ValueError('interventions must be a list of at most 100000 changes')
    selected = {binding['id']: binding for binding in bindings}
    scheduled, seen = {}, set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'time', 'binding', 'port', 'value', 'unit'}:
            raise ValueError('Intervention requires exactly time, binding, port, value and unit')
        if not isinstance(item['time'], str) or re.search(r'\.\d{7,}', item['time']):
            raise ValueError('Intervention timestamps require microsecond precision or coarser')
        at = instant(item['time'])
        if not start <= at < end:
            raise ValueError('Intervention time must be at or after start and before end')
        if not isinstance(item['binding'], str) or item['binding'] not in selected:
            raise ValueError('Intervention binding must be an active process binding')
        binding = selected[item['binding']]
        port = item['port']
        if not isinstance(port, str) or not isinstance(binding['inputs_resolved'].get(port), dict):
            raise ValueError('Intervention port must be an existing literal input')
        implementation = binding['implementation']
        if (implementation.get('work_estimator') == 'worldmodel.economy_processes:estimate_process_work'
                and port == implementation.get('work_input')):
            raise ValueError('Economy replay input changes rewrite history; use a separate scenario instead')
        expected = specs[binding['process_id']]['inputs'][port]
        if item['unit'] != expected.get('unit'):
            raise ValueError('Intervention input unit mismatch')
        _typed(item['value'], expected['type'])
        offset_us = round((at-start).total_seconds()*1000000)
        if offset_us % round(binding['cadence_seconds']*1000000):
            raise ValueError('Intervention time must align with binding cadence relative to start')
        identity = (offset_us, item['binding'], port)
        if identity in seen:
            raise ValueError('Duplicate intervention time/binding/port')
        seen.add(identity)
        scheduled.setdefault(offset_us / 1000000, []).append({**deepcopy(item), 'time': at.isoformat()})
    return scheduled


def _snapshots(state, targets, at, request):
    abstraction = request.get('abstraction', 'entity')
    if abstraction == 'entity':
        return [{'time': at.isoformat(), 'entity': entity, 'variable': variable,
                 **deepcopy(state[(entity, variable)])} for entity, variable in targets]
    if abstraction != 'group':
        raise ValueError('Supported abstraction levels are entity and group')
    groups = request.get('groups', [])
    reducers = request.get('reducers', {})
    if not groups:
        raise ValueError('Group abstraction requires groups and explicit reducers')
    results, group_ids = [], set()
    for group in groups:
        identifier(group['id'])
        if group['id'] in group_ids:
            raise ValueError('Group IDs must be unique')
        group_ids.add(group['id'])
        members = group['members']
        if not members or len(members) != len(set(members)):
            raise ValueError('Group members must be nonempty and unique')
        for variable in sorted({v for _,v in targets}):
            reducer = reducers.get(variable)
            if reducer not in ('sum', 'mean', 'min', 'max'):
                raise ValueError(f'Explicit supported reducer required for {variable}')
            group_state = {}
            for entity in members:
                if (entity, variable) not in targets:
                    raise ValueError(f'Group member lacks requested target: {(entity, variable)}')
                group_state[(entity, variable)] = state[(entity, variable)]
            units = {item['unit'] for item in group_state.values()}
            if len(units) != 1 or any(item['type'] != 'number' for item in group_state.values()):
                raise ValueError('Group reducers require compatible numeric units')
            values = [item['value'] for item in group_state.values()]
            value = {'sum': sum, 'mean': lambda x: sum(x)/len(x), 'min': min, 'max': max}[reducer](values)
            origins = sorted({item['origin'] for item in group_state.values()})
            results.append({'time': at.isoformat(), 'entity': group['id'], 'variable': variable,
                            'value': value, 'unit': next(iter(units)), 'type': 'number', 'members': members,
                            'coverage': 1.0, 'reducer': reducer, 'constituent_origins': origins,
                            'origin': 'observed' if origins == ['observed'] else 'scenario_aggregate',
                            'evidence': collect_evidence(group_state)})
    return results


def materialize(store, graph_ref, request, registry=None, agent_backend=None):
    from .processes import combine_pressures
    from .process_library import default_registry
    registry = registry or default_registry()
    request, start, end, sample_times, targets = _request(request)
    duration = (end-start).total_seconds()
    bindings, keys, plan, specs = _plan(registry, request, targets, duration, agent_backend)
    interventions = _interventions(request, start, end, bindings, specs)
    if request.get('lifecycle'):
        from .lifecycle import materialize_lifecycle, require_actor_eligible
        for at in (request['start'], request['end']):
            lifecycle = materialize_lifecycle(request['lifecycle'], at, request['known_at'])
            for entity in {key[0] for key in keys} & set(lifecycle['entities']):
                require_actor_eligible(lifecycle, entity)
        plan['lifecycle_policy'] = 'Known actor lifecycle must remain active through the requested interval; split views at end events.'
    evidence = EvidenceState(store, graph_ref, keys)
    cutoff = instant(request['known_at'])
    state, reconciliation = evidence.select(start, cutoff, request.get('initial_state', []),
                                             request.get('reconciliation', 'error'))
    _compatible(state, bindings, specs)
    plan['work'] = []
    for binding in bindings:
        impl = binding['implementation']
        if impl.get('work_estimator'):
            if impl['work_estimator'] == 'worldmodel.economy_processes:estimate_process_work':
                from .economy_processes import estimate_process_work
            elif impl['work_estimator'] == 'worldmodel.fields:estimate_process_work':
                from .fields import estimate_process_work
            else:
                raise ValueError('Unsupported process work estimator')
            item = binding['inputs_resolved'][impl['work_input']]
            value = state[item]['value'] if isinstance(item, tuple) else item['value']
            calls = (round(duration*1000000)+round(binding['cadence_seconds']*1000000)-1)//round(binding['cadence_seconds']*1000000)
            plan['work'].append({'binding': binding['id'], 'unit': impl['work_unit'], **estimate_process_work(value, calls)})
            checked = set()
            for offset in sorted(interventions):
                for change in interventions[offset]:
                    if change['binding'] != binding['id'] or change['port'] != impl['work_input']:
                        continue
                    fingerprint = digest(change['value'])
                    if fingerprint in checked:
                        continue
                    checked.add(fingerprint)
                    # Conservatively bound each replacement over the full requested prefix.
                    plan['work'].append({'binding': binding['id'], 'unit': impl['work_unit'],
                                         'intervention_time': change['time'],
                                         **estimate_process_work(change['value'], calls)})
    snapshots = _snapshots(state, targets, start, request)  # Validate groups before any process calls.
    if len(snapshots)*len(sample_times) > request['max_points']:
        raise ValueError('Group snapshots exceed max_points budget')
    code = capture_code(PROJECT, 'worldmodel.materialize:materialize')
    traces, memories, held, next_due, rngs = [], {}, {}, {}, {}
    applied_interventions = []
    bindings_by_id = {binding['id']: binding for binding in bindings}
    for binding in bindings:
        name = binding['id']
        next_due[name] = 0.0
        rngs[name] = random.Random(int(digest([request['seed'], name]), 16))
    elapsed, sample_index, calls, cost = 0.0, 1, 0, 0.0
    if request['mode'] == 'observed':
        for offset in sample_times[1:]:
            selected, decisions = evidence.select(start+timedelta(seconds=offset), cutoff,
                                                  policy=request.get('reconciliation', 'error'))
            snapshots += _snapshots(selected, targets, start+timedelta(seconds=offset), request)
            reconciliation += decisions
    else:
        while elapsed < duration:
            for item in interventions.get(elapsed, []):
                binding = bindings_by_id[item['binding']]
                binding['inputs_resolved'][item['port']] = deepcopy({'value': item['value'], 'unit': item['unit']})
                applied_interventions.append(deepcopy(item))
            due = [b for b in bindings if next_due[b['id']] <= elapsed]
            pending_predictions = []
            for binding in due:
                name = binding['id']
                inputs = {port: {'value': deepcopy(state[value]['value']), 'unit': state[value]['unit']}
                          if isinstance(value, tuple) else deepcopy(value)
                          for port, value in binding['inputs_resolved'].items()}
                memory = deepcopy(memories.get(binding['entity_id'], {}))
                context = {'dt_seconds': min(binding['cadence_seconds'], duration-elapsed),
                           'time': (start+timedelta(seconds=elapsed)).isoformat(), 'rng': rngs[name],
                           'state': {port: deepcopy(state[key]['value'])
                                     for port,key in binding['outputs_resolved'].items()},
                           'entity_id': binding['entity_id'], 'memory': memory, 'agent_backend': agent_backend,
                           'budget': request['budget'], 'remaining_budget': request['budget']-cost}
                pending_cost = sum(b['implementation']['cost_per_call'] for b, *_ in pending_predictions)
                if calls + len(pending_predictions) + 1 > request['max_calls'] or cost + pending_cost + binding['implementation']['cost_per_call'] > request['budget']:
                    raise ValueError('Materialization runtime budget exceeded before handler call')
                prediction = registry.predict(binding['implementation']['id'], inputs,
                                              deepcopy(binding.get('parameters', {})), context)
                pending_predictions.append((binding, inputs, memory, prediction))
            # All due handlers above saw the same pre-update state and memory.
            for binding, inputs, memory, prediction in pending_predictions:
                name = binding['id']
                held[name] = prediction['pressures']
                if binding['implementation']['fidelity'] == 'agent':
                    memories[binding['entity_id']] = deepcopy(prediction.get('memory', memory))
                next_due[name] = round(elapsed + binding['cadence_seconds'], 6)
                calls += 1
                cost += binding['implementation']['cost_per_call']
                traces.append({'time': (start+timedelta(seconds=elapsed)).isoformat(),
                               'binding': name, 'implementation': binding['implementation']['id'],
                               'inputs': inputs, 'prediction': prediction, 'memory_before': memory,
                               'memory_after': deepcopy(memories.get(binding['entity_id'], {}))})
            boundary = min([duration, sample_times[sample_index]] + list(next_due.values()))
            dt = boundary - elapsed
            if dt <= 0:
                raise ValueError('Temporal scheduling made no progress')
            influences = {}
            for binding in bindings:
                if binding['implementation'].get('output_timing') == 'end_of_step' and boundary < next_due[binding['id']]:
                    continue
                for pressure in held.get(binding['id'], []):
                    key = binding['outputs_resolved'][pressure['port']]
                    influences.setdefault(key, []).append(pressure)
            new_state = deepcopy(state)
            for item in new_state.values():
                if item['origin'] == 'observed':
                    item['origin'] = 'persistence_assumption'
            provenance = collect_evidence(state)
            for key, pressures in influences.items():
                item = state[key]
                new_state[key]['value'] = combine_pressures(item['value'], pressures, dt, value_type=item['type'])
                new_state[key]['origin'] = 'forecast'
                new_state[key]['evidence'] = provenance
            for binding in bindings:
                for port, key in binding['outputs_resolved'].items():
                    output_spec = specs[binding['process_id']]['outputs'][port]
                    value = new_state[key]['value']
                    if type(value) in (int, float):
                        if value < output_spec.get('minimum', -math.inf) or value > output_spec.get('maximum', math.inf):
                            raise ValueError(f'Forecast violates declared output bounds for {key}')
            state, elapsed = new_state, boundary
            if elapsed == sample_times[sample_index]:
                snapshots += _snapshots(state, targets, start+timedelta(seconds=elapsed), request)
                sample_index += 1
    result = {'schema_version': 1, 'graph': graph_ref, 'request': request, 'plan': plan,
              'snapshots': snapshots, 'trace': traces, 'input_interventions': applied_interventions,
              'reconciliation': reconciliation,
              'execution': {'calls': calls, 'cost': cost, 'agent_memories': memories},
              'backend_identity': deepcopy(getattr(agent_backend, 'identity', None)),
              'limitations': ['Process library predictions are illustrative and uncalibrated.',
                             'Temporal sampling does not add observational precision.',
                             'Forecast inputs without update processes persist as explicit assumptions after the initial time.',
                             'No inferred microstate is created from aggregate observations.']}
    canonical(result)
    store.verify(graph_ref)
    for name, checksum in code['files'].items():
        if file_hash(PROJECT/name) != checksum:
            raise ValueError('Code changed during materialization; rerun with stable code')
    ref = _publish(store, result, code, registry.describe())
    return {**result, 'artifact': ref}


def _publish(store, result, code, registry):
    dataset = slug(result['request'].get('dataset', 'materialized_view'))
    with store.lock(dataset) as base:
        run_id = uuid.uuid4().hex
        staging = base/'processing'/run_id
        staging.mkdir()
        try:
            atomic_json(staging/'view.json', result)
            with (staging/'records.jsonl').open('wb') as stream:
                for index, snapshot in enumerate(result['snapshots']):
                    record = {'kind': 'observation', 'id': 'view:' + digest([result['graph'], result['request'], result['plan'], result['backend_identity'], snapshot, index]),
                              'subject': snapshot['entity'], 'metric': snapshot['variable'],
                              'value': snapshot['value'], 'unit': snapshot['unit'] or 'dimensionless',
                              'dimensions': {'entity': snapshot['entity'], 'at': snapshot['time']},
                              'observed_at': result['request']['known_at'], 'valid_from': snapshot['time'],
                              'valid_to': (instant(snapshot['time'])+timedelta(microseconds=1)).isoformat(),
                              'evidence': snapshot['evidence'], 'epistemic_status': snapshot['origin'],
                              'methodology': 'Explicit requested materialization; inspect view.json for assumptions and process trace'}
                    if snapshot['unit'] is None:
                        record['kind'] = 'assertion'
                        record['predicate'] = record.pop('metric')
                        record['unit'] = None
                    validate_record(record)
                    stream.write(canonical(record)+b'\n')
            identity = {'schema_version': 1, 'dataset': dataset,
                        'definition': {'id': dataset, 'kind': 'derived', 'schema_version': 1,
                                       'entrypoint': 'worldmodel.materialize:materialize'},
                        'parameters': result['request'], 'inputs': [result['graph']], 'raw_inputs': [],
                        'code': code, 'registry': registry, 'backend_identity': result['backend_identity'],
                        'outputs': {name: {'sha256': file_hash(staging/name), 'bytes': (staging/name).stat().st_size}
                                    for name in ('view.json', 'records.jsonl')}}
            version = digest(identity)
            ref = {'dataset': dataset, 'version': version}
            atomic_json(staging/'manifest.json', {**identity, 'version': version})
            destination = store.version_dir(ref)
            if destination.exists():
                store.verify(ref)
            else:
                os.rename(staging, destination)
            atomic_json(base/'latest.json', ref)
            atomic_json(base/'runs'/f'{run_id}.json', {'run_id': run_id, 'status': 'succeeded',
                        'output': ref, 'completed_at': now(), 'kind': 'materialization'})
            return ref
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def load_view(store, ref):
    store.verify(ref)
    return read_json(store.version_dir(ref)/'view.json')
