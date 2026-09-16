"""Region-scale OSM network extraction from a PBF extract (stdlib only).

Produces compact parsed rows for the ``parsed`` stage:

* ``summary``: header metadata and counts (first row);
* ``edge``: simplified way chains between graph nodes (intersections, shared
  nodes and way endpoints) with haversine length, direction and key tags, per
  network (road, rail, power, pipeline, ferry), plus an encoded polyline;
* ``feature``: tagged transport/energy facilities (airports, ports, ferry
  terminals, power plants, substations, refineries, rail stations/yards, fuel
  stations, military sites) from nodes, ways and multipolygon relations, with
  centroid and bounding box;
* ``node``: graph nodes with coordinates and the networks they join.

Work is split into passes over independent PBF blocks executed in forked worker
processes. Per-block results go to a scratch directory; the parent keeps sorted
``array`` indexes of needed node IDs (8 bytes per referenced node) instead of
Python sets or dicts over all nodes. For California (~1.3 GB PBF) peak parent
memory is a few GB, dominated by sorting way node references once.
"""
import json
import math
import multiprocessing
import os
import pickle
from array import array
from bisect import bisect_left, bisect_right
from pathlib import Path

from .helpers import PrimitiveBlock, blob_index, decode_blob, parse_header, read_blob

ROAD_HIGHWAYS = ('motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary',
                 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified', 'residential', 'living_street',
                 'service', 'road', 'busway')
EXCLUDED_SERVICE = ('parking_aisle', 'driveway', 'drive-through', 'drive_through', 'emergency_access')
RAILWAYS = ('rail', 'light_rail', 'subway', 'tram', 'narrow_gauge', 'monorail', 'funicular')
POWER_LINES = ('line', 'minor_line', 'cable')
NETWORK_BITS = {'road': 1, 'rail': 2, 'power': 4, 'pipeline': 8, 'ferry': 16}
EDGE_TAGS = ('highway', 'name', 'ref', 'maxspeed', 'lanes', 'oneway', 'junction', 'access', 'motor_vehicle', 'hgv',
             'bridge', 'tunnel', 'surface', 'service', 'toll', 'railway', 'usage', 'gauge', 'electrified', 'voltage',
             'frequency', 'operator', 'substance', 'location', 'route', 'cables', 'circuits', 'maxweight',
             'maxheight', 'duration')
FEATURE_TAGS = ('name', 'operator', 'iata', 'icao', 'faa', 'ref', 'aerodrome:type', 'aerodrome', 'military',
                'plant:source', 'plant:method', 'plant:output:electricity', 'voltage', 'capacity', 'brand', 'harbour',
                'seamark:harbour:category', 'station', 'network', 'landuse', 'industrial', 'power', 'aeroway',
                'amenity', 'railway', 'wikidata', 'addr:city', 'addr:state')
MISSING = -(2 ** 31)
EARTH_RADIUS_M = 6371008.8
DEFAULTS = {'road_highways': list(ROAD_HIGHWAYS), 'excluded_service': list(EXCLUDED_SERVICE),
            'railways': list(RAILWAYS), 'power_lines': list(POWER_LINES), 'edge_geometry': True}

_STATE = {}


class Classifier:
    def __init__(self, config=None):
        config = {**DEFAULTS, **(config or {})}
        self.roads = frozenset(config['road_highways'])
        self.excluded_service = frozenset(config['excluded_service'])
        self.railways = frozenset(config['railways'])
        self.power = frozenset(config['power_lines'])
        self.geometry = bool(config['edge_geometry'])

    def networks(self, tags):
        found = []
        if tags.get('highway') in self.roads and tags.get('area') != 'yes' and tags.get('service') not in self.excluded_service:
            found.append('road')
        if tags.get('railway') in self.railways:
            found.append('rail')
        if tags.get('power') in self.power:
            found.append('power')
        if tags.get('man_made') == 'pipeline':
            found.append('pipeline')
        if tags.get('route') == 'ferry':
            found.append('ferry')
        return found

    @staticmethod
    def feature(tags):
        get = tags.get
        if get('aeroway') == 'aerodrome':
            return 'airport'
        if get('landuse') == 'port' or get('industrial') == 'port' or get('harbour') == 'yes' or get('seamark:type') == 'harbour':
            return 'port'
        if get('amenity') == 'ferry_terminal':
            return 'ferry_terminal'
        if get('power') == 'plant':
            return 'power_plant'
        if get('power') == 'substation':
            return 'substation'
        if get('industrial') == 'refinery' or (get('man_made') == 'works' and get('product') in ('oil', 'petroleum', 'fuel')):
            return 'refinery'
        if get('railway') == 'station':
            return 'rail_station'
        if get('railway') == 'yard':
            return 'rail_yard'
        if get('amenity') == 'fuel':
            return 'fuel_station'
        if get('landuse') == 'military' or get('military') in ('base', 'airfield', 'naval_base'):
            return 'military_site'
        return None

    def wanted_way(self, tags):
        return bool(tags) and (bool(self.networks(tags)) or self.feature(tags) is not None)

    def wanted_relation(self, tags):
        return tags.get('type') == 'multipolygon' and self.feature(tags) is not None


