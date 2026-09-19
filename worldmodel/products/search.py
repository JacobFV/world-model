"""The products companion index: name search, sanctions aliases and county-keyed hazard events.

The unified index has no index on labels, and two publishers that describe counties anchor their
records on events rather than on the county (NOAA Storm Events on ``noaa:storm_event:<id>``,
OpenFEMA on ``fema:disaster:<id>``), so a GEOID join never reaches them. ``wm products-index``
reads the unified index once, read-only, and writes a small SQLite file beside it with:

- ``labels``: every published entity label, plus the aliases the sanctions publishers print,
  with an FTS5 token index. This is what ``wm dossier <name>`` and ``wm screen`` search. It mirrors
  ``wm search-entities`` (case-insensitive label and alias match, grouped by asserted cluster)
  over the unified index instead of an in-memory reference graph.
- ``place_events``: storm events and disaster declarations whose *publisher* names a county
  GEOID in the event's participants. That county is the publisher's own field, not a spatial join.
- ``place_named``: OpenFEMA assistance observations, which publish a county *name* and a state
  rather than a GEOID, summed per (state, county name, disaster, metric). Joining these to a GEOID
  is a name match and every answer that uses them says so.

The file pins the unified index it was built from (``inputs_digest``); a product refuses to use a
companion built for a different index.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
import uuid

from .base import (ProductError, canonical_map, connect, dataset_span, decode, index_info, paged_rowids,
                   resolution_digest)

PRODUCTS_INDEX_SCHEMA = '1'
ALIAS_DATASETS = ('ofac_sanctions', 'other_sanctions_lists', 'opensanctions')
ALIAS_PREDICATES = ('sanctions_alias', 'alias', 'also_known_as', 'alternate_name')
STATE_FIPS = {
    '01': ('AL', 'Alabama'), '02': ('AK', 'Alaska'), '04': ('AZ', 'Arizona'), '05': ('AR', 'Arkansas'),
    '06': ('CA', 'California'), '08': ('CO', 'Colorado'), '09': ('CT', 'Connecticut'), '10': ('DE', 'Delaware'),
    '11': ('DC', 'District of Columbia'), '12': ('FL', 'Florida'), '13': ('GA', 'Georgia'), '15': ('HI', 'Hawaii'),
    '16': ('ID', 'Idaho'), '17': ('IL', 'Illinois'), '18': ('IN', 'Indiana'), '19': ('IA', 'Iowa'),
    '20': ('KS', 'Kansas'), '21': ('KY', 'Kentucky'), '22': ('LA', 'Louisiana'), '23': ('ME', 'Maine'),
    '24': ('MD', 'Maryland'), '25': ('MA', 'Massachusetts'), '26': ('MI', 'Michigan'), '27': ('MN', 'Minnesota'),
    '28': ('MS', 'Mississippi'), '29': ('MO', 'Missouri'), '30': ('MT', 'Montana'), '31': ('NE', 'Nebraska'),
    '32': ('NV', 'Nevada'), '33': ('NH', 'New Hampshire'), '34': ('NJ', 'New Jersey'), '35': ('NM', 'New Mexico'),
    '36': ('NY', 'New York'), '37': ('NC', 'North Carolina'), '38': ('ND', 'North Dakota'), '39': ('OH', 'Ohio'),
    '40': ('OK', 'Oklahoma'), '41': ('OR', 'Oregon'), '42': ('PA', 'Pennsylvania'), '44': ('RI', 'Rhode Island'),
    '45': ('SC', 'South Carolina'), '46': ('SD', 'South Dakota'), '47': ('TN', 'Tennessee'), '48': ('TX', 'Texas'),
    '49': ('UT', 'Utah'), '50': ('VT', 'Vermont'), '51': ('VA', 'Virginia'), '53': ('WA', 'Washington'),
    '54': ('WV', 'West Virginia'), '55': ('WI', 'Wisconsin'), '56': ('WY', 'Wyoming'), '60': ('AS', 'American Samoa'),
    '66': ('GU', 'Guam'), '69': ('MP', 'Northern Mariana Islands'), '72': ('PR', 'Puerto Rico'),
    '78': ('VI', 'U.S. Virgin Islands')}
STATE_BY_NAME = {name.casefold(): usps for usps, name in STATE_FIPS.values()}
STATE_BY_USPS = {usps: fips for fips, (usps, _) in STATE_FIPS.items()}
COUNTY_PREFIX = 'geo:US:county:'
_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_.\-]*:\S+$')


def norm(text):
    """Case-, accent- and punctuation-insensitive form used for matching labels."""
    text = unicodedata.normalize('NFKD', str(text).casefold())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(re.sub(r'[^\w]+', ' ', text).split())


def county_name_key(name):
    """'Autauga (County)' and 'Autauga County' both become 'autauga county'."""
    return norm(re.sub(r'\(([^)]*)\)', r' \1 ', str(name)))


def state_usps(value):
    value = str(value or '').strip()
    if value.upper() in STATE_BY_USPS:
        return value.upper()
    return STATE_BY_NAME.get(value.casefold())


def looks_like_id(text):
    return bool(_ID.match(str(text).strip()))


# -- building --------------------------------------------------------------------------------

def build(index, output=None, *, progress=None, batch=20000, parts=('labels', 'aliases', 'places')):
    """Build the products companion index for ``index``; returns a summary with timings.

    Streams: rows go to the output in batches of ``batch``; the only in-memory state is the
    per-(state, county, disaster, metric) OpenFEMA sums.
    """
    from .base import default_products_index
    index = Path(index)
    output = Path(output or default_products_index(index))
    say = progress or (lambda message: None)
    source = connect(index)
    info = index_info(source, index)
    temporary = output.with_name(output.name + '.' + uuid.uuid4().hex + '.tmp')
    target = sqlite3.connect(temporary)
    counts, seconds = {}, {}
    try:
        target.execute('PRAGMA journal_mode=OFF')
        target.execute('PRAGMA synchronous=OFF')
        target.execute('PRAGMA cache_size=-65536')
        target.executescript('''
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE labels (entity_id TEXT, dataset TEXT, entity_type TEXT, label TEXT, norm TEXT, source TEXT);
            CREATE TABLE place_events (geoid TEXT, dataset TEXT, record_rowid INTEGER, record_id TEXT, category TEXT,
                event_type TEXT, occurred_at TEXT, reference TEXT, title TEXT, damage_property REAL, damage_crops REAL,
                deaths REAL, injuries REAL, latitude REAL, longitude REAL);
            CREATE TABLE place_named (state TEXT, county_key TEXT, county_published TEXT, dataset TEXT, disaster TEXT,
                metric TEXT, unit TEXT, total REAL, observations INTEGER);
        ''')
        pinned = info['pinned_versions']
        order = list(pinned)
        if 'labels' in parts:
            start = time.time()
            rows, n = [], 0
            if source.execute("SELECT 1 FROM records WHERE kind='entity' AND metric IS NOT NULL LIMIT 1").fetchone():
                raise ProductError('This index has entity records with a metric; the label scan assumes none')
            for row in _entities(source):
                body = decode(row['body'])
                label = body.get('label')
                n += 1
                if isinstance(label, str) and label.strip() and label != row['entity_id']:
                    rows.append((row['entity_id'], row['dataset'], body.get('entity_type'), label[:500],
                                 norm(label)[:500], 'label'))
                if len(rows) >= batch:
                    target.executemany('INSERT INTO labels VALUES (?,?,?,?,?,?)', rows)
                    counts['labels'] = counts.get('labels', 0) + len(rows)
                    rows = []
                if n % 1_000_000 == 0:
                    say('labels: %d entity records read, %.0f s' % (n, time.time() - start))
            target.executemany('INSERT INTO labels VALUES (?,?,?,?,?,?)', rows)
            counts['labels'] = counts.get('labels', 0) + len(rows)
            counts['entity_records_read'] = n
            seconds['labels'] = round(time.time() - start, 1)
        if 'aliases' in parts:
            start = time.time()
            rows = []
            for dataset in ALIAS_DATASETS:
                if dataset not in pinned:
                    continue
                span = dataset_span(source, dataset, order)
                if not span:
                    continue
                for row in paged_rowids(source, "SELECT subject, body FROM records WHERE rowid > ? AND rowid <= ? "
                                                "AND +kind='assertion' AND +object IS NULL", span):
                    body = decode(row['body'])
                    if body.get('predicate') not in ALIAS_PREDICATES:
                        continue
                    value = body.get('value')
                    name = value.get('name') if isinstance(value, dict) else value
                    if isinstance(name, str) and name.strip():
                        rows.append((row['subject'], dataset, None, name[:500], norm(name)[:500],
                                     'alias:' + body['predicate']))
                say('aliases: %s done, %.0f s' % (dataset, time.time() - start))
            target.executemany('INSERT INTO labels VALUES (?,?,?,?,?,?)', rows)
            counts['aliases'] = len(rows)
            seconds['aliases'] = round(time.time() - start, 1)
        if 'places' in parts:
            start = time.time()
            counts.update(_build_places(source, target, order, batch, say))
            seconds['places'] = round(time.time() - start, 1)
        start = time.time()
        target.executescript('''
            CREATE INDEX labels_norm ON labels(norm);
            CREATE INDEX labels_entity ON labels(entity_id);
            CREATE INDEX place_events_geoid ON place_events(geoid, dataset);
            CREATE INDEX place_named_key ON place_named(state, county_key);
            CREATE VIRTUAL TABLE labels_fts USING fts5(norm, content='labels', content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2');
            INSERT INTO labels_fts(labels_fts) VALUES ('rebuild');
        ''')
        seconds['indexes'] = round(time.time() - start, 1)
        meta = {'schema': PRODUCTS_INDEX_SCHEMA, 'source_index': str(index.resolve()),
                'inputs_digest': info['inputs_digest'], 'resolution_view_digest_at_build': resolution_digest(source),
                'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'counts': counts, 'seconds': seconds,
                'parts': list(parts)}
        target.executemany('INSERT INTO meta VALUES (?,?)', [(k, json.dumps(v)) for k, v in meta.items()])
        target.commit()
        target.close()
        os.replace(temporary, output)
        return {'products_index': str(output), **meta, 'bytes': output.stat().st_size}
    finally:
        target.close()
        source.close()
        Path(temporary).unlink(missing_ok=True)


def _entities(source, page=5000):
    """Every entity record, paged through the (kind, metric) index in rowid order."""
    cursor = 0
    while True:
        rows = source.execute("SELECT rowid, dataset, entity_id, body FROM records WHERE kind='entity' AND metric IS NULL "
                              'AND rowid > ? ORDER BY rowid LIMIT ?', (cursor, page)).fetchall()
        if not rows:
            return
        yield from rows
        cursor = rows[-1]['rowid']


def _build_places(source, target, order, batch, say):
    counts = {'storm_events_with_a_county': 0, 'storm_events_on_a_forecast_zone': 0,
              'disaster_declarations_with_a_county': 0, 'fema_named_county_observations': 0}
    rows = []

    def flush(force=False):
        nonlocal rows
        if rows and (force or len(rows) >= batch):
            target.executemany('INSERT INTO place_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            rows = []

    span = dataset_span(source, 'noaa_storm_events', order)
    if span:
        for row in paged_rowids(source, "SELECT rowid, id, body FROM records WHERE rowid > ? AND rowid <= ? "
                                        "AND +kind='event'", span):
            body = decode(row['body'])
            counties = [p for p in body.get('participants') or [] if str(p).startswith(COUNTY_PREFIX)]
            if not counties:
                counts['storm_events_on_a_forecast_zone'] += 1
                continue
            attributes = body.get('attributes') or {}
            number = lambda *keys: (sum(float(attributes[k]) for k in keys if isinstance(attributes.get(k), (int, float)))
                                    if any(isinstance(attributes.get(k), (int, float)) for k in keys) else None)
            reference = next((p for p in body.get('participants') or [] if str(p).startswith('noaa:storm_event:')), None)
            for county in counties:
                rows.append((county, 'noaa_storm_events', row['rowid'], row['id'], 'storm_event', body.get('event_type'),
                             body.get('occurred_at'), reference, attributes.get('event_type_label'),
                             number('damage_property'), number('damage_crops'),
                             number('deaths_direct', 'deaths_indirect'), number('injuries_direct', 'injuries_indirect'),
                             attributes.get('begin_lat'), attributes.get('begin_lon')))
                counts['storm_events_with_a_county'] += 1
            flush()
        say('places: noaa_storm_events done')
    span = dataset_span(source, 'openfema', order)
    named = {}
    if span:
        for row in paged_rowids(source, "SELECT rowid, id, kind, subject, body FROM records WHERE rowid > ? "
                                        "AND rowid <= ? AND +kind IN ('event', 'observation')", span):
            body = decode(row['body'])
            if row['kind'] == 'event':
                counties = [p for p in body.get('participants') or [] if str(p).startswith(COUNTY_PREFIX)]
                attributes = body.get('attributes') or {}
                reference = next((p for p in body.get('participants') or [] if str(p).startswith('fema:disaster:')),
                                 None)
                for county in counties:
                    rows.append((county, 'openfema', row['rowid'], row['id'], 'disaster_declaration',
                                 attributes.get('incidentType'), body.get('occurred_at'), reference,
                                 '%s %s' % (attributes.get('femaDeclarationString') or '',
                                            attributes.get('declarationTitle') or ''),
                                 None, None, None, None, None, None))
                    counts['disaster_declarations_with_a_county'] += 1
                flush()
                continue
            dimensions = body.get('dimensions') or {}
            county, state = dimensions.get('county'), state_usps(dimensions.get('state'))
            value = body.get('value')
            if not county or not state or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            key = (state, county_name_key(county), str(row['subject']), body.get('metric'), body.get('unit'))
            item = named.setdefault(key, [county, 0.0, 0])
            item[1] += float(value)
            item[2] += 1
            counts['fema_named_county_observations'] += 1
        say('places: openfema done')
    flush(force=True)
    target.executemany('INSERT INTO place_named VALUES (?,?,?,?,?,?,?,?,?)',
                       [(state, key, published, 'openfema', disaster, metric, unit, total, n)
                        for (state, key, disaster, metric, unit), (published, total, n) in named.items()])
    counts['fema_named_county_groups'] = len(named)
    return counts


# -- reading ---------------------------------------------------------------------------------

def open_products_index(path, info):
    """The companion index, or ``None`` when it has not been built. Refuses a stale one."""
    path = Path(path)
    if not path.is_file():
        return None
    connection = connect(path)
    meta = {row['key']: json.loads(row['value']) for row in connection.execute('SELECT key, value FROM meta')}
    if meta.get('inputs_digest') != info['inputs_digest']:
        connection.close()
        raise ProductError('The products index at %s was built for a different unified index (inputs digest %s, '
                           'this index %s); rebuild it with "python3 -m worldmodel products-index"'
                           % (path, meta.get('inputs_digest'), info['inputs_digest']))
    connection.meta = meta
    return connection


def products_index_info(products):
    if products is None:
        return {'available': False,
                'note': 'Name search, sanctions aliases, storm events and disaster assistance need the companion '
                        'index: python3 -m worldmodel products-index'}
    meta = products.meta
    return {'available': True, 'built_at': meta.get('built_at'), 'counts': meta.get('counts'),
            'resolution_view_digest_at_build': meta.get('resolution_view_digest_at_build')}


def _fts_query(tokens):
    quoted = ['"%s"' % token.replace('"', '') for token in tokens]
    if quoted:
        quoted[-1] += '*'
    return ' AND '.join(quoted)


def search(connection, products, text, *, limit=10, prefixes=None, max_rows=400):
    """Asserted-identity clusters whose published label or alias matches ``text``.

    Exact (normalised) label matches rank first, then token matches by BM25. Every candidate is
    labelled as a text match: a name is evidence of nothing about identity.
    """
    if products is None:
        raise ProductError('Searching by name needs the products index; build it with '
                           '"python3 -m worldmodel products-index", or pass a namespaced entity ID')
    key = norm(text)
    tokens = key.split()
    if not tokens:
        raise ProductError('Empty search')
    hits = {}
    for row in products.execute('SELECT entity_id, dataset, entity_type, label, source FROM labels WHERE norm=? '
                                'LIMIT ?', (key, max_rows)):
        rows = hits.setdefault(row['entity_id'], {'rows': [], 'exact': True, 'rank': -1e9})['rows']
        if not any(r['label'] == row['label'] and r['dataset'] == row['dataset'] for r in rows):
            rows.append(dict(row))
    for row in products.execute('SELECT l.entity_id, l.dataset, l.entity_type, l.label, l.source, bm25(labels_fts) AS rank '
                                'FROM labels_fts JOIN labels l ON l.rowid = labels_fts.rowid '
                                'WHERE labels_fts MATCH ? ORDER BY rank LIMIT ?', (_fts_query(tokens), max_rows)):
        item = hits.setdefault(row['entity_id'], {'rows': [], 'exact': False, 'rank': row['rank']})
        item['rank'] = min(item['rank'], row['rank'])
        if not any(r['label'] == row['label'] and r['dataset'] == row['dataset'] for r in item['rows']):
            item['rows'].append({k: row[k] for k in ('entity_id', 'dataset', 'entity_type', 'label', 'source')})
    if prefixes:
        hits = {k: v for k, v in hits.items() if k.startswith(tuple(prefixes))}
    canon = canonical_map(connection, hits)
    clusters = {}
    for entity_id, item in hits.items():
        canonical_id = canon.get(entity_id, entity_id)
        group = clusters.setdefault(canonical_id, {'canonical_id': canonical_id, 'asserted_cluster': entity_id in canon,
                                                   'exact': False, 'rank': 0.0, 'matched': []})
        group['exact'] = group['exact'] or item['exact']
        group['rank'] = min(group['rank'], item['rank'])
        group['asserted_cluster'] = group['asserted_cluster'] or entity_id in canon
        for row in item['rows'][:4]:
            group['matched'].append({'entity_id': entity_id, 'label': row['label'], 'from_dataset': row['dataset'],
                                     'entity_type': row['entity_type'], 'matched_on': row['source']})
    ranked = sorted(clusters.values(), key=lambda c: (not c['exact'],
                                                      -len({m['from_dataset'] for m in c['matched']}), c['rank'],
                                                      c['canonical_id']))
    out = []
    for group in ranked[:limit]:
        out.append({'canonical_id': group['canonical_id'], 'asserted_cluster': group['asserted_cluster'],
                    'match': 'exact label' if group['exact'] else 'label tokens',
                    'match_basis': 'published label or alias text match - INFERRED, not asserted',
                    'matched': group['matched'][:8]})
    return {'query': text, 'normalised': key, 'candidates': out, 'clusters_matched': len(clusters),
            'truncated': len(hits) >= max_rows}


def resolve_input(connection, products, text, *, limit=10):
    """An exact entity ID when the text is one the index knows, else name-search candidates."""
    from .base import exists
    text = str(text).strip()
    if not text:
        raise ProductError('Empty input')
    if looks_like_id(text):
        where = exists(connection, text)
        if where:
            return {'kind': 'id', 'entity_id': text, 'found_in': where,
                    'match_basis': 'exact entity ID supplied by the caller'}
    if products is None:
        return {'kind': 'unresolved', 'input': text,
                'reason': ('not an entity ID in this index' if looks_like_id(text) else 'a name')
                          + '; name search needs the products index (python3 -m worldmodel products-index)'}
    found = search(connection, products, text, limit=limit)
    if not found['candidates']:
        return {'kind': 'not_found', 'input': text, 'search': found}
    return {'kind': 'name', 'input': text, 'search': found}


def main_progress(message):
    print(message, file=sys.stderr, flush=True)
