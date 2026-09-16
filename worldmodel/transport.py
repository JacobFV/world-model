"""Bounded multimodal itinerary planning; plans never establish actual travel.

Scale: ``compile_network(network)`` validates a network once (edge ids, endpoints,
durations, parsed departures/windows/validity, and an index of transfer rules by
node and mode pair) into per-source adjacency lists. ``route`` accepts either the
compiled network or the original dict (compiled on the fly), with identical
search order and results. Size ceilings are the named limits transport_max_nodes,
transport_max_edges, transport_max_search_labels, transport_max_transfers,
transport_max_windows and transport_max_departures (worldmodel.limits).
"""
from __future__ import annotations

import bisect
import copy
import heapq
import math
from datetime import datetime, timezone, timedelta

from .limits import LimitExceeded, resolve_limits

MODES = {'road', 'walk', 'air', 'sea', 'rail', 'transfer'}


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{name} must be a finite {"positive" if positive else "nonnegative"} number')
    return value


def _time(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('timezone required')
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('Times require ISO-8601 timestamps with a timezone') from exc


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def network_from_osm(elements, speed_kph=30):
    """Convert actual OSM way node sequences; skip absent topology, never infer it.

    Speed is an explicit constant assumption, not a traffic estimate. Vehicle
    one-way and roundabout direction is preserved; pedestrian ways permit both
    directions unless oneway:foot is supplied. Access-restricted ways are omitted.
    """
    _number(speed_kph, 'speed_kph', True)
    nodes = {}
    ways = []
    for item in elements:
        if item.get('type') == 'node' and 'lat' in item and 'lon' in item:
            lat, lon = float(item['lat']), float(item['lon'])
            if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180:
                raise ValueError('Invalid OSM coordinates')
            nodes[item['id']] = {'id': f"osm:node:{item['id']}", 'lat': lat, 'lon': lon}
        elif item.get('type') == 'way':
            ways.append(item)
    # Overpass out geom embeds the coordinates in the exact way-node order.
    for way in ways:
        ids, geometry = way.get('nodes', []), way.get('geometry', [])
        if geometry and len(ids) == len(geometry):
            for node_id, point in zip(ids, geometry):
                if not point or 'lat' not in point or 'lon' not in point:
                    continue
                lat, lon = float(point['lat']), float(point['lon'])
                if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180:
                    raise ValueError('Invalid embedded OSM coordinates')
                node = {'id': f'osm:node:{node_id}', 'lat': lat, 'lon': lon}
                if node_id in nodes and nodes[node_id] != node:
                    raise ValueError('Conflicting coordinates for the same OSM node')
                nodes[node_id] = node
    edges = []
    coverage = {'ways_seen': len(ways), 'ways_without_node_sequence': 0, 'segments_missing_nodes': 0, 'restricted_ways_skipped': 0}
    for way in ways:
        tags = way.get('tags', {})
        if not tags.get('highway'):
            continue
        if tags.get('access') in ('no', 'private'):
            coverage['restricted_ways_skipped'] += 1
            continue
        ids = way.get('nodes', [])
        if len(ids) < 2:
            coverage['ways_without_node_sequence'] += 1
            continue
        mode = 'walk' if tags['highway'] in ('footway', 'pedestrian', 'steps', 'path') else 'road'
        oneway = str(tags.get('oneway:foot', 'no') if mode == 'walk' else tags.get('oneway', 'yes' if tags.get('junction') == 'roundabout' or tags['highway'] == 'motorway' else 'no')).lower()
        if oneway not in ('yes', 'true', '1', '-1', 'no', 'false', '0'):
            # Conditional/reversible directions cannot safely become static roads.
            coverage['restricted_ways_skipped'] += 1
            continue
        for index, (a, b) in enumerate(zip(ids, ids[1:])):
            if a not in nodes or b not in nodes:
                coverage['segments_missing_nodes'] += 1
                continue
            na, nb = nodes[a], nodes[b]
            lat1, lat2 = math.radians(na['lat']), math.radians(nb['lat'])
            dlat, dlon = lat2 - lat1, math.radians(nb['lon'] - na['lon'])
            hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            distance = 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, hav))))
            pairs = [(a, b)] if oneway in ('yes', 'true', '1') else [(b, a)] if oneway == '-1' else [(a, b), (b, a)]
            for source, target in pairs:
                edges.append({'id': f"osm:way:{way['id']}:{index}:{source}:{target}",
                              'source': nodes[source]['id'], 'target': nodes[target]['id'], 'mode': mode,
                              'duration_seconds': distance / (5 if mode == 'walk' else speed_kph) * 3600,
                              'distance_km': distance, 'cost': 0,
                              'evidence': [{'source': 'openstreetmap', 'type': 'way', 'id': way['id'], 'version': way.get('version')}],
                              'assumptions': {'speed_kph': 5 if mode == 'walk' else speed_kph, 'cost_unspecified_assumed_zero': True}})
    return {'nodes': list(nodes.values()), 'edges': edges, 'coverage': coverage,
            'assumptions': ['Constant assumed speed; no congestion, turn restrictions, conditional access or transfer inference.',
                            'OSM IDs identify source features; retain the raw artifact separately for immutable provenance.']}


