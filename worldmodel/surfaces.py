"""Bounded standalone HTML views of immutable materializations and graph records.

render_surface(data, spec) returns HTML; it never writes files. ``data`` has
``snapshots`` and/or typed ``records`` lists. ``spec`` has ``title`` and up to 16
``panels``. Panels have kind=value/table/plot/map/graph and optional title.
Value/table/plot selectors: entity, variable, path (list of object keys/list
indices). Selection preserves input order; value displays the last selected row.
Table/plot limit defaults to 100 (1..surface_max_panel_rows); omitted rows are counted explicitly.
Plots require one entity/variable/unit series and numeric or ISO date times.
Observation records use metric, valid_from/observed_at, and entity/subject or
``dimensions[entity_dimension]`` (default geo); metadata includes their record id.
Maps select latitude/longitude variables (defaults literal latitude/longitude),
optional entity, and pair coordinates at identical entity/time; degree units
are mandatory; identical coordinate sources merge with their provenance intact,
and conflicting coordinates fail. Map limit is 1..surface_max_panel_rows. Graph coalesces
compatible entity descriptions and uses object-valued assertions; limit is
1..surface_max_graph_nodes nodes, with ``edge_limit`` (default 500, at most
surface_max_graph_edges) edge reference rows (visible edges first). Optional ``seeds``
is a nonempty list of existing entity IDs for deterministic undirected BFS;
only reachable nodes are selected. Without seeds, IDs are sorted. Missing
and excluded endpoints are explicitly reported; positions are abstract.
Hard bounds are named limits: surface_max_rows input rows, surface_max_field_chars
characters per displayed field and surface_max_html_bytes of final HTML (see
worldmodel.limits; pass ``limits={...}``). Oversized fields/documents are
rejected with LimitExceeded, never silently clipped.
All labels, values and provenance are escaped. Static mode has no scripts;
interactive=True adds local DOM controls with no remote assets.
"""

from datetime import datetime, timezone
from collections import deque
from html import escape
import json
import math

from .limits import current_limits, resolve_limits, use_limits


def _text(value):
    if isinstance(value, str):
        result = value
    else:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    current_limits().check('surface_max_field_chars', len(result), 'surface field exceeds displayed character bound')
    return escape(result, quote=True)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('surface requires finite numeric values')
    return value


def _time(value):
    if isinstance(value, (int, float)):
        return _number(value)
    if not isinstance(value, str):
        raise ValueError('plot time must be numeric or ISO date')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError as error:
        raise ValueError('plot time must be numeric or ISO date') from error


def _limit(panel, graph=False):
    limits = current_limits()
    name = 'surface_max_graph_nodes' if graph else 'surface_max_panel_rows'
    maximum = getattr(limits, name)
    limit = panel.get('limit', min(100, maximum))
    if type(limit) is not int or limit < 1:
        raise ValueError(f'panel limit must be an integer from 1 to {maximum}')
    return limits.check(name, limit, 'panel limit')


def _edge_limit(panel):
    limits = current_limits()
    value = panel.get('edge_limit', min(500, limits.surface_max_graph_edges))
    if type(value) is not int or value < 0:
        raise ValueError('graph edge_limit must be a nonnegative integer')
    return limits.check('surface_max_graph_edges', value, 'graph edge_limit')


def _notice(count, noun='rows'):
    return f'<p>Truncated: {count} {noun} omitted.</p>' if count else ''


def _rows(data, panel):
    rows = list(data.get('snapshots', []))
    dimension = panel.get('entity_dimension', 'geo')
    if not isinstance(dimension, str):
        raise ValueError('entity_dimension must be a string')
    for record in data.get('records', []):
        if record.get('kind') == 'observation':
            dimensions = record.get('dimensions', {})
            if not isinstance(dimensions, dict):
                raise ValueError('observation dimensions must be an object')
            rows.append(dict(time=record.get('valid_from') or record.get('observed_at'),
                             entity=record.get('entity', record.get('subject', dimensions.get(dimension))),
                             variable=record.get('metric'), value=record.get('value'),
                             unit=record.get('unit'), origin=record.get('origin', 'observation'),
                             evidence=record.get('evidence', []), record_id=record.get('id')))
    return rows


