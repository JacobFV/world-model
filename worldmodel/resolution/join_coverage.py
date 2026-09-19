"""How much of a unified index takes part in a cross-dataset identity join, measured.

An entity is *joined* when the evidence about it comes from at least two datasets. Two published
mechanisms put a second dataset behind one entity, and they are counted separately so that the
effect of identity resolution is never confused with the effect of shared naming:

* **shared ID**: two datasets publish an entity record under the same entity ID
  (``geo:US:county:01001`` in both Census geography and FEMA NRI), with no resolution at all;
* **resolved**: the entity ID is in an asserted-identity cluster (the ``resolved`` table that
  ``unify-resolve`` attaches, or a candidate ``clusters.jsonl`` not yet attached), and another
  member of that cluster has an entity record in a different dataset.

Three strengths of "joined" are reported, strictest last:

* ``joined_with_mentions`` (opt-in, index only): a dataset also counts where any of its records
  names a member as ``subject`` or ``object`` - a GLEIF Level 2 relationship about an LEI, a 13F
  holding of a CUSIP. A mention is evidence *about* the entity, not a description of it.
* ``joined``: at least two datasets publish an *entity record* for a member. The headline.
* ``joined_independent``: those datasets come from at least two publishers
  (:func:`publisher_family`), so ``sec_gleif`` + ``gleif_parent_relationships`` or two Census
  products do not count.

The denominator is the distinct entity IDs that have an entity record. Everything runs as SQL
against an on-disk work database, so peak memory does not scale with the index.

What this does **not** establish: that a joined entity is correctly joined (the clusters are only
as good as the published identifiers behind them), that a dataset describes the whole entity, or
that a republished list (the Consolidated Screening List's copy of an OFAC entry) is independent
evidence. ``top_dataset_combinations`` is reported so what each join consists of is visible.
"""
from bisect import bisect_right
from collections import Counter
import json
from pathlib import Path
import random
import re
import sqlite3
import time

WHAT_THIS_DOES_NOT_ESTABLISH = [
    'That a join is correct: clusters are exactly as reliable as the published identifiers behind them.',
    'Independence beyond the publisher: the Consolidated Screening List republishes OFAC entries, so a '
    'us_csl-ofac join is two publishers but one designation. See top_dataset_combinations.',
    'Coverage of the world: this is a share of the entities this scope holds, not of any population '
    '(see coverage-estimate for that).',
    'Mentions, where reported, are evidence about an entity, not a description of it.',
]

# Publishers that print their name differently across the catalog's declarations.
_PUBLISHER_ALIASES = (
    ('gleif', 'GLEIF'), ('securities and exchange commission', 'SEC'), ('census bureau', 'U.S. Census Bureau'),
    ('opensanctions', 'OpenSanctions'), ('federal election commission', 'FEC'),
    ('federal reserve bank of st. louis', 'FRED'), ('openstreetmap', 'OpenStreetMap'), ('cepii', 'CEPII'),
    ('state street', 'State Street'), ('noaa', 'NOAA'), ('energy information administration', 'EIA'),
    ('nasdaq', 'Nasdaq'), ('geological survey', 'USGS'), ('bureau of economic analysis', 'BEA'),
    ('department of agriculture', 'USDA'), ('bureau of labor statistics', 'BLS'),
)


def publisher_family(publisher):
    """A coarse publisher key: the organisation before its division, with known aliases folded."""
    text = str(publisher or '').strip()
    if text == 'SEC':
        return 'SEC'
    lowered = text.lower()
    for needle, family in _PUBLISHER_ALIASES:
        if needle in lowered:
            return family
    return re.split(r'[,(;]', text, maxsplit=1)[0].strip() or None


def publisher_families(catalog_root):
    """``{dataset: publisher family}`` from every ``<catalog>/<dataset>/dataset.json``."""
    families = {}
    for path in sorted(Path(catalog_root).glob('*/dataset.json')):
        try:
            declaration = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        family = publisher_family((declaration.get('source') or {}).get('publisher'))
        families[declaration.get('id', path.parent.name)] = family or declaration.get('id', path.parent.name)
    return families


