'First100 highway ways in a small San Francisco bounding box; full node IDs and geometry per retained way; bounded subgraph, not global coverage'
import json
import math
from worldmodel.util import digest
from worldmodel.source_helpers import SERIES, next_day, month_end

def run(context):
    dataset = 'osm_topology'
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    seen = set()
    record_ids = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            out = []

            def base(kind, identity, **fields):
                r = {'kind': kind, 'id': 'strategic:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': context.raw_evidence('line:' + str(line_number), index), 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only'}, **fields}
                if r['id'] not in record_ids:
                    record_ids.add(r['id'])
                    out.append(r)
                return r

            def entity(key, typ, label=None, **attrs):
                if key not in seen:
                    seen.add(key)
                    r = base('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    r['attributes'].update(attrs)
                return key

            def relation(subject, predicate, obj, **attrs):
                r = base('assertion', [line_number, subject, predicate, obj, attrs], subject=subject, predicate=predicate, object=obj)
                r['attributes'].update(attrs)

            def observation(subject, metric, value, unit, start=None, end=None, scale=1, **attrs):
                missing = value is None or str(value).strip() in ('', '.', 'NA', 'N/A', 'null')
                if not missing:
                    value = float(value) * scale
                    if not math.isfinite(value):
                        raise ValueError('Nonfinite source measurement')
                    if value.is_integer():
                        value = int(value)
                r = base('observation', [line_number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                if missing:
                    r['missing_reason'] = 'source_missing'
                r['attributes'].update(attrs)
            if row.get('type') != 'way':
                raise ValueError('OSM topology adapter expects ways with geometry')
            nodes = row.get('nodes', [])
            geometry = row.get('geometry', [])
            tags = row.get('tags', {})
            if len(nodes) < 2 or len(nodes) != len(geometry) or any(('lat' not in g or 'lon' not in g for g in geometry)):
                raise ValueError('OSM way missing complete node geometry')
            way = entity('osm:way:' + str(row['id']), 'road', tags.get('name'), osm_tags=tags, node_ids=nodes, geometry=geometry)
            for node, point in zip(nodes, geometry):
                key = entity('osm:node:' + str(node), 'road_junction', osm_node_id=node)
                relation(way, 'road_has_junction', key)
                for coordinate, metric in [('lat', 'latitude'), ('lon', 'longitude')]:
                    observation(key, metric, point[coordinate], 'degrees', acquired, validity_basis='acquired_snapshot')
            one = str(tags.get('oneway', '')).lower()
            forward = one not in ('-1', 'reverse')
            backward = one in ('no', '0', 'false') or (not one and tags.get('junction') != 'roundabout' and (tags.get('highway') not in ('motorway', 'motorway_link')))
            if one in ('-1', 'reverse'):
                backward = True
            for i, (a, b) in enumerate(zip(nodes, nodes[1:])):
                for source, target in ([(a, b)] if forward else []) + ([(b, a)] if backward else []):
                    relation('osm:node:' + str(source), 'road_connects_to', 'osm:node:' + str(target), way_id=way, segment_index=i, osm_tags=tags, access_rules_require_routing_interpretation=True)
            yield from out