def subset(tags, names):
    return {name: tags[name] for name in names if name in tags}


def direction(network, tags):
    if network != 'road' and network != 'ferry':
        return 'both'
    oneway = str(tags.get('oneway', '')).lower()
    if oneway in ('yes', 'true', '1'):
        return 'forward'
    if oneway in ('-1', 'reverse'):
        return 'backward'
    if oneway == 'reversible':
        return 'reversible'
    if oneway in ('no', 'false', '0'):
        return 'both'
    if tags.get('junction') in ('roundabout', 'circular') or tags.get('highway') in ('motorway', 'motorway_link'):
        return 'forward'
    return 'both'


def haversine_e7(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1 * 1e-7), math.radians(lat2 * 1e-7)
    dp = p2 - p1
    dl = math.radians((lon2 - lon1) * 1e-7)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def encode_polyline(points):
    """Google encoded polyline (precision 1e-5) from ``(lat_e7, lon_e7)`` points."""
    out = []
    previous_lat = previous_lon = 0
    for lat, lon in points:
        lat5, lon5 = round(lat / 100), round(lon / 100)
        for delta in (lat5 - previous_lat, lon5 - previous_lon):
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        previous_lat, previous_lon = lat5, lon5
    return ''.join(out)


def _block(task):
    index, data_offset, size = task
    with open(_STATE['path'], 'rb') as stream:
        stream.seek(data_offset)
        return PrimitiveBlock(decode_blob(stream.read(size)))


def _write_array(path, values, typecode):
    if len(values):
        with open(path, 'wb') as stream:
            array(typecode, values).tofile(stream)


def _read_arrays(work, pattern, typecode):
    result = array(typecode)
    for path in sorted(Path(work).glob(pattern)):
        with open(path, 'rb') as stream:
            result.frombytes(stream.read())
    return result


def _scan(task):
    """Pass 1: select network/feature ways and feature relations from one block."""
    index = task[0]
    classifier, work = _STATE['classifier'], _STATE['work']
    block = _block(task)
    kinds = set()
    ways, relations = [], []
    graph_refs, endpoints, feature_refs = [], [], []
    for group in block.groups:
        kind = block.group_kind(group)
        kinds.add(kind)
        if kind == 'ways':
            for way_id, tags, refs in block.ways(group, want=classifier.wanted_way):
                if len(refs) < 2:
                    continue
                networks = classifier.networks(tags)
                category = classifier.feature(tags)
                ways.append((way_id, networks, category, subset(tags, EDGE_TAGS if networks else FEATURE_TAGS),
                             subset(tags, FEATURE_TAGS) if category else None, refs))
                if networks:
                    graph_refs.extend(refs)
                    endpoints.append(refs[0])
                    endpoints.append(refs[-1])
                if category:
                    feature_refs.extend(refs)
        elif kind == 'relations':
            for relation_id, tags, members in block.relations(group, want=classifier.wanted_relation):
                member_ways = [ref for member_type, ref, role in members if member_type == 'way' and role in ('outer', '')]
                if member_ways:
                    relations.append((relation_id, classifier.feature(tags), subset(tags, FEATURE_TAGS), member_ways))
    if ways or relations:
        with open(Path(work) / f'p1-{index:07d}.pkl', 'wb') as stream:
            pickle.dump((ways, relations), stream, protocol=pickle.HIGHEST_PROTOCOL)
    _write_array(Path(work) / f'g-{index:07d}.q', graph_refs, 'q')
    _write_array(Path(work) / f'e-{index:07d}.q', endpoints, 'q')
    _write_array(Path(work) / f'f-{index:07d}.q', feature_refs, 'q')
    return index, sorted(kinds), len(ways), len(relations)


