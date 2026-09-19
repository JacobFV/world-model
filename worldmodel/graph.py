"""Rebuildable disk-backed index. Claims remain evidence, including contradictions.

Schema 3 adds an ``edges`` table (one row per entity-to-entity assertion, with weight and
both time axes), a ``resolved`` table mapping entity IDs to canonical cluster IDs from an
auditable resolution view, and bounded traversal/aggregate queries. Indexes are created
after batch loading. Schema-2 indexes remain readable for the original queries.

Schema 4 adds the **publication date**. ``observed_at`` is the moment a record was *ingested*
- for most of this catalog a single unify run's wall clock - so filtering an as-of query on it
claims a point-in-time view nobody has. ``records.published_at`` and ``edges.published_at``
hold the date the fact became *public*, with ``records.published_source`` naming where that
date came from, populated in one priority order and never guessed:

1. ``dimensions.available_at`` - the publisher's own availability date, where the adapter
   emits one (the dated panels do);
2. ``attributes.realtime_start`` - the ALFRED vintage date of a real-time-vintaged row;
3. a declared dataset-level publication rule (:data:`PUBLICATION_RULES`), applied to the end
   of the record's own reference period;
4. otherwise ``NULL``, which means *unknown* and must never silently become the ingest time.

:meth:`Graph` therefore filters ``known_at`` on ``published_at`` and, by default, **excludes**
records whose publication date is unknown; every as-of result carries a ``publication`` block
saying how many rows that dropped and from which datasets. ``include_unknown_publication=True``
restores the old ingestion-time behaviour for the unknown rows and says so in the same result.
Schema-2 and schema-3 indexes carry no publication date at all: they stay readable, and an
as-of query against one reports that it cannot answer rather than guessing.
"""
from collections import Counter
from datetime import date
import json
import math
import os
from pathlib import Path
import sqlite3
import uuid
import zlib
from .model import instant
from .util import canonical

SCHEMA_VERSION = '4'
READABLE_SCHEMAS = ('2', '3', '4')
#: The first schema that stores a publication date. Below it, ``known_at`` has only ingest time.
PUBLICATION_SCHEMA = '4'

#: Declared dataset-level publication rules, in the shape
#: :data:`worldmodel.embedding.county_panel.SOURCES` already uses: months after the end of the
#: record's own reference period at which the value is treated as public, the revision class,
#: and the release fact the lag rests on. Each entry names the adapter that declared it; the
#: numbers are not restated here for a second time, they are checked against that declaration
#: by ``tests/test_graph_publication.py``.
#:
#: A rule applies only to a record that carries a reference period (``valid_to``, else
#: ``valid_from``). An entity or a standing assertion has no period, so a rule cannot date it
#: and it stays unknown. Sources whose declared lag is ``None`` (county_panel's static
#: geography and the CBSA bulletins) are deliberately absent: "available from 1980" is a
#: panel-local convention, not a statement about when the dataset became public.
PUBLICATION_RULES = {
    'bea_national_regional': {
        'lag_months': 12, 'revisions': 'major', 'declared_by': 'worldmodel.embedding.county_panel.SOURCES["bea"]',
        'rule': 'BEA releases county personal income for y in November of y+1 and county GDP in December of y+1; '
                'dated at the end of December y+1. Annual and comprehensive revisions rewrite history.'},
    'bls_labor': {
        'lag_months': 9, 'revisions': 'major', 'declared_by': 'worldmodel.embedding.county_panel.SOURCES["qcew"]',
        'rule': 'BLS publishes QCEW county annual averages for year y in early September of y+1; the panel dates '
                'them at the end of September y+1. county_panel declares a shorter 4-month lag for the LAUS series '
                'in the same dataset; a dataset-level rule cannot tell the two series apart, so the longer QCEW lag '
                'is applied to both. Later is the conservative direction: it can only withhold, never leak.'},
    'irs_soi_migration': {
        'lag_months': 18, 'revisions': 'none', 'declared_by': 'worldmodel.embedding.county_panel.SOURCES["migration"]',
        'rule': 'IRS SOI county-to-county migration for filing years y to y+1 is released about 18 months after the '
                'second filing year ends; dated 18 months after the end of the reference period.'},
    'noaa_climdiv': {
        'lag_months': 1, 'revisions': 'minor', 'declared_by': 'worldmodel.embedding.county_panel.SOURCES["climdiv"]',
        'rule': 'nClimDiv county annual values for y are published in early January of y+1; dated at the end of '
                'January y+1. Each release recomputes the record with the current homogenization.'},
    'noaa_storm_events': {
        'lag_months': 4, 'revisions': 'minor', 'declared_by': 'worldmodel.embedding.county_panel.SOURCES["storms"]',
        'rule': 'Storm Data events are finalized roughly 75 days after the month ends; county-year totals for y are '
                'dated at the end of April y+1.'},
    'openfema': {
        'lag_months': 0, 'revisions': 'none', 'declared_by': 'worldmodel.embedding.county_panel.SOURCES["fema"]',
        'rule': 'A disaster declaration is public on its declaration date; a record is dated at the end of its own '
                'reference period.'},
}


