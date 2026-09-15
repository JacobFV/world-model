"""Disk-backed explicit supports, topology and conservative lifecycle changes.

Coordinates are support locations, not inferred polygon boundaries. Bbox queries
use the declared axis order and inclusive point coordinates (no antimeridian
wrapping). Query limits bound rows brought into Python. Lifecycle batches use a
single SQLite transaction; foreign keys prevent dangling topology/membership.
Signed scalars and vectors can be stored and conserved componentwise. Evolution
deliberately delegates only nonnegative scalar selections to FieldWorld.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import sqlite3

from .model import identifier
from .util import canonical, digest


def _finite(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{label} must be finite' + (' and positive' if positive else ''))
    return value


def _limit(value, maximum=1000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'limit must be an integer in 1..{maximum}')
    return value


def _json(value):
    payload = canonical(value)
    if len(payload) > 1_000_000:
        raise ValueError('spatial row exceeds 1 MB payload bound')
    return payload.decode('utf-8')


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

    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cells (
                id TEXT PRIMARY KEY, measure REAL NOT NULL CHECK(measure > 0),
                x REAL, y REAL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS cells_coordinates ON cells(x,y,id);
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
        ''')

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
        return {r['name']: json.loads(r['payload']) for r in self.db.execute('SELECT * FROM fields ORDER BY name')}

    def _cell(self, key):
        row = self.db.execute('SELECT payload FROM cells WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Unknown support cell: ' + str(key))
        return json.loads(row['payload'])

    def _insert_cell(self, cell):
        if not isinstance(cell, dict) or set(cell) - {'id', 'measure', 'coordinates', 'tags'}:
            raise ValueError('Cell requires id, measure and optional coordinates/tags')
        identifier(cell['id'])
        if self.db.execute('SELECT 1 FROM claims WHERE id=? OR claimant=? LIMIT 1', (cell['id'], cell['id'])).fetchone():
            raise ValueError('Support identifier aliases a claim or claimant')
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
            if self._metadata()['coordinate_system']['kind'] == 'geodetic' and (abs(coords[0]) > 90 or abs(coords[1]) > 180):
                raise ValueError('Latitude/longitude outside degree ranges')
        self.db.execute('INSERT INTO cells VALUES(?,?,?,?,?)',
                        (cell['id'], cell['measure'], *(coords or [None, None]), _json(cell)))

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
        if not isinstance(values, dict) or set(values) != set(definitions):
            raise ValueError('Values must explicitly cover every field')
        for name, value in values.items():
            self._validate_value(value, definitions[name])
            self.db.execute('INSERT OR REPLACE INTO field_values VALUES(?,?,?)', (cell, name, _json(value)))

    def _values(self, cell):
        return {r['field']: json.loads(r['value']) for r in self.db.execute(
            'SELECT field,value FROM field_values WHERE cell=? ORDER BY field', (cell,))}

    def _insert_edge(self, edge):
        if not isinstance(edge, dict) or set(edge) - {'id', 'source', 'target', 'conductance', 'transport_rate'}:
            raise ValueError('Unknown topology edge field')
        identifier(edge['id'])
        item = deepcopy(edge)
        for name in ('conductance', 'transport_rate'):
            item[name] = _finite(item.get(name, 0), name)
            if item[name] < 0:
                raise ValueError('Topology coefficients must be nonnegative')
        self.db.execute('INSERT INTO edges VALUES(?,?,?,?)', (edge['id'], edge['source'], edge['target'], _json(item)))

    def initialize(self, config, *, coordinate_system):
        """Load explicit bounded input; signed/vector fields extend FieldWorld data."""
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
        _limit(len(config['cells']), 100000)
        _limit(len(config['fields']), 100)
        if len(config.get('edges', [])) > 100000 or len(config.get('claims', [])) > 10000 or len(config['cells']) * len(config['fields']) > 1000000:
            raise ValueError('Spatial import storage budget exceeded')
        try:
            with self._transaction(write=True):
                if self.db.execute('SELECT 1 FROM metadata LIMIT 1').fetchone():
                    raise ValueError('Spatial store already initialized')
                for key, value in {'coordinate_system': coordinate_system, 'measure_unit': config['measure_unit'],
                                   'description': config.get('description', ''), 'source_hash': digest([config, coordinate_system])}.items():
                    self.db.execute('INSERT INTO metadata VALUES(?,?)', (key, _json(value)))
                for cell in config['cells']:
                    self._insert_cell(cell)
                ids = {c['id'] for c in config['cells']}
                for name, field in config['fields'].items():
                    if not isinstance(name, str) or not name or field['kind'] not in ('extensive', 'intensive') or not isinstance(field['unit'], str) or not field['unit']:
                        raise ValueError('Fields require name, kind and explicit unit')
                    if set(field['values']) != ids:
                        raise ValueError('Every field must explicitly cover all cells')
                    definition = {key: field[key] for key in ('kind', 'unit')}
                    definition['value_type'] = field.get('value_type', 'scalar')
                    if definition['value_type'] == 'vector':
                        first = next(iter(field['values'].values()))
                        if not isinstance(first, list):
                            raise ValueError('Vector field values must be lists')
                        definition['components'] = _limit(len(first), 32)
                    elif definition['value_type'] != 'scalar':
                        raise ValueError('Only signed scalar and vector storage supported')
                    self.db.execute('INSERT INTO fields VALUES(?,?)', (name, _json(definition)))
                for cell in config['cells']:
                    self._put_values(cell['id'], {name: f['values'][cell['id']] for name, f in config['fields'].items()})
                for edge in config.get('edges', []):
                    self._insert_edge(edge)
                memberships = 0
                claim_ids = {claim['id'] for claim in config.get('claims', [])}
                for claim in config.get('claims', []):
                    identifier(claim['id']); identifier(claim['claimant'])
                    if claim['id'] in ids or claim['claimant'] in ids or claim['claimant'] in claim_ids:
                        raise ValueError('Claim, claimant and support identifiers must be distinct')
                    if len(set(claim['cells'])) != len(claim['cells']):
                        raise ValueError('Duplicate claim membership')
                    memberships += len(claim['cells'])
                    if memberships > 1000000:
                        raise ValueError('Claim membership budget exceeded')
                    self.db.execute('INSERT INTO claims VALUES(?,?,?)', (claim['id'], claim['claimant'], _json({k: v for k, v in claim.items() if k != 'cells'})))
                    self.db.executemany('INSERT INTO memberships VALUES(?,?)', [(claim['id'], key) for key in claim['cells']])
        except sqlite3.IntegrityError as error:
            raise ValueError('Duplicate identifier or dangling topology/claim endpoint') from error

    def bbox(self, bounds, *, limit=1000):
        """Inclusive bounded point query in declared axis order; returns truncation."""
        _limit(limit)
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

    def neighborhood(self, cell, *, hops=1, limit=1000, max_edges=10000):
        """Undirected reachability over explicit edges, with bounded visited work."""
        _limit(limit); _limit(max_edges, 10000)
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
                    rows = self.db.execute('SELECT id,source,target FROM edges WHERE source=? OR target=? ORDER BY id LIMIT ?',
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
            return {'cells': [self._cell(key) for key in sorted(seen)], 'truncated': truncated,
                    'edges_examined': len(examined), 'hops': hops}

    def _incident(self, ids, limit=10000):
        marks = ','.join('?' for _ in ids)
        rows = self.db.execute(f'SELECT payload FROM edges WHERE source IN ({marks}) OR target IN ({marks}) ORDER BY id LIMIT ?',
                               (*ids, *ids, limit + 1)).fetchall()
        if len(rows) > limit:
            raise ValueError('Incident topology exceeds edge query budget')
        return [json.loads(r['payload']) for r in rows]

    def select(self, *, cells=None, bounds=None, fields=None, limit=1000):
        """Return a detached bounded state with exact boundary/lineage metadata.

        Unlike bbox/neighborhood, selection fails rather than silently truncate
        a field domain. A cut domain contains only its induced internal edges.
        """
        _limit(limit)
        if cells is not None and bounds is not None:
            raise ValueError('Select cells or bounds, not both')
        with self._transaction():
            metadata = self._metadata()
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
            else:
                if not isinstance(cells, list) or not 1 <= len(cells) <= limit or any(not isinstance(c, str) for c in cells) or len(set(cells)) != len(cells):
                    raise ValueError('Selection requires distinct bounded cell IDs')
                chosen = [self._cell(key) for key in sorted(cells)]
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
            for key in ids:
                values = self._values(key)
                for name in fields:
                    state['fields'][name]['values'][key] = values[name]
            incident = self._incident(ids)
            selected = set(ids)
            state['edges'] = [edge for edge in incident if edge['source'] in selected and edge['target'] in selected]
            marks = ','.join('?' for _ in ids)
            rows = self.db.execute(f'SELECT c.id,c.payload,m.cell FROM memberships m JOIN claims c ON c.id=m.claim WHERE m.cell IN ({marks}) ORDER BY c.id,m.cell LIMIT 10001', ids).fetchall()
            if len(rows) > 10000:
                raise ValueError('Selected claim membership exceeds query budget')
            claims = {}
            for row in rows:
                claims.setdefault(row['id'], {**json.loads(row['payload']), 'cells': []})['cells'].append(row['cell'])
            state['claims'] = list(claims.values())
            last = self.db.execute('SELECT revision,hash FROM lifecycle_audit ORDER BY revision DESC LIMIT 1').fetchone()
            source = {'initial_hash': metadata['source_hash'], 'revision': last['revision'] if last else 0,
                      'history_hash': last['hash'] if last else metadata['source_hash'], 'selection_hash': digest(state)}
            return {'state': state, 'coordinate_system': metadata['coordinate_system'], 'source': source,
                    'selection': {'cells': len(ids), 'boundary_edges': len(incident) - len(state['edges']), 'truncated': False},
                    'limitations': ['Support coordinates are points, not polygon geometry.',
                                    'Selected edges form an induced topology; excluded boundaries are reported.',
                                    'Claims remain claims, not established sovereignty.']}

    def materialize(self, *, cells=None, bounds=None, fields=None, limit=1000, allow_boundary_cut=False, evolution=None):
        """Selected FieldWorld-compatible scalar state, optionally evolved in RAM."""
        from .fields import FieldWorld
        result = self.select(cells=cells, bounds=bounds, fields=fields, limit=limit)
        if result['selection']['boundary_edges'] and not allow_boundary_cut:
            raise ValueError('Selected domain cuts boundary edges; explicitly allow_boundary_cut for a closed-domain assumption')
        for field in result['state']['fields'].values():
            if field['value_type'] != 'scalar' or any(v < 0 for v in field['values'].values()):
                raise ValueError('FieldWorld evolution supports nonnegative scalar fields only; signed/vector storage has no implied diffusion solver')
        world = FieldWorld(result['state'])
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

    def _integrals(self, ids):
        definitions = self._definitions()
        values = {key: self._values(key) for key in ids}
        measures = {key: self._cell(key)['measure'] for key in ids}
        return {name: _sum([_scale(values[key][name], measures[key] if field['kind'] == 'intensive' else 1) for key in ids])
                for name, field in definitions.items()}

    def _memberships(self, ids):
        marks = ','.join('?' for _ in ids)
        rows = self.db.execute(f'SELECT DISTINCT claim FROM memberships WHERE cell IN ({marks}) LIMIT 10001', ids).fetchall()
        if len(rows) > 10000:
            raise ValueError('Lifecycle claim budget exceeded')
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
            if not isinstance(ids, list) or not 2 <= len(ids) <= 1000 or len(set(ids)) != len(ids):
                raise ValueError('Merge requires 2..1000 distinct source cells')
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
            for edge in edges:
                updated = {**edge, 'source': child['id'] if edge['source'] in ids else edge['source'],
                           'target': child['id'] if edge['target'] in ids else edge['target']}
                if updated['source'] != updated['target']:
                    self._insert_edge(updated)
            self.db.executemany('INSERT INTO memberships VALUES(?,?)', [(claim, child['id']) for claim in claims])
            return {'type': kind, 'cells': ids, 'cell': child['id'], 'conservation': self._conservation(before, self._integrals([child['id']]))}
        key = event['cell']; source = self._cell(key)
        children = event['children']
        if not isinstance(children, list) or not 1 <= len(children) <= 1000:
            raise ValueError('Split requires 1..1000 explicit children')
        measures = [_finite(child['measure'], 'split measure', True) for child in children]
        if not math.isclose(math.fsum(measures), source['measure'], rel_tol=1e-12, abs_tol=0):
            raise ValueError('Split measures must conserve source measure')
        incident = self._incident([key])
        if incident and 'edges' not in event:
            raise ValueError('Split requires explicit replacement edges for incident topology')
        edges = event.get('edges', [])
        if not isinstance(edges, list) or len(edges) > 10000:
            raise ValueError('Split replacement edges exceed budget')
        before, values, claims = self._integrals([key]), self._values(key), self._memberships([key])
        if len(children) * len(claims) > 100000:
            raise ValueError('Split claim membership expansion exceeds 100000 row budget')
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
        if not isinstance(events, list) or not 1 <= len(events) <= 100 or any(not isinstance(event, dict) for event in events):
            raise ValueError('Lifecycle batch requires 1..100 event objects')
        _json(events)
        try:
            with self._transaction(write=True):
                metadata = self._metadata()
                reports = [self._event(event) for event in events]
                last = self.db.execute('SELECT hash FROM lifecycle_audit ORDER BY revision DESC LIMIT 1').fetchone()
                report = {'events': reports, 'requests': events, 'previous_hash': last['hash'] if last else metadata['source_hash']}
                checksum = digest(report)
                cursor = self.db.execute('INSERT INTO lifecycle_audit(payload,hash) VALUES(?,?)', (_json(report), checksum))
                return {**report, 'revision': cursor.lastrowid, 'hash': checksum}
        except sqlite3.IntegrityError as error:
            raise ValueError('Duplicate identifier or dangling lifecycle topology/claim endpoint') from error

    def audit(self, *, after_revision=0, limit=100):
        _limit(limit)
        if type(after_revision) is not int or after_revision < 0:
            raise ValueError('after_revision must be nonnegative integer')
        rows = self.db.execute('SELECT * FROM lifecycle_audit WHERE revision>? ORDER BY revision LIMIT ?', (after_revision, limit)).fetchall()
        return [{**json.loads(row['payload']), 'revision': row['revision'], 'hash': row['hash']} for row in rows]
