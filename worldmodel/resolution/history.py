"""Keep an index's attached resolution recoverable before it is replaced.

``Graph.attach_resolution`` replaces the ``resolved`` table and the ``resolution`` metadata row in
one transaction. :func:`export_resolution` first writes what is attached to a directory in exactly
the form ``wm graph-attach-resolution --workdir <directory>`` reads (``view.json`` plus
``clusters.jsonl``), so the previous resolution can be re-attached byte-for-byte in its clusters
and its audited view digest.
"""
import json
from pathlib import Path
import sqlite3

from ..util import atomic_json


def export_resolution(index_path, directory):
    """Write the index's attached resolution to ``directory``; returns a summary, or None if none is attached."""
    index_path = Path(index_path)
    connection = sqlite3.connect('file:%s?mode=ro' % index_path.resolve(), uri=True)
    try:
        row = connection.execute("SELECT value FROM metadata WHERE key='resolution'").fetchone()
        if row is None:
            return None
        view = json.loads(row[0])
        clusters = {}
        for entity_id, canonical_id in connection.execute(
                'SELECT entity_id, canonical_id FROM resolved ORDER BY canonical_id, entity_id'):
            clusters.setdefault(canonical_id, []).append(entity_id)
    finally:
        connection.close()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'clusters.jsonl').open('w', encoding='utf-8') as stream:
        for canonical_id in sorted(clusters):
            stream.write(json.dumps({'canonical_id': canonical_id, 'members': sorted(clusters[canonical_id])},
                                    sort_keys=True) + '\n')
    atomic_json(directory / 'view.json', view)
    return {'directory': str(directory), 'view_digest': view.get('view_digest'), 'clusters': len(clusters),
            'resolved_entities': sum(len(m) for m in clusters.values()),
            'restore': 'python3 -m worldmodel graph-attach-resolution --workdir %s --index %s' % (directory, index_path)}
