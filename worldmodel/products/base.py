"""Shared machinery for the evidence products: read-only index access, provenance and rights.

Every product answer is wrapped in one envelope (:func:`envelope`) so the conventions of the
unified graph survive the trip to a non-author:

- every edge, observation and event names the dataset that published it (``from_dataset``);
- identity comes only from the asserted clusters in the index's ``resolved`` table, and any
  name-based step is labelled ``INFERRED, not asserted``;
- every answer carries ``where_the_evidence_runs_out`` and ``what_this_does_not_establish``;
- the rights and use-policy metadata of every contributing dataset travel with the answer.

The index is only ever opened read-only (``mode=ro``). Another process may attach a new
resolution while a product runs, so reads retry on a locked database and the resolution view
digest is recorded at the start and the end of every answer.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import zlib

from ..resources import resource_roots

PRODUCT_SCHEMA = 1
#: Busy timeout for the read-only connection, in seconds. A resolution attach holds its write
#: lock for well under this.
BUSY_TIMEOUT = 60
#: Page cache per connection. Keeps a product process well under its memory budget.
CACHE_KIB = 65536

COMMON_LIMITS = [
    'Absence of an edge, observation or event here is absence of published evidence in this index '
    'scope, not absence in the world. The index pins one version of each input dataset.',
    'Nothing is merged: records are the publishers\' own claims, copied verbatim, and contradictory '
    'claims stay side by side.',
    'Identity is asserted only (published same_as links, shared unique identifiers, published crosswalk '
    'fields). Entities whose names merely look alike are never joined; where a product had to use a '
    'name, it says INFERRED, not asserted.',
    'Rights and use-policy metadata are an inventory, not a legal determination, and nothing here '
    'authorises republishing the acquired data.',
]


class ProductError(ValueError):
    """A product request that cannot be answered as asked (bad input, missing index, bound hit)."""


def default_index(data_root=None):
    explicit = os.environ.get('WORLD_MODEL_INDEX')
    if explicit:
        return Path(explicit)
    return Path(data_root or resource_roots()['data']) / 'world_evidence/index.sqlite'


def default_products_index(index):
    explicit = os.environ.get('WORLD_MODEL_PRODUCTS_INDEX')
    return Path(explicit) if explicit else Path(index).with_name('products.sqlite')


class Connection(sqlite3.Connection):
    """A connection that can carry product metadata (``meta``) alongside it."""
    meta = None


def connect(path, *, readonly=True):
    """A read-only connection with a busy timeout, a bounded page cache and dict-like rows.

    Callers keep every statement short (bounded queries, paged scans): in rollback-journal mode a
    long-running read would hold the shared lock that a concurrent resolution attach must wait for.
    """
    path = Path(path)
    if not path.is_file():
        raise ProductError('No index at %s; build one with "python3 -m worldmodel unify"' % path)
    uri = path.resolve().as_uri() + ('?mode=ro' if readonly else '')
    connection = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT, check_same_thread=False, factory=Connection)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA cache_size=%d' % -CACHE_KIB)
    return connection


def retrying(function, *args, attempts=4, **kwargs):
    """Run a read, retrying if a concurrent resolution attach holds the database lock."""
    for attempt in range(attempts):
        try:
            return function(*args, **kwargs)
        except sqlite3.OperationalError as error:
            if 'locked' not in str(error) and 'busy' not in str(error):
                raise
            if attempt == attempts - 1:
                raise ProductError('The index stayed locked by a writer for too long: %s' % error) from error
            time.sleep(2 ** attempt)


def decode(body):
    """A record body, stored deflated or as text."""
    return json.loads(zlib.decompress(body) if isinstance(body, bytes) else body)


def prefix_range(prefix):
    """(low, high) bounds so an ID-prefix match uses the B-tree index (LIKE does not)."""
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)


def chunks(values, size=500):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def dataset_span(connection, dataset, order):
    """(first rowid, last rowid) of one dataset's records, by binary search on point lookups.

    ``unify`` loads datasets one after another in the order its ``metadata.inputs`` lists them,
    so each dataset is one contiguous rowid block. Point lookups keep every statement short; a
    ``MIN(rowid) WHERE dataset=?`` walks the whole primary-key range and holds the shared lock
    for tens of seconds on the largest datasets.
    """
    position = {name: i for i, name in enumerate(order)}
    if dataset not in position:
        return None
    target = position[dataset]
    top = connection.execute('SELECT MAX(rowid) AS m FROM records').fetchone()['m'] or 0

    def at(rowid):
        row = connection.execute('SELECT rowid, dataset FROM records WHERE rowid >= ? ORDER BY rowid LIMIT 1',
                                 (rowid,)).fetchone()
        return (row['rowid'], position.get(row['dataset'], -1)) if row else (top + 1, len(order))

    def first_at_or_after(rank):
        low, high = 1, top + 1
        while low < high:
            middle = (low + high) // 2
            if at(middle)[1] >= rank:
                high = middle
            else:
                low = middle + 1
        return low

    low = at(first_at_or_after(target))[0]
    high = first_at_or_after(target + 1) - 1
    if low > high:
        return None
    for rowid in (low, high, (low + high) // 2):
        row = connection.execute('SELECT dataset FROM records WHERE rowid >= ? ORDER BY rowid LIMIT 1',
                                 (rowid,)).fetchone()
        if not row or row['dataset'] != dataset:
            raise ProductError('Records are not in metadata.inputs order in this index; cannot locate %s' % dataset)
    return low, high


def paged_rowids(connection, query, span, *, window=20000):
    """Rows of ``query`` (which must bind ``rowid > ? AND rowid <= ?``) over ``span``, in short
    windows, each fetched and released before the next, so a writer is never starved."""
    low, high = span
    cursor = low - 1
    while cursor < high:
        stop = min(cursor + window, high)
        rows = connection.execute(query, (cursor, stop)).fetchall()
        yield from rows
        cursor = stop


class Timer:
    """Named wall-clock timings, reported in every answer as ``timings_s``."""

    def __init__(self):
        self.started = time.perf_counter()
        self.marks = {}

    @contextmanager
    def step(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.marks[name] = round(self.marks.get(name, 0.0) + time.perf_counter() - start, 4)

    def report(self):
        return {**self.marks, 'total': round(time.perf_counter() - self.started, 4)}


# -- the index ------------------------------------------------------------------------------

def index_info(connection, path):
    """What the answer was computed on: the pinned inputs and the attached resolution view."""
    meta = {row['key']: row['value'] for row in connection.execute('SELECT key, value FROM metadata')}
    inputs = json.loads(meta.get('inputs') or '[]')
    resolution = json.loads(meta['resolution']) if meta.get('resolution') else None
    return {'path': str(path), 'schema_version': meta.get('schema_version'),
            'inputs': len(inputs),
            'inputs_digest': hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
            'resolution_view_digest': (resolution or {}).get('view_digest'),
            'resolution_policy': (resolution or {}).get('policy'),
            'pinned_versions': {item['dataset']: {'stage': item.get('stage'), 'version': item.get('version')}
                                for item in inputs}}


def resolution_digest(connection):
    row = connection.execute("SELECT value FROM metadata WHERE key='resolution'").fetchone()
    return json.loads(row['value']).get('view_digest') if row else None


def cluster(connection, entity_id):
    """(canonical ID, sorted members, asserted?) for one entity ID; a singleton when unresolved."""
    row = connection.execute('SELECT canonical_id FROM resolved WHERE entity_id=?', (entity_id,)).fetchone()
    if not row:
        return entity_id, [entity_id], False
    members = [r['entity_id'] for r in connection.execute(
        'SELECT entity_id FROM resolved WHERE canonical_id=? ORDER BY entity_id', (row['canonical_id'],))]
    return row['canonical_id'], members or [entity_id], True


def canonical_map(connection, entity_ids):
    """{entity ID: canonical ID} for every ID that belongs to an asserted cluster."""
    out = {}
    for chunk in chunks(sorted(set(entity_ids))):
        marks = ','.join('?' * len(chunk))
        for row in connection.execute('SELECT entity_id, canonical_id FROM resolved WHERE entity_id IN (%s)' % marks,
                                      chunk):
            out[row['entity_id']] = row['canonical_id']
    return out


def members_map(connection, canonical_ids):
    """{canonical ID: [members]} for canonical IDs; unresolved IDs map to themselves."""
    out = {key: [] for key in canonical_ids}
    for chunk in chunks(sorted(set(canonical_ids))):
        marks = ','.join('?' * len(chunk))
        for row in connection.execute('SELECT entity_id, canonical_id FROM resolved WHERE canonical_id IN (%s) '
                                      'ORDER BY entity_id' % marks, chunk):
            out[row['canonical_id']].append(row['entity_id'])
    return {key: value or [key] for key, value in out.items()}


def exists(connection, entity_id):
    """Where, if anywhere, the index knows an ID: an entity record, an edge endpoint or a subject."""
    for where, query in (('resolved', 'SELECT 1 FROM resolved WHERE entity_id=? LIMIT 1'),
                         ('entity', "SELECT 1 FROM records WHERE entity_id=? LIMIT 1"),
                         ('edge_subject', 'SELECT 1 FROM edges WHERE subject=? LIMIT 1'),
                         ('edge_object', 'SELECT 1 FROM edges WHERE object=? LIMIT 1'),
                         ('record_subject', 'SELECT 1 FROM records WHERE subject=? LIMIT 1')):
        if connection.execute(query, (entity_id,)).fetchone():
            return where
    return None


def entity_records(connection, entity_ids, *, per_entity=12):
    """{entity ID: [description]} from the published entity records, each naming its dataset."""
    out = {}
    for entity_id in entity_ids:
        rows = connection.execute("SELECT rowid, dataset, id, body FROM records WHERE entity_id=? "
                                  'ORDER BY dataset, id LIMIT ?', (entity_id, per_entity)).fetchall()
        described = []
        for row in rows:
            body = decode(row['body'])
            described.append({'entity_id': entity_id, 'label': body.get('label'),
                              'entity_type': body.get('entity_type'), 'from_dataset': row['dataset'],
                              'record_id': row['id'], 'attributes': body.get('attributes') or {}})
        out[entity_id] = described
    return out


def best_label(descriptions, entity_id=None):
    for item in descriptions:
        label = item.get('label')
        if label and label != entity_id and label != item.get('entity_id'):
            return {'label': label, 'label_from': item['from_dataset'], 'entity_type': item.get('entity_type')}
    return {'label': None, 'label_from': None,
            'entity_type': next((d.get('entity_type') for d in descriptions if d.get('entity_type')), None)}


def labels(connection, entity_ids, *, per_entity=4):
    """{entity ID: {label, label_from, entity_type}} for a bounded set of IDs."""
    records = entity_records(connection, entity_ids, per_entity=per_entity)
    return {entity_id: best_label(records.get(entity_id, []), entity_id) for entity_id in entity_ids}


def record_sources(connection, rowids):
    """{records.rowid: {dataset, record_id, observed_at}} for the assertion records behind edges."""
    out = {}
    for chunk in chunks(sorted({r for r in rowids if r is not None})):
        marks = ','.join('?' * len(chunk))
        for row in connection.execute('SELECT rowid, dataset, id, observed_at FROM records WHERE rowid IN (%s)' % marks,
                                      chunk):
            out[row['rowid']] = {'from_dataset': row['dataset'], 'record_id': row['id'],
                                 'observed_at': row['observed_at']}
    return out


def describe_edges(connection, edges):
    """Edges from the ``edges`` table or a Graph traversal, each annotated with its dataset."""
    sources = record_sources(connection, [edge.get('record_rowid') for edge in edges])
    out = []
    for edge in edges:
        source = sources.get(edge.get('record_rowid'), {})
        item = {'subject': edge.get('source_subject', edge.get('subject')), 'predicate': edge['predicate'],
                'object': edge.get('source_object', edge.get('object')), 'weight': edge.get('weight'),
                'valid_from': edge.get('valid_from'), 'valid_to': edge.get('valid_to'),
                'from_dataset': source.get('from_dataset'), 'record_id': source.get('record_id')}
        out.append(item)
    return out


def bounded_count(connection, query, args, cap):
    """COUNT(*) of a query, stopping at ``cap`` + 1 so a hub cannot stall the answer."""
    row = connection.execute('SELECT COUNT(*) AS n FROM (%s LIMIT %d)' % (query, cap + 1), args).fetchone()
    return row['n'], row['n'] > cap


# -- rights and use policy --------------------------------------------------------------------

_RIGHTS_CACHE = {}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def dataset_rights(dataset, pin, *, data_root=None, catalog_root=None):
    """Rights and use-policy metadata for one dataset at the version the index pins.

    Read from the dataset's compact tracked manifest (``<data>/<dataset>/manifests/<stage>/
    <version>.json``), which carries the inherited rights inventory; the full manifest beside the
    payload is authoritative. The identified-persons rule comes from the dataset declaration.
    """
    roots = resource_roots()
    data_root = Path(data_root or roots['data'])
    catalog_root = Path(catalog_root or roots['catalog'])
    key = (dataset, (pin or {}).get('version'), str(data_root), str(catalog_root))
    if key in _RIGHTS_CACHE:
        return _RIGHTS_CACHE[key]
    stage, version = (pin or {}).get('stage') or 'normalized', (pin or {}).get('version')
    manifest = None
    for root in (data_root, catalog_root):
        if version and manifest is None:
            manifest = _read_json(root / dataset / 'manifests' / stage / (version + '.json'))
    declaration = _read_json(catalog_root / dataset / 'dataset.json') or {}
    source = declaration.get('source') or {}
    rights = (manifest or {}).get('rights')
    metadata = [row.get('metadata') or {} for row in (rights or {}).get('sources', [])] or ([source] if source else [])
    values = lambda field: sorted({str(m[field]) for m in metadata if m.get(field) not in (None, '')})
    statuses = values('license_status')
    non_commercial = ('non_commercial_only' in statuses or any(m.get('non_commercial') is True for m in metadata)
                      or source.get('non_commercial') is True)
    try:
        from ..rights import retain_identified_persons
        _, persons = retain_identified_persons(source)
    except ValueError as error:
        persons = {'error': str(error)}
    result = {'dataset': dataset, 'stage': stage, 'version': version,
              'rights_metadata_from': ('compact manifest of the pinned version' if rights
                                       else 'dataset declaration only (no manifest found for the pinned version)'
                                       if source else 'not found'),
              'publisher': values('publisher'), 'license_id': values('license_id'), 'license': values('license'),
              'license_status': statuses, 'redistribution': values('redistribution'),
              'attribution': values('attribution'), 'terms_url': values('terms_url'),
              'non_commercial_only': non_commercial,
              'redistribution_review_required': bool((rights or {}).get('redistribution_review_required', True)),
              'identified_persons': {k: persons.get(k) for k in ('policy', 'condition', 'authority',
                                                                 'declared_purpose', 'identified_persons_retained')}
              if 'error' not in persons else persons}
    _RIGHTS_CACHE[key] = result
    return result


def rights_block(datasets, pinned, *, data_root=None, catalog_root=None):
    """The rights and use-policy metadata of every dataset that contributed to an answer."""
    from ..rights import commercial_use
    try:
        purpose = 'commercial' if commercial_use() else 'non_commercial'
    except ValueError as error:
        purpose = 'unparseable: %s' % error
    rows = [dataset_rights(name, pinned.get(name), data_root=data_root, catalog_root=catalog_root)
            for name in sorted(set(datasets))]
    non_commercial = [row['dataset'] for row in rows if row['non_commercial_only']]
    restricted = [row['dataset'] for row in rows if set(row['redistribution']) & {'restricted', 'prohibited', 'False'}]
    review = [row['dataset'] for row in rows if row['redistribution_review_required']]
    notices = []
    if non_commercial:
        notices.append('%s %s licensed for non-commercial use only. This deployment\'s declared purpose is %s '
                       '(WM_COMMERCIAL_USE; unset means commercial). The products do not gate on it: they carry '
                       'the terms so the reader can decide.' % (', '.join(non_commercial),
                                                                 'is' if len(non_commercial) == 1 else 'are', purpose))
    if review:
        notices.append('Redistribution review is required before sharing evidence from: %s.' % ', '.join(review))
    return {'declared_purpose': purpose, 'datasets': rows,
            'non_commercial_only': non_commercial, 'redistribution_restricted': restricted,
            'redistribution_review_required': review, 'notices': notices,
            'computation_policy': 'metadata_only_no_local_execution_gate',
            'interpretation': 'Source metadata inventory; compatibility and derivative-work obligations are not '
                              'automatically adjudicated. See DATA_RIGHTS.md and docs/use-policy.md.'}


def datasets_named(value):
    """Every dataset named anywhere in an answer (``from_dataset`` / ``dataset`` keys)."""
    names = set()

    def walk(item):
        if isinstance(item, dict):
            for key, inner in item.items():
                if key in ('from_dataset', 'dataset') and isinstance(inner, str):
                    names.add(inner)
                elif key == 'from_datasets' and isinstance(inner, list):
                    names.update(x for x in inner if isinstance(x, str))
                walk(inner)
        elif isinstance(item, list):
            for inner in item:
                walk(inner)

    walk(value)
    return sorted(names)


def envelope(product, *, query, info, answer, runs_out, not_established, timer, resolution_at_start,
             resolution_at_end, data_root=None, catalog_root=None, extra=None):
    """The one shape every product answer has."""
    used = [name for name in datasets_named(answer) if name in info['pinned_versions']]
    unknown = [name for name in datasets_named(answer) if name not in info['pinned_versions']]
    result = {'product': product, 'product_schema': PRODUCT_SCHEMA, 'query': query,
              'index': {k: v for k, v in info.items() if k != 'pinned_versions'},
              'answer': answer,
              'where_the_evidence_runs_out': runs_out,
              'what_this_does_not_establish': list(not_established) + COMMON_LIMITS,
              'datasets_used': used,
              'rights': rights_block(used, info['pinned_versions'], data_root=data_root, catalog_root=catalog_root)}
    if unknown:
        result['datasets_named_but_not_pinned_by_the_index'] = unknown
    if resolution_at_start != resolution_at_end:
        result['resolution_changed_during_answer'] = {
            'at_start': resolution_at_start, 'at_end': resolution_at_end,
            'note': 'Another process attached a new identity resolution while this answer was computed; cluster '
                    'membership may mix the two views. Re-run for a consistent answer.'}
    if extra:
        result.update(extra)
    result['timings_s'] = timer.report()
    return result