def _members(task):
    """Pass 1b: node references of ways that are members of feature relations."""
    index = task[0]
    wanted = _STATE['member_ways']
    block = _block(task)
    found = {}
    for group in block.groups:
        if block.group_kind(group) == 'ways':
            for way_id, _, refs in block.ways(group):
                if way_id in wanted:
                    found[way_id] = refs
    if found:
        with open(Path(_STATE['work']) / f'm-{index:07d}.pkl', 'wb') as stream:
            pickle.dump(found, stream, protocol=pickle.HIGHEST_PROTOCOL)
        _write_array(Path(_STATE['work']) / f'f-m{index:07d}.q', [r for refs in found.values() for r in refs], 'q')
    return index, len(found)


def _locate(block, ids, lats, lons, sorted_ids, out):
    lo = bisect_left(sorted_ids, min(ids))
    hi = bisect_right(sorted_ids, max(ids))
    if lo == hi:
        return
    local = {sorted_ids[k]: k for k in range(lo, hi)}
    get = local.get
    indexes, out_lat, out_lon = out
    lat_offset, lon_offset = block.lat_offset, block.lon_offset
    fast = block.granularity == 100 and lat_offset == 0 and lon_offset == 0
    for position, node_id in enumerate(ids):
        k = get(node_id)
        if k is not None:
            indexes.append(k)
            if fast:
                out_lat.append(lats[position])
                out_lon.append(lons[position])
            else:
                out_lat.append(block.e7(lats[position], lat_offset))
                out_lon.append(block.e7(lons[position], lon_offset))


def _nodes(task):
    """Pass 2: coordinates of needed nodes and tagged feature nodes from one block."""
    index = task[0]
    classifier, work = _STATE['classifier'], Path(_STATE['work'])
    gid, fid = _STATE['gid'], _STATE['fid']
    block = _block(task)
    graph = (array('q'), array('i'), array('i'))
    extra = (array('q'), array('i'), array('i'))
    features = []
    count = 0
    for group in block.groups:
        kind = block.group_kind(group)
        if kind == 'dense':
            ids, lats, lons, keys_vals = block.dense_raw(group)
            tagged = block.dense_tags(keys_vals, len(ids))
        elif kind == 'nodes':
            rows = list(block.nodes(group))
            ids = [row[0] for row in rows]
            lats = [round((row[1] * 1e9 - block.lat_offset) / block.granularity) for row in rows]
            lons = [round((row[2] * 1e9 - block.lon_offset) / block.granularity) for row in rows]
            tagged = ((position, row[3]) for position, row in enumerate(rows) if row[3])
        else:
            continue
        if not ids:
            continue
        count += len(ids)
        _locate(block, ids, lats, lons, gid, graph)
        _locate(block, ids, lats, lons, fid, extra)
        for position, tags in tagged:
            category = classifier.feature(tags)
            if category:
                features.append({'type': 'feature', 'osm_type': 'node', 'osm_id': ids[position], 'category': category,
                                 'lat': round(block.lat(lats[position]), 7), 'lon': round(block.lon(lons[position]), 7),
                                 'tags': subset(tags, FEATURE_TAGS)})
    for name, (indexes, lat, lon) in (('ng', graph), ('nf', extra)):
        if len(indexes):
            with open(work / f'{name}-{index:07d}.bin', 'wb') as stream:
                pickle.dump((indexes, lat, lon), stream, protocol=pickle.HIGHEST_PROTOCOL)
    if features:
        with open(work / f'nfeat-{index:07d}.pkl', 'wb') as stream:
            pickle.dump(features, stream, protocol=pickle.HIGHEST_PROTOCOL)
    return index, count, len(features)


def _coordinate(ref):
    gid = _STATE['gid']
    k = bisect_left(gid, ref)
    if k < len(gid) and gid[k] == ref and _STATE['glat'][k] != MISSING:
        return _STATE['glat'][k], _STATE['glon'][k]
    fid = _STATE['fid']
    k = bisect_left(fid, ref)
    if k < len(fid) and fid[k] == ref and _STATE['flat'][k] != MISSING:
        return _STATE['flat'][k], _STATE['flon'][k]
    return None


def _area(refs):
    points = []
    missing = 0
    for ref in (refs[:-1] if len(refs) > 2 and refs[0] == refs[-1] else refs):
        point = _coordinate(ref)
        if point is None:
            missing += 1
        else:
            points.append(point)
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return {'lat': round(sum(lats) / len(lats) * 1e-7, 7), 'lon': round(sum(lons) / len(lons) * 1e-7, 7),
            'bbox': [round(min(lons) * 1e-7, 7), round(min(lats) * 1e-7, 7), round(max(lons) * 1e-7, 7), round(max(lats) * 1e-7, 7)],
            'centroid_method': 'mean_of_vertices', 'missing_nodes': missing}