def time_key(value):
    return instant(value).isoformat() if value else None


def _add_months(day, months):
    year, month = divmod((day.year * 12 + day.month - 1) + months, 12)
    return date(year, month + 1, min(day.day, [31, 29 if not year % 4 and (year % 100 or not year % 400) else 28,
                                               31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month]))


def rule_publication(record, rule):
    """The declared publication date for one record, or ``None`` when the rule cannot date it.

    ``valid_to`` is the exclusive end of the reference period, so a rule anchored on it lands
    one day later than an anchor on the period's last day. That direction is deliberate: a
    publication date that is a day late withholds a record, it never leaks one.
    """
    anchor = record.get('valid_to') or record.get('valid_from')
    if not anchor or rule.get('lag_months') is None:
        return None
    try:
        return time_key(_add_months(instant(anchor).date(), rule['lag_months']).isoformat())
    except ValueError:
        return None


#: ``attributes.vintage`` values that say the row is the *current* vintage as of retrieval, not an
#: archived one. ``bls_labor`` and ``bls_prices`` say so and then fill ``attributes.realtime_start``
#: with the retrieval date, because the BLS flat files carry no vintage at all. Taking that field at
#: face value would put the ingest clock back into ``published_at`` wearing a vintage field's name -
#: the exact defect this column exists to remove - so it is refused, counted under
#: ``refused:<marker>`` in the census, and the record stays unknown.
RETRIEVAL_VINTAGES = frozenset({'current_at_retrieval'})


def publication(record, rule=None):
    """``(publication date, source)`` for one record: when the fact became public, and how we know.

    Priority: ``dimensions.available_at``, then ``attributes.realtime_start``, then the declared
    dataset rule, then ``(None, None)``. ``None`` means unknown; it is never the ingest time.
    """
    attributes = record.get('attributes')
    attributes = attributes if isinstance(attributes, dict) else {}
    refused = None
    for field, container in (('available_at', record.get('dimensions')), ('realtime_start', attributes)):
        value = (container or {}).get(field) if isinstance(container, dict) else None
        if not value:
            continue
        if field == 'realtime_start' and str(attributes.get('vintage')) in RETRIEVAL_VINTAGES:
            refused = 'refused:' + str(attributes.get('vintage'))
            continue
        try:
            return time_key(value if isinstance(value, str) else str(value)), (
                'dimensions.available_at' if field == 'available_at' else 'attributes.realtime_start')
        except ValueError:
            return None, 'unparsable:' + field
    if rule:
        value = rule_publication(record, rule)
        if value:
            return value, 'rule'
    return None, refused


def edge_weight(record):
    """Edge weight: record.weight, attributes.weight, match.score, confidence, else 1.0."""
    for value in (record.get('weight'), (record.get('attributes') or {}).get('weight'),
                  (record.get('match') or {}).get('score'), record.get('confidence')):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return 1.0


def _coverage_report(census):
    """Per-dataset publication-date census plus totals; the number the whole exercise is for."""
    rows = []
    totals = Counter()
    sources = Counter()
    for key in sorted(census):
        row = dict(census[key])
        row['sources'] = dict(row['sources'])
        row['records_share'] = round(row['records_published'] / row['records'], 6) if row['records'] else None
        row['edges_share'] = round(row['edges_published'] / row['edges'], 6) if row['edges'] else None
        for field in ('records', 'records_published', 'edges', 'edges_published'):
            totals[field] += row[field]
        sources.update(row['sources'])
        rows.append(row)
    return {'datasets': rows, 'totals': dict(totals) | {
        'records_share': round(totals['records_published'] / totals['records'], 6) if totals['records'] else None,
        'edges_share': round(totals['edges_published'] / totals['edges'], 6) if totals['edges'] else None,
        'sources': dict(sources)},
        'unknown_means': 'No publication date could be established for this record; it is excluded from as-of '
                         'queries by default rather than dated by its ingestion time.'}


