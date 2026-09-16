"""Disk-backed explicit supports, topology and conservative lifecycle changes.

Coordinates are support locations, not inferred polygon boundaries. Bbox queries
use the declared axis order and inclusive point coordinates (no antimeridian
wrapping). Query limits bound rows brought into Python. Lifecycle batches use a
single SQLite transaction; foreign keys prevent dangling topology/membership.
Signed scalars and vectors can be stored and conserved componentwise. Evolution
offers opt-in signed/vector timelines; legacy materialization uses FieldWorld.
Explicit planar polygons have separate bounded queries from support locations.

Scale: imports and value updates use batched executemany; set-based selections
use a TEMP id table instead of SQL variable lists; ``load_arrays`` and
``put_value_arrays`` move whole domains as field arrays. Ceilings are named
limits (field_max_*, spatial_max_*) resolved per call from ``SpatialStore(path,
limits=...)``, ``use_limits`` or ``WORLD_MODEL_LIMITS``.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import sqlite3

from .limits import resolve_limits
from .model import identifier
from .util import canonical, digest

_ENCODE = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode


def _finite(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{label} must be finite' + (' and positive' if positive else ''))
    return value


def _json(value, maximum=None):
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('Out of range float values are not JSON compliant')
        return repr(value)
    if type(value) is int:
        return repr(value)
    payload = _ENCODE(value)
    maximum = resolve_limits().spatial_max_row_bytes if maximum is None else maximum
    if len(payload) * 4 > maximum and len(payload.encode('utf-8')) > maximum:
        raise ValueError(f'spatial row exceeds {maximum} byte payload bound (limit spatial_max_row_bytes)')
    return payload


def _scale(value, factor):
    if isinstance(value, list):
        return [_finite(v * factor, 'weighted component') for v in value]
    return _finite(value * factor, 'weighted value')


def _sum(values):
    values = list(values)
    try:
        result = ([math.fsum(v[i] for v in values) for i in range(len(values[0]))]
                  if isinstance(values[0], list) else math.fsum(values))
    except OverflowError as error:
        raise ValueError('Integral exceeds finite range') from error
    for value in result if isinstance(result, list) else [result]:
        _finite(value, 'integral')
    return result


def _decode(text, definition):
    return float(text) if definition['value_type'] == 'scalar' and text[0] != '[' else json.loads(text)


class SpatialStore:
    """SQLite field state. Use initialize once, then select/apply across reopen.

    Lifecycle events: birth(cell, values, external_input=False), death(cell,
    transfer_to=None), merge(cells, cell), split(cell, children, edges).
    Birth of nonzero state requires explicit external_input=True and is audited
    as an import. Death requires zero integrals or an existing transfer target;
    the target's measure remains unchanged. Merge preserves total measure and
    rewires external edges, dropping internal edges. Split requires an explicit
    replacement for incident topology; no geometry/topology is inferred.
    """

    def __init__(self, path, *, limits=None):
        self._limit_overrides = limits
        self._definitions_cache = None
        self.db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA cache_size=-262144')
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cells (
                id TEXT PRIMARY KEY, measure REAL NOT NULL CHECK(measure > 0),
                x REAL, y REAL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS cells_coordinates ON cells(x,y,id);
            CREATE TABLE IF NOT EXISTS cell_geometry (
                cell TEXT PRIMARY KEY REFERENCES cells(id) ON DELETE CASCADE,
                xmin REAL, ymin REAL, xmax REAL, ymax REAL);
            CREATE INDEX IF NOT EXISTS geometry_bounds ON cell_geometry(xmin,xmax,ymin,ymax);
            CREATE TABLE IF NOT EXISTS fields (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS field_values (
                cell TEXT NOT NULL REFERENCES cells(id) ON DELETE CASCADE,
                field TEXT NOT NULL REFERENCES fields(name), value TEXT NOT NULL,
                PRIMARY KEY(cell,field));
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL REFERENCES cells(id) ON DELETE CASCADE,
                target TEXT NOT NULL REFERENCES cells(id) ON DELETE CASCADE,
                payload TEXT NOT NULL, CHECK(source != target));
            CREATE INDEX IF NOT EXISTS edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS edges_target ON edges(target);
            CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, claimant TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS claims_claimant ON claims(claimant);
            CREATE TABLE IF NOT EXISTS memberships (
                claim TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                cell TEXT NOT NULL REFERENCES cells(id) ON DELETE CASCADE,
                PRIMARY KEY(claim,cell));
            CREATE INDEX IF NOT EXISTS memberships_cell ON memberships(cell);
            CREATE TABLE IF NOT EXISTS lifecycle_audit (
                revision INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TEMP TABLE IF NOT EXISTS wm_ids (id TEXT PRIMARY KEY, idx INTEGER NOT NULL);
        ''')

    def _limits(self):
        return resolve_limits(self._limit_overrides)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _transaction(self, write=False):
        if self.db.in_transaction:
            yield
            return
        self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def _metadata(self):
        data = {row['key']: json.loads(row['value']) for row in self.db.execute('SELECT * FROM metadata')}
        if 'coordinate_system' not in data:
            raise ValueError('Initialize spatial store first')
        return data

    def _definitions(self):
        if self._definitions_cache is None:
            definitions = {r['name']: json.loads(r['payload']) for r in self.db.execute('SELECT * FROM fields ORDER BY name')}
            if not definitions:
                return definitions
            self._definitions_cache = definitions
        return self._definitions_cache

    def _fill_ids(self, ids):
        """Load distinct IDs (in the given order) into the TEMP id table."""
        self.db.execute('DELETE FROM wm_ids')
        self.db.executemany('INSERT INTO wm_ids VALUES(?,?)', ((key, i) for i, key in enumerate(ids)))

    def _source(self, metadata=None):
        metadata = metadata or self._metadata()
        last = self.db.execute('SELECT revision,hash FROM lifecycle_audit ORDER BY revision DESC LIMIT 1').fetchone()
        return {'initial_hash': metadata['source_hash'], 'revision': last['revision'] if last else 0,
                'history_hash': last['hash'] if last else metadata['source_hash']}

    def _cell(self, key):
        row = self.db.execute('SELECT payload FROM cells WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Unknown support cell: ' + str(key))
        return json.loads(row['payload'])

    def _cell_row(self, cell, coordinate_kind, limits):
        if not isinstance(cell, dict) or set(cell) - {'id', 'measure', 'coordinates', 'tags', 'geometry'}:
            raise ValueError('Cell requires id, measure and optional coordinates/tags')
        identifier(cell['id'])
        _finite(cell['measure'], 'cell measure', True)
        tags = cell.get('tags', [])
        if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise ValueError('Cell tags must be strings')
        coords = cell.get('coordinates')
        if coords is not None:
            if not isinstance(coords, list) or len(coords) != 2:
                raise ValueError('Coordinates require two explicit values in declared axis order')
            for coordinate in coords:
                _finite(coordinate, 'coordinate')
            if coordinate_kind == 'geodetic' and (abs(coords[0]) > 90 or abs(coords[1]) > 180):
                raise ValueError('Latitude/longitude outside degree ranges')
        return (cell['id'], cell['measure'], *(coords or [None, None]), _json(cell, limits.spatial_max_row_bytes))

    def _geometry(self, cell, metadata, limits):
        geometry = cell.get('geometry')
        if geometry is None:
            return None
        from .spatial_geometry import validate_polygon, polygon_bounds
        validate_polygon(geometry, limits=limits)
        if {k: v for k, v in geometry['crs'].items() if k != 'id'} != metadata['coordinate_system'] or metadata.get('geometry_crs', geometry['crs']) != geometry['crs']:
            raise ValueError('Polygon CRS differs from store CRS')
        if 'geometry_crs' not in metadata:
            metadata['geometry_crs'] = geometry['crs']
            self.db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', ('geometry_crs', _json(geometry['crs'])))
        return (cell['id'], *polygon_bounds(geometry))

    def _insert_cell(self, cell):
        limits = self._limits()
        if not isinstance(cell, dict) or set(cell) - {'id', 'measure', 'coordinates', 'tags', 'geometry'}:
            raise ValueError('Cell requires id, measure and optional coordinates/tags')
        identifier(cell['id'])
        if self.db.execute('SELECT 1 FROM claims WHERE id=? OR claimant=? LIMIT 1', (cell['id'], cell['id'])).fetchone():
            raise ValueError('Support identifier aliases a claim or claimant')
        metadata = self._metadata()
        row = self._cell_row(cell, metadata['coordinate_system']['kind'], limits)
        geometry = self._geometry(cell, metadata, limits)
        self.db.execute('INSERT INTO cells VALUES(?,?,?,?,?)', row)
        if geometry is not None:
            self.db.execute('INSERT INTO cell_geometry VALUES(?,?,?,?,?)', geometry)

    def _validate_value(self, value, definition):
        if definition['value_type'] == 'vector':
            if not isinstance(value, list) or len(value) != definition['components']:
                raise ValueError('Field vector shape mismatch')
            for component in value:
                _finite(component, 'field vector component')
        else:
            _finite(value, 'field value')

    def _put_values(self, cell, values):
        definitions = self._definitions()
        if not isinstance(values, dict) or values.keys() != definitions.keys():
            raise ValueError('Values must explicitly cover every field')
        rows = []
        for name, value in values.items():
            self._validate_value(value, definitions[name])
            rows.append((cell, name, _json(value)))
        self.db.executemany('INSERT OR REPLACE INTO field_values VALUES(?,?,?)', rows)

    def _values(self, cell):
        return {r['field']: json.loads(r['value']) for r in self.db.execute(
            'SELECT field,value FROM field_values WHERE cell=? ORDER BY field', (cell,))}

    def _edge_row(self, edge, limits):
        if not isinstance(edge, dict) or set(edge) - {'id', 'source', 'target', 'conductance', 'transport_rate'}:
            raise ValueError('Unknown topology edge field')
        identifier(edge['id'])
        item = dict(edge)
        for name in ('conductance', 'transport_rate'):
            item[name] = _finite(item.get(name, 0), name)
            if item[name] < 0:
                raise ValueError('Topology coefficients must be nonnegative')
        return (edge['id'], edge['source'], edge['target'], _json(item, limits.spatial_max_row_bytes))

    def _insert_edge(self, edge):
        self.db.execute('INSERT INTO edges VALUES(?,?,?,?)', self._edge_row(edge, self._limits()))

    def initialize(self, config, *, coordinate_system):
        """Load explicit bounded input; signed/vector fields extend FieldWorld data."""
        limits = self._limits()
        if not isinstance(coordinate_system, dict) or set(coordinate_system) != {'kind', 'axes', 'unit'}:
            raise ValueError('Explicit coordinate system kind, axes and unit required')
        if coordinate_system['kind'] == 'geodetic':
            if coordinate_system['axes'] != ['latitude', 'longitude'] or coordinate_system['unit'] != 'degree':
                raise ValueError('Geodetic coordinates require latitude, longitude axes in degree units')
        elif coordinate_system['kind'] == 'cartesian':
            if coordinate_system['axes'] != ['x', 'y'] or not isinstance(coordinate_system['unit'], str) or not coordinate_system['unit']:
                raise ValueError('Cartesian coordinates require x,y axes and explicit unit')
        else:
            raise ValueError('Unsupported coordinate system')
        if not isinstance(config, dict) or set(config) - {'cells', 'edges', 'fields', 'claims', 'measure_unit', 'description'}:
            raise ValueError('Unknown spatial config fields')
        if not isinstance(config.get('measure_unit'), str) or not config['measure_unit']:
            raise ValueError('Explicit support measure_unit required')
        limits.integer('field_max_cells', len(config['cells']), 'Spatial import cells')
        limits.integer('field_max_fields', len(config['fields']), 'Spatial import fields')
        limits.check('field_max_edges', len(config.get('edges', [])), 'Spatial import storage budget exceeded: edges')
        limits.check('field_max_claims', len(config.get('claims', [])), 'Spatial import storage budget exceeded: claims')
        limits.check('field_max_values', len(config['cells']) * len(config['fields']), 'Spatial import storage budget exceeded: values')
        self._definitions_cache = None
        try:
            with self._transaction(write=True):
                if self.db.execute('SELECT 1 FROM metadata LIMIT 1').fetchone():
                    raise ValueError('Spatial store already initialized')
                for key, value in {'coordinate_system': coordinate_system, 'measure_unit': config['measure_unit'],
                                   'description': config.get('description', ''), 'source_hash': digest([config, coordinate_system])}.items():
                    self.db.execute('INSERT INTO metadata VALUES(?,?)', (key, _json(value)))
                metadata = self._metadata()
                kind = coordinate_system['kind']
                geometries = []

                def cell_rows():
                    for cell in config['cells']:
                        row = self._cell_row(cell, kind, limits)
                        geometry = self._geometry(cell, metadata, limits)
                        if geometry is not None:
                            geometries.append(geometry)
                        yield row

                self.db.executemany('INSERT INTO cells VALUES(?,?,?,?,?)', cell_rows())
                self.db.executemany('INSERT INTO cell_geometry VALUES(?,?,?,?,?)', geometries)
                ids = {c['id'] for c in config['cells']}
                definitions = {}
                for name, field in config['fields'].items():
                    if not isinstance(name, str) or not name or field['kind'] not in ('extensive', 'intensive') or not isinstance(field['unit'], str) or not field['unit']:
                        raise ValueError('Fields require name, kind and explicit unit')
                    if field['values'].keys() != ids:
                        raise ValueError('Every field must explicitly cover all cells')
                    definition = {key: field[key] for key in ('kind', 'unit')}
                    definition['value_type'] = field.get('value_type', 'scalar')
                    if definition['value_type'] == 'vector':
                        first = next(iter(field['values'].values()))
                        if not isinstance(first, list):
                            raise ValueError('Vector field values must be lists')
                        if not 1 <= len(first) <= 32:
                            raise ValueError('limit must be an integer in 1..32')
                        definition['components'] = len(first)
                    elif definition['value_type'] != 'scalar':
                        raise ValueError('Only signed scalar and vector storage supported')
                    self.db.execute('INSERT INTO fields VALUES(?,?)', (name, _json(definition)))
                    definitions[name] = definition

                def value_rows():
                    for cell in config['cells']:
                        key = cell['id']
                        for name, field in config['fields'].items():
                            value = field['values'][key]
                            self._validate_value(value, definitions[name])
                            yield key, name, _json(value)

                self.db.executemany('INSERT OR REPLACE INTO field_values VALUES(?,?,?)', value_rows())
                self.db.executemany('INSERT INTO edges VALUES(?,?,?,?)', (self._edge_row(edge, limits) for edge in config.get('edges', [])))
                memberships = 0
                claim_ids = {claim['id'] for claim in config.get('claims', [])}
                for claim in config.get('claims', []):
                    identifier(claim['id']); identifier(claim['claimant'])
                    if claim['id'] in ids or claim['claimant'] in ids or claim['claimant'] in claim_ids:
                        raise ValueError('Claim, claimant and support identifiers must be distinct')
                    if len(set(claim['cells'])) != len(claim['cells']):
                        raise ValueError('Duplicate claim membership')
                    memberships += len(claim['cells'])
                    limits.check('field_max_claim_memberships', memberships, 'Claim membership budget exceeded')
                    self.db.execute('INSERT INTO claims VALUES(?,?,?)', (claim['id'], claim['claimant'], _json({k: v for k, v in claim.items() if k != 'cells'})))
                    self.db.executemany('INSERT INTO memberships VALUES(?,?)', [(claim['id'], key) for key in claim['cells']])
        except sqlite3.IntegrityError as error:
            raise ValueError('Duplicate identifier or dangling topology/claim endpoint') from error
        finally:
            self._definitions_cache = None

    def bbox(self, bounds, *, limit=1000):
        """Inclusive bounded point query in declared axis order; returns truncation."""
        self._limits().integer('spatial_max_query_rows', limit, 'limit')
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError('Bounds require four coordinates')
        for value in bounds:
            _finite(value, 'bound')
        if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            raise ValueError('Bounds must be ordered; antimeridian wrapping requires two queries')
        with self._transaction():
            metadata = self._metadata()
            rows = self.db.execute('SELECT payload FROM cells WHERE x>=? AND y>=? AND x<=? AND y<=? ORDER BY x,y,id LIMIT ?',
                                   (*bounds, limit + 1)).fetchall()
            return {'cells': [json.loads(r['payload']) for r in rows[:limit]], 'truncated': len(rows) > limit,
                    'coordinate_system': metadata['coordinate_system']}

    def _geometry_query(self, bounds, predicate, *, crs, limit, max_candidates):
        from .spatial_geometry import validate_crs, validate_bounds
        limits = self._limits()
        limits.integer('spatial_max_query_rows', limit, 'limit')
        limits.integer('spatial_max_candidates', max_candidates, 'max_candidates')
        validate_bounds(bounds); validate_crs(crs)
        with self._transaction():
            if self._metadata().get('geometry_crs') != crs:
                raise ValueError('CRS mismatch or no declared polygon geometry')
            rows = self.db.execute("""SELECT c.payload FROM cell_geometry g JOIN cells c ON c.id=g.cell
                WHERE g.xmax>=? AND g.ymax>=? AND g.xmin<=? AND g.ymin<=?
                ORDER BY c.id LIMIT ?""", (*bounds, max_candidates+1)).fetchall()
            matches = []
            for row in rows[:max_candidates]:
                cell = json.loads(row['payload'])
                if predicate(cell):
                    matches.append(cell)
                    if len(matches)>limit:
                        break
            return {'cells':matches[:limit], 'truncated':len(matches)>limit or len(rows)>max_candidates,
                    'candidate_truncated':len(rows)>max_candidates, 'crs':deepcopy(crs)}

    def geometry_bbox(self, bounds, *, crs, limit=1000, max_candidates=10000):
        """Planar polygon intersection; independent of the point bbox query."""
        from .spatial_geometry import polygon_intersects_bbox
        return self._geometry_query(bounds, lambda c: polygon_intersects_bbox(c['geometry'],bounds,crs=crs),
                                    crs=crs,limit=limit,max_candidates=max_candidates)

    def containing(self, point, *, crs, limit=1000, max_candidates=10000):
        from .spatial_geometry import point_in_polygon, _point
        _point(point)
        return self._geometry_query(list(point)+list(point), lambda c: point_in_polygon(point,c['geometry'],crs=crs),
                                    crs=crs,limit=limit,max_candidates=max_candidates)

    def geometry_neighbors(self, cell, *, crs, limit=1000, max_candidates=10000):
        from .spatial_geometry import polygon_bounds, polygons_adjacent
        with self._transaction():
            geometry = self._cell(cell).get('geometry')
            if geometry is None:
                raise ValueError('Cell has no polygon geometry')
            return self._geometry_query(polygon_bounds(geometry), lambda c: c['id'] != cell and polygons_adjacent(geometry,c['geometry'],crs=crs),
                                        crs=crs,limit=limit,max_candidates=max_candidates)

    def neighborhood(self, cell, *, hops=1, limit=1000, max_edges=10000):
        """Undirected reachability over explicit edges, with bounded visited work."""
        limits = self._limits()
        limits.integer('spatial_max_query_rows', limit, 'limit')
        limits.integer('spatial_max_edge_rows', max_edges, 'max_edges')
        if type(hops) is not int or not 0 <= hops <= 20:
            raise ValueError('hops must be in 0..20')
        with self._transaction():
            self._cell(cell)
            seen, frontier, examined = {cell}, [cell], set()
            truncated = False
            for _ in range(hops):
                following = []
                for key in frontier:
                    remaining = max_edges - len(examined)
                    rows = self.db.execute('SELECT id,source,target FROM edges WHERE source=? UNION SELECT id,source,target FROM edges WHERE target=? ORDER BY id LIMIT ?',
                                           (key, key, max_edges + 1)).fetchall()
                    fresh = [row for row in rows if row['id'] not in examined]
                    if len(rows) > max_edges or len(fresh) > remaining:
                        truncated = True
                    for edge in fresh[:remaining]:
                        examined.add(edge['id'])
                        other = edge['target'] if edge['source'] == key else edge['source']
                        if other in seen:
                            continue
                        if len(seen) == limit:
                            truncated = True
                            continue
                        seen.add(other); following.append(other)
                    if truncated:
                        break
                frontier = following
                if truncated or not frontier:
                    break
            ordered = sorted(seen)
            self._fill_ids(ordered)
            payloads = {r['id']: json.loads(r['payload']) for r in self.db.execute('SELECT c.id,c.payload FROM wm_ids s JOIN cells c ON c.id=s.id')}
            return {'cells': [payloads[key] for key in ordered], 'truncated': truncated,
                    'edges_examined': len(examined), 'hops': hops}

    def _incident(self, ids, limit=None):
        limit = self._limits().spatial_max_edge_rows if limit is None else limit
        self._fill_ids(ids)
        rows = self.db.execute('SELECT payload FROM edges WHERE source IN (SELECT id FROM wm_ids) OR target IN (SELECT id FROM wm_ids) ORDER BY id LIMIT ?',
                               (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise ValueError(f'Incident topology exceeds edge query budget (limit spatial_max_edge_rows={limit})')
        return [json.loads(r['payload']) for r in rows]

    def select(self, *, cells=None, bounds=None, fields=None, limit=1000):
        """Return a detached bounded state with exact boundary/lineage metadata.

        Unlike bbox/neighborhood, selection fails rather than silently truncate
        a field domain. A cut domain contains only its induced internal edges.
        """
        limits = self._limits()
        limits.integer('spatial_max_query_rows', limit, 'limit')
        if cells is not None and bounds is not None:
            raise ValueError('Select cells or bounds, not both')
        with self._transaction():
            metadata = self._metadata()
            everything = False
            if bounds is not None:
                selection = self.bbox(bounds, limit=limit)
                if selection['truncated']:
                    raise ValueError('Selection exceeds cell limit')
                chosen = selection['cells']
            elif cells is None:
                rows = self.db.execute('SELECT payload FROM cells ORDER BY id LIMIT ?', (limit + 1,)).fetchall()
                if len(rows) > limit:
                    raise ValueError('Selection exceeds cell limit')
                chosen = [json.loads(row['payload']) for row in rows]
                everything = True
            else:
                if not isinstance(cells, list) or not 1 <= len(cells) <= limit or any(not isinstance(c, str) for c in cells) or len(set(cells)) != len(cells):
                    raise ValueError('Selection requires distinct bounded cell IDs')
                ordered = sorted(cells)
                self._fill_ids(ordered)
                found = {r['id']: r['payload'] for r in self.db.execute('SELECT c.id,c.payload FROM wm_ids s JOIN cells c ON c.id=s.id')}
                for key in ordered:
                    if key not in found:
                        raise ValueError('Unknown support cell: ' + str(key))
                chosen = [json.loads(found[key]) for key in ordered]
            if not chosen:
                raise ValueError('Selection contains no cells')
            ids = [cell['id'] for cell in chosen]
            definitions = self._definitions()
            if fields is None:
                fields = list(definitions)
            if not isinstance(fields, list) or not fields or any(not isinstance(f, str) for f in fields) or len(fields) != len(set(fields)) or not set(fields) <= set(definitions):
                raise ValueError('Select distinct existing fields')
            state = {'cells': chosen, 'edges': [], 'fields': {name: {**definitions[name], 'values': {}} for name in fields},
                     'claims': [], 'measure_unit': metadata['measure_unit'], 'description': metadata['description']}
            wanted = set(fields)
            stored = {name: {} for name in fields}
            if everything:
                cursor = self.db.execute('SELECT cell,field,value FROM field_values')
            else:
                self._fill_ids(ids)
                cursor = self.db.execute('SELECT v.cell,v.field,v.value FROM wm_ids s JOIN field_values v ON v.cell=s.id')
            for cell, name, value in cursor:
                if name in wanted:
                    stored[name][cell] = json.loads(value)
            for name in fields:
                source_values = stored[name]
                state['fields'][name]['values'] = {key: source_values[key] for key in ids}
            edge_limit = limits.spatial_max_edge_rows
            if everything:
                incident = [json.loads(r['payload']) for r in self.db.execute('SELECT payload FROM edges ORDER BY id LIMIT ?', (edge_limit + 1,))]
                if len(incident) > edge_limit:
                    raise ValueError(f'Incident topology exceeds edge query budget (limit spatial_max_edge_rows={edge_limit})')
                state['edges'] = incident
            else:
                incident = self._incident(ids, edge_limit)
                selected = set(ids)
                state['edges'] = [edge for edge in incident if edge['source'] in selected and edge['target'] in selected]
            membership_limit = limits.field_max_claim_memberships
            if everything:
                rows = self.db.execute('SELECT c.id,c.payload,m.cell FROM memberships m JOIN claims c ON c.id=m.claim ORDER BY c.id,m.cell LIMIT ?', (membership_limit + 1,)).fetchall()
            else:
                rows = self.db.execute('SELECT c.id,c.payload,m.cell FROM memberships m JOIN claims c ON c.id=m.claim WHERE m.cell IN (SELECT id FROM wm_ids) ORDER BY c.id,m.cell LIMIT ?', (membership_limit + 1,)).fetchall()
            if len(rows) > membership_limit:
                raise ValueError(f'Selected claim membership exceeds query budget (limit field_max_claim_memberships={membership_limit})')
            claims = {}
            for row in rows:
                claims.setdefault(row['id'], {**json.loads(row['payload']), 'cells': []})['cells'].append(row['cell'])
            state['claims'] = list(claims.values())
            source = {**self._source(metadata), 'selection_hash': digest(state)}
            return {'state': state, 'coordinate_system': metadata['coordinate_system'], 'source': source,
                    'selection': {'cells': len(ids), 'boundary_edges': len(incident) - len(state['edges']), 'truncated': False},
                    'limitations': ['Support coordinates are points, not polygon geometry.',
                                    'Selected edges form an induced topology; excluded boundaries are reported.',
                                    'Claims remain claims, not established sovereignty.']}

    def load_arrays(self, *, cells=None, fields=None, limit=None, backend=None, limits=None):
        """Load a domain as a FieldArrays core (ids sorted) without building dict state.

        Returns ids, core (reported values weighted into amounts), definitions,
        boundary_edges and lineage source. ``cells=None`` selects every cell and
        fails above ``limit`` (default limits.field_max_cells).
        """
        from .field_arrays import FieldArrays
        from .backends import resolve_backend
        limits = resolve_limits(limits if limits is not None else self._limit_overrides)
        limit = limits.field_max_cells if limit is None else limits.integer('field_max_cells', limit, 'limit')
        with self._transaction():
            metadata = self._metadata()
            everything = cells is None
            if everything:
                rows = self.db.execute('SELECT id,measure FROM cells ORDER BY id LIMIT ?', (limit + 1,)).fetchall()
                if len(rows) > limit:
                    raise ValueError('Selection exceeds cell limit')
                ids = [r[0] for r in rows]; measures = [r[1] for r in rows]
            else:
                if not isinstance(cells, list) or not 1 <= len(cells) <= limit or any(not isinstance(c, str) for c in cells) or len(set(cells)) != len(cells):
                    raise ValueError('Selection requires distinct bounded cell IDs')
                ids = sorted(cells)
                self._fill_ids(ids)
                found = dict(self.db.execute('SELECT c.id,c.measure FROM wm_ids s JOIN cells c ON c.id=s.id').fetchall())
                for key in ids:
                    if key not in found:
                        raise ValueError('Unknown support cell: ' + str(key))
                measures = [found[key] for key in ids]
            if not ids:
                raise ValueError('Selection contains no cells')
            index = {key: i for i, key in enumerate(ids)}
            definitions = self._definitions()
            names = list(definitions) if fields is None else fields
            if not isinstance(names, list) or not names or len(set(names)) != len(names) or not set(names) <= set(definitions):
                raise ValueError('Select distinct existing fields')
            limits.check('field_max_values', len(ids) * sum(definitions[n].get('components', 1) for n in names), 'Field component storage')
            columns = {name: [None] * len(ids) for name in names}
            if everything:
                cursor = self.db.execute('SELECT cell,field,value FROM field_values')
            else:
                cursor = self.db.execute('SELECT v.cell,v.field,v.value FROM wm_ids s JOIN field_values v ON v.cell=s.id')
            for cell, name, value in cursor:
                column = columns.get(name)
                if column is not None:
                    column[index[cell]] = _decode(value, definitions[name])
            if everything:
                cursor = self.db.execute("SELECT source,target,json_extract(payload,'$.conductance'),json_extract(payload,'$.transport_rate') FROM edges")
                internal = cursor.fetchall()
                boundary = 0
            else:
                cursor = self.db.execute("SELECT source,target,json_extract(payload,'$.conductance'),json_extract(payload,'$.transport_rate') FROM edges "
                                         "WHERE source IN (SELECT id FROM wm_ids) OR target IN (SELECT id FROM wm_ids)")
                incident = cursor.fetchall()
                internal = [row for row in incident if row[0] in index and row[1] in index]
                boundary = len(incident) - len(internal)
            limits.check('field_max_edges', len(internal), 'Selected edges')
            chosen = resolve_backend(backend, size=len(ids) + len(internal))
            core = FieldArrays(measures, [index[r[0]] for r in internal], [index[r[1]] for r in internal],
                               [r[2] or 0.0 for r in internal], [r[3] or 0.0 for r in internal], backend=chosen, validate=False)
            for name in names:
                column = columns[name]
                if any(v is None for v in column):
                    raise ValueError('Stored field values do not cover every selected cell')
                definition = definitions[name]
                core.add_field(name, definition['kind'], column, vector=definition['value_type'] == 'vector')
            return {'ids': ids, 'core': core, 'definitions': {n: definitions[n] for n in names},
                    'boundary_edges': boundary, 'source': self._source(metadata),
                    'coordinate_system': metadata['coordinate_system'], 'measure_unit': metadata['measure_unit']}

    def put_value_arrays(self, ids, core):
        """Persist reported values of every core field for ids (one executemany per field)."""
        definitions = self._definitions()
        if set(core.fields) != set(definitions):
            raise ValueError('Values must explicitly cover every field')
        if len(ids) != core.n:
            raise ValueError('Value arrays must match selected cells')
        with self._transaction(write=True):
            for name in core.fields:
                reported = core.reported(name)
                if core.backend == 'numpy':
                    reported = reported.tolist()
                if definitions[name]['value_type'] == 'vector':
                    rows = ((key, name, _ENCODE(value)) for key, value in zip(ids, reported))
                else:
                    rows = ((key, name, repr(value)) for key, value in zip(ids, reported))
                self.db.executemany('INSERT OR REPLACE INTO field_values VALUES(?,?,?)', rows)

    def materialize(self, *, cells=None, bounds=None, fields=None, limit=1000, allow_boundary_cut=False, evolution=None, backend=None):
        """Selected FieldWorld-compatible scalar state, optionally evolved in RAM."""
        from .fields import FieldWorld
        result = self.select(cells=cells, bounds=bounds, fields=fields, limit=limit)
        if result['selection']['boundary_edges'] and not allow_boundary_cut:
            raise ValueError('Selected domain cuts boundary edges; explicitly allow_boundary_cut for a closed-domain assumption')
        for field in result['state']['fields'].values():
            if field['value_type'] != 'scalar' or any(v < 0 for v in field['values'].values()):
                raise ValueError('FieldWorld evolution supports nonnegative scalar fields only; signed/vector storage has no implied diffusion solver')
        world = FieldWorld(result['state'], limits=self._limit_overrides, backend=backend)
        if evolution is not None:
            result['evolution'] = world.evolve(evolution)
            result['state'] = result['evolution']['state']
        result['limitations'].append('Evolution is nonnegative scalar constant-coefficient diffusion/transport; uncalibrated.')
        if result['selection']['boundary_edges']:
            result['limitations'].append('Caller explicitly treats the selected cut boundary as closed; external fluxes are omitted.')
        return result

    def geodetic_distance(self, source, target):
        """Great-circle sphere distance; never inferred from Cartesian coordinates."""
        with self._transaction():
            if self._metadata()['coordinate_system']['kind'] != 'geodetic':
                raise ValueError('Distance requires explicit geodetic latitude/longitude degree coordinates')
            a, b = self._cell(source).get('coordinates'), self._cell(target).get('coordinates')
            if a is None or b is None:
                raise ValueError('Distance requires explicit coordinates on both cells')
            lat1, lon1, lat2, lon2 = map(math.radians, [*a, *b])
            h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
            return {'distance_m': 2 * 6371008.8 * math.asin(math.sqrt(min(1, max(0, h)))),
                    'model': 'great-circle sphere, mean Earth radius 6371008.8 m; not ellipsoidal or road distance',
                    'coordinate_unit': 'degree'}

    def evolve_timeline(self, request, *, limits=None, backend=None):
        """Atomically persist bounded signed/vector evolution and dated lifecycle."""
        from .field_dynamics import evolve_spatial_timeline
        return evolve_spatial_timeline(self, request, limits=limits if limits is not None else self._limit_overrides, backend=backend)

    def _integrals(self, ids):
        definitions = self._definitions()
        ordered = list(dict.fromkeys(ids))
        self._fill_ids(ordered)
        measures = dict(self.db.execute('SELECT c.id,c.measure FROM wm_ids s JOIN cells c ON c.id=s.id').fetchall())
        for key in ordered:
            if key not in measures:
                raise ValueError('Unknown support cell: ' + str(key))
        values = {key: {} for key in ordered}
        for cell, name, value in self.db.execute('SELECT v.cell,v.field,v.value FROM wm_ids s JOIN field_values v ON v.cell=s.id'):
            values[cell][name] = json.loads(value)
        return {name: _sum([_scale(values[key][name], measures[key] if field['kind'] == 'intensive' else 1) for key in ordered])
                for name, field in definitions.items()}

    def _memberships(self, ids):
        limit = self._limits().field_max_claim_memberships
        self._fill_ids(list(dict.fromkeys(ids)))
        rows = self.db.execute('SELECT DISTINCT claim FROM memberships WHERE cell IN (SELECT id FROM wm_ids) LIMIT ?', (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise ValueError(f'Lifecycle claim budget exceeded (limit field_max_claim_memberships={limit})')
        return [r['claim'] for r in rows]

    def _conservation(self, before, after):
        result = {}
        for name in before:
            a, b = before[name], after[name]
            differences = [v - u for u, v in zip(a, b)] if isinstance(a, list) else b - a
            pairs = zip(a, b) if isinstance(a, list) else [(a, b)]
            if any(not math.isclose(u, v, rel_tol=1e-10, abs_tol=1e-9) for u, v in pairs):
                raise ValueError('Lifecycle integral conservation violated: ' + name)
            result[name] = {'before': a, 'after': b, 'difference': differences}
        return result

    def _event(self, event):
        limits = self._limits()
        kind = event.get('type')
        allowed = {'birth': {'type', 'cell', 'values', 'external_input'},
                   'death': {'type', 'cell', 'transfer_to'},
                   'merge': {'type', 'cells', 'cell'}, 'split': {'type', 'cell', 'children', 'edges'}}
        if kind not in allowed or set(event) - allowed[kind]:
            raise ValueError('Unsupported lifecycle event or option')
        if kind == 'birth':
            self._insert_cell(event['cell'])
            key = event['cell']['id']
            self._put_values(key, event['values'])
            integral = self._integrals([key])
            nonzero = any(any(v != 0 for v in value) if isinstance(value, list) else value != 0 for value in integral.values())
            if nonzero and event.get('external_input') is not True:
                raise ValueError('Nonzero birth requires explicit external_input=True')
            return {'type': kind, 'cell': key, 'external_input': integral}
        if kind == 'death':
            key = event['cell']; self._cell(key)
            target = event.get('transfer_to')
            if target == key:
                raise ValueError('Death transfer target must be distinct')
            before = self._integrals([key] + ([target] if target else []))
            if target:
                measure = self._cell(target)['measure']
                self._put_values(target, {name: _scale(value, 1 / measure if self._definitions()[name]['kind'] == 'intensive' else 1)
                                          for name, value in before.items()})
            elif any(any(v != 0 for v in value) if isinstance(value, list) else value != 0 for value in before.values()):
                raise ValueError('Nonzero death requires explicit transfer_to to conserve integrals')
            self.db.execute('DELETE FROM cells WHERE id=?', (key,))
            after = self._integrals([target]) if target else before
            return {'type': kind, 'cell': key, 'transfer_to': target, 'conservation': self._conservation(before, after)}
        if kind == 'merge':
            ids = event['cells']
            if not isinstance(ids, list) or len(ids) < 2 or len(set(ids)) != len(ids):
                raise ValueError(f'Merge requires 2..{limits.spatial_max_lifecycle_cells} distinct source cells')
            limits.check('spatial_max_lifecycle_cells', len(ids), 'Merge source cells')
            source = [self._cell(key) for key in ids]
            child = event['cell']
            if not math.isclose(_finite(child['measure'], 'merge measure', True), math.fsum(c['measure'] for c in source), rel_tol=1e-12, abs_tol=0):
                raise ValueError('Merge measure must equal summed source measures')
            before = self._integrals(ids)
            edges, claims = self._incident(ids), self._memberships(ids)
            self._insert_cell(child)
            self._put_values(child['id'], {name: _scale(value, 1 / child['measure'] if self._definitions()[name]['kind'] == 'intensive' else 1)
                                          for name, value in before.items()})
            self.db.executemany('DELETE FROM cells WHERE id=?', [(key,) for key in ids])
            merged = set(ids)
            for edge in edges:
                updated = {**edge, 'source': child['id'] if edge['source'] in merged else edge['source'],
                           'target': child['id'] if edge['target'] in merged else edge['target']}
                if updated['source'] != updated['target']:
                    self._insert_edge(updated)
            self.db.executemany('INSERT INTO memberships VALUES(?,?)', [(claim, child['id']) for claim in claims])
            return {'type': kind, 'cells': ids, 'cell': child['id'], 'conservation': self._conservation(before, self._integrals([child['id']]))}
        key = event['cell']; source = self._cell(key)
        children = event['children']
        if not isinstance(children, list) or not children:
            raise ValueError(f'Split requires 1..{limits.spatial_max_lifecycle_cells} explicit children')
        limits.check('spatial_max_lifecycle_cells', len(children), 'Split children')
        measures = [_finite(child['measure'], 'split measure', True) for child in children]
        if not math.isclose(math.fsum(measures), source['measure'], rel_tol=1e-12, abs_tol=0):
            raise ValueError('Split measures must conserve source measure')
        incident = self._incident([key])
        if incident and 'edges' not in event:
            raise ValueError('Split requires explicit replacement edges for incident topology')
        edges = event.get('edges', [])
        if not isinstance(edges, list):
            raise ValueError('Split replacement edges exceed budget')
        limits.check('spatial_max_edge_rows', len(edges), 'Split replacement edges exceed budget')
        before, values, claims = self._integrals([key]), self._values(key), self._memberships([key])
        limits.check('field_max_claim_memberships', len(children) * len(claims), 'Split claim membership expansion exceeds row budget')
        definitions = self._definitions()
        for child in children:
            self._insert_cell(child)
            self._put_values(child['id'], {name: _scale(value, child['measure'] / source['measure'] if definitions[name]['kind'] == 'extensive' else 1)
                                          for name, value in values.items()})
            self.db.executemany('INSERT INTO memberships VALUES(?,?)', [(claim, child['id']) for claim in claims])
        self.db.execute('DELETE FROM cells WHERE id=?', (key,))
        for edge in edges:
            self._insert_edge(edge)
        ids = [child['id'] for child in children]
        return {'type': kind, 'cell': key, 'children': ids, 'conservation': self._conservation(before, self._integrals(ids))}

    def apply(self, events):
        """Commit a whole lifecycle batch or roll it all back, including audit."""
        limits = self._limits()
        if not isinstance(events, list) or not events or any(not isinstance(event, dict) for event in events):
            raise ValueError(f'Lifecycle batch requires 1..{limits.spatial_max_lifecycle_batch} event objects')
        limits.check('spatial_max_lifecycle_batch', len(events), 'Lifecycle batch events')
        _json(events, limits.spatial_max_row_bytes)
        try:
            with self._transaction(write=True):
                metadata = self._metadata()
                reports = [self._event(event) for event in events]
                last = self.db.execute('SELECT hash FROM lifecycle_audit ORDER BY revision DESC LIMIT 1').fetchone()
                report = {'events': reports, 'requests': events, 'previous_hash': last['hash'] if last else metadata['source_hash']}
                checksum = digest(report)
                cursor = self.db.execute('INSERT INTO lifecycle_audit(payload,hash) VALUES(?,?)', (_json(report, limits.spatial_max_row_bytes), checksum))
                return {**report, 'revision': cursor.lastrowid, 'hash': checksum}
        except sqlite3.IntegrityError as error:
            raise ValueError('Duplicate identifier or dangling lifecycle topology/claim endpoint') from error

    def audit(self, *, after_revision=0, limit=100):
        self._limits().integer('spatial_max_query_rows', limit, 'limit')
        if type(after_revision) is not int or after_revision < 0:
            raise ValueError('after_revision must be nonnegative integer')
        rows = self.db.execute('SELECT * FROM lifecycle_audit WHERE revision>? ORDER BY revision LIMIT ?', (after_revision, limit)).fetchall()
        return [{**json.loads(row['payload']), 'revision': row['revision'], 'hash': row['hash']} for row in rows]