def _edges(task):
    """Pass 3: split selected ways into edges between graph nodes and locate way features."""
    index = task[0]
    classifier, work = _STATE['classifier'], Path(_STATE['work'])
    gid, gflags, glat, glon = _STATE['gid'], _STATE['gflags'], _STATE['glat'], _STATE['glon']
    path = work / f'p1-{index:07d}.pkl'
    if not path.exists():
        return index, 0, 0, 0
    with open(path, 'rb') as stream:
        ways, _ = pickle.load(stream)
    endpoint_index, endpoint_mask = array('q'), array('B')
    features = []
    edges = missing = 0
    with open(work / f'edges-{index:07d}.jsonl', 'w', encoding='utf-8') as out:
        for way_id, networks, category, tags, feature_tags, refs in ways:
            if networks:
                positions = [bisect_left(gid, ref) for ref in refs]
                size = len(refs)
                splits = [0] + [k for k in range(1, size - 1) if gflags[positions[k]]] + [size - 1]
                mask = 0
                for network in networks:
                    mask |= NETWORK_BITS[network]
                for seq, (a, b) in enumerate(zip(splits, splits[1:])):
                    if refs[a] == refs[b] and b - a == 1:
                        continue
                    chain = positions[a:b + 1]
                    points = [(glat[k], glon[k]) for k in chain]
                    if any(lat == MISSING for lat, _ in points):
                        missing += 1
                        continue
                    length = 0.0
                    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
                        length += haversine_e7(lat1, lon1, lat2, lon2)
                    geometry = encode_polyline(points) if classifier.geometry else None
                    for network in networks:
                        row = {'type': 'edge', 'network': network, 'way': way_id, 'seq': seq, 'from': refs[a],
                               'to': refs[b], 'length_m': round(length, 2), 'points': b - a + 1,
                               'direction': direction(network, tags), 'tags': tags}
                        if geometry is not None:
                            row['geometry'] = geometry
                        out.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
                        edges += 1
                    endpoint_index.append(positions[a])
                    endpoint_mask.append(mask)
                    endpoint_index.append(positions[b])
                    endpoint_mask.append(mask)
            if category:
                located = _area(refs)
                if located:
                    features.append({'type': 'feature', 'osm_type': 'way', 'osm_id': way_id, 'category': category,
                                     **located, 'tags': feature_tags})
    with open(work / f'masks-{index:07d}.bin', 'wb') as stream:
        pickle.dump((endpoint_index, endpoint_mask), stream, protocol=pickle.HIGHEST_PROTOCOL)
    if features:
        with open(work / f'wfeat-{index:07d}.pkl', 'wb') as stream:
            pickle.dump(features, stream, protocol=pickle.HIGHEST_PROTOCOL)
    return index, edges, missing, len(features)


def _run_pool(function, tasks, workers):
    if workers <= 1 or len(tasks) <= 1:
        return [function(task) for task in tasks]
    context = multiprocessing.get_context('fork')
    with context.Pool(workers) as pool:
        return list(pool.imap(function, tasks, chunksize=1))


def _unique_counts(values):
    """Sorted unique IDs and saturating reference counts from an unsorted ``array('q')``."""
    ordered = sorted(values)
    ids, counts = array('q'), array('H')
    previous = None
    for value in ordered:
        if value == previous:
            if counts[-1] < 65535:
                counts[-1] += 1
        else:
            ids.append(value)
            counts.append(1)
            previous = value
    return ids, counts