class AsOf:
    """The as-of policy for one query, and the accounting of what it withheld.

    ``known_at`` filters on ``published_at``. A record whose publication date is unknown is
    excluded by default: the index does not know when it became public, so it cannot honestly
    be shown to a reader asking what was knowable on a date. ``include_unknown_publication``
    brings those rows back under the old ingestion-time rule (``observed_at <= known_at``),
    which is a *weaker* claim, and :meth:`report` says so in the same result.
    """
    EXCLUDE, INCLUDE = 'exclude_unknown_publication', 'include_unknown_publication_as_ingested'

    def __init__(self, schema, valid_at, known_at, include_unknown_publication=False):
        self.schema = schema
        self.valid_at = time_key(valid_at)
        self.known_at = time_key(known_at)
        self.include_unknown = bool(include_unknown_publication)
        self.dated = schema >= PUBLICATION_SCHEMA
        self.excluded = Counter()
        if self.known_at and not self.dated and not self.include_unknown:
            raise ValueError(
                'This index is graph schema %s and carries no publication dates, so it cannot answer a --known-at '
                'query: filtering it would date every record by when it was ingested. Rebuild at schema %s '
                '(wm unify / wm graph-build), or pass include_unknown_publication to accept ingestion time and have '
                'the result say so.' % (schema, PUBLICATION_SCHEMA))

    @property
    def policy(self):
        return self.INCLUDE if self.include_unknown else self.EXCLUDE

    def filters(self):
        """``(WHERE suffix, args)`` for the query proper."""
        clauses, args = [], []
        if self.valid_at:
            clauses.extend(['(valid_from IS NULL OR valid_from <= ?)', '(valid_to IS NULL OR valid_to > ?)'])
            args.extend([self.valid_at] * 2)
        if self.known_at:
            if not self.dated:
                clauses.append('observed_at <= ?')
                args.append(self.known_at)
            elif self.include_unknown:
                clauses.append('(published_at <= ? OR (published_at IS NULL AND observed_at <= ?))')
                args.extend([self.known_at] * 2)
            else:
                clauses.append('(published_at IS NOT NULL AND published_at <= ?)')
                args.append(self.known_at)
        return ''.join(' AND ' + clause for clause in clauses), args

    def drop_clause(self):
        """``(clause, args)`` selecting the rows whose publication date is unknown, or ``(None, [])``.

        Under the default policy those rows are what the query withheld. Under
        ``include_unknown_publication`` they are the rows it let through on ingestion time
        alone, narrowed to the ones that horizon actually admitted. Either way they are the
        rows the answer cannot place in real time, and the count names them.
        """
        if not self.known_at or not self.dated:
            return None, []
        clauses, args = [], []
        if self.valid_at:
            clauses.extend(['(valid_from IS NULL OR valid_from <= ?)', '(valid_to IS NULL OR valid_to > ?)'])
            args.extend([self.valid_at] * 2)
        clauses.append('published_at IS NULL')
        if self.include_unknown:
            clauses.append('observed_at <= ?')
            args.append(self.known_at)
        return ' AND '.join(clauses), args

    def report(self):
        """The disclosure every as-of result carries."""
        if not self.known_at:
            return {'known_at': None, 'policy': 'no as-of filter', 'graph_schema': self.schema,
                    'publication_dates_available': self.dated}
        total = sum(self.excluded.values())
        by_dataset = dict(sorted(self.excluded.items(), key=lambda kv: (-kv[1], kv[0])))
        note = {'known_at': self.known_at, 'policy': self.policy, 'graph_schema': self.schema,
                'publication_dates_available': self.dated,
                'filtered_on': 'published_at' if self.dated else 'observed_at',
                'excluded_unknown_publication': 0 if self.include_unknown else total,
                'included_unknown_publication': total if self.include_unknown else 0,
                'unknown_publication_by_dataset': by_dataset}
        if not self.include_unknown:
            note['excluded_by_dataset'] = by_dataset
        if not self.dated:
            note['disclosure'] = (
                'This index predates the publication date (graph schema %s). "%s" is the *ingestion* time of the '
                'unify run, not the date these facts became public, so this result is not a point-in-time view, and '
                'the counts above are unavailable. Rebuild at schema %s to get one.'
                % (self.schema, self.known_at, PUBLICATION_SCHEMA))
        elif self.include_unknown:
            note['disclosure'] = (
                'include_unknown_publication was requested: %d candidate rows with no publication date were kept '
                'using their ingestion time (when this catalog indexed them, not when the fact became public). '
                'Those rows are not point-in-time and may not have been knowable on %s; '
                'unknown_publication_by_dataset says where they came from.' % (total, self.known_at))
        else:
            note['disclosure'] = (
                '%d candidate rows were excluded because no publication date could be established for them, so this '
                'index cannot say whether they were public on %s; excluded_by_dataset says where they came from. '
                'Absence here means "not known to have been public by then", not "did not exist".'
                % (total, self.known_at))
        return note


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

    def build_from_records(self, groups, *, batch_size=50000, validate=True, cache_mb=None, compress_bodies=False,
                           publication_rules=None):
        """Build from [(ref, iterable_of_records)] without a Store (e.g. resolution outputs, scale tests)."""
        groups = list(groups)
        refs = [ref for ref, _ in groups]
        if not refs:
            raise ValueError('Graph build requires at least one input')
        if validate:
            from .model import validate_record
            groups = [(ref, (validate_record(r) for r in records)) for ref, records in groups]
        return self._build(groups, refs, batch_size=batch_size, cache_mb=cache_mb, compress_bodies=compress_bodies,
                           publication_rules=publication_rules)

    def _build(self, groups, refs, *, batch_size, after=None, cache_mb=None, compress_bodies=False,
               publication_rules=None):
        """``cache_mb`` bounds the SQLite page cache; the 2 MiB default thrashes on catalog-scale loads.

        ``compress_bodies`` stores each record body as a deflated BLOB instead of text. Record
        text dominates a catalog-scale index (roughly 0.6 KB per record), and ``_decode``
        transparently reads either form, so indexes built either way stay queryable.

        ``publication_rules`` overrides :data:`PUBLICATION_RULES` (pass ``{}`` to date records
        only from what the publisher itself emits). The build counts, per dataset, how many
        records and edges got a publication date and from which of the three sources; that
        census is written to ``metadata.publication_coverage`` so a reader can see what the
        index cannot answer as-of without scanning it.
        """
        if cache_mb is not None and not 1 <= cache_mb <= 65536:
            raise ValueError('cache_mb must be in 1..65536')
        rules = PUBLICATION_RULES if publication_rules is None else publication_rules
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        connection = sqlite3.connect(temporary)
        count = edges = 0
        coverage, applied = {}, {}
        try:
            connection.execute('PRAGMA journal_mode=OFF')
            connection.execute('PRAGMA synchronous=OFF')
            if cache_mb is not None:
                connection.execute('PRAGMA cache_size=%d' % -(cache_mb * 1024))
                connection.execute('PRAGMA temp_store=FILE')
            connection.executescript('''
                CREATE TABLE records (
                    dataset TEXT, stage TEXT, version TEXT, input_ref TEXT, id TEXT, entity_id TEXT, kind TEXT, metric TEXT,
                    subject TEXT, object TEXT, observed_at TEXT, published_at TEXT, published_source TEXT, valid_from TEXT,
                    valid_to TEXT, body TEXT, PRIMARY KEY(dataset,stage,version,id));
                CREATE TABLE edges (subject TEXT, predicate TEXT, object TEXT, weight REAL, valid_from TEXT, valid_to TEXT,
                    observed_at TEXT, published_at TEXT, dataset_id INTEGER, record_rowid INTEGER);
                CREATE TABLE resolved (entity_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, cluster_size INTEGER);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            ''')
            body = (lambda raw: zlib.compress(raw, 1)) if compress_bodies else (lambda raw: raw.decode())
            with connection:
                edge_datasets = []
                for dataset_id, (ref, records) in enumerate(groups):
                    input_ref = canonical(ref).decode()
                    # edges carry the position in this list, not a repeated dataset string.
                    edge_datasets.append({'dataset': ref['dataset'], 'stage': ref.get('stage', 'final')})
                    rule = rules.get(ref['dataset'])
                    if rule:
                        applied[ref['dataset']] = rule
                    census = coverage.setdefault(ref['dataset'] + '@' + ref.get('stage', 'final'), {
                        'dataset': ref['dataset'], 'stage': ref.get('stage', 'final'), 'records': 0,
                        'records_published': 0, 'edges': 0, 'edges_published': 0, 'sources': Counter()})
                    rows, edge_rows = [], []
                    for record in records:
                        published_at, source = publication(record, rule)
                        census['records'] += 1
                        if published_at:
                            census['records_published'] += 1
                        if source:
                            census['sources'][source] += 1
                        rows.append((ref['dataset'], ref.get('stage', 'final'), ref['version'], input_ref, record['id'],
                                     record.get('entity_id', record['id']) if record['kind'] == 'entity' else None,
                                     record['kind'], record.get('metric'), record.get('subject'), record.get('object'),
                                     time_key(record['observed_at']), published_at, source,
                                     time_key(record.get('valid_from')),
                                     time_key(record.get('valid_to')), body(canonical(record))))
                        if record['kind'] == 'assertion' and record.get('object') and record.get('subject'):
                            edge_rows.append((len(rows) - 1, record, published_at))
                            census['edges'] += 1
                            if published_at:
                                census['edges_published'] += 1
                        if len(rows) >= batch_size:
                            edges += self._flush(connection, rows, edge_rows, dataset_id)
                            count += len(rows)
                            rows, edge_rows = [], []
                    edges += self._flush(connection, rows, edge_rows, dataset_id)
                    count += len(rows)
                connection.executescript('''
                    CREATE INDEX subject_idx ON records(subject);
                    CREATE INDEX object_idx ON records(object);
                    CREATE INDEX entity_idx ON records(entity_id);
                    CREATE INDEX metric_idx ON records(kind,metric);
                    CREATE INDEX published_idx ON records(published_at);
                    CREATE INDEX edge_subject_idx ON edges(subject, predicate);
                    CREATE INDEX edge_object_idx ON edges(object, predicate);
                    CREATE INDEX edge_predicate_idx ON edges(predicate);
                    CREATE INDEX edge_published_idx ON edges(published_at);
                    CREATE INDEX resolved_canonical_idx ON resolved(canonical_id);
                ''')
                report = _coverage_report(coverage)
                connection.executemany('INSERT INTO metadata VALUES (?,?)', [
                    ('inputs', canonical(refs).decode()),
                    ('schema_version', SCHEMA_VERSION),
                    ('edge_datasets', canonical(edge_datasets).decode()),
                    ('publication_rules', canonical(applied).decode()),
                    ('publication_coverage', canonical(report).decode())])
            if after:
                after()
            connection.close()
            os.replace(temporary, self.path)
            return {'records': count, 'edges': edges, 'inputs': refs, 'path': str(self.path),
                    'publication_coverage': report}
        finally:
            connection.close()
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _flush(connection, rows, edge_rows, dataset_id):
        if not rows:
            return 0
        cursor = connection.execute('SELECT COALESCE(MAX(rowid), 0) FROM records')
        base = cursor.fetchone()[0]
        connection.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        # rowids are assigned sequentially for appended rows in a fresh table without deletes.
        connection.executemany('INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?)', [
            (r['subject'], r['predicate'], r['object'], edge_weight(r), time_key(r.get('valid_from')),
             time_key(r.get('valid_to')), time_key(r['observed_at']), published_at, dataset_id, base + 1 + offset)
            for offset, r, published_at in edge_rows])
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

    def _schema(self, connection):
        row = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        return row['value'] if row else '0'

    def _as_of(self, connection, valid_at, known_at, include_unknown_publication=False):
        return AsOf(self._schema(connection), valid_at, known_at, include_unknown_publication)

    def _filters(self, connection, valid_at, known_at, include_unknown_publication=False):
        """Back-compatible helper: the WHERE fragment plus the as-of policy that produced it."""
        as_of = self._as_of(connection, valid_at, known_at, include_unknown_publication)
        suffix, args = as_of.filters()
        return suffix, args, as_of

    @staticmethod
    def _edge_datasets(connection):
        row = connection.execute("SELECT value FROM metadata WHERE key='edge_datasets'").fetchone()
        return json.loads(row['value']) if row else []

    def _count_drops(self, connection, as_of, table, scope, params, *, dataset_column=None):
        """Count, by dataset, the rows in this query's scope whose publication date is unknown.

        Under the default policy those are the rows the query withheld; under
        ``include_unknown_publication`` they are the rows it let through on ingestion time alone.
        ``as_of.report`` labels them accordingly. ``scope`` is the query's own WHERE body (without
        the as-of clauses) and ``params`` its arguments. The count is of *candidate* rows, before
        any result limit: a ``LIMIT`` narrows what is returned, never what the policy did.
        """
        clause, drop_args = as_of.drop_clause()
        if clause is None:
            return
        if dataset_column is None:
            dataset_column = 'dataset' if table == 'records' else 'dataset_id'
        rows = connection.execute(
            'SELECT %s AS bucket, COUNT(*) AS n FROM %s WHERE %s AND %s GROUP BY bucket'
            % (dataset_column, table, scope, clause), [*params, *drop_args])
        names = self._edge_datasets(connection) if table == 'edges' else None
        for row in rows:
            key = row['bucket']
            if names is not None:
                item = names[key] if isinstance(key, int) and 0 <= key < len(names) else None
                key = item['dataset'] if item else 'dataset_id:%s' % (key,)
            as_of.excluded[key] += row['n']

    @staticmethod
    def _decode(row):
        body = row['body']
        return {**json.loads(zlib.decompress(body) if isinstance(body, bytes) else body), '_provenance': {
            'input': json.loads(row['input_ref']), 'record_id': row['id']}}

    # -- original queries (same shape; as-of results now carry a publication disclosure) ---------
    def neighbors(self, entity, hops=1, limit=100, valid_at=None, known_at=None,
                  include_unknown_publication=False):
        if not 1 <= hops <= 6 or not 1 <= limit <= 1000:
            raise ValueError('hops must be 1..6 and limit 1..1000')
        from .model import identifier
        identifier(entity)
        visited, frontier, claims = {entity}, {entity}, {}
        truncated = False
        connection = self._connect()
        try:
            suffix, time_args, as_of = self._filters(connection, valid_at, known_at, include_unknown_publication)
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
            for node in sorted(visited):
                self._count_drops(connection, as_of, 'records',
                                  "kind IN ('assertion','entity') AND (subject=? OR object=? OR entity_id=?)",
                                  [node, node, node])
            return {'root': entity, 'entities': entities, 'assertions': list(claims.values()),
                    'truncated': truncated, 'hops': hops, 'publication': as_of.report()}
        finally:
            connection.close()

    def observations(self, metric, limit=100, valid_at=None, known_at=None, include_unknown_publication=False):
        """``{'metric', 'records', 'publication'}``. ``records`` was this method's whole return
        value before the publication date existed; the disclosure now travels with it."""
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be 1..1000')
        connection = self._connect()
        try:
            suffix, args, as_of = self._filters(connection, valid_at, known_at, include_unknown_publication)
            records = [self._decode(row) for row in connection.execute(
                "SELECT * FROM records WHERE kind='observation' AND metric=?" + suffix
                + ' ORDER BY dataset,stage,version,id LIMIT ?', [metric, *args, limit])]
            self._count_drops(connection, as_of, 'records', "kind='observation' AND metric=?", [metric])
            return {'metric': metric, 'records': records, 'publication': as_of.report()}
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

    def publication_coverage(self):
        """What share of this index carries a real publication date, per dataset and overall.

        Read from ``metadata`` (written at build time), so it costs one row, not a scan. An
        index built before schema 4 has none and says so.
        """
        connection = self._connect()
        try:
            schema = self._schema(connection)
            row = connection.execute("SELECT value FROM metadata WHERE key='publication_coverage'").fetchone()
            rules = connection.execute("SELECT value FROM metadata WHERE key='publication_rules'").fetchone()
            if row is None:
                return {'graph_schema': schema, 'publication_dates_available': False, 'coverage': None,
                        'reason': 'This index was built before graph schema %s and records no publication dates.'
                                  % PUBLICATION_SCHEMA}
            return {'graph_schema': schema, 'publication_dates_available': schema >= PUBLICATION_SCHEMA,
                    'coverage': json.loads(row['value']),
                    'rules_applied': json.loads(rules['value']) if rules else {}}
        finally:
            connection.close()

    def resolved_entity(self, entity, *, limit=100, valid_at=None, known_at=None, include_unknown_publication=False):
        """Canonical ID, cluster members and their entity records; source records are unchanged."""
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be 1..1000')
        connection = self._connect(require='3')
        try:
            suffix, args, as_of = self._filters(connection, valid_at, known_at, include_unknown_publication)
            canonical_id = self._canonical(connection, entity)
            members = self._members(connection, canonical_id)
            records = []
            for member in members:
                for row in connection.execute("SELECT * FROM records WHERE kind='entity' AND entity_id=?" + suffix
                                              + ' ORDER BY dataset,stage,version,id LIMIT ?', [member, *args, limit - len(records)]):
                    records.append(self._decode(row))
                if len(records) >= limit:
                    break
            for member in members:
                self._count_drops(connection, as_of, 'records', "kind='entity' AND entity_id=?", [member])
            view = connection.execute("SELECT value FROM metadata WHERE key='resolution'").fetchone()
            return {'entity': entity, 'canonical_id': canonical_id, 'members': members, 'entities': records,
                    'truncated': len(records) >= limit, 'resolution': json.loads(view['value']) if view else None,
                    'publication': as_of.report()}
        finally:
            connection.close()

    # -- scalable traversal -------------------------------------------------------------------------
    def _edge_query(self, connection, predicates, min_weight, valid_at, known_at, include_unknown_publication=False):
        """``(as-of suffix, args, extra scope, extra args, as_of)``: the as-of clauses are separated
        from the predicate/weight scope so the exclusion count can reuse the scope alone."""
        as_of = self._as_of(connection, valid_at, known_at, include_unknown_publication)
        suffix, args = as_of.filters()
        scope, scope_args = '', []
        if predicates:
            scope += ' AND predicate IN (%s)' % ','.join('?' * len(predicates))
            scope_args.extend(predicates)
        if min_weight is not None:
            scope += ' AND weight >= ?'
            scope_args.append(float(min_weight))
        return suffix + scope, args + scope_args, scope, scope_args, as_of

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

    def _count_incident_drops(self, connection, as_of, nodes, direction, scope, scope_args):
        """Edges incident to the nodes a traversal reached that the as-of policy withheld.

        An edge reachable only *through* a withheld edge is not counted: the traversal never
        got to its endpoint, so the index cannot say what was on the other side. That is the
        honest bound, and it is why the count is a floor, not a total.
        """
        clause, drop_args = as_of.drop_clause()
        if clause is None:
            return
        nodes = list(nodes)
        for start in range(0, len(nodes), 500):
            chunk = nodes[start:start + 500]
            marks = ','.join('?' * len(chunk))
            parts, params = [], []
            for column in (('subject',) if direction == 'out' else ('object',) if direction == 'in'
                           else ('subject', 'object')):
                parts.append('SELECT e.rowid AS eid, e.dataset_id AS dataset_id FROM edges e WHERE %s IN (%s)%s AND %s'
                             % (column, marks, scope, clause))
                params.extend([*chunk, *scope_args, *drop_args])
            query = ('SELECT dataset_id AS bucket, COUNT(*) AS n FROM (%s) GROUP BY bucket'
                     % ' UNION '.join(parts))
            names = self._edge_datasets(connection)
            for row in connection.execute(query, params):
                item = names[row['bucket']] if isinstance(row['bucket'], int) and 0 <= row['bucket'] < len(names) else None
                as_of.excluded[item['dataset'] if item else 'dataset_id:%s' % (row['bucket'],)] += row['n']

    def neighborhood(self, entity, *, hops=2, limit=1000, predicates=None, direction='both', min_weight=None,
                     valid_at=None, known_at=None, resolved=False, include_unknown_publication=False):
        """Bounded BFS over the edge index with predicate, weight and bitemporal filters."""
        if not 1 <= hops <= 6 or not 1 <= limit <= 100000:
            raise ValueError('hops must be 1..6 and limit 1..100000')
        if direction not in ('in', 'out', 'both'):
            raise ValueError('direction must be in, out or both')
        from .model import identifier
        identifier(entity)
        connection = self._connect(require='3')
        try:
            suffix, args, scope, scope_args, as_of = self._edge_query(
                connection, predicates, min_weight, valid_at, known_at, include_unknown_publication)
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
            self._count_incident_drops(connection, as_of, sorted(depth), direction, scope, scope_args)
            return {'root': entity, 'canonical_root': root, 'resolved': resolved, 'hops': hops,
                    'nodes': [{'id': node, 'depth': d} for node, d in sorted(depth.items(), key=lambda x: (x[1], x[0]))],
                    'edges': edges, 'truncated': truncated, 'publication': as_of.report()}
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
              valid_at=None, known_at=None, resolved=False, max_expansions=200000,
              include_unknown_publication=False):
        """Shortest paths (up to ``limit``) via bounded BFS; each step lists the supporting edge."""
        if not 1 <= max_hops <= 8 or not 1 <= limit <= 1000:
            raise ValueError('max_hops must be 1..8 and limit 1..1000')
        connection = self._connect(require='3')
        try:
            suffix, args, scope, scope_args, as_of = self._edge_query(
                connection, predicates, min_weight, valid_at, known_at, include_unknown_publication)
            canon = (lambda x: self._canonical(connection, x)) if resolved else (lambda x: x)
            start, goal = canon(source), canon(target)
            if start == goal:
                return {'source': source, 'target': target, 'paths': [[]], 'length': 0, 'truncated': False,
                        'publication': as_of.report()}
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
            self._count_incident_drops(connection, as_of, sorted(parents), direction, scope, scope_args)
            return {'source': source, 'target': target, 'canonical_source': start, 'canonical_target': goal,
                    'paths': paths, 'length': len(paths[0]) if paths else None, 'truncated': truncated or len(paths) >= limit,
                    'expansions': expansions, 'publication': as_of.report()}
        finally:
            connection.close()

    # -- aggregates ----------------------------------------------------------------------------------
    def degree_centrality(self, *, predicates=None, direction='both', weighted=False, limit=100, valid_at=None,
                          known_at=None, resolved=False, include_unknown_publication=False):
        """``{'rows', 'publication'}``; ``rows`` is what this method used to return on its own."""
        if not 1 <= limit <= 10000 or direction not in ('in', 'out', 'both'):
            raise ValueError('Invalid centrality request')
        connection = self._connect(require='3')
        try:
            suffix, args, scope, scope_args, as_of = self._edge_query(
                connection, predicates, None, valid_at, known_at, include_unknown_publication)
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
            rows = [{'node': r['node'], 'score': r['score'], 'degree': r['degree']}
                    for r in connection.execute(query, [*params, limit])]
            self._count_drops(connection, as_of, 'edges', '1=1' + scope, scope_args)
            return {'rows': rows, 'publication': as_of.report()}
        finally:
            connection.close()

    def pagerank(self, *, predicates=None, damping=0.85, iterations=50, tolerance=1e-10, limit=100, max_edges=5000000,
                 valid_at=None, known_at=None, weighted=True, resolved=False, include_unknown_publication=False):
        """Weighted PageRank over the (filtered) directed edge set; refuses above ``max_edges``."""
        if not 0 < damping < 1:
            raise ValueError('damping must be in (0, 1)')
        connection = self._connect(require='3')
        try:
            suffix, args, scope, scope_args, as_of = self._edge_query(
                connection, predicates, None, valid_at, known_at, include_unknown_publication)
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
                self._count_drops(connection, as_of, 'edges', '1=1' + scope, scope_args)
                return {'nodes': 0, 'edges': 0, 'ranks': [], 'iterations': 0, 'converged': True,
                        'publication': as_of.report()}
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
            self._count_drops(connection, as_of, 'edges', '1=1' + scope, scope_args)
            return {'nodes': n, 'edges': total, 'iterations': iteration, 'converged': converged, 'damping': damping,
                    'ranks': [{'node': names[i], 'rank': rank[i]} for i in ranked], 'publication': as_of.report()}
        finally:
            connection.close()

    def flow_aggregate(self, predicate, *, group_by='subject', limit=100, valid_at=None, known_at=None, resolved=False,
                       include_unknown_publication=False):
        """Sum edge weights (e.g. award amounts, shipment volumes) by subject, object or pair."""
        if group_by not in ('subject', 'object', 'pair') or not 1 <= limit <= 10000:
            raise ValueError('group_by must be subject, object or pair')
        connection = self._connect(require='3')
        try:
            suffix, args, scope, scope_args, as_of = self._edge_query(
                connection, [predicate], None, valid_at, known_at, include_unknown_publication)
            node = lambda column: (f'COALESCE((SELECT canonical_id FROM resolved WHERE entity_id = e.{column}), e.{column})'
                                   if resolved else f'e.{column}')
            keys = {'subject': f'{node("subject")} AS subject', 'object': f'{node("object")} AS object',
                    'pair': f'{node("subject")} AS subject, {node("object")} AS object'}[group_by]
            group = {'subject': 'subject', 'object': 'object', 'pair': 'subject, object'}[group_by]
            query = (f'SELECT {keys}, SUM(weight) AS total, COUNT(*) AS edges FROM edges e WHERE 1=1{suffix} '
                     f'GROUP BY {group} ORDER BY total DESC, {group} LIMIT ?')
            rows = [dict(r) for r in connection.execute(query, [*args, limit])]
            self._count_drops(connection, as_of, 'edges', '1=1' + scope, scope_args)
            return {'predicate': predicate, 'group_by': group_by, 'resolved': resolved, 'rows': rows,
                    'publication': as_of.report(),
                    'interpretation': 'Sums edge weights as recorded; units must already agree across the selected edges.'}
        finally:
            connection.close()