def _select(rows, panel):
    for field in ('entity', 'variable'):
        if field in panel and not isinstance(panel[field], str):
            raise ValueError(f'{field} selector must be a string')
    path = panel.get('path', [])
    if not isinstance(path, list) or len(path) > 32 or any(type(key) not in (str, int) for key in path):
        raise ValueError('path must be a list of keys or indices, at most 32 deep')
    selected = []
    for row in rows:
        if any(field in panel and row.get(field) != panel[field] for field in ('entity', 'variable')):
            continue
        value = row.get('value')
        for key in path:
            if isinstance(value, dict) and isinstance(key, str) and key in value:
                value = value[key]
            elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
                value = value[key]
            else:
                raise ValueError('path does not resolve in selected value')
        selected.append(dict(row, value=value))
    if not selected:
        raise ValueError('surface selection contains no rows')
    return selected


def _table(rows):
    fields = ('time', 'entity', 'variable', 'value', 'unit', 'origin')
    return '<div class="table-scroll"><table><thead><tr>' + ''.join(f'<th>{f}</th>' for f in fields) + '<th>provenance</th></tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>' + _text(row.get(f)) + '</td>' for f in fields) + '<td>' + _sources(row) + '</td></tr>'
        for row in rows) + '</tbody></table></div>'


def _details(label, content):
    return '<details><summary>' + _text(label) + '</summary>' + content + '</details>'


def _sources(row):
    provenance = {field: row.get(field) for field in ('record_id', 'evidence', 'sources') if field in row}
    return _details('Sources', '<pre>' + _text(provenance) + '</pre>')


def _value(row):
    return '<div class="scalar">' + _text(row.get('value')) + '<span class="scalar-unit">' + _text(row.get('unit')) + '</span></div><p class="value-context">' + _text(row.get('entity')) + ' · ' + _text(row.get('variable')) + '<br>Time: ' + _text(row.get('time')) + ' · Origin: ' + _text(row.get('origin')) + '</p>' + _sources(row)


def _svg(body):
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 420" role="img">' + body + '</svg>'