class _Edge:
    """Validated scalar view of one source edge; the source dict supplies evidence."""
    __slots__ = ('edge', 'id', 'source', 'target', 'mode', 'cost', 'nominal', 'upper', 'interval',
                 'departures', 'closures', 'capacity_windows', 'valid_from', 'valid_to', 'capacity', 'closed')


class CompiledNetwork:
    """Validated network reusable across route requests; do not mutate the source afterwards.

    Scalars are captured at compile time; evidence and vehicle IDs are deep-copied
    from the source edges only when a route result is produced.
    """
    def __init__(self, nodes, adjacency, edge_ids, transfers, geographic_bounds, edge_count):
        self.nodes = nodes
        self.adjacency = adjacency
        self.edge_ids = edge_ids
        self.transfers = transfers
        self.geographic_bounds = geographic_bounds
        self.node_count = len(nodes)
        self.edge_count = edge_count


def compile_network(network, *, limits=None):
    """Validate a network dict once into per-source adjacency lists and a transfer index."""
    limits = resolve_limits(limits)
    if len(network['nodes']) > limits.transport_max_nodes:
        raise LimitExceeded('transport_max_nodes', len(network['nodes']), limits.transport_max_nodes, 'Network size budget exceeded: nodes')
    if len(network['edges']) > limits.transport_max_edges:
        raise LimitExceeded('transport_max_edges', len(network['edges']), limits.transport_max_edges, 'Network size budget exceeded: edges')
    node_ids = [n['id'] for n in network['nodes']]
    if any(not isinstance(x, str) or not x for x in node_ids) or len(set(node_ids)) != len(node_ids):
        raise ValueError('Network node IDs must be unique nonempty strings')
    nodes = set(node_ids)
    if 'geographic_bounds' in network:
        bounds = network['geographic_bounds']
        if not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds): raise ValueError('Invalid geographic bounds')
        west, south, east, north = bounds
        if not -180 <= west < east <= 180 or not -90 <= south < north <= 90: raise ValueError('Invalid geographic bounds')
        for node in network['nodes']:
            if not all(type(node.get(k)) in (int, float) and math.isfinite(node[k]) for k in ('lat', 'lon')) or not (south <= node['lat'] <= north and west <= node['lon'] <= east): raise ValueError('Node outside explicit geographic bounds or missing coordinates')
    transfers = network.get('transfers')
    index = None
    if transfers is not None:
        if not isinstance(transfers, list): raise ValueError('Transfer budget exceeded')
        limits.check('transport_max_transfers', len(transfers), 'Transfer budget exceeded')
        index = {}
        for rule in transfers:
            if set(rule) != {'node', 'from_mode', 'to_mode', 'minimum_seconds', 'valid_from', 'valid_to'} or rule['node'] not in nodes or rule['from_mode'] not in MODES or rule['to_mode'] not in MODES: raise ValueError('Invalid dated transfer')
            _number(rule['minimum_seconds'], 'minimum_seconds')
            valid_from, valid_to = _time(rule['valid_from']), _time(rule['valid_to'])
            if valid_from >= valid_to: raise ValueError('Invalid transfer interval')
            index.setdefault((rule['node'], rule['from_mode'], rule['to_mode']), []).append(
                (valid_from, valid_to, timedelta(seconds=rule['minimum_seconds'])))
    adjacency = {}
    edge_ids = set()
    for edge in network['edges']:
        if not isinstance(edge['id'], str) or not edge['id'] or edge['id'] in edge_ids:
            raise ValueError('Edge IDs must be unique nonempty strings')
        edge_ids.add(edge['id'])
        if edge['source'] not in nodes or edge['target'] not in nodes or edge['mode'] not in MODES:
            raise ValueError('Invalid edge endpoint or mode')
        _number(edge['duration_seconds'], 'duration_seconds')
        _number(edge['cost'], 'cost')
        interval = copy.deepcopy(edge['duration_range_seconds']) if 'duration_range_seconds' in edge else [edge['duration_seconds'], edge['duration_seconds']]
        if not isinstance(interval, list) or len(interval) != 2: raise ValueError('Duration range requires two bounds')
        for x in interval: _number(x, 'duration range')
        if not interval[0] <= edge['duration_seconds'] <= interval[1]: raise ValueError('Nominal duration must lie within uncertainty bounds')
        record = _Edge()
        record.edge, record.id, record.source, record.target, record.mode = edge, edge['id'], edge['source'], edge['target'], edge['mode']
        record.cost, record.nominal, record.upper, record.interval = edge['cost'], edge['duration_seconds'], interval[1], interval
        if ('valid_from' in edge) != ('valid_to' in edge): raise ValueError('Edge validity requires both bounds')
        record.valid_from = record.valid_to = None
        if 'valid_from' in edge:
            record.valid_from, record.valid_to = _time(edge['valid_from']), _time(edge['valid_to'])
            if record.valid_from >= record.valid_to: raise ValueError('Invalid edge interval')
        parsed = {}
        for kind in ('closures', 'capacity_windows'):
            windows = edge.get(kind, [])
            if not isinstance(windows, list): raise ValueError('Window budget exceeded')
            limits.check('transport_max_windows', len(windows), 'Window budget exceeded')
            parsed[kind] = []
            for window in windows:
                if set(window) != ({'valid_from', 'valid_to', 'capacity'} if kind == 'capacity_windows' else {'valid_from', 'valid_to'}): raise ValueError('Invalid edge window')
                start, finish = _time(window['valid_from']), _time(window['valid_to'])
                if start >= finish: raise ValueError('Invalid edge window interval')
                if kind == 'capacity_windows':
                    _number(window['capacity'], 'capacity')
                    parsed[kind].append((start, finish, window['capacity']))
                else:
                    parsed[kind].append((start, finish))
        record.closures, record.capacity_windows = parsed['closures'], parsed['capacity_windows']
        record.capacity = None
        if 'capacity' in edge:
            record.capacity = _number(edge['capacity'], 'capacity')
        record.departures = None
        if 'departures' in edge:
            limits.check('transport_max_departures', len(edge['departures']), 'Departure budget exceeded')
            record.departures = sorted(_time(x) for x in edge['departures'])
        record.closed = bool(edge.get('closed', False))
        adjacency.setdefault(edge['source'], []).append(record)
    return CompiledNetwork(nodes, adjacency, edge_ids, index, network.get('geographic_bounds'), len(network['edges']))


