"""Scalable probabilistic entity resolution over SQLite.

Pipeline (each stage is restartable and deterministic):

1. ``add_records``: normalize names/addresses, emit blocking keys (streamed, batched).
2. ``candidate_pairs``: self-join blocks with a maximum block size; oversized blocks
   are skipped and reported (never silently expanded to O(n^2)).
3. ``compare``: discrete agreement patterns per pair (Jaro-Winkler, TF-IDF cosine,
   postal/city/country, identifiers), optionally with worker processes.
4. ``estimate``: u from random pairs, m and prior by EM over pattern counts.
5. ``cluster``: greedy union by descending probability under cannot-link constraints
   (conflicting unique identifiers, rejected reviews, entity types, size caps) with a
   transitivity audit.
6. ``match_assertions`` / ``resolved_view``: explicit ``same_as`` assertions carrying
   score, method, features and reviewer status, plus an auditable, regenerable view.

The SQLite file is a rebuildable work index, not a source of truth.
"""
from collections import Counter
import hashlib
import json
import math
import os
import random
import sqlite3
import time

from .fellegi_sunter import FellegiSunter
from .normalize import normalize_address, normalize_organization, normalize_person, soundex
from .similarity import jaro_winkler

UNIQUE_DEFAULT = ('lei', 'sec_cik', 'bioguide', 'icpsr', 'fec_candidate', 'fec_committee', 'uei', 'duns', 'fdic_cert',
                  'rssd', 'figi', 'isin', 'ofac_sdn', 'imo', 'mmsi', 'wikidata', 'eia_plant')
ORGANIZATION_COMPARISONS = [('name', 4), ('tfidf', 3), ('postal', 3), ('city', 2), ('country', 2), ('identifier', 2)]
PERSON_COMPARISONS = [('name', 4), ('family', 3), ('given', 3), ('birth_year', 2), ('postal', 3), ('identifier', 2)]
REVIEW_STATUSES = ('accepted', 'rejected')

_WORKER = {}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _identifier_map(raw):
    out = {}
    for namespace, values in sorted((raw or {}).items()):
        values = values if isinstance(values, (list, tuple, set)) else [values]
        cleaned = sorted({str(v).strip().upper() for v in values if v not in (None, '') and str(v).strip()})
        if cleaned:
            out[str(namespace).strip().lower()] = cleaned
    return out


def _levels_org(a, b, idf, documents, unique):
    # a/b: (norm, tokens, postal, city, country, ids_json)
    name_a, name_b = a[0], b[0]
    if name_a and name_a == name_b:
        name = 3
    elif not name_a or not name_b:
        name = None
    else:
        jw = jaro_winkler(name_a, name_b)
        name = 2 if jw >= 0.95 else 1 if jw >= 0.85 else 0
    ta, tb = a[1].split(), b[1].split()
    if ta and tb:
        def vector(tokens):
            counts = Counter(tokens)
            vec = {t: c * (math.log((1 + documents) / (1 + idf.get(t, 0))) + 1) for t, c in counts.items()}
            norm = math.sqrt(sum(v * v for v in vec.values()))
            return {t: v / norm for t, v in vec.items()}
        va, vb = vector(ta), vector(tb)
        cosine = sum(v * vb.get(t, 0.0) for t, v in va.items())
        tfidf = 2 if cosine >= 0.85 else 1 if cosine >= 0.5 else 0
    else:
        tfidf = None
    postal = None if not a[2] or not b[2] else 2 if a[2] == b[2] else 1 if a[2][:3] == b[2][:3] else 0
    city = None if not a[3] or not b[3] else int(a[3] == b[3])
    country = None if not a[4] or not b[4] else int(a[4] == b[4])
    return (name, tfidf, postal, city, country, _identifier_level(a[5], b[5], unique))


def _identifier_level(ids_a, ids_b, unique):
    if ids_a == '{}' or ids_b == '{}':
        return None
    a, b = json.loads(ids_a), json.loads(ids_b)
    shared = any(set(a[ns]) & set(b.get(ns, ())) for ns in a)
    if shared:
        return 1
    conflict = any(ns in unique and ns in b and not set(a[ns]) & set(b[ns]) for ns in a)
    return 0 if conflict else None


