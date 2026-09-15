"""Rebuildable disk-backed index. Claims remain evidence, including contradictions."""
import json
import os
from pathlib import Path
import sqlite3
import uuid
from .model import instant
from .util import canonical


def time_key(value):
    return instant(value).isoformat() if value else None


class Graph:
    def __init__(self, path):
        self.path = Path(path)

    def build(self, store, refs):
        if not refs:
            raise ValueError('Graph build requires at least one version')
        if len({ref['dataset'] for ref in refs}) != len(refs):
            raise ValueError('Select one version per dataset for a graph snapshot')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        connection = sqlite3.connect(temporary)
        count = 0
        try:
            connection.executescript('''
                CREATE TABLE records (
                    dataset TEXT, version TEXT, id TEXT, entity_id TEXT, kind TEXT, metric TEXT,
                    subject TEXT, object TEXT, observed_at TEXT, valid_from TEXT,
                    valid_to TEXT, body TEXT, PRIMARY KEY(dataset,version,id));
                CREATE INDEX subject_idx ON records(subject);
                CREATE INDEX object_idx ON records(object);
                CREATE INDEX entity_idx ON records(entity_id);
                CREATE INDEX metric_idx ON records(kind,metric);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            ''')
            with connection:
                for ref in refs:
                    store.verify(ref)
                    for record in store.records(ref, verify=False):
                        connection.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                           (ref['dataset'], ref['version'], record['id'],
                                            record.get('entity_id', record['id']) if record['kind'] == 'entity' else None,
                                            record['kind'],
                                            record.get('metric'), record.get('subject'), record.get('object'),
                                            time_key(record['observed_at']), time_key(record.get('valid_from')),
                                            time_key(record.get('valid_to')), canonical(record).decode()))
                        count += 1
                connection.execute('INSERT INTO metadata VALUES (?,?)', ('inputs', canonical(refs).decode()))
                connection.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', '1'))
            for ref in refs:
                store.verify(ref)
            connection.close()
            os.replace(temporary, self.path)
            return {'records': count, 'inputs': refs, 'path': str(self.path)}
        finally:
            connection.close()
            temporary.unlink(missing_ok=True)

    def _connect(self):
        if not self.path.is_file():
            raise ValueError('Graph index missing; run graph-build first')
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
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
        return {**json.loads(row['body']), '_provenance': {
            'input': {'dataset': row['dataset'], 'version': row['version']}, 'record_id': row['id']}}

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
                        + ' ORDER BY dataset,version,id LIMIT ?', [node, node, *time_args, limit + 1])
                    for row in rows:
                        key = (row['dataset'], row['version'], row['id'])
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
                                          + ' ORDER BY dataset,version,id LIMIT ?', [node, *time_args, limit + 1])
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
                + ' ORDER BY dataset,version,id LIMIT ?', [metric, *args, limit])]
        finally:
            connection.close()