def dataset_ranges(connection, *, schema='idx', check_samples=2000, seed=0):
    """Contiguous rowid ranges per dataset in ``records``, found by binary search.

    ``unify`` loads datasets one after another into a fresh table, so each dataset's records hold
    one contiguous rowid block. The ranges are found by binary search on that property and then
    spot-checked on ``check_samples`` random rowids; a violated assumption raises instead of
    silently attributing a record to the wrong dataset.
    """
    table = '%s.records' % schema

    def at(rowid):
        return connection.execute('SELECT rowid, dataset FROM %s WHERE rowid >= ? ORDER BY rowid LIMIT 1'
                                  % table, (rowid,)).fetchone()

    lowest = connection.execute('SELECT MIN(rowid) FROM %s' % table).fetchone()[0]
    highest = connection.execute('SELECT MAX(rowid) FROM %s' % table).fetchone()[0]
    ranges = []
    if lowest is None:
        return ranges
    row = at(lowest)
    while row is not None:
        start, dataset = row
        # The first row at or after x belongs to ``dataset`` exactly for x <= its last rowid, so the
        # largest such x is that last rowid.
        low, high = start, highest
        while low < high:
            middle = (low + high + 1) // 2
            probe = at(middle)
            if probe is not None and probe[1] == dataset:
                low = middle
            else:
                high = middle - 1
        ranges.append((start, low, dataset))
        row = at(low + 1)
    names = [r[2] for r in ranges]
    if len(names) != len(set(names)):
        raise ValueError('records are not dataset-contiguous by rowid; mention attribution needs a full scan')
    rng = random.Random(seed)
    starts = [r[0] for r in ranges]
    for rowid in (rng.randint(lowest, highest) for _ in range(check_samples)):
        probe = at(rowid)
        position = bisect_right(starts, probe[0]) - 1
        if ranges[position][2] != probe[1] or probe[0] > ranges[position][1]:
            raise ValueError('rowid %d is %s but its range says %s' % (probe[0], probe[1], ranges[position][2]))
    return ranges


def _load_clusters(work, clusters):
    work.execute('CREATE TABLE res (entity_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL)')
    if clusters == 'index':
        work.execute('INSERT INTO res SELECT entity_id, canonical_id FROM src.resolved')
        return 'resolved table of the index'
    if clusters is None:
        return 'none (shared entity IDs only)'
    path = Path(clusters)
    batch = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            cluster = json.loads(line)
            batch.extend((member, cluster['canonical_id']) for member in cluster['members'])
            if len(batch) >= 50000:
                work.executemany('INSERT OR IGNORE INTO res VALUES (?,?)', batch)
                batch = []
    work.executemany('INSERT OR IGNORE INTO res VALUES (?,?)', batch)
    return str(path)


def _mentions(work, ranges, progress):
    """Distinct (entity ID, dataset) pairs for every subject and object in ``records``."""
    starts = [r[0] for r in ranges]
    names = [r[2] for r in ranges]
    work.execute('CREATE TABLE men (entity_id TEXT, dataset TEXT)')
    for column, index in (('subject', 'subject_idx'), ('object', 'object_idx')):
        started, seen, batch = time.time(), 0, []
        current, datasets = None, set()
        cursor = work.execute('SELECT %s, rowid FROM src.records INDEXED BY %s WHERE %s IS NOT NULL'
                              % (column, index, column))
        for value, rowid in cursor:
            seen += 1
            if value != current:
                batch.extend((current, name) for name in datasets)
                current, datasets = value, set()
                if len(batch) >= 100000:
                    work.executemany('INSERT INTO men VALUES (?,?)', batch)
                    batch = []
            datasets.add(names[bisect_right(starts, rowid) - 1])
            if progress and seen % 20_000_000 == 0:
                progress('%s mentions: %d rows, %.0f s' % (column, seen, time.time() - started))
        batch.extend((current, name) for name in datasets if current is not None)
        work.executemany('INSERT INTO men VALUES (?,?)', batch)


def _count(work, sql, *args):
    return work.execute(sql, args).fetchone()[0]