def _levels_person(a, b, idf, documents, unique):
    # a/b: (norm, tokens, postal, city, country, ids_json, given, family, birth)
    if a[0] and a[0] == b[0]:
        name = 3
    elif not a[0] or not b[0]:
        name = None
    else:
        jw = jaro_winkler(a[0], b[0])
        name = 2 if jw >= 0.95 else 1 if jw >= 0.85 else 0
    family = None if not a[7] or not b[7] else 2 if a[7] == b[7] else 1 if jaro_winkler(a[7], b[7]) >= 0.92 else 0
    given = None if not a[6] or not b[6] else 2 if a[6] == b[6] else 1 if a[6][0] == b[6][0] else 0
    birth = None if not a[8] or not b[8] else int(a[8] == b[8])
    postal = None if not a[2] or not b[2] else 2 if a[2] == b[2] else 1 if a[2][:3] == b[2][:3] else 0
    return (name, family, given, birth, postal, _identifier_level(a[5], b[5], unique))


def _encode(pattern):
    return ''.join('_' if level is None else str(level) for level in pattern)


def _decode(text):
    return tuple(None if c == '_' else int(c) for c in text)


def _compare_chunk(bounds):
    lo, hi = bounds
    state = _WORKER
    connection = sqlite3.connect(state['path'], uri=state['path'].startswith('file:'))
    try:
        rows = connection.execute(state['query'], (lo, hi)).fetchall()
    finally:
        connection.close()
    width = state['width']
    levels = state['levels']
    out = []
    for row in rows:
        a, b = row[1:1 + width], row[1 + width:1 + 2 * width]
        out.append((_encode(levels(a, b, state['idf'], state['documents'], state['unique'])), row[0]))
    return out


