"""OpenStreetMap network topology for the Geofabrik US Northeast extract (osm_us_northeast).

Dataset-local copy of the osm_topology implementation: ``helpers.py`` is the stdlib PBF
decoder and ``graph.py`` builds simplified network edges. The ``parsed`` stage decodes the
sharded PBF acquisition; ``normalized`` emits compact evidence. Keep the three files in sync
with data/osm_topology when fixing bugs.
"""
import math
import os
import shutil
import tempfile
from pathlib import Path

EDGE_PREDICATES = {'road': 'road_connects_to', 'rail': 'rail_connects_to', 'power': 'power_line_connects_to',
                   'pipeline': 'pipeline_connects_to', 'ferry': 'ferry_connects_to'}
FEATURE_TYPES = {'airport': 'airport', 'port': 'port', 'ferry_terminal': 'port', 'power_plant': 'facility',
                 'substation': 'infrastructure', 'refinery': 'refinery', 'rail_station': 'facility',
                 'rail_yard': 'infrastructure', 'fuel_station': 'facility', 'military_site': 'facility'}
MPH_TO_KPH = 1.609344


def _full(context):
    return context.raw_inputs and context.raw_coverage()['layout'] == 'shards'


def parse(context):
    """Decode a full PBF acquisition into summary/feature/edge/node rows (empty for samples)."""
    if not _full(context):
        return
    shards = list(context.raw_shards())
    if len(shards) != 1:
        raise ValueError('osm_topology expects exactly one .osm.pbf shard')
    from .graph import extract
    parameters = context.parameters
    workers = int(parameters.get('workers') or min(12, os.cpu_count() or 1))
    config = {key: parameters[key] for key in ('road_highways', 'excluded_service', 'railways', 'power_lines', 'edge_geometry')
              if key in parameters}
    scratch = context.store.scratch_dir(context.definition['id'])
    work = tempfile.mkdtemp(prefix='osm-graph-', dir=scratch)
    try:
        yield from extract(shards[0]['path'], work, workers=workers, config=config)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def maxspeed_kph(value):
    if not value:
        return None
    text = str(value).split(';')[0].strip().lower()
    factor = 1.0
    if text.endswith('mph'):
        factor, text = MPH_TO_KPH, text[:-3].strip()
    elif text.endswith('km/h') or text.endswith('kmh'):
        text = text.replace('km/h', '').replace('kmh', '').strip()
    try:
        speed = float(text) * factor
    except ValueError:
        return None
    return round(speed, 1) if math.isfinite(speed) and speed > 0 else None


def first_int(value):
    try:
        return int(str(value).split(';')[0].strip())
    except (TypeError, ValueError):
        return None


def _normalize_full(context):
    raw = context.raw_inputs[0]
    observed = context.raw_receipt()['retrieved_at']
    snapshot = None

    def evidence(kind, osm_id):
        return [{'input': raw, 'locator': f'shard:0/{kind}:{osm_id}'}]

    for row in context.stage_records('parsed'):
        kind = row['type']
        if kind == 'summary':
            stamp = row['header'].get('replication_timestamp')
            if stamp:
                from datetime import datetime, timezone
                snapshot = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            continue
        if kind == 'edge':
            tags = row['tags']
            network = row['network']
            forward = row['direction'] != 'backward'
            attributes = {'way': f"osm:way:{row['way']}", 'segment': row['seq'], 'network': network,
                          'length_m': row['length_m'], 'vertices': row['points'],
                          'bidirectional': row['direction'] in ('both', 'reversible'),
                          'direction_source': row['direction'], 'snapshot_at': snapshot}
            if network == 'road':
                attributes.update(highway=tags.get('highway'), maxspeed_kph=maxspeed_kph(tags.get('maxspeed')),
                                  lanes=first_int(tags.get('lanes')))
            for key in ('name', 'ref', 'maxspeed', 'access', 'bridge', 'tunnel', 'toll', 'surface', 'service',
                        'railway', 'usage', 'electrified', 'gauge', 'voltage', 'operator', 'substance', 'location',
                        'junction', 'hgv', 'route'):
                if key in tags:
                    attributes['osm_' + key.replace(':', '_')] = tags[key]
            source, target = (row['from'], row['to']) if forward else (row['to'], row['from'])
            yield {'kind': 'assertion', 'id': f"osm:way:{row['way']}:segment:{row['seq']}:{network}",
                   'observed_at': observed, 'evidence': evidence('way', row['way']),
                   'subject': f'osm:node:{source}', 'predicate': EDGE_PREDICATES[network], 'object': f'osm:node:{target}',
                   'attributes': {k: v for k, v in attributes.items() if v is not None}}
        elif kind == 'node':
            networks = row['networks']
            entity_type = 'road_junction' if 'road' in networks else 'location'
            yield {'kind': 'entity', 'id': f"osm:node:{row['osm_id']}", 'entity_id': f"osm:node:{row['osm_id']}",
                   'entity_type': entity_type, 'label': f"OSM node {row['osm_id']}", 'observed_at': observed,
                   'evidence': evidence('node', row['osm_id']),
                   'attributes': {'lat': row['lat'], 'lon': row['lon'], 'networks': networks,
                                  'way_references': row['way_refs'], 'graph_role': 'intersection_or_endpoint',
                                  'coordinate_unit': 'degrees_wgs84', 'snapshot_at': snapshot}}
        elif kind == 'feature':
            key = f"osm:feature:{row['osm_type']}:{row['osm_id']}"
            tags = row.get('tags') or {}
            attributes = {'category': row['category'], 'osm_type': row['osm_type'], 'osm_id': row['osm_id'],
                          'osm_tags': tags, 'snapshot_at': snapshot}
            for field in ('lat', 'lon', 'bbox', 'centroid_method', 'missing_nodes', 'member_ways', 'member_ways_found'):
                if field in row:
                    attributes[field] = row[field]
            if 'lat' not in row:
                attributes['location_missing_reason'] = 'relation_member_ways_not_found'
            yield {'kind': 'entity', 'id': key, 'entity_id': key, 'entity_type': FEATURE_TYPES[row['category']],
                   'label': tags.get('name') or f"OSM {row['category'].replace('_', ' ')} {row['osm_id']}",
                   'observed_at': observed, 'evidence': evidence(row['osm_type'], row['osm_id']),
                   'attributes': attributes}
            if tags.get('iata'):
                yield {'kind': 'assertion', 'id': key + ':same_as:iata', 'observed_at': observed,
                       'evidence': evidence(row['osm_type'], row['osm_id']), 'subject': key, 'predicate': 'identified_by',
                       'value': 'iata:' + tags['iata'], 'attributes': {'scheme': 'IATA airport code'}}
        else:
            raise ValueError('Unknown parsed OSM row type: ' + str(kind))


def run(context):
    if not context.raw_inputs or not _full(context):
        raise ValueError('osm_us_northeast expects a sharded Geofabrik .osm.pbf acquisition (wm acquire osm_us_northeast)')
    yield from _normalize_full(context)