def _measure(work, *, mentions, families, domain_of, top):
    """The report, from ``eds(entity_id, dataset)``, ``res`` and (optionally) ``men`` in ``work``."""
    work.execute('CREATE TABLE fam (dataset TEXT PRIMARY KEY, family TEXT)')
    datasets = [row[0] for row in work.execute('SELECT DISTINCT dataset FROM eds')]
    work.executemany('INSERT INTO fam VALUES (?,?)', [(d, families.get(d) or d) for d in datasets])
    work.executescript(
        'CREATE TABLE ids AS SELECT DISTINCT entity_id FROM eds;'
        'CREATE TABLE grp AS SELECT i.entity_id AS entity_id, COALESCE(r.canonical_id, i.entity_id) AS g, '
        '  r.entity_id IS NOT NULL AS clustered FROM ids i LEFT JOIN res r ON r.entity_id = i.entity_id;'
        'CREATE INDEX grp_id ON grp(entity_id);'
        # every ID that carries evidence for a group: entity IDs, plus cluster members without records
        'CREATE TABLE members AS SELECT entity_id, g FROM grp UNION SELECT entity_id, canonical_id FROM res;'
        'CREATE INDEX members_id ON members(entity_id);'
        'CREATE TABLE gd AS SELECT DISTINCT m.g AS g, e.dataset AS dataset FROM members m '
        '  JOIN eds e ON e.entity_id = m.entity_id;'
        'CREATE INDEX gd_g ON gd(g, dataset);'
        'CREATE TABLE gn AS SELECT gd.g AS g, COUNT(*) AS n, COUNT(DISTINCT fam.family) AS f FROM gd '
        '  JOIN fam ON fam.dataset = gd.dataset GROUP BY gd.g;'
        'CREATE INDEX gn_g ON gn(g);'
        'CREATE TABLE idn AS SELECT entity_id, COUNT(*) AS n FROM eds GROUP BY entity_id;'
        'CREATE INDEX idn_id ON idn(entity_id);')
    mention_column = mention_join = ''
    if mentions:
        work.executescript(
            'CREATE INDEX men_id ON men(entity_id);'
            'CREATE TABLE gm AS SELECT g, dataset FROM gd UNION '
            '  SELECT m.g, x.dataset FROM members m JOIN men x ON x.entity_id = m.entity_id;'
            'CREATE TABLE gmn AS SELECT g, COUNT(*) AS n FROM gm GROUP BY g;'
            'CREATE INDEX gmn_g ON gmn(g);')
        mention_column, mention_join = ', SUM(gmn.n >= 2)', ' JOIN gmn ON gmn.g = grp.g'
    by_dataset = []
    for row in work.execute(
            'SELECT e.dataset, COUNT(*), SUM(idn.n >= 2), SUM(gn.n >= 2), SUM(gn.f >= 2), SUM(grp.clustered)%s '
            'FROM eds e JOIN grp ON grp.entity_id = e.entity_id JOIN idn ON idn.entity_id = e.entity_id '
            'JOIN gn ON gn.g = grp.g%s GROUP BY e.dataset' % (mention_column, mention_join)):
        dataset, entities, shared, joined, independent, clustered = row[:6]
        item = {'dataset': dataset, 'domain': domain_of.get(dataset), 'publisher': families.get(dataset),
                'entities': entities, 'in_a_cluster': clustered, 'joined_by_shared_id': shared, 'joined': joined,
                'joined_fraction': round(joined / entities, 6), 'joined_independent': independent}
        if mentions:
            item['joined_with_mentions'] = row[6]
        by_dataset.append(item)
    by_dataset.sort(key=lambda item: (-item['entities'], item['dataset']))
    distinct = _count(work, 'SELECT COUNT(*) FROM ids')

    def share(value):
        return round(value / distinct, 6) if distinct else None

    joined = _count(work, 'SELECT COUNT(*) FROM grp JOIN gn ON gn.g = grp.g WHERE gn.n >= 2')
    independent = _count(work, 'SELECT COUNT(*) FROM grp JOIN gn ON gn.g = grp.g WHERE gn.f >= 2')
    shared = _count(work, 'SELECT COUNT(*) FROM idn WHERE n >= 2')
    totals = {'distinct_entity_ids': distinct,
              'entity_ids_in_a_cluster': _count(work, 'SELECT COUNT(*) FROM grp WHERE clustered'),
              'joined_by_shared_id_only': shared, 'joined_by_shared_id_only_fraction': share(shared),
              'joined': joined, 'joined_fraction': share(joined),
              'joined_independent': independent, 'joined_independent_fraction': share(independent),
              'cluster_rows': _count(work, 'SELECT COUNT(*) FROM res'),
              'clusters': _count(work, 'SELECT COUNT(DISTINCT canonical_id) FROM res')}
    if mentions:
        with_mentions = _count(work, 'SELECT COUNT(*) FROM grp JOIN gmn ON gmn.g = grp.g WHERE gmn.n >= 2')
        totals.update(joined_with_mentions=with_mentions, joined_with_mentions_fraction=share(with_mentions))
    domains = {}
    for item in by_dataset:
        slot = domains.setdefault(item['domain'] or 'unassigned', Counter())
        for key in ('entities', 'in_a_cluster', 'joined_by_shared_id', 'joined', 'joined_independent',
                    'joined_with_mentions'):
            if key in item:
                slot[key] += item[key]
    by_domain = {name: dict(values, joined_fraction=round(values['joined'] / values['entities'], 6))
                 for name, values in sorted(domains.items())}
    combinations = [{'datasets': key.split(','), 'groups': count} for key, count in work.execute(
        "SELECT combo, COUNT(*) FROM (SELECT g, GROUP_CONCAT(dataset, ',') AS combo FROM "
        '(SELECT gd.g AS g, gd.dataset AS dataset FROM gd JOIN gn ON gn.g = gd.g WHERE gn.n >= 2 '
        'ORDER BY gd.g, gd.dataset) GROUP BY g) GROUP BY combo ORDER BY 2 DESC, 1 LIMIT ?', (top,))]
    return {'totals': totals, 'by_domain': by_domain, 'by_dataset': by_dataset,
            'top_dataset_combinations': combinations}