class ResolutionEngine:
    def __init__(self, path, *, kind='organization', max_block_size=1000, link_mode='dedupe', unique_namespaces=UNIQUE_DEFAULT,
                 seed=0, fresh=False):
        if kind not in ('organization', 'person'):
            raise ValueError('kind must be organization or person')
        if link_mode not in ('dedupe', 'link'):
            raise ValueError('link_mode must be dedupe (all pairs) or link (cross-source pairs only)')
        if not isinstance(max_block_size, int) or max_block_size < 2:
            raise ValueError('max_block_size must be an integer >= 2')
        self.path = str(path)
        if fresh and self.path != ':memory:' and os.path.exists(self.path):
            os.remove(self.path)
        self.kind, self.max_block_size, self.link_mode = kind, max_block_size, link_mode
        self.unique = frozenset(unique_namespaces)
        self.seed = seed
        self.comparisons = ORGANIZATION_COMPARISONS if kind == 'organization' else PERSON_COMPARISONS
        self.db = sqlite3.connect(self.path)
        # WAL lets forked comparison workers read while the parent writes patterns (no "database is locked").
        self.db.execute('PRAGMA journal_mode=' + ('OFF' if self.path == ':memory:' else 'WAL'))
        self.db.execute('PRAGMA synchronous=OFF')
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute('PRAGMA cache_size=-1048576')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS records(rid INTEGER PRIMARY KEY, entity_id TEXT UNIQUE NOT NULL, source TEXT, sid INTEGER,
                entity_type TEXT, name TEXT, norm TEXT, tokens TEXT, postal TEXT, city TEXT, country TEXT, ids TEXT,
                given TEXT, family TEXT, birth TEXT, legal TEXT, evidence TEXT);
            CREATE TABLE IF NOT EXISTS blocks(key TEXT, rid INTEGER, sid INTEGER);
            CREATE TABLE IF NOT EXISTS pairs(a INTEGER, b INTEGER, pattern TEXT);
            CREATE TABLE IF NOT EXISTS token_df(token TEXT PRIMARY KEY, df INTEGER);
            CREATE TABLE IF NOT EXISTS pattern_scores(pattern TEXT PRIMARY KEY, weight REAL, probability REAL, count INTEGER);
            CREATE TABLE IF NOT EXISTS clusters(rid INTEGER PRIMARY KEY, cluster TEXT);
            CREATE TABLE IF NOT EXISTS reviews(a TEXT, b TEXT, status TEXT, reviewer TEXT, reason TEXT, PRIMARY KEY(a,b));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        ''')
        self.model = None
        self.timings = {}
        self._df = Counter()
        self._sources = {}
        self.stats = {}

    # -- loading ------------------------------------------------------------------------
    def _source_id(self, source):
        if source not in self._sources:
            self._sources[source] = len(self._sources) + 1
        return self._sources[source]

    def _prepare(self, item):
        entity_id, name = item.get('entity_id'), item.get('name')
        if not isinstance(entity_id, str) or ':' not in entity_id:
            raise ValueError('Resolution input needs a namespaced entity_id')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Resolution input needs a nonempty name: ' + entity_id)
        source = str(item.get('source') or entity_id.split(':', 1)[0])
        ids = _identifier_map(item.get('identifiers'))
        address = normalize_address(item['address']) if item.get('address') else None
        postal = (str(item.get('postal') or '') or (address or {}).get('postal5') or '')[:5] or None
        city = ' '.join(normalize_address(item['city'])['tokens']) if item.get('city') else None
        country = str(item['country']).strip().upper() if item.get('country') else None
        if self.kind == 'person':
            normalized = normalize_person(name)
            given, family, legal = normalized['given'], normalized['family'], ''
        else:
            normalized = normalize_organization(name)
            given = family = None
            legal = ' '.join(normalized['legal_forms'])
        birth = str(item['birth_year']) if item.get('birth_year') else None
        tokens = normalized['tokens']
        return (entity_id, source, self._source_id(source), item.get('entity_type'), name, normalized['normalized'],
                ' '.join(tokens), postal, city, country, _json(ids), given, family, birth, legal,
                _json(item.get('evidence')) if item.get('evidence') is not None else None), tokens, ids

    def _keys(self, norm, tokens, postal, country, ids, family, given):
        keys = set()
        compact = norm.replace(' ', '')
        if compact:
            # Scoped by ZIP3/country: an unscoped 6-character prefix grows quadratically with corpus size
            # (it produced >90% of candidate pairs at 200k records). Cross-location typos are still caught by
            # the Soundex key and identifier keys.
            keys.add('p:' + compact[:6] + '|' + (postal[:3] if postal else country or ''))
        if tokens:
            keys.add('f:' + tokens[0] + '|' + (postal[:3] if postal else country or ''))
            keys.add('s:' + ''.join(soundex(t) for t in tokens[:2]) + '|' + (country or ''))
            if len(tokens) > 1:
                keys.add('k:' + ' '.join(sorted(tokens)[:2]))
        if self.kind == 'person' and family:
            keys.add('n:' + soundex(family) + '|' + (given[:1] if given else ''))
        for namespace, values in ids.items():
            for value in values:
                keys.add('i:' + namespace + ':' + value)
        return keys

    def add_records(self, items, *, batch_size=20000):
        started = time.perf_counter()
        record_rows, block_rows, count = [], [], 0
        next_rid = (self.db.execute('SELECT COALESCE(MAX(rid),0) FROM records').fetchone()[0]) + 1

        def flush():
            self.db.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', record_rows)
            self.db.executemany('INSERT INTO blocks VALUES (?,?,?)', block_rows)
            record_rows.clear()
            block_rows.clear()

        with self.db:
            for item in items:
                row, tokens, ids = self._prepare(item)
                rid = next_rid
                next_rid += 1
                record_rows.append((rid, *row))
                self._df.update(set(tokens))
                for key in sorted(self._keys(row[5], tokens, row[7], row[9], ids, row[12], row[11])):
                    block_rows.append((key, rid, row[2]))
                count += 1
                if len(record_rows) >= batch_size:
                    flush()
            flush()
            self.db.executemany('INSERT INTO token_df VALUES (?,?) ON CONFLICT(token) DO UPDATE SET df=excluded.df',
                                sorted(self._df.items()))
        self.timings['add_records'] = self.timings.get('add_records', 0) + time.perf_counter() - started
        self.stats['records'] = self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        return count

    # -- blocking ---------------------------------------------------------------------------
    def _canonicalize(self):
        """Renumber records by entity_id so every downstream order (sampling, tie-breaks) is input-order independent."""
        self.db.execute('DROP TABLE IF EXISTS remap')
        self.db.execute('CREATE TEMP TABLE remap(old INTEGER PRIMARY KEY, new INTEGER)')
        self.db.execute('INSERT INTO remap SELECT rid, ROW_NUMBER() OVER (ORDER BY entity_id) FROM records')
        if self.db.execute('SELECT COUNT(*) FROM remap WHERE old != new').fetchone()[0]:
            columns = [row[1] for row in self.db.execute('PRAGMA table_info(records)')][1:]
            self.db.execute('CREATE TABLE records_canonical AS SELECT m.new AS rid, ' + ', '.join('r.' + c for c in columns)
                            + ' FROM records r JOIN remap m ON m.old = r.rid ORDER BY m.new')
            self.db.execute('DELETE FROM records')
            self.db.execute('INSERT INTO records SELECT * FROM records_canonical')
            self.db.execute('DROP TABLE records_canonical')
            self.db.execute('CREATE TABLE blocks_canonical AS SELECT b.key, m.new AS rid, b.sid FROM blocks b JOIN remap m ON m.old = b.rid')
            self.db.execute('DROP INDEX IF EXISTS blocks_key')
            self.db.execute('DELETE FROM blocks')
            self.db.execute('INSERT INTO blocks SELECT * FROM blocks_canonical ORDER BY key, rid')
            self.db.execute('DROP TABLE blocks_canonical')
        self.db.execute('DROP TABLE remap')

    def candidate_pairs(self):
        started = time.perf_counter()
        with self.db:
            self._canonicalize()
            self.db.execute('DELETE FROM pairs')
            self.db.execute('CREATE INDEX IF NOT EXISTS blocks_key ON blocks(key, rid)')
            self.db.execute('DROP TABLE IF EXISTS block_sizes')
            self.db.execute('CREATE TEMP TABLE block_sizes AS SELECT key, COUNT(*) AS n FROM blocks GROUP BY key')
            oversized = self.db.execute('SELECT COUNT(*), COALESCE(SUM(n),0) FROM block_sizes WHERE n > ?',
                                        (self.max_block_size,)).fetchone()
            examples = [dict(zip(('key', 'size'), r)) for r in self.db.execute(
                'SELECT key, n FROM block_sizes WHERE n > ? ORDER BY n DESC, key LIMIT 20', (self.max_block_size,))]
            source_clause = ' AND x.sid != y.sid' if self.link_mode == 'link' else ''
            self.db.execute('DROP TABLE IF EXISTS raw_pairs')
            self.db.execute('CREATE TEMP TABLE raw_pairs(a INTEGER, b INTEGER)')
            self.db.execute('INSERT INTO raw_pairs SELECT x.rid, y.rid FROM block_sizes s JOIN blocks x ON x.key = s.key '
                            'JOIN blocks y ON y.key = s.key AND x.rid < y.rid WHERE s.n <= ? AND s.n > 1' + source_clause,
                            (self.max_block_size,))
            self.db.execute('INSERT INTO pairs(a, b) SELECT DISTINCT a, b FROM raw_pairs ORDER BY a, b')
            self.db.execute('DROP TABLE raw_pairs')
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS pairs_ab ON pairs(a, b)')
        total = self.db.execute('SELECT COUNT(*) FROM pairs').fetchone()[0]
        n = self.stats.get('records') or self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        self.stats['blocking'] = {'candidate_pairs': total, 'all_pairs': n * (n - 1) // 2,
                                  'reduction_ratio': 1 - total / (n * (n - 1) / 2) if n > 1 else 0.0,
                                  'oversized_blocks': oversized[0], 'oversized_block_members': oversized[1],
                                  'oversized_examples': examples, 'max_block_size': self.max_block_size, 'link_mode': self.link_mode}
        self.timings['candidate_pairs'] = time.perf_counter() - started
        return self.stats['blocking']

    # -- comparison --------------------------------------------------------------------------
    def _columns(self):
        return ['norm', 'tokens', 'postal', 'city', 'country', 'ids'] + (['given', 'family', 'birth'] if self.kind == 'person' else [])

    def compare(self, *, workers=1, chunk_size=200000):
        started = time.perf_counter()
        columns = self._columns()
        select = ', '.join([f'ra.{c}' for c in columns] + [f'rb.{c}' for c in columns])
        query = (f'SELECT p.rowid, {select} FROM pairs p JOIN records ra ON ra.rid = p.a JOIN records rb ON rb.rid = p.b '
                 'WHERE p.rowid BETWEEN ? AND ?')
        idf = dict(self.db.execute('SELECT token, df FROM token_df'))
        documents = self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        low, high = self.db.execute('SELECT COALESCE(MIN(rowid),1), COALESCE(MAX(rowid),0) FROM pairs').fetchone()
        chunks = [(lo, min(lo + chunk_size - 1, high)) for lo in range(low, high + 1, chunk_size)]
        _WORKER.clear()
        _WORKER.update({'path': self.path, 'query': query, 'width': len(columns), 'idf': idf, 'documents': documents,
                        'unique': self.unique, 'levels': _levels_org if self.kind == 'organization' else _levels_person})
        compared = 0
        if workers > 1 and self.path != ':memory:' and len(chunks) > 1:
            import multiprocessing
            self.db.commit()
            context = multiprocessing.get_context('fork')
            with context.Pool(workers) as pool, self.db:
                for result in pool.imap(_compare_chunk, chunks):
                    self.db.executemany('UPDATE pairs SET pattern = ? WHERE rowid = ?', result)
                    compared += len(result)
        else:
            with self.db:
                for lo, hi in chunks:
                    rows = self.db.execute(query, (lo, hi)).fetchall()
                    levels = _WORKER['levels']
                    width = len(columns)
                    result = [(_encode(levels(r[1:1 + width], r[1 + width:], idf, documents, self.unique)), r[0]) for r in rows]
                    self.db.executemany('UPDATE pairs SET pattern = ? WHERE rowid = ?', result)
                    compared += len(result)
        _WORKER.clear()
        self.timings['compare'] = time.perf_counter() - started
        self.stats['compared_pairs'] = compared
        return compared

    # -- estimation --------------------------------------------------------------------------
    def estimate(self, *, u_sample=100000, max_iter=500, tol=1e-7, fix_u=None, training='auto', min_labeled_pairs=30,
                 identifier_m=(0.01, 0.99)):
        """Estimate m/u/prior.

        training='identifier': m from candidate pairs sharing a published unique identifier (labels
        independent of name/address errors), u from random pairs, EM for the prior only.
        training='em': unsupervised EM over all candidate patterns (u initialized from random pairs).
        training='auto': identifier if at least ``min_labeled_pairs`` labeled pairs exist, else em.
        The identifier comparison's m cannot be learned from identifier-selected labels and uses
        ``identifier_m`` (conflict, agreement) as a stated assumption.
        """
        if training not in ('auto', 'em', 'identifier'):
            raise ValueError('training must be auto, em or identifier')
        started = time.perf_counter()
        counts = {_decode(p): c for p, c in self.db.execute('SELECT pattern, COUNT(*) FROM pairs WHERE pattern IS NOT NULL GROUP BY pattern')}
        if not counts:
            raise ValueError('No compared candidate pairs; run candidate_pairs() and compare() first')
        model = FellegiSunter(self.comparisons)
        id_index = [name for name, _ in self.comparisons].index('identifier')
        labeled = {p: c for p, c in counts.items() if p[id_index] == 1}
        method = training
        if training == 'auto':
            method = 'identifier' if sum(labeled.values()) >= min_labeled_pairs else 'em'
        if method == 'identifier' and sum(labeled.values()) < min_labeled_pairs:
            raise ValueError(f'Only {sum(labeled.values())} identifier-labeled pairs; need {min_labeled_pairs}')
        if fix_u is None:
            fix_u = method == 'identifier'
        n = self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        random_counts = Counter()
        if u_sample and n > 1:
            rng = random.Random(self.seed)
            columns = self._columns()
            query = f'SELECT {", ".join(columns)} FROM records WHERE rid = ?'
            idf = dict(self.db.execute('SELECT token, df FROM token_df'))
            levels = _levels_org if self.kind == 'organization' else _levels_person
            max_rid = self.db.execute('SELECT MAX(rid) FROM records').fetchone()[0]
            cache = {}
            def row(rid):
                if rid not in cache:
                    if len(cache) > 200000:
                        cache.clear()
                    cache[rid] = self.db.execute(query, (rid,)).fetchone()
                return cache[rid]
            draws = min(u_sample, n * (n - 1) // 2)
            for _ in range(draws):
                a, b = rng.randint(1, max_rid), rng.randint(1, max_rid)
                if a == b:
                    continue
                ra, rb = row(a), row(b)
                if ra is None or rb is None:
                    continue
                random_counts[levels(ra, rb, idf, n, self.unique)] += 1
            if random_counts:
                model.set_u(random_counts, fixed=fix_u)
        nonmatching = {p: c for p, c in counts.items() if p[id_index] == 0}
        u_source = 'random_pairs'
        if method == 'identifier':
            model.set_m(labeled, exclude=('identifier',), exclude_m={'identifier': list(identifier_m)})
            if sum(nonmatching.values()) >= min_labeled_pairs:
                # Blocked candidates that carry conflicting unique identifiers are known non-matches
                # drawn from the same blocking selection, so they estimate candidate-level u without bias
                # from comparing against uniformly random pairs. The identifier comparison keeps random-pair u.
                model.set_u(nonmatching, fixed=True, exclude=('identifier',), source='identifier_conflict_candidates')
                u_source = 'identifier_conflict_candidates'
        pairs = sum(counts.values())
        # Each record has at most a few true matches, so the matching share of candidates is <= records/pairs.
        model.fit(counts, max_iter=max_iter, tol=tol, initial_lambda=min(0.5, max(1e-4, n / max(pairs, 1) * 0.5)))
        self.model = model
        with self.db:
            self.db.execute('DELETE FROM pattern_scores')
            self.db.executemany('INSERT INTO pattern_scores VALUES (?,?,?,?)',
                                [(_encode(p), model.weight(p), model.probability(p), c) for p, c in sorted(counts.items(), key=lambda x: _encode(x[0]))])
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('model', ?)", (_json(model.to_dict()),))
        self.timings['estimate'] = time.perf_counter() - started
        self.stats['model'] = {**model.to_dict(), 'training': method, 'labeled_pairs': sum(labeled.values()),
                               'labeled_nonmatching_pairs': sum(nonmatching.values()), 'u_estimated_from': u_source,
                               'distinct_patterns': len(counts), 'u_sample_pairs': sum(random_counts.values())}
        return self.stats['model']

    def model_digest(self):
        if self.model is None:
            raise ValueError('Estimate a model first')
        return hashlib.sha256(_json(self.model.to_dict()).encode()).hexdigest()

    # -- reviews -----------------------------------------------------------------------------
    def add_reviews(self, reviews):
        rows = []
        for review in reviews:
            if set(review) - {'a', 'b', 'status', 'reviewer', 'reason'} or review.get('status') not in REVIEW_STATUSES:
                raise ValueError('Review needs a, b, status accepted|rejected, reviewer and reason')
            if not all(isinstance(review.get(k), str) and review[k].strip() for k in ('a', 'b', 'reviewer', 'reason')):
                raise ValueError('Review fields must be nonempty strings')
            a, b = sorted((review['a'], review['b']))
            rows.append((a, b, review['status'], review['reviewer'], review['reason']))
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?)', rows)
        return len(rows)

    # -- clustering --------------------------------------------------------------------------
    def cluster(self, *, threshold=0.95, lower=0.5, max_cluster_size=50, accept='auto', audit_limit=1000):
        """Union accepted links under constraints. accept='auto' uses threshold; 'reviewed_only' uses reviews only."""
        if accept not in ('auto', 'reviewed_only') or not 0 < lower <= threshold <= 1:
            raise ValueError('Invalid clustering policy')
        started = time.perf_counter()
        n_max = self.db.execute('SELECT COALESCE(MAX(rid),0) FROM records').fetchone()[0]
        parent = list(range(n_max + 1))
        size = [1] * (n_max + 1)
        entity = dict(self.db.execute('SELECT rid, entity_id FROM records'))
        rid_of = {v: k for k, v in entity.items()}
        ids = {}
        types = {}
        for rid, raw, etype in self.db.execute('SELECT rid, ids, entity_type FROM records'):
            parsed = {ns: set(v) for ns, v in json.loads(raw).items() if ns in self.unique}
            if parsed:
                ids[rid] = parsed
            if etype:
                types[rid] = {etype}
        cannot = {}
        forced = []
        for a, b, status, reviewer, reason in self.db.execute('SELECT a, b, status, reviewer, reason FROM reviews ORDER BY a, b'):
            if a not in rid_of or b not in rid_of:
                continue
            ra, rb = rid_of[a], rid_of[b]
            if status == 'rejected':
                cannot.setdefault(ra, set()).add(rb)
                cannot.setdefault(rb, set()).add(ra)
            else:
                forced.append((1.0, ra, rb, 'review:' + reviewer))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Transitivity constraint: never merge two clusters that contain a scored non-match pair.
        negative = {}
        for a, b in self.db.execute('SELECT p.a, p.b FROM pairs p JOIN pattern_scores s ON s.pattern = p.pattern '
                                    'WHERE s.probability < ?', (lower,)):
            negative.setdefault(a, []).append(b)
            negative.setdefault(b, []).append(a)
        members = {}

        rejected, rejected_counts, used = [], Counter(), []

        def union(a, b, probability, method):
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            reason = None
            if size[ra] + size[rb] > max_cluster_size:
                reason = 'max_cluster_size'
            elif any(find(x) == rb for x in cannot.get(ra, ())) or any(find(x) == ra for x in cannot.get(rb, ())):
                reason = 'rejected_review'
            else:
                ia, ib = ids.get(ra, {}), ids.get(rb, {})
                if any(ns in ib and not ia[ns] & ib[ns] for ns in ia) or any(len(ia.get(ns, set()) | ib.get(ns, set())) > 1 for ns in set(ia) & set(ib)):
                    reason = 'identifier_conflict'
                else:
                    ta, tb = types.get(ra, set()), types.get(rb, set())
                    if ta and tb and ta != tb:
                        reason = 'entity_type_conflict'
                    elif method.startswith('fellegi_sunter'):
                        small, other = (ra, rb) if size[ra] <= size[rb] else (rb, ra)
                        if any(find(x) == other for m in members.get(small, (small,)) for x in negative.get(m, ())):
                            reason = 'transitivity_conflict'
            if reason:
                rejected_counts[reason] += 1
                if len(rejected) < audit_limit:
                    rejected.append({'a': entity[a], 'b': entity[b], 'probability': probability, 'reason': reason, 'method': method})
                return
            if size[ra] < size[rb] or (size[ra] == size[rb] and ra > rb):
                ra, rb = rb, ra
            parent[rb] = ra
            size[ra] += size[rb]
            members[ra] = members.pop(ra, [ra]) + members.pop(rb, [rb])
            if rb in ids:
                merged = ids.setdefault(ra, {})
                for ns, values in ids.pop(rb).items():
                    merged.setdefault(ns, set()).update(values)
            if rb in types:
                types.setdefault(ra, set()).update(types.pop(rb))
            if rb in cannot:
                cannot.setdefault(ra, set()).update(cannot.pop(rb))
            used.append((entity[a], entity[b], probability, method))

        for probability, a, b, method in forced:
            union(a, b, probability, method)
        if accept == 'auto':
            for a, b, probability in self.db.execute(
                    'SELECT p.a, p.b, s.probability FROM pairs p JOIN pattern_scores s ON s.pattern = p.pattern '
                    'WHERE s.probability >= ? ORDER BY s.probability DESC, p.a, p.b', (threshold,)):
                union(a, b, probability, 'fellegi_sunter_em')
        groups = {}
        for rid in entity:
            groups.setdefault(find(rid), []).append(rid)
        assignments = []
        cluster_of = {}
        for members in groups.values():
            label = min(entity[r] for r in members)
            for r in members:
                assignments.append((r, label))
                cluster_of[r] = label
        with self.db:
            self.db.execute('DELETE FROM clusters')
            self.db.executemany('INSERT INTO clusters VALUES (?,?)', sorted(assignments))
            self.db.execute('CREATE INDEX IF NOT EXISTS clusters_label ON clusters(cluster)')
        # Transitivity audit: scored pairs inside a cluster that the model rates below `lower`.
        violations = []
        for a, b, probability in self.db.execute(
                'SELECT p.a, p.b, s.probability FROM pairs p JOIN pattern_scores s ON s.pattern = p.pattern '
                'JOIN clusters ca ON ca.rid = p.a JOIN clusters cb ON cb.rid = p.b '
                'WHERE ca.cluster = cb.cluster AND s.probability < ? ORDER BY s.probability, p.a, p.b LIMIT ?', (lower, audit_limit)):
            violations.append({'cluster': cluster_of[a], 'a': entity[a], 'b': entity[b], 'probability': probability})
        sizes = Counter(len(m) for m in groups.values())
        self.policy = {'threshold': threshold, 'lower': lower, 'max_cluster_size': max_cluster_size, 'accept': accept,
                       'unique_namespaces': sorted(self.unique), 'link_mode': self.link_mode, 'max_block_size': self.max_block_size}
        self._used_links = sorted(used)
        self.stats['clustering'] = {'clusters': len(groups), 'entities': len(entity), 'merged_entities': len(entity) - len(groups),
                                    'largest_cluster': max(sizes) if sizes else 0, 'size_histogram': dict(sorted(sizes.items())),
                                    'links_used': len(used), 'rejected_merges': dict(rejected_counts),
                                    'rejected_examples': rejected, 'transitivity_violations': violations, 'policy': self.policy}
        self.timings['cluster'] = time.perf_counter() - started
        return self.stats['clustering']

    # -- outputs -----------------------------------------------------------------------------
    def match_assertions(self, *, observed_at, evidence, lower=0.5, threshold=0.95):
        """Yield explicit same_as assertions for candidate pairs with probability >= lower.

        Every assertion is epistemic_status 'inferred' with reviewer_status from reviews (default
        'unreviewed'); decision is auto_match (>= threshold) or possible_match. Nothing is merged here.
        """
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('Match assertions require evidence referencing the resolution input')
        model_digest = self.model_digest()
        reviews = {(a, b): (s, r) for a, b, s, r in self.db.execute('SELECT a, b, status, reviewer FROM reviews')}
        names = [name for name, _ in self.comparisons]
        query = ('SELECT ra.entity_id, rb.entity_id, p.pattern, s.weight, s.probability FROM pairs p '
                 'JOIN pattern_scores s ON s.pattern = p.pattern JOIN records ra ON ra.rid = p.a JOIN records rb ON rb.rid = p.b '
                 'WHERE s.probability >= ? ORDER BY ra.entity_id, rb.entity_id')
        for a, b, pattern, weight, probability in self.db.execute(query, (lower,)):
            subject, obj = sorted((a, b))
            status, reviewer = reviews.get((subject, obj), ('unreviewed', None))
            key = hashlib.sha256(_json([model_digest, subject, obj]).encode()).hexdigest()
            yield {'kind': 'assertion', 'id': 'match:' + key, 'subject': subject, 'predicate': 'same_as', 'object': obj,
                   'observed_at': observed_at, 'evidence': evidence, 'epistemic_status': 'inferred',
                   'confidence': round(probability, 12),
                   'match': {'score': round(probability, 12), 'weight': round(weight, 12), 'method': 'fellegi_sunter_em',
                             'model_digest': model_digest, 'decision': 'auto_match' if probability >= threshold else 'possible_match',
                             'reviewer_status': status, **({'reviewer': reviewer} if reviewer else {}),
                             'features': {n: (None if c == '_' else int(c)) for n, c in zip(names, pattern)}}}

    def clusters(self, *, min_size=2, limit=None):
        query = ('SELECT c.cluster, r.entity_id FROM clusters c JOIN records r ON r.rid = c.rid WHERE c.cluster IN '
                 '(SELECT cluster FROM clusters GROUP BY cluster HAVING COUNT(*) >= ?) ORDER BY c.cluster, r.entity_id')
        current, members, emitted = None, [], 0
        for label, entity_id in self.db.execute(query, (min_size,)):
            if label != current and current is not None:
                yield {'canonical_id': current, 'members': members}
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
                members = []
            current = label
            members.append(entity_id)
        if current is not None and (limit is None or emitted < limit):
            yield {'canonical_id': current, 'members': members}

    def input_digest(self):
        h = hashlib.sha256()
        for row in self.db.execute('SELECT entity_id, source, entity_type, name, postal, city, country, ids, birth FROM records ORDER BY entity_id'):
            h.update(_json(row).encode())
        return h.hexdigest()

    def resolved_view(self):
        """Auditable summary; regenerating with identical inputs, reviews and policy yields identical digests."""
        if 'clustering' not in self.stats:
            raise ValueError('Run cluster() first')
        h = hashlib.sha256()
        for cluster in self.clusters(min_size=2):
            h.update(_json(cluster).encode())
        reviews = [dict(zip(('a', 'b', 'status', 'reviewer', 'reason'), r)) for r in self.db.execute('SELECT * FROM reviews ORDER BY a, b')]
        clustering = self.stats['clustering']
        view = {'method': 'blocking + Fellegi-Sunter (EM) + constrained greedy clustering',
                'input_digest': self.input_digest(), 'model_digest': self.model_digest(), 'policy': self.policy,
                'reviews': reviews, 'clusters_digest': h.hexdigest(),
                'counts': {k: clustering[k] for k in ('clusters', 'entities', 'merged_entities', 'largest_cluster', 'links_used')},
                'rejected_merges': clustering['rejected_merges'], 'transitivity_violations': clustering['transitivity_violations'],
                'blocking': self.stats.get('blocking'), 'interpretation': 'Clusters are inferred candidates; source records are unchanged and '
                'same_as assertions remain reviewable.'}
        view['view_digest'] = hashlib.sha256(_json({k: v for k, v in view.items() if k != 'blocking'}).encode()).hexdigest()
        return view

    def run(self, items, *, workers=1, threshold=0.95, lower=0.5, max_cluster_size=50, u_sample=100000, reviews=()):
        self.add_records(items)
        self.candidate_pairs()
        self.compare(workers=workers)
        self.estimate(u_sample=u_sample)
        if reviews:
            self.add_reviews(reviews)
        self.cluster(threshold=threshold, lower=lower, max_cluster_size=max_cluster_size)
        return self.resolved_view()

    def close(self):
        self.db.close()


def inputs_from_records(records, *, entity_types=None):
    """Build resolution inputs from evidence records (entities + identifier/address assertions)."""
    entities, identifiers, extra = {}, {}, {}
    for record in records:
        if record.get('kind') == 'entity' and record.get('epistemic_status') in (None, 'observed'):
            if entity_types and record.get('entity_type') not in entity_types:
                continue
            key = record.get('entity_id', record['id'])
            if key not in entities:
                source = ((record.get('evidence') or [{}])[0].get('input') or {}).get('dataset') or key.split(':', 1)[0]
                attributes = record.get('attributes') or {}
                entities[key] = {'entity_id': key, 'name': record['label'], 'source': source, 'entity_type': record.get('entity_type'),
                                 'postal': attributes.get('postal_code'), 'city': attributes.get('city'),
                                 'country': attributes.get('country'), 'address': attributes.get('address'),
                                 'birth_year': attributes.get('birth_year')}
        elif record.get('kind') == 'assertion' and record.get('predicate') == 'identifier_assignment':
            value = record.get('value') or {}
            identifiers.setdefault(record['subject'], {}).setdefault(value.get('namespace'), []).append(value.get('value'))
        elif record.get('kind') == 'assertion' and record.get('predicate') in ('postal_code', 'address', 'city', 'country'):
            extra.setdefault(record['subject'], {})[record['predicate'] if record['predicate'] != 'postal_code' else 'postal'] = record.get('value')
    for key, item in sorted(entities.items()):
        item['identifiers'] = identifiers.get(key, {})
        item.update({k: v for k, v in extra.get(key, {}).items() if isinstance(v, str)})
        yield item
