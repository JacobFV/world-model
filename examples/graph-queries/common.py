"""Shared helpers for the cross-dataset queries in this directory.

Every query here runs against a unified index built by ``python3 -m worldmodel unify``
(see ``docs/unified-graph.md``). The helpers exist to make one thing easy and one thing
impossible: easy to say *which dataset supplied each edge*, impossible to present a
name-similarity guess as if a publisher had asserted it.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from worldmodel.graph import Graph  # noqa: E402
from worldmodel.resources import resource_roots  # noqa: E402

OUTPUTS = Path(__file__).resolve().parent / 'outputs'


def default_index():
    return Path(os.environ.get('WORLD_MODEL_INDEX') or resource_roots()['data'] / 'world_evidence/index.sqlite')


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument('--index', type=Path, default=default_index())
    result.add_argument('--limit', type=int, default=25)
    result.add_argument('--save', action='store_true', help='Write the result to outputs/<name>.json')
    return result


def open_index(path):
    path = Path(path)
    if not path.is_file():
        raise SystemExit('No unified index at %s; run: python3 -m worldmodel unify' % path)
    return Graph(path), sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def rows(connection, query, args=()):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(query, args)]


def stream(connection, query, args=()):
    """Iterate a query lazily; catalog-scale scans must not be materialized."""
    connection.row_factory = sqlite3.Row
    for row in connection.execute(query, args):
        yield dict(row)


def body_of(row):
    return json.loads(_text(row['body']))


def prefix_range(prefix):
    """(low, high) bounds for an ID prefix. A range predicate uses the subject/entity_id index;
    LIKE 'x%' does not, because SQLite's default LIKE is case-insensitive."""
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)


def label(connection, entity_id):
    """Best published label for an entity ID, with the dataset that published it."""
    found = rows(connection, "SELECT dataset, body FROM records WHERE kind='entity' AND entity_id=? "
                             'ORDER BY dataset LIMIT 4', (entity_id,))
    for row in found:
        body = json.loads(_text(row['body']))
        if body.get('label'):
            return {'entity_id': entity_id, 'label': body['label'], 'entity_type': body.get('entity_type'),
                    'label_from': row['dataset'], 'also_described_by': sorted({r['dataset'] for r in found})}
    return {'entity_id': entity_id, 'label': None, 'entity_type': None, 'label_from': None,
            'also_described_by': sorted({r['dataset'] for r in found})}


def _text(body):
    import zlib
    return zlib.decompress(body) if isinstance(body, bytes) else body


def edge_sources(connection, edges):
    """{edge rowid: {'dataset', 'record_id'}} for the assertion records behind graph edges."""
    ids = sorted({edge['record_rowid'] for edge in edges if edge.get('record_rowid')})
    out = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ','.join('?' * len(chunk))
        for row in rows(connection, 'SELECT rowid, dataset, stage, version, id FROM records '
                                    'WHERE rowid IN (%s)' % marks, chunk):
            out[row['rowid']] = {'dataset': row['dataset'], 'stage': row['stage'],
                                 'version': row['version'], 'record_id': row['id']}
    return out


def described(connection, edges):
    """Edges annotated with the dataset that published each one."""
    sources = edge_sources(connection, edges)
    described_edges = []
    for edge in edges:
        source = sources.get(edge.get('record_rowid'), {})
        described_edges.append({'subject': edge['subject'], 'predicate': edge['predicate'],
                                'object': edge['object'], 'weight': edge['weight'],
                                'valid_from': edge.get('valid_from'), 'valid_to': edge.get('valid_to'),
                                'observed_at': edge.get('observed_at'),
                                'from_dataset': source.get('dataset'), 'record_id': source.get('record_id')})
    return described_edges


def observations_for(connection, subject, metrics=None, limit=40, dataset=None):
    """Observations recorded against one subject, with the dataset that published each.

    Pass ``dataset`` when comparing several publishers for the same subject: one unfiltered query
    with a row cap lets the alphabetically first dataset consume the whole quota.
    """
    query = "SELECT dataset, body FROM records WHERE subject=? AND kind='observation'"
    args = [subject]
    if dataset:
        query += ' AND dataset=?'
        args.append(dataset)
    found = rows(connection, query + ' ORDER BY dataset, observed_at LIMIT ?', (*args, limit * 8))
    out = []
    for row in found:
        body = json.loads(_text(row['body']))
        if metrics and body.get('metric') not in metrics:
            continue
        out.append({'from_dataset': row['dataset'], 'metric': body.get('metric'), 'value': body.get('value'),
                    'unit': body.get('unit'), 'valid_from': body.get('valid_from'),
                    'valid_to': body.get('valid_to'), 'dimensions': body.get('dimensions'),
                    'missing_reason': body.get('missing_reason')})
        if len(out) >= limit:
            break
    return out


def cluster_members(connection, entity_id):
    """The asserted-identity cluster an entity belongs to (empty when no resolution is attached)."""
    row = rows(connection, 'SELECT canonical_id FROM resolved WHERE entity_id=?', (entity_id,))
    if not row:
        return []
    return [r['entity_id'] for r in rows(connection, 'SELECT entity_id FROM resolved WHERE canonical_id=? '
                                                     'ORDER BY entity_id', (row[0]['canonical_id'],))]


def datasets_used(result):
    """Every dataset named anywhere in a result payload, for the 'which dataset supplied what' line."""
    names = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('from_dataset', 'dataset') and isinstance(item, str):
                    names.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result)
    return sorted(names)


def emit(name, result, *, save=False):
    result = {'query': name, **result}
    result.setdefault('datasets_used', datasets_used(result))
    text = json.dumps(result, indent=1, ensure_ascii=False, sort_keys=False, allow_nan=False)
    print(text)
    if save:
        OUTPUTS.mkdir(parents=True, exist_ok=True)
        (OUTPUTS / (name + '.json')).write_text(text + '\n', encoding='utf-8')
    return result