def _open_work(workdir):
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / 'join_coverage.sqlite'
    path.unlink(missing_ok=True)
    work = sqlite3.connect(path)
    work.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144; '
                       'PRAGMA temp_store=FILE;')
    return work, path


def _defaults(domain_of, families):
    if domain_of is None:
        from ..unify import DOMAIN_OF as domain_of
    if families is None:
        from ..resources import resource_roots
        families = publisher_families(resource_roots()['catalog'])
    return domain_of, families


def join_coverage(index_path, *, workdir, clusters='index', mentions=False, domain_of=None, families=None,
                  progress=None, top=25):
    """Measure the share of an index's entities whose evidence comes from at least two datasets.

    ``clusters='index'`` uses the index's attached ``resolved`` table; a path evaluates a candidate
    ``clusters.jsonl`` (as ``unify-resolve --no-attach`` writes it), so a resolution can be measured
    before it is attached; ``None`` measures shared entity IDs alone. Returns a JSON-ready report;
    the work database stays in ``workdir`` for follow-up queries.
    """
    domain_of, families = _defaults(domain_of, families)
    index_path = Path(index_path)
    if not index_path.is_file():
        raise ValueError('No index at %s' % index_path)
    started = time.time()
    say = progress or (lambda message: None)
    work, path = _open_work(workdir)
    try:
        work.execute('ATTACH DATABASE ? AS src', ('file:%s?mode=ro' % index_path.resolve(),))
        work.execute('CREATE TABLE ent (entity_id TEXT, dataset TEXT)')
        work.execute("INSERT INTO ent SELECT entity_id, dataset FROM src.records WHERE kind='entity' "
                     "AND entity_id IS NOT NULL")
        records = _count(work, 'SELECT COUNT(*) FROM ent')
        say('entity records copied: %d (%.0f s)' % (records, time.time() - started))
        work.executescript('CREATE TABLE eds AS SELECT DISTINCT entity_id, dataset FROM ent; DROP TABLE ent;'
                           'CREATE INDEX eds_id ON eds(entity_id, dataset);')
        source = _load_clusters(work, clusters)
        if mentions:
            _mentions(work, dataset_ranges(work, schema='src'), say)
            say('mentions attributed (%.0f s)' % (time.time() - started))
        report = _measure(work, mentions=mentions, families=families, domain_of=domain_of, top=top)
        report['totals']['entity_records'] = records
        return {'scope': 'index', 'index': str(index_path), 'resolution': source,
                'measures': ['joined', 'joined_independent'] + (['joined_with_mentions'] if mentions else []),
                **report,
                'definition': ('An entity ID is joined when the entity records of its asserted-identity group (its '
                               'cluster, or itself when unclustered) come from at least two datasets; independent '
                               'when those datasets have at least two publishers. The denominator is distinct '
                               'entity IDs with an entity record.'),
                'what_this_does_not_establish': WHAT_THIS_DOES_NOT_ESTABLISH,
                'seconds': round(time.time() - started, 1), 'work_database': str(path)}
    finally:
        work.close()


def scope_join_coverage(identity_database, *, workdir, clusters, domain_of=None, families=None, top=25):
    """The same measurement over a ``unify-resolve`` scope rather than an index.

    ``unify-resolve`` records every entity record it reads in the ``entities`` table of its work
    database, so the joined share of a scope that has no index - one that adds a bulk dataset such
    as ``companies_house_uk`` - can be measured without building one. Mentions need an index.
    """
    domain_of, families = _defaults(domain_of, families)
    started = time.time()
    work, path = _open_work(workdir)
    try:
        work.execute('ATTACH DATABASE ? AS src', ('file:%s?mode=ro' % Path(identity_database).resolve(),))
        records = _count(work, 'SELECT COUNT(*) FROM src.entities')
        work.executescript('CREATE TABLE eds AS SELECT DISTINCT entity_id, dataset FROM src.entities '
                           'WHERE entity_id IS NOT NULL AND dataset IS NOT NULL;'
                           'CREATE INDEX eds_id ON eds(entity_id, dataset);')
        source = _load_clusters(work, clusters)
        report = _measure(work, mentions=False, families=families, domain_of=domain_of, top=top)
        report['totals']['entity_records'] = records
        return {'scope': 'resolution scope', 'identity_database': str(identity_database), 'resolution': source,
                'measures': ['joined', 'joined_independent'], **report,
                'what_this_does_not_establish': WHAT_THIS_DOES_NOT_ESTABLISH,
                'seconds': round(time.time() - started, 1), 'work_database': str(path)}
    finally:
        work.close()
