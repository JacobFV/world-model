"""Bounded multimodal itinerary planning; plans never establish actual travel."""
from __future__ import annotations

import bisect
import copy
import heapq
import math
from datetime import datetime, timezone, timedelta

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


def route(network, request):
    """Earliest-arrival / least-cost Pareto label search with a hard label budget.

    Free waiting is allowed. Edges have constant nonnegative duration and cost;
    departures, when present, enumerate finite scheduled service opportunities.
    Capacity is a per-edge scenario check, not a reservation or congestion model.
    max_expansions bounds popped labels; max_labels bounds generated labels.
    """
    allowed = {'origin', 'destination', 'departure_time', 'objective', 'permitted_modes', 'closed_edges', 'demand', 'max_expansions', 'max_labels'}
    if set(request) - allowed:
        raise ValueError(f'Unknown route request fields: {sorted(set(request) - allowed)}')
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
    for name, value in [('max_expansions', max_expansions), ('max_labels', max_labels)]:
        if type(value) is not int or not 1 <= value <= 1000000:
            raise ValueError(f'{name} must be an integer between 1 and 1000000')
    node_ids = [n['id'] for n in network['nodes']]
    if any(not isinstance(x, str) or not x for x in node_ids) or len(set(node_ids)) != len(node_ids):
        raise ValueError('Network node IDs must be unique nonempty strings')
    nodes = set(node_ids)
    origin, destination = request['origin'], request['destination']
    if origin not in nodes or destination not in nodes:
        raise ValueError('Origin and destination must exist in network')
    adjacency = {n: [] for n in nodes}
    edge_ids = set()
    for original in network['edges']:
        edge = copy.deepcopy(original)
        if not isinstance(edge['id'], str) or not edge['id'] or edge['id'] in edge_ids:
            raise ValueError('Edge IDs must be unique nonempty strings')
        edge_ids.add(edge['id'])
        if edge['source'] not in nodes or edge['target'] not in nodes or edge['mode'] not in MODES:
            raise ValueError('Invalid edge endpoint or mode')
        _number(edge['duration_seconds'], 'duration_seconds')
        _number(edge['cost'], 'cost')
        if 'capacity' in edge:
            _number(edge['capacity'], 'capacity')
        if 'departures' in edge:
            edge['_departures'] = sorted(_time(x) for x in edge['departures'])
        if edge['mode'] in modes and edge['id'] not in closed and not edge.get('closed', False) and edge.get('capacity', demand) >= demand:
            adjacency[edge['source']].append(edge)
    if closed - edge_ids:
        raise ValueError('Closed edge does not exist')
    labels = [{'node': origin, 'at': start, 'cost': 0, 'parent': None, 'leg': None, 'active': True}]
    frontiers = {n: [] for n in nodes}; frontiers[origin] = [0]
    queue = [(0, 0, 0)]
    expansions = 0
    base = {'request': copy.deepcopy(request), 'epistemic_status': 'planned_scenario',
            'assumptions': ['Constant edge durations and costs; free waiting.', 'Capacity checks do not reserve capacity.', 'A planned route does not establish a person traveled.']}

    def result(status, final=None):
        value = dict(base, status=status, optimal=status == 'ok', search={'expansions': expansions, 'labels_generated': len(labels), 'max_expansions': max_expansions, 'max_labels': max_labels})
        if final is not None:
            legs = []; cursor = final
            while labels[cursor]['parent'] is not None:
                legs.append(labels[cursor]['leg']); cursor = labels[cursor]['parent']
            legs.reverse()
            value.update(legs=legs, edge_ids=[x['edge_id'] for x in legs], arrival_time=_iso(labels[final]['at']),
                         departure_time=_iso(start), duration_seconds=(labels[final]['at'] - start).total_seconds(), total_cost=labels[final]['cost'])
        return value

    while queue:
        if expansions >= max_expansions:
            return result('budget_exhausted')
        _, _, index = heapq.heappop(queue)
        current = labels[index]
        if not current['active']:
            continue
        expansions += 1
        if current['node'] == destination:
            return result('ok', index)
        for edge in adjacency[current['node']]:
            departure = current['at']
            if '_departures' in edge:
                i = bisect.bisect_left(edge['_departures'], departure)
                if i == len(edge['_departures']):
                    continue
                departure = edge['_departures'][i]
            arrival = departure + timedelta(seconds=edge['duration_seconds'])
            cost = current['cost'] + edge['cost']
            frontier = frontiers[edge['target']]
            if any(labels[j]['at'] <= arrival and labels[j]['cost'] <= cost for j in frontier):
                continue
            if len(labels) >= max_labels:
                return result('budget_exhausted')
            retained = []
            for j in frontier:
                if arrival <= labels[j]['at'] and cost <= labels[j]['cost']:
                    labels[j]['active'] = False
                else:
                    retained.append(j)
            leg = {'edge_id': edge['id'], 'source': edge['source'], 'target': edge['target'], 'mode': edge['mode'],
                   'departure_time': _iso(departure), 'arrival_time': _iso(arrival), 'wait_seconds': (departure - current['at']).total_seconds(),
                   'duration_seconds': edge['duration_seconds'], 'cost': edge['cost'], 'evidence': edge.get('evidence', [])}
            if 'vehicle_id' in edge:
                leg['vehicle_id'] = edge['vehicle_id']
            new = len(labels)
            labels.append({'node': edge['target'], 'at': arrival, 'cost': cost, 'parent': index, 'leg': leg, 'active': True})
            frontiers[edge['target']] = retained + [new]
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