def _plot(rows):
    if len({_text(r.get('unit')) for r in rows}) != 1:
        raise ValueError('plot requires compatible units')
    if len({(_text(r.get('entity')), _text(r.get('variable'))) for r in rows}) != 1:
        raise ValueError('plot requires one entity/variable series; use separate panels')
    numeric_time = [isinstance(r.get('time'), (int, float)) for r in rows]
    if any(numeric_time) and not all(numeric_time):
        raise ValueError('plot cannot mix numeric and date times')
    points = sorted((_time(r.get('time')), _number(r.get('value'))) for r in rows)
    xmin, xmax = min(x for x, y in points), max(x for x, y in points)
    ymin, ymax = min(y for x, y in points), max(y for x, y in points)
    coords = [(60 + (x - xmin) / (xmax - xmin or 1) * 700,
               350 - (y - ymin) / (ymax - ymin or 1) * 300) for x, y in points]
    polyline = ' '.join(f'{x:.3f},{y:.3f}' for x, y in coords)
    body = '<path d="M60 40V350H770" fill="none" stroke="#64748b"/>'
    body += f'<polyline points="{polyline}" fill="none" stroke="#2563eb" stroke-width="2"/>'
    body += ''.join(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3" fill="#2563eb"/>' for x, y in coords)
    body += f'<text x="60" y="20">{_text(rows[0].get("variable"))} ({_text(rows[0].get("unit"))})</text>'
    body += f'<text x="60" y="385">Time range: {_text(min(rows, key=lambda r: _time(r["time"]))["time"])} to {_text(max(rows, key=lambda r: _time(r["time"]))["time"])}</text>'
    body += f'<text x="60" y="410">Value range: {_text(ymin)} to {_text(ymax)}</text>'
    return _svg(body)


def _map(rows, panel, limit):
    latitude = panel.get('latitude', 'latitude')
    longitude = panel.get('longitude', 'longitude')
    if not isinstance(latitude, str) or not isinstance(longitude, str) or latitude == longitude:
        raise ValueError('map coordinate variables must be distinct strings')
    if 'path' in panel or 'variable' in panel:
        raise ValueError('map uses latitude and longitude selectors')
    if 'entity' in panel and not isinstance(panel['entity'], str):
        raise ValueError('entity selector must be a string')
    pairs = {}
    for row in rows:
        axis = row.get('variable')
        if axis not in (latitude, longitude) or ('entity' in panel and row.get('entity') != panel['entity']):
            continue
        allowed = ('degree', 'degrees', 'deg', 'degrees_north' if axis == latitude else 'degrees_east')
        if row.get('unit') not in allowed:
            raise ValueError('map coordinates require degree units appropriate to their axis')
        value = _number(row.get('value'))
        if abs(value) > (90 if axis == latitude else 180):
            raise ValueError('map coordinate outside latitude/longitude range')
        if not isinstance(row.get('entity'), str) or row.get('time') is None:
            raise ValueError('map coordinates require explicit entity and time')
        key = (_text(row['entity']), _text(row['time']))
        pair = pairs.setdefault(key, {})
        if axis in pair:
            if pair[axis]['value'] != value:
                raise ValueError('conflicting map coordinate for entity/time')
            pair[axis]['sources'].append(row)
        else:
            pair[axis] = dict(row, sources=[row])
    if not pairs or any(set(pair) != {latitude, longitude} for pair in pairs.values()):
        raise ValueError('map requires paired latitude/longitude coordinates at identical entity/time')
    shown = list(pairs.values())[:limit]
    lats = [pair[latitude]['value'] for pair in shown]
    lons = [pair[longitude]['value'] for pair in shown]
    latpad = max((max(lats) - min(lats)) * .08, .000001)
    lonpad = max((max(lons) - min(lons)) * .08, .000001)
    south, north = max(-90, min(lats) - latpad), min(90, max(lats) + latpad)
    west, east = max(-180, min(lons) - lonpad), min(180, max(lons) + lonpad)
    body = '<rect x="40" y="40" width="720" height="320" fill="#eff6ff" stroke="#64748b"/>'
    body += '<text x="40" y="20">Latitude / longitude (degrees); coordinate grid, no basemap</text>'
    for i in range(1, 4):
        body += f'<path d="M{40 + i * 180} 40V360 M40 {40 + i * 80}H760" stroke="#dbeafe"/>'
    body += f'<text x="40" y="385">Extent (degrees): longitude {west:.6f} to {east:.6f}; latitude {south:.6f} to {north:.6f}</text>'
    body += '<text x="40" y="410">Auto-fit to displayed coordinates; hover markers for entity and time.</text>'
    source_rows = []
    for pair in shown:
        lat, lon = pair[latitude], pair[longitude]
        x, y = 40 + (lon['value'] - west) / (east - west) * 720, 360 - (lat['value'] - south) / (north - south) * 320
        body += f'<circle cx="{x:.3f}" cy="{y:.3f}" r="4" fill="#2563eb" fill-opacity="0.7"><title>{_text(lat["entity"])} · time {_text(lat["time"])} · latitude {_text(lat["value"])} · longitude {_text(lon["value"])}</title></circle>'
        source_rows.extend((lat, lon))
    return _svg(body) + _notice(len(pairs) - len(shown), 'coordinate pairs') + _details('Coordinates and sources', _table(source_rows))


def _graph(records, limit, panel):
    nodes = {}
    for record in records:
        if record.get('kind') == 'entity':
            key = record.get('entity_id', record.get('id'))
            if not isinstance(key, str):
                raise ValueError('graph entity needs an identifier')
            if key in nodes:
                from .ontology import is_a
                previous = nodes[key]['entity_type']
                current = record.get('entity_type')
                if previous is not None and current is not None:
                    if not (is_a(previous, current) or is_a(current, previous)):
                        raise ValueError('incompatible graph entity types')
                if previous is None or (current is not None and is_a(current, previous)):
                    nodes[key]['entity_type'] = current
                nodes[key]['sources'].append(record)
                label = record.get('label', key)
                if label not in nodes[key]['labels']:
                    nodes[key]['labels'].append(label)
            else:
                nodes[key] = {'entity_id': key, 'entity_type': record.get('entity_type'),
                              'labels': [record.get('label', key)], 'sources': [record]}
    edges = [r for r in records if r.get('kind') == 'assertion' and 'object' in r]
    neighbors = {}
    for edge in edges:
        source, target = edge.get('subject'), edge.get('object')
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError('graph edge endpoints must be identifiers')
        if source in nodes and target in nodes:
            neighbors.setdefault(source, set()).add(target)
            neighbors.setdefault(target, set()).add(source)
    if 'seeds' in panel:
        seeds = panel['seeds']
        if (not isinstance(seeds, list) or not seeds or len(seeds) > limit
                or any(not isinstance(seed, str) or seed not in nodes for seed in seeds)):
            raise ValueError('graph seeds must be existing entity IDs, with count at most limit')
        queue = deque(sorted(set(seeds)))
        visited, keys = set(queue), []
        while queue and len(keys) < limit:
            key = queue.popleft()
            keys.append(key)
            for neighbor in sorted(neighbors.get(key, ())):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
    else:
        keys = sorted(nodes)[:limit]
    positions = {key: (400 + 290 * math.cos(2 * math.pi * i / max(1, len(keys))),
                       210 + 150 * math.sin(2 * math.pi * i / max(1, len(keys))))
                 for i, key in enumerate(keys)}
    edges.sort(key=lambda edge: not (edge['subject'] in positions and edge['object'] in positions))
    edge_limit = _edge_limit(panel)
    body, references = '', []
    for edge in edges[:edge_limit]:
        source, target = edge.get('subject'), edge.get('object')
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError('graph edge endpoints must be identifiers')
        status = 'shown'
        if source not in nodes or target not in nodes:
            status = 'unresolved endpoint'
        elif source not in positions or target not in positions:
            status = 'endpoint omitted by node limit'
        else:
            x1, y1 = positions[source]
            x2, y2 = positions[target]
            body += f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="#94a3b8"><title>{_text(edge.get("predicate"))}</title></line>'
        references.append('<li>' + _text(dict(subject=source, predicate=edge.get('predicate'), object=target,
                          status=status, id=edge.get('id'), origin=edge.get('origin'), evidence=edge.get('evidence', []))) + '</li>')
    for key in keys:
        x, y = positions[key]
        body += f'<circle cx="{x:.3f}" cy="{y:.3f}" r="6" fill="#2563eb"/>'
        body += f'<text x="{x + 9:.3f}" y="{y:.3f}">{_text(nodes[key]["labels"])}</text>'
    return '<p>Abstract topology layout; not geographic. Node omissions include selection and node limits.</p>' + _svg(body) + _notice(len(nodes) - len(keys), 'nodes') + _notice(max(0, len(edges) - edge_limit), 'edge references') + _details('Node provenance', '<ul>' + ''.join('<li>' + _text(nodes[k]) + '</li>' for k in keys) + '</ul>') + _details('Edge references', '<ul>' + ''.join(references) + '</ul>')


def render_surface(data, spec, *, limits=None):
    """Render a validated, bounded declarative surface as a standalone HTML string."""
    with use_limits(resolve_limits(limits)):
        return _render(data, spec)


def _render(data, spec):
    limits = current_limits()
    if not isinstance(data, dict) or not isinstance(spec, dict):
        raise ValueError('surface data and specification must be objects')
    for field in ('snapshots', 'records'):
        rows = data.get(field, [])
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError(f'{field} must be a list of at most {limits.surface_max_rows} objects')
        limits.check('surface_max_rows', len(rows), f'{field} must be a list of at most {limits.surface_max_rows} objects')
    limits.check('surface_max_rows', sum(len(data.get(f, [])) for f in ('snapshots', 'records')), 'surface input exceeds row bound')
    if 'interactive' in spec and type(spec['interactive']) is not bool:
        raise ValueError('interactive must be boolean')
    panels = spec.get('panels')
    if not isinstance(panels, list) or len(panels) > 16:
        raise ValueError('panels must be a list of at most 16 panels')
    title = _text(spec.get('title', 'Materialization surface'))
    sections = []
    for panel in panels:
        if not isinstance(panel, dict):
            raise ValueError('panel must be an object')
        kind = panel.get('kind')
        limit = _limit(panel, kind == 'graph')
        if kind == 'spatial' or (kind == 'graph' and panel.get('source') == 'spatial'):
            if not spec.get('interactive') or not data.get('spatial_frames'):
                raise ValueError('Spatial panels require interactive explicit frames')
            content = '<p>Explicit spatial frames; enable JavaScript to inspect the selected state.</p>'
        elif kind == 'graph':
            content = _graph(data.get('records', []), limit, panel)
        elif kind == 'map':
            content = _map(_rows(data, panel), panel, limit)
        elif kind in ('value', 'table', 'plot'):
            selected = _select(_rows(data, panel), panel)
            if kind == 'value':
                content = _value(selected[-1]) + '<p class="selection-note">Last selected row in input order.</p>' + _notice(len(selected) - 1)
            else:
                shown = selected[:limit]
                content = (_plot(shown) + _details('Values and sources', _table(shown)) if kind == 'plot' else _table(shown)) + _notice(len(selected) - len(shown))
        else:
            raise ValueError('unknown surface panel kind')
        sections.append('<section><h2>' + _text(panel.get('title', kind.title())) + '</h2>' + content + '</section>')
    html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + title + '</title><style>body{font:15px system-ui;margin:24px;color:#172033;background:#f8fafc}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,600px),1fr));gap:20px}section{background:white;border:1px solid #cbd5e1;border-radius:12px;padding:22px;min-width:0;overflow:auto}h2{margin-top:0}.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:650px;font-size:12px}td,th{border:1px solid #e2e8f0;padding:9px;text-align:left;vertical-align:top;min-width:70px;overflow-wrap:break-word}th{white-space:nowrap;background:#f1f5f9}td:last-child{min-width:100px}svg{width:100%;min-width:380px}text{font:12px system-ui}details{margin:10px 0;max-width:100%}summary{cursor:pointer;color:#2563eb;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:65ch;font:12px/1.6 ui-monospace,monospace;background:#f8fafc;padding:12px}li{overflow-wrap:anywhere;margin:8px 0}.scalar{font-size:48px;font-weight:650;line-height:1.2;overflow-wrap:anywhere;margin:20px 0 12px}.scalar-unit{font-size:20px;color:#64748b;font-weight:400;margin-left:12px}.value-context{line-height:1.8;color:#475569}.selection-note{font-size:12px;color:#64748b}</style></head><body><h1>' + title + '</h1><main>' + ''.join(sections) + '</main></body></html>'
    if spec.get('interactive'):
        from .interactive_surfaces import enhance
        html = enhance(html, data, spec)
    limits.check('surface_max_html_bytes', len(html.encode('utf-8')), 'surface output exceeds HTML byte bound')
    return html
