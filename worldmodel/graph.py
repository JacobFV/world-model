"""Rebuildable disk-backed index. Claims remain evidence, including contradictions.

Schema 3 adds an ``edges`` table (one row per entity-to-entity assertion, with weight and
both time axes), a ``resolved`` table mapping entity IDs to canonical cluster IDs from an
auditable resolution view, and bounded traversal/aggregate queries. Indexes are created
after batch loading. Schema-2 indexes remain readable for the original queries.
"""
from collections import deque
import json
import math
import os
from pathlib import Path
import sqlite3
import uuid
import zlib
from .model import instant
from .util import canonical

SCHEMA_VERSION = '3'
READABLE_SCHEMAS = ('2', '3')


def time_key(value):
    return instant(value).isoformat() if value else None


def edge_weight(record):
    """Edge weight: record.weight, attributes.weight, match.score, confidence, else 1.0."""
    for value in (record.get('weight'), (record.get('attributes') or {}).get('weight'),
                  (record.get('match') or {}).get('score'), record.get('confidence')):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return 1.0


class Graph:
    def __init__(self, path):
        self.path = Path(path)

    # -- building -------------------------------------------------------------------------
    def build(self, store, refs, *, batch_size=50000):
        if not refs:
            raise ValueError('Graph build requires at least one version')
        if len({(ref['dataset'],ref.get('stage','final')) for ref in refs}) != len(refs):
            raise ValueError('Select one version per dataset stage for a graph snapshot')

        def groups():
            for ref in refs:
                store.verify(ref)
                yield ref, store.records(ref, verify=False)

        result = self._build(groups(), refs, batch_size=batch_size, after=lambda: [store.verify(ref) for ref in refs])
        return result

    def build_from_records(self, groups, *, batch_size=50000, validate=True, cache_mb=None, compress_bodies=False):
        """Build from [(ref, iterable_of_records)] without a Store (e.g. resolution outputs, scale tests)."""
        groups = list(groups)
        refs = [ref for ref, _ in groups]
        if not refs:
            raise ValueError('Graph build requires at least one input')
        if validate:
            from .model import validate_record
            groups = [(ref, (validate_record(r) for r in records)) for ref, records in groups]
        return self._build(groups, refs, batch_size=batch_size, cache_mb=cache_mb, compress_bodies=compress_bodies)

    def _build(self, groups, refs, *, batch_size, after=None, cache_mb=None, compress_bodies=False):
        """``cache_mb`` bounds the SQLite page cache; the 2 MiB default thrashes on catalog-scale loads.

        ``compress_bodies`` stores each record body as a deflated BLOB instead of text. Record
        text dominates a catalog-scale index (roughly 0.6 KB per record), and ``_decode``
        transparently reads either form, so indexes built either way stay queryable.
        """
        if cache_mb is not None and not 1 <= cache_mb <= 65536:
            raise ValueError('cache_mb must be in 1..65536')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        connection = sqlite3.connect(temporary)
        count = edges = 0
        try:
            connection.execute('PRAGMA journal_mode=OFF')
            connection.execute('PRAGMA synchronous=OFF')
            if cache_mb is not None:
                connection.execute('PRAGMA cache_size=%d' % -(cache_mb * 1024))
                connection.execute('PRAGMA temp_store=FILE')
            connection.executescript('''
                CREATE TABLE records (
                    dataset TEXT, stage TEXT, version TEXT, input_ref TEXT, id TEXT, entity_id TEXT, kind TEXT, metric TEXT,
                    subject TEXT, object TEXT, observed_at TEXT, valid_from TEXT,
                    valid_to TEXT, body TEXT, PRIMARY KEY(dataset,stage,version,id));
                CREATE TABLE edges (subject TEXT, predicate TEXT, object TEXT, weight REAL, valid_from TEXT, valid_to TEXT,
                    observed_at TEXT, record_rowid INTEGER);
                CREATE TABLE resolved (entity_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, cluster_size INTEGER);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            ''')
            body = (lambda raw: zlib.compress(raw, 1)) if compress_bodies else (lambda raw: raw.decode())
            with connection:
                for ref in refs:
                    pass
                for ref, records in groups:
                    input_ref = canonical(ref).decode()
                    rows, edge_rows = [], []
                    for record in records:
                        rows.append((ref['dataset'], ref.get('stage', 'final'), ref['version'], input_ref, record['id'],
                                     record.get('entity_id', record['id']) if record['kind'] == 'entity' else None,
                                     record['kind'], record.get('metric'), record.get('subject'), record.get('object'),
                                     time_key(record['observed_at']), time_key(record.get('valid_from')),
                                     time_key(record.get('valid_to')), body(canonical(record))))
                        if record['kind'] == 'assertion' and record.get('object') and record.get('subject'):
                            edge_rows.append((len(rows) - 1, record))
                        if len(rows) >= batch_size:
                            edges += self._flush(connection, rows, edge_rows)
                            count += len(rows)
                            rows, edge_rows = [], []
                    edges += self._flush(connection, rows, edge_rows)
                    count += len(rows)
                connection.executescript('''
                    CREATE INDEX subject_idx ON records(subject);
                    CREATE INDEX object_idx ON records(object);
                    CREATE INDEX entity_idx ON records(entity_id);
                    CREATE INDEX metric_idx ON records(kind,metric);
                    CREATE INDEX edge_subject_idx ON edges(subject, predicate);
                    CREATE INDEX edge_object_idx ON edges(object, predicate);
                    CREATE INDEX edge_predicate_idx ON edges(predicate);
                    CREATE INDEX resolved_canonical_idx ON resolved(canonical_id);
                ''')
                connection.execute('INSERT INTO metadata VALUES (?,?)', ('inputs', canonical(refs).decode()))
                connection.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', SCHEMA_VERSION))
            if after:
                after()
            connection.close()
            os.replace(temporary, self.path)
            return {'records': count, 'edges': edges, 'inputs': refs, 'path': str(self.path)}
        finally:
            connection.close()
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _flush(connection, rows, edge_rows):
        if not rows:
            return 0
        cursor = connection.execute('SELECT COALESCE(MAX(rowid), 0) FROM records')
        base = cursor.fetchone()[0]
        connection.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        # rowids are assigned sequentially for appended rows in a fresh table without deletes.
        connection.executemany('INSERT INTO edges VALUES (?,?,?,?,?,?,?,?)', [
            (r['subject'], r['predicate'], r['object'], edge_weight(r), time_key(r.get('valid_from')),
             time_key(r.get('valid_to')), time_key(r['observed_at']), base + 1 + offset) for offset, r in edge_rows])
        return len(edge_rows)

    def attach_resolution(self, clusters, *, view):
        """Store canonical IDs from a resolution view (e.g. ResolutionEngine.clusters()); replaces previous resolution."""
        if not isinstance(view, dict) or not view.get('view_digest'):
            raise ValueError('attach_resolution requires the audited resolution view (with view_digest)')
        connection = self._connect(writable=True, require='3')
        try:
            with connection:
                connection.execute('DELETE FROM resolved')
                total = 0
                batch = []
                for cluster in clusters:
                    members = sorted(set(cluster['members']))
                    canonical_id = cluster['canonical_id']
                    if canonical_id not in members:
                        raise ValueError('canonical_id must be a cluster member')
                    batch.extend((m, canonical_id, len(members)) for m in members)
                    if len(batch) >= 50000:
                        connection.executemany('INSERT INTO resolved VALUES (?,?,?)', batch)
                        total += len(batch)
                        batch = []
                connection.executemany('INSERT INTO resolved VALUES (?,?,?)', batch)
                total += len(batch)
                connection.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('resolution', canonical(
                    {k: view[k] for k in ('view_digest', 'input_digest', 'model_digest', 'policy') if k in view}).decode()))
            return {'resolved_entities': total, 'view_digest': view['view_digest']}
        finally:
            connection.close()

    # -- connections ------------------------------------------------------------------------
    def _connect(self, writable=False, require=None):
        if not self.path.is_file():
            raise ValueError('Graph index missing; run graph-build first')
        uri = self.path.resolve().as_uri() + ('' if writable else '?mode=ro')
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if row is None or row['value'] not in READABLE_SCHEMAS:
                raise ValueError('Graph index schema changed; run graph-build to rebuild')
            if require and row['value'] < require:
                raise ValueError('This query needs graph schema ' + require + '; run graph-build to rebuild')
        except (sqlite3.Error, ValueError) as error:
            connection.close()
            raise ValueError('Graph index schema unsupported; run graph-build to rebuild') from error
        return connection

    @staticmethod
    def _filters(valid_at, known_at):
        clauses, args = [], []
        if valid_at:
            clauses.extend(['(valid_from IS NULL OR valid_from <= ?)', '(valid_to IS NULL OR valid_to > ?)'])
            args.extend([time_key(valid_at)] * 2)
        if known_at:
            clauses.append('observed_at <= ?')
            args.append(time_key(known_at))
        return ''.join(' AND ' + clause for clause in clauses), args

    @staticmethod
    def _decode(row):
        body = row['body']
        return {**json.loads(zlib.decompress(body) if isinstance(body, bytes) else body), '_provenance': {
            'input': json.loads(row['input_ref']), 'record_id': row['id']}}

    # -- original queries (unchanged contracts) -------------------------------------------------
    def neighbors(self, entity, hops=1, limit=100, valid_at=None, known_at=None):
        if not 1 <= hops <= 6 or not 1 <= limit <= 1000:
            raise ValueError('hops must be 1..6 and limit 1..1000')
        from .model import identifier
        identifier(entity)
        suffix, time_args = self._filters(valid_at, known_at)
        visited, frontier, claims = {entity}, {entity}, {}
        truncated = False
        connection = self._connect()
        try:
            for _ in range(hops):
                next_frontier = set()
                for node in sorted(frontier):
                    rows = connection.execute(
                        "SELECT * FROM records WHERE kind='assertion' AND (subject=? OR object=?)" + suffix
                        + ' ORDER BY dataset,stage,version,id LIMIT ?', [node, node, *time_args, limit + 1])
                    for row in rows:
                        key = (row['dataset'], row['stage'], row['version'], row['id'])
                        if key in claims:
                            continue
                        if len(claims) == limit:
                            truncated = True
                            break
                        claims[key] = self._decode(row)
                        for endpoint in (row['subject'], row['object']):
                            if endpoint and endpoint not in visited:
                                next_frontier.add(endpoint)
                    if truncated:
                        break
                visited.update(next_frontier)
                frontier = next_frontier
                if truncated or not frontier:
                    break
            entities = []
            for node in sorted(visited):
                rows = connection.execute("SELECT * FROM records WHERE kind='entity' AND entity_id=?" + suffix
                                          + ' ORDER BY dataset,stage,version,id LIMIT ?', [node, *time_args, limit + 1])
                for row in rows:
                    if len(entities) == limit:
                        truncated = True
                        break
                    entities.append(self._decode(row))
                if len(entities) == limit:
                    break
            return {'root': entity, 'entities': entities, 'assertions': list(claims.values()),
                    'truncated': truncated, 'hops': hops}
        finally:
            connection.close()

    def observations(self, metric, limit=100, valid_at=None, known_at=None):
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be 1..1000')
        suffix, args = self._filters(valid_at, known_at)
        connection = self._connect()
        try:
            return [self._decode(row) for row in connection.execute(
                "SELECT * FROM records WHERE kind='observation' AND metric=?" + suffix
                + ' ORDER BY dataset,stage,version,id LIMIT ?', [metric, *args, limit])]
        finally:
            connection.close()

    # -- resolved entities ----------------------------------------------------------------------
    @staticmethod
    def _canonical(connection, entity):
        row = connection.execute('SELECT canonical_id FROM resolved WHERE entity_id=?', (entity,)).fetchone()
        return row['canonical_id'] if row else entity

    @staticmethod
    def _members(connection, canonical_id):
        rows = [r['entity_id'] for r in connection.execute(
            'SELECT entity_id FROM resolved WHERE canonical_id=? ORDER BY entity_id', (canonical_id,))]
        return rows or [canonical_id]

    def resolution(self):
        connection = self._connect(require='3')
        try:
            row = connection.execute("SELECT value FROM metadata WHERE key='resolution'").fetchone()
            counts = connection.execute('SELECT COUNT(*) AS entities, COUNT(DISTINCT canonical_id) AS clusters FROM resolved').fetchone()
            return {'view': json.loads(row['value']) if row else None, 'entities': counts['entities'], 'clusters': counts['clusters']}
        finally:
            connection.close()

    def resolved_entity(self, entity, *, limit=100, valid_at=None, known_at=None):
        """Canonical ID, cluster members and their entity records; source records are unchanged."""
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be 1..1000')
        suffix, args = self._filters(valid_at, known_at)
        connection = self._connect(require='3')
        try:
            canonical_id = self._canonical(connection, entity)
            members = self._members(connection, canonical_id)
            records = []
            for member in members:
                for row in connection.execute("SELECT * FROM records WHERE kind='entity' AND entity_id=?" + suffix
                                              + ' ORDER BY dataset,stage,version,id LIMIT ?', [member, *args, limit - len(records)]):
                    records.append(self._decode(row))
                if len(records) >= limit:
                    break
            view = connection.execute("SELECT value FROM metadata WHERE key='resolution'").fetchone()
            return {'entity': entity, 'canonical_id': canonical_id, 'members': members, 'entities': records,
                    'truncated': len(records) >= limit, 'resolution': json.loads(view['value']) if view else None}
        finally:
            connection.close()

    # -- scalable traversal -------------------------------------------------------------------------
    def _edge_query(self, predicates, min_weight, valid_at, known_at):
        suffix, args = self._filters(valid_at, known_at)
        if predicates:
            suffix += ' AND predicate IN (%s)' % ','.join('?' * len(predicates))
            args.extend(predicates)
        if min_weight is not None:
            suffix += ' AND weight >= ?'
            args.append(float(min_weight))
        return suffix, args

    def _incident(self, connection, nodes, direction, suffix, args, limit):
        nodes = list(nodes)
        out = []
        for start in range(0, len(nodes), 500):
            chunk = nodes[start:start + 500]
            marks = ','.join('?' * len(chunk))
            parts = []
            if direction in ('out', 'both'):
                parts.append(f'SELECT e.*, e.rowid AS eid FROM edges e WHERE subject IN ({marks})' + suffix)
            if direction in ('in', 'both'):
                parts.append(f'SELECT e.*, e.rowid AS eid FROM edges e WHERE object IN ({marks})' + suffix)
            query = ' UNION '.join(parts) + ' ORDER BY eid LIMIT ?'
            params = []
            for _ in parts:
                params.extend(chunk)
                params.extend(args)
            out.extend(connection.execute(query, [*params, limit + 1 - len(out)]))
            if len(out) > limit:
                break
        return out

    def neighborhood(self, entity, *, hops=2, limit=1000, predicates=None, direction='both', min_weight=None,
                     valid_at=None, known_at=None, resolved=False):
        """Bounded BFS over the edge index with predicate, weight and bitemporal filters."""
        if not 1 <= hops <= 6 or not 1 <= limit <= 100000:
            raise ValueError('hops must be 1..6 and limit 1..100000')
        if direction not in ('in', 'out', 'both'):
            raise ValueError('direction must be in, out or both')
        from .model import identifier
        identifier(entity)
        connection = self._connect(require='3')
        try:
            suffix, args = self._edge_query(predicates, min_weight, valid_at, known_at)
            canon = (lambda x: self._canonical(connection, x)) if resolved else (lambda x: x)
            root = canon(entity)
            depth = {root: 0}
            frontier = [root]
            edges, seen, truncated = [], set(), False
            for level in range(1, hops + 1):
                query_nodes = []
                for node in frontier:
                    query_nodes.extend(self._members(connection, node) if resolved else [node])
                rows = self._incident(connection, sorted(set(query_nodes)), direction, suffix, args, limit - len(edges))
                next_frontier = set()
                for row in rows:
                    if row['eid'] in seen:
                        continue
                    if len(edges) >= limit:
                        truncated = True
                        break
                    seen.add(row['eid'])
                    subject, obj = canon(row['subject']), canon(row['object'])
                    edges.append({'subject': subject, 'predicate': row['predicate'], 'object': obj, 'weight': row['weight'],
                                  'valid_from': row['valid_from'], 'valid_to': row['valid_to'], 'observed_at': row['observed_at'],
                                  'source_subject': row['subject'], 'source_object': row['object'], 'edge': row['eid'],
                                  'record_rowid': row['record_rowid']})
                    for endpoint in (subject, obj):
                        if endpoint not in depth:
                            depth[endpoint] = level
                            next_frontier.add(endpoint)
                frontier = sorted(next_frontier)
                if truncated or not frontier:
                    break
            return {'root': entity, 'canonical_root': root, 'resolved': resolved, 'hops': hops,
                    'nodes': [{'id': node, 'depth': d} for node, d in sorted(depth.items(), key=lambda x: (x[1], x[0]))],
                    'edges': edges, 'truncated': truncated}
        finally:
            connection.close()

    def edge_records(self, edges):
        """Fetch the source assertion records (with provenance) behind neighborhood/path edges."""
        connection = self._connect(require='3')
        try:
            ids = sorted({e['record_rowid'] for e in edges})
            out = []
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                out.extend(self._decode(r) for r in connection.execute(
                    'SELECT * FROM records WHERE rowid IN (%s) ORDER BY rowid' % ','.join('?' * len(chunk)), chunk))
            return out
        finally:
            connection.close()

    def paths(self, source, target, *, max_hops=4, limit=10, predicates=None, direction='both', min_weight=None,
              valid_at=None, known_at=None, resolved=False, max_expansions=200000):
        """Shortest paths (up to ``limit``) via bounded BFS; each step lists the supporting edge."""
        if not 1 <= max_hops <= 8 or not 1 <= limit <= 1000:
            raise ValueError('max_hops must be 1..8 and limit 1..1000')
        connection = self._connect(require='3')
        try:
            suffix, args = self._edge_query(predicates, min_weight, valid_at, known_at)
            canon = (lambda x: self._canonical(connection, x)) if resolved else (lambda x: x)
            start, goal = canon(source), canon(target)
            if start == goal:
                return {'source': source, 'target': target, 'paths': [[]], 'length': 0, 'truncated': False}
            parents = {start: []}
            frontier, expansions, truncated, found = [start], 0, False, False
            for _ in range(max_hops):
                layer = {}
                query_nodes = []
                for node in frontier:
                    query_nodes.extend(self._members(connection, node) if resolved else [node])
                rows = self._incident(connection, sorted(set(query_nodes)), direction, suffix, args, max_expansions - expansions)
                expansions += len(rows)
                if expansions >= max_expansions:
                    truncated = True
                for row in rows:
                    subject, obj = canon(row['subject']), canon(row['object'])
                    steps = []
                    if direction in ('out', 'both') and subject in frontier:
                        steps.append((subject, obj))
                    if direction in ('in', 'both') and obj in frontier:
                        steps.append((obj, subject))
                    for here, there in steps:
                        if there in parents and there not in layer:
                            continue
                        edge = {'from': here, 'to': there, 'predicate': row['predicate'], 'weight': row['weight'],
                                'edge': row['eid'], 'record_rowid': row['record_rowid'],
                                'direction': 'forward' if here == subject else 'reverse'}
                        layer.setdefault(there, []).append((here, edge))
                for node, items in layer.items():
                    parents[node] = sorted(items, key=lambda x: (x[0], x[1]['edge']))
                frontier = set(layer)
                if goal in layer:
                    found = True
                    break
                if not frontier or truncated:
                    break
            paths = []
            if found:
                def walk(node, suffix_path):
                    if len(paths) >= limit:
                        return
                    if node == start:
                        paths.append(list(reversed(suffix_path)))
                        return
                    for parent, edge in parents[node]:
                        walk(parent, suffix_path + [edge])
                walk(goal, [])
            return {'source': source, 'target': target, 'canonical_source': start, 'canonical_target': goal,
                    'paths': paths, 'length': len(paths[0]) if paths else None, 'truncated': truncated or len(paths) >= limit,
                    'expansions': expansions}
        finally:
            connection.close()

    # -- aggregates ----------------------------------------------------------------------------------
    def degree_centrality(self, *, predicates=None, direction='both', weighted=False, limit=100, valid_at=None,
                          known_at=None, resolved=False):
        if not 1 <= limit <= 10000 or direction not in ('in', 'out', 'both'):
            raise ValueError('Invalid centrality request')
        connection = self._connect(require='3')
        try:
            suffix, args = self._edge_query(predicates, None, valid_at, known_at)
            value = 'SUM(weight)' if weighted else 'COUNT(*)'
            parts, params = [], []
            node = lambda column: (f'COALESCE((SELECT canonical_id FROM resolved WHERE entity_id = e.{column}), e.{column})'
                                   if resolved else f'e.{column}')
            if direction in ('out', 'both'):
                parts.append(f'SELECT {node("subject")} AS node, weight FROM edges e WHERE 1=1' + suffix)
                params.extend(args)
            if direction in ('in', 'both'):
                parts.append(f'SELECT {node("object")} AS node, weight FROM edges e WHERE 1=1' + suffix)
                params.extend(args)
            query = (f'SELECT node, {value} AS score, COUNT(*) AS degree FROM ({" UNION ALL ".join(parts)}) '
                     'GROUP BY node ORDER BY score DESC, node LIMIT ?')
            return [{'node': r['node'], 'score': r['score'], 'degree': r['degree']}
                    for r in connection.execute(query, [*params, limit])]
        finally:
            connection.close()

    def pagerank(self, *, predicates=None, damping=0.85, iterations=50, tolerance=1e-10, limit=100, max_edges=5000000,
                 valid_at=None, known_at=None, weighted=True, resolved=False):
        """Weighted PageRank over the (filtered) directed edge set; refuses above ``max_edges``."""
        if not 0 < damping < 1:
            raise ValueError('damping must be in (0, 1)')
        connection = self._connect(require='3')
        try:
            suffix, args = self._edge_query(predicates, None, valid_at, known_at)
            total = connection.execute('SELECT COUNT(*) FROM edges WHERE 1=1' + suffix, args).fetchone()[0]
            if total > max_edges:
                raise ValueError(f'{total} edges exceed max_edges={max_edges}; filter predicates or raise the bound')
            index, names, out = {}, [], {}
            resolved_map = dict(connection.execute('SELECT entity_id, canonical_id FROM resolved')) if resolved else {}
            def node(x):
                x = resolved_map.get(x, x)
                if x not in index:
                    index[x] = len(names)
                    names.append(x)
                return index[x]
            for row in connection.execute('SELECT subject, object, weight FROM edges WHERE 1=1' + suffix + ' ORDER BY rowid', args):
                a, b = node(row['subject']), node(row['object'])
                if a != b:
                    out.setdefault(a, {})
                    out[a][b] = out[a].get(b, 0.0) + (max(row['weight'], 0.0) if weighted else 1.0)
            n = len(names)
            if not n:
                return {'nodes': 0, 'edges': 0, 'ranks': [], 'iterations': 0, 'converged': True}
            rank = [1 / n] * n
            converged, iteration = False, 0
            for iteration in range(1, iterations + 1):
                new = [(1 - damping) / n] * n
                dangling = 0.0
                for a in range(n):
                    targets = out.get(a)
                    total_weight = sum(targets.values()) if targets else 0.0
                    if not total_weight:
                        dangling += rank[a]
                        continue
                    share = damping * rank[a] / total_weight
                    for b, w in targets.items():
                        new[b] += share * w
                spread = damping * dangling / n
                new = [v + spread for v in new]
                delta = sum(abs(x - y) for x, y in zip(new, rank))
                rank = new
                if delta < tolerance:
                    converged = True
                    break
            ranked = sorted(range(n), key=lambda i: (-rank[i], names[i]))[:limit]
            return {'nodes': n, 'edges': total, 'iterations': iteration, 'converged': converged, 'damping': damping,
                    'ranks': [{'node': names[i], 'rank': rank[i]} for i in ranked]}
        finally:
            connection.close()

    def flow_aggregate(self, predicate, *, group_by='subject', limit=100, valid_at=None, known_at=None, resolved=False):
        """Sum edge weights (e.g. award amounts, shipment volumes) by subject, object or pair."""
        if group_by not in ('subject', 'object', 'pair') or not 1 <= limit <= 10000:
            raise ValueError('group_by must be subject, object or pair')
        connection = self._connect(require='3')
        try:
            suffix, args = self._edge_query([predicate], None, valid_at, known_at)
            node = lambda column: (f'COALESCE((SELECT canonical_id FROM resolved WHERE entity_id = e.{column}), e.{column})'
                                   if resolved else f'e.{column}')
            keys = {'subject': f'{node("subject")} AS subject', 'object': f'{node("object")} AS object',
                    'pair': f'{node("subject")} AS subject, {node("object")} AS object'}[group_by]
            group = {'subject': 'subject', 'object': 'object', 'pair': 'subject, object'}[group_by]
            query = (f'SELECT {keys}, SUM(weight) AS total, COUNT(*) AS edges FROM edges e WHERE 1=1{suffix} '
                     f'GROUP BY {group} ORDER BY total DESC, {group} LIMIT ?')
            return {'predicate': predicate, 'group_by': group_by, 'resolved': resolved,
                    'rows': [dict(r) for r in connection.execute(query, [*args, limit])],
                    'interpretation': 'Sums edge weights as recorded; units must already agree across the selected edges.'}
        finally:
            connection.close()