def route(network, request, *, limits=None):
    """Earliest-arrival / least-cost Pareto label search with a hard label budget.

    Free waiting is allowed. Edges have constant nonnegative duration and cost;
    departures, when present, enumerate finite scheduled service opportunities.
    Capacity is a per-edge scenario check, not a reservation or congestion model.
    max_expansions bounds popped labels; max_labels bounds generated labels.
    ``network`` may be a dict or a CompiledNetwork from compile_network.
    """
    limits = resolve_limits(limits)
    allowed = {'origin', 'destination', 'departure_time', 'objective', 'permitted_modes', 'closed_edges', 'demand', 'max_expansions', 'max_labels', 'duration_policy'}
    if set(request) - allowed:
        raise ValueError(f'Unknown route request fields: {sorted(set(request) - allowed)}')
    compiled = network if isinstance(network, CompiledNetwork) else compile_network(network, limits=limits)
    duration_policy = request.get('duration_policy', 'upper_bound')
    if duration_policy not in ('nominal', 'upper_bound'): raise ValueError('Unknown duration policy')
    upper = duration_policy == 'upper_bound'
    start = _time(request['departure_time'])
    objective = request.get('objective', 'earliest_arrival')
    if objective not in ('earliest_arrival', 'least_cost'):
        raise ValueError('objective must be earliest_arrival or least_cost')
    modes = set(request.get('permitted_modes', MODES))
    if modes - MODES:
        raise ValueError('Unknown transport mode')
    closed = set(request.get('closed_edges', []))
    demand = _number(request.get('demand', 1), 'demand', True)
    max_expansions = request.get('max_expansions', 10000)
    max_labels = request.get('max_labels', 100000)
    ceiling = limits.transport_max_search_labels
    for name, value in [('max_expansions', max_expansions), ('max_labels', max_labels)]:
        if type(value) is not int or value < 1:
            raise ValueError(f'{name} must be an integer between 1 and {ceiling}')
        if value > ceiling:
            raise LimitExceeded('transport_max_search_labels', value, ceiling, name)
    origin, destination = request['origin'], request['destination']
    if origin not in compiled.nodes or destination not in compiled.nodes:
        raise ValueError('Origin and destination must exist in network')
    if closed - compiled.edge_ids:
        raise ValueError('Closed edge does not exist')
    transfers = compiled.transfers
    adjacency = compiled.adjacency
    # Parallel label arrays: node, arrival, cost, parent, edge record, departure, active.
    label_node, label_at, label_cost = [origin], [start], [0]
    label_parent, label_edge, label_departure, label_active = [None], [None], [None], [True]
    # Each (node, mode) frontier is a Pareto staircase: arrival strictly increasing,
    # cost strictly decreasing (mutually nondominated). Dominance checks and removals
    # are bisections; outcomes equal an unordered scan because order is irrelevant.
    frontiers = {(origin, None): ([start], [0], [0])}
    queue = [(0, 0, 0)]
    expansions = 0
    base = {'request': copy.deepcopy(request), 'epistemic_status': 'planned_scenario',
            'geographic_bounds': compiled.geographic_bounds, 'duration_policy': duration_policy,
            'assumptions': ['Chosen nominal or upper-bound duration; free waiting; uncertainty is a bound, not a probability.', 'Transfers require dated rules when network.transfers is supplied; otherwise interchange time is assumed zero.', 'Capacity checks do not reserve capacity.', 'A planned route does not establish a person traveled.']}

    def result(status, final=None):
        value = dict(base, status=status, optimal=status == 'ok', search={'expansions': expansions, 'labels_generated': len(label_node), 'max_expansions': max_expansions, 'max_labels': max_labels})
        if final is not None:
            legs = []; cursor = final
            while label_parent[cursor] is not None:
                record, parent = label_edge[cursor], label_parent[cursor]
                departure, arrival = label_departure[cursor], label_at[cursor]
                leg = {'edge_id': record.id, 'source': record.source, 'target': record.target, 'mode': record.mode,
                       'departure_time': _iso(departure), 'arrival_time': _iso(arrival), 'wait_seconds': (departure - label_at[parent]).total_seconds(),
                       'duration_seconds': record.upper if upper else record.nominal, 'duration_range_seconds': copy.deepcopy(record.interval),
                       'cost': record.cost, 'evidence': copy.deepcopy(record.edge.get('evidence', []))}
                if 'vehicle_id' in record.edge:
                    leg['vehicle_id'] = copy.deepcopy(record.edge['vehicle_id'])
                legs.append(leg); cursor = parent
            legs.reverse()
            value.update(legs=legs, edge_ids=[x['edge_id'] for x in legs], arrival_time=_iso(label_at[final]),
                         departure_time=_iso(start), duration_seconds=(label_at[final] - start).total_seconds(), total_cost=label_cost[final])
        return value

    while queue:
        if expansions >= max_expansions:
            return result('budget_exhausted')
        _, _, index = heapq.heappop(queue)
        if not label_active[index]:
            continue
        expansions += 1
        node = label_node[index]
        if node == destination:
            return result('ok', index)
        current_at, current_cost = label_at[index], label_cost[index]
        previous = label_edge[index].mode if label_edge[index] is not None else None
        for record in adjacency.get(node, ()):
            if record.mode not in modes or record.id in closed or record.closed or (record.capacity if record.capacity is not None else demand) < demand:
                continue
            departure = current_at
            mode = record.mode
            if transfers is not None and previous is not None:
                rules = transfers.get((node, previous, mode))
                if previous != mode or rules:
                    ready = []
                    for valid_from, valid_to, minimum in rules or ():
                        candidate = max(departure, valid_from) + minimum
                        if candidate <= valid_to: ready.append(candidate)
                    if not ready: continue
                    departure = min(ready)
            if record.valid_from is not None: departure = max(departure, record.valid_from)
            blocked = record.closures + [(a, b) for a, b, capacity in record.capacity_windows if capacity < demand] if record.capacity_windows else record.closures
            duration = timedelta(seconds=record.upper if upper else record.nominal)
            feasible = False
            for attempt in range(len(blocked) + 2):
                if record.departures is not None:
                    i = bisect.bisect_left(record.departures, departure)
                    if i == len(record.departures): break
                    departure = record.departures[i]
                arrival = departure + duration
                if record.valid_to is not None and (departure >= record.valid_to or arrival > record.valid_to): break
                overlaps = [b for a, b in blocked if departure < b and (arrival > a or departure == arrival and departure >= a)]
                if overlaps: departure = max(overlaps); continue
                feasible = True; break
            if not feasible: continue
            cost = current_cost + record.cost
            key = (record.target, mode)
            frontier = frontiers.get(key)
            if frontier is not None:
                times, costs, members = frontier
                position = bisect.bisect_right(times, arrival)
                if position and costs[position - 1] <= cost:
                    continue
            if len(label_node) >= max_labels:
                return result('budget_exhausted')
            new = len(label_node)
            label_node.append(record.target); label_at.append(arrival); label_cost.append(cost)
            label_parent.append(index); label_edge.append(record); label_departure.append(departure); label_active.append(True)
            if frontier is None:
                frontiers[key] = ([arrival], [cost], [new])
            else:
                first = bisect.bisect_left(times, arrival); last = first
                while last < len(times) and costs[last] >= cost:
                    label_active[members[last]] = False; last += 1
                times[first:last] = [arrival]; costs[first:last] = [cost]; members[first:last] = [new]
            elapsed = (arrival - start).total_seconds()
            primary, secondary = (elapsed, cost) if objective == 'earliest_arrival' else (cost, elapsed)
            heapq.heappush(queue, (primary, secondary, new))
    return result('unreachable')


def traveler_events(route_result, person_id, evidence=None):
    """Produce planned itinerary events, even when an itinerary has evidence.

    Road/vessel/aircraft source evidence is never evidence of a person's presence.
    Actual boarding requires separately ingested observations, not this function.
    """
    if not isinstance(person_id, str) or not person_id:
        raise ValueError('person_id must be a nonempty string')
    if evidence is not None and (not isinstance(evidence, list) or any(not isinstance(x, dict) for x in evidence)):
        raise ValueError('evidence must be a list of explicit evidence objects')
    if route_result.get('status') != 'ok':
        return []
    events = []
    for index, leg in enumerate(route_result['legs']):
        for kind, at, place in [('departure', leg['departure_time'], leg['source']), ('arrival', leg['arrival_time'], leg['target'])]:
            events.append({'event_type': f'planned_{kind}', 'person_id': person_id, 'time': at, 'place_id': place,
                           'leg_index': index, 'edge_id': leg['edge_id'], 'mode': leg['mode'],
                           'epistemic_status': 'evidence_backed_plan' if evidence else 'scenario',
                           'evidence': copy.deepcopy(evidence or []), 'network_evidence': copy.deepcopy(leg['evidence']),
                           'actual_boarding_established': False})
    return events