def extract(path, work, workers=8, config=None):
    """Yield parsed rows for ``path``; ``work`` is an empty scratch directory."""
    classifier = Classifier(config)
    work = Path(work)
    blobs = blob_index(path)
    headers = [b for b in blobs if b['type'] == 'OSMHeader']
    if not headers:
        raise ValueError('PBF file has no OSMHeader block')
    header = parse_header(read_blob(path, headers[0]))
    data_blobs = [(b['index'], b['data_offset'], b['size']) for b in blobs if b['type'] == 'OSMData']
    _STATE.clear()
    _STATE.update(path=str(path), work=str(work), classifier=classifier)

    scanned = _run_pool(_scan, data_blobs, workers)
    kinds = {index: set(k) for index, k, _, _ in scanned}
    way_blocks = [task for task in data_blobs if 'ways' in kinds[task[0]]]
    node_blocks = [task for task in data_blobs if kinds[task[0]] & {'dense', 'nodes'}]
    relations = []
    for file in sorted(work.glob('p1-*.pkl')):
        with open(file, 'rb') as stream:
            relations.extend(pickle.load(stream)[1])
    member_ways = {ref for relation in relations for ref in relation[3]}
    if member_ways:
        _STATE['member_ways'] = member_ways
        _run_pool(_members, way_blocks, workers)
        _STATE.pop('member_ways')

    gid, gcount = _unique_counts(_read_arrays(work, 'g-*.q', 'q'))
    gflags = array('B', bytes(len(gid)))
    for k, count in enumerate(gcount):
        if count > 1:
            gflags[k] = 1
    for ref in set(_read_arrays(work, 'e-*.q', 'q')):
        gflags[bisect_left(gid, ref)] = 1
    fid, _ = _unique_counts(_read_arrays(work, 'f-*.q', 'q'))
    for pattern in ('g-*.q', 'e-*.q', 'f-*.q'):
        for file in work.glob(pattern):
            file.unlink()
    _STATE.update(gid=gid, fid=fid)
    node_results = _run_pool(_nodes, node_blocks, workers)

    glat = array('i', [MISSING]) * len(gid)
    glon = array('i', [MISSING]) * len(gid)
    flat = array('i', [MISSING]) * len(fid)
    flon = array('i', [MISSING]) * len(fid)
    for pattern, lat_target, lon_target in (('ng-*.bin', glat, glon), ('nf-*.bin', flat, flon)):
        for file in sorted(work.glob(pattern)):
            with open(file, 'rb') as stream:
                indexes, lats, lons = pickle.load(stream)
            for k, lat, lon in zip(indexes, lats, lons):
                lat_target[k] = lat
                lon_target[k] = lon
            file.unlink()
    _STATE.update(gflags=gflags, glat=glat, glon=glon, flat=flat, flon=flon)
    edge_results = _run_pool(_edges, way_blocks, workers)

    masks = array('B', bytes(len(gid)))
    for file in sorted(work.glob('masks-*.bin')):
        with open(file, 'rb') as stream:
            indexes, values = pickle.load(stream)
        for k, value in zip(indexes, values):
            masks[k] |= value
        file.unlink()
    member_refs = {}
    for file in sorted(work.glob('m-*.pkl')):
        with open(file, 'rb') as stream:
            member_refs.update(pickle.load(stream))

    counts = {'blobs': len(blobs), 'data_blocks': len(data_blobs), 'nodes': sum(r[1] for r in node_results),
              'selected_ways': sum(r[2] for r in scanned), 'feature_relations': len(relations),
              'referenced_graph_nodes': len(gid), 'graph_nodes': sum(1 for m in masks if m),
              'edges': sum(r[1] for r in edge_results), 'segments_missing_coordinates': sum(r[2] for r in edge_results),
              'missing_graph_node_coordinates': sum(1 for lat in glat if lat == MISSING)}
    yield {'type': 'summary', 'header': header, 'counts': counts,
           'config': {'road_highways': sorted(classifier.roads), 'excluded_service': sorted(classifier.excluded_service),
                      'railways': sorted(classifier.railways), 'power_lines': sorted(classifier.power),
                      'edge_geometry': classifier.geometry, 'polyline_precision': 5}}
    for index, _, _ in data_blobs:
        for prefix in ('nfeat', 'wfeat'):
            file = work / f'{prefix}-{index:07d}.pkl'
            if file.exists():
                with open(file, 'rb') as stream:
                    yield from pickle.load(stream)
    for relation_id, category, tags, ways in relations:
        refs = [ref for way in ways for ref in member_refs.get(way, ())]
        located = _area(refs) if refs else None
        row = {'type': 'feature', 'osm_type': 'relation', 'osm_id': relation_id, 'category': category, 'tags': tags,
               'member_ways': len(ways), 'member_ways_found': sum(1 for way in ways if way in member_refs)}
        if located:
            row.update(located)
        yield row
    for task in way_blocks:
        file = work / f'edges-{task[0]:07d}.jsonl'
        if file.exists():
            with open(file, encoding='utf-8') as stream:
                for line in stream:
                    yield json.loads(line)
            file.unlink()
    names = [(bit, name) for name, bit in NETWORK_BITS.items()]
    for k, mask in enumerate(masks):
        if mask and glat[k] != MISSING:
            yield {'type': 'node', 'osm_id': gid[k], 'lat': round(glat[k] * 1e-7, 7), 'lon': round(glon[k] * 1e-7, 7),
                   'networks': [name for bit, name in names if mask & bit], 'way_refs': gcount[k]}
    _STATE.clear()
