"""OpenSanctions default FollowTheMoney graph -> scoped entity/relationship evidence.

CC BY-NC 4.0 (non-commercial). Two streaming passes with an on-disk SQLite index
keep memory bounded:

1. Seeds are "thing" entities whose ``topics`` match SCOPE_TOPICS. Every relationship
   entity (Ownership, Directorship, …, Sanction) is indexed with its endpoint ids.
2. Relationships touching a seed are kept, their endpoints become in-scope
   counterparties, and matching lines are emitted.

Entity ids are ``opensanctions:<ftm id>``, the same namespace as the ``opensanctions``
targets dataset.
"""
import json
import os
import re
import sqlite3
import tempfile

from .countries import country_entity, iso3_from_iso2
from .evidence import Evidence

SCOPE_PREFIXES = ('sanction', 'role.pep', 'role.rca', 'poi', 'crime', 'wanted', 'export.control')
# schema -> (source property, target property, default predicate)
RELATIONSHIPS = {
    'Ownership': ('owner', 'asset', 'owns'),
    'Directorship': ('director', 'organization', 'director_of'),
    'Family': ('person', 'relative', 'family_member_of'),
    'Associate': ('person', 'associate', 'associate_of'),
    'Membership': ('member', 'organization', 'member_of_organization'),
    'Employment': ('employee', 'employer', 'employed_by'),
    'Representation': ('agent', 'client', 'represents'),
    'Succession': ('predecessor', 'successor', 'succeeded_by'),
    'UnknownLink': ('subject', 'object', 'linked_to'),
    'Occupancy': ('holder', 'post', 'holds_position'),
}
SANCTION = 'Sanction'
ENTITY_TYPES = {'Person': 'person', 'Company': 'business', 'Organization': 'organization', 'LegalEntity': 'organization',
                'PublicBody': 'government_agency', 'Vessel': 'vessel', 'Airplane': 'aircraft', 'Position': 'office',
                'Security': 'security', 'CryptoWallet': 'account', 'BankAccount': 'account', 'Address': 'location',
                'Asset': 'asset', 'RealEstate': 'asset', 'Vehicle': 'vehicle', 'License': 'contract', 'Contract': 'contract',
                'Project': 'entity', 'Trip': 'entity', 'Documentation': 'publication', 'Article': 'publication'}
IDENTIFIERS = {'leiCode': 'lei', 'imoNumber': 'imo', 'swiftBic': 'swift', 'isinCode': 'isin', 'wikidataId': 'wikidata',
               'mmsi': 'mmsi', 'innCode': 'ru_inn', 'ogrnCode': 'ru_ogrn', 'permId': 'permid', 'dunsCode': 'duns',
               'registrationNumber': None, 'taxNumber': None, 'idNumber': None, 'passportNumber': None,
               'uniqueEntityId': None, 'callSign': None, 'icaoCode': None, 'publicKey': None, 'ticker': None}
COUNTRY_PROPS = {'country': 'associated_country', 'nationality': 'nationality', 'citizenship': 'citizenship',
                 'jurisdiction': 'registered_in', 'mainCountry': 'associated_country', 'flag': 'flag_state'}
SCHEMA_RE = re.compile(r'"schema":\s*"([A-Za-z]+)"')
ID_RE = re.compile(r'"id":\s*"((?:[^"\\]|\\.)*)"')
MAX_LIST = 20
MAX_TEXT = 1000


def in_scope(topics):
    return any(t == p or t.startswith(p + '.') for t in topics for p in SCOPE_PREFIXES)


def first(props, name):
    values = props.get(name) or []
    return values[0] if values else None


def short(value, limit=MAX_TEXT):
    return value if len(value) <= limit else value[:limit] + '…'


def run(context):
    ev = Evidence(context, 'osgraph')
    directory = tempfile.mkdtemp(prefix='osgraph-', dir=str(context.store.scratch_dir(context.definition['id'])))
    path = os.path.join(directory, 'index.sqlite')
    db = sqlite3.connect(path)
    try:
        db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144; PRAGMA temp_store=FILE;'
                         'CREATE TABLE seed (id TEXT PRIMARY KEY) WITHOUT ROWID;'
                         'CREATE TABLE edge (rel TEXT, a TEXT, b TEXT);')
        shards = list(context.raw_shards())
        _index(db, shards)
        db.executescript('CREATE INDEX edge_a ON edge(a); CREATE INDEX edge_b ON edge(b);'
                         'CREATE TABLE keep_rel (id TEXT PRIMARY KEY) WITHOUT ROWID;'
                         'INSERT OR IGNORE INTO keep_rel SELECT rel FROM edge WHERE a IN (SELECT id FROM seed) OR b IN (SELECT id FROM seed);'
                         'CREATE TABLE keep_ent (id TEXT PRIMARY KEY) WITHOUT ROWID;'
                         'INSERT OR IGNORE INTO keep_ent SELECT id FROM seed;'
                         'INSERT OR IGNORE INTO keep_ent SELECT e.a FROM edge e JOIN keep_rel k ON k.id=e.rel WHERE e.a IS NOT NULL;'
                         'INSERT OR IGNORE INTO keep_ent SELECT e.b FROM edge e JOIN keep_rel k ON k.id=e.rel WHERE e.b IS NOT NULL;'
                         'DROP TABLE edge;')
        db.commit()
        for shard in shards:
            yield from _emit(context, ev, db, shard)
    finally:
        db.close()
        ev.close()
        for name in os.listdir(directory):
            os.unlink(os.path.join(directory, name))
        os.rmdir(directory)


def _lines(shard):
    with open(shard['path'], 'rb') as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                yield number, line


def _index(db, shards):
    batch_seed, batch_edge = [], []
    for shard in shards:
        for _, line in _lines(shard):
            head = line[:600].decode('utf-8', 'ignore')
            match = SCHEMA_RE.search(head)
            schema = match[1] if match else None
            if schema in RELATIONSHIPS or schema == SANCTION:
                record = json.loads(line)
                props = record.get('properties') or {}
                if schema == SANCTION:
                    for entity in props.get('entity') or []:
                        batch_edge.append((record['id'], entity, None))
                else:
                    source, target, _ = RELATIONSHIPS[schema]
                    for a in props.get(source) or [None]:
                        for b in props.get(target) or [None]:
                            batch_edge.append((record['id'], a, b))
            elif b'"topics"' in line:
                record = json.loads(line)
                if in_scope((record.get('properties') or {}).get('topics') or []):
                    batch_seed.append((record['id'],))
            if len(batch_edge) >= 50000 or len(batch_seed) >= 50000:
                db.executemany('INSERT OR IGNORE INTO seed VALUES (?)', batch_seed)
                db.executemany('INSERT INTO edge VALUES (?,?,?)', batch_edge)
                batch_seed, batch_edge = [], []
    db.executemany('INSERT OR IGNORE INTO seed VALUES (?)', batch_seed)
    db.executemany('INSERT INTO edge VALUES (?,?,?)', batch_edge)
    db.commit()


def _emit(context, ev, db, shard):
    keep_rel = lambda i: db.execute('SELECT 1 FROM keep_rel WHERE id=?', (i,)).fetchone() is not None
    keep_ent = lambda i: db.execute('SELECT 1 FROM keep_ent WHERE id=?', (i,)).fetchone() is not None
    for number, line in _lines(shard):
        match = ID_RE.search(line[:400].decode('utf-8', 'ignore'))
        if not match:
            continue
        head_schema = SCHEMA_RE.search(line[:600].decode('utf-8', 'ignore'))
        schema = head_schema[1] if head_schema else None
        relationship = schema in RELATIONSHIPS or schema == SANCTION
        ftm_id = json.loads('"' + match[1] + '"')
        if not (keep_rel(ftm_id) if relationship else keep_ent(ftm_id)):
            continue
        record = json.loads(line)
        locator = f'shard:{shard["index"]}/line:{number}'
        if schema == SANCTION:
            yield from _sanction(ev, record, locator)
        elif relationship:
            yield from _relationship(ev, record, schema, locator)
        else:
            yield from _thing(ev, record, locator, seed=db.execute('SELECT 1 FROM seed WHERE id=?', (ftm_id,)).fetchone() is not None)


def _meta(record):
    datasets = record.get('datasets') or []
    return {'ftm_schema': record.get('schema'), 'datasets': datasets[:MAX_LIST], 'dataset_count': len(datasets),
            'first_seen': record.get('first_seen'), 'last_seen': record.get('last_seen')}


def _thing(ev, record, locator, seed):
    key = 'opensanctions:' + record['id']
    props = record.get('properties') or {}
    topics = props.get('topics') or []
    entity = ev.entity(key, ENTITY_TYPES.get(record.get('schema'), 'entity'), record.get('caption') or first(props, 'name') or key,
                       locator, **_meta(record), topics=topics[:MAX_LIST], target=bool(record.get('target')),
                       scope='seed' if seed else 'counterparty')
    if not entity:
        return
    yield entity
    names = [n for n in (props.get('name') or []) + (props.get('alias') or []) if n != entity['label']]
    for index, name in enumerate(dict.fromkeys(names)):
        if index >= MAX_LIST:
            break
        yield ev.claim(key, 'sanctions_alias', {'name': short(name, 300)}, locator, identity=[record['id'], 'alias', index])
    for prop, predicate in COUNTRY_PROPS.items():
        for code in dict.fromkeys(props.get(prop) or []):
            iso3 = iso3_from_iso2(code.split('-')[0])
            if not iso3:
                continue
            country, country_record = country_entity(ev, iso3, locator)
            if country_record:
                yield country_record
            relation = ev.relation(key, predicate, country, locator, opensanctions_code=code)
            if relation:
                yield relation
    for prop, namespace in IDENTIFIERS.items():
        for index, value in enumerate(dict.fromkeys(props.get(prop) or [])):
            if index >= MAX_LIST:
                break
            claim = {'scheme': prop, 'value': short(value, 300)}
            compact = re.sub(r'\s+', '', value)
            if namespace == 'imo':
                digits = re.sub(r'\D', '', compact)
                if len(digits) == 7:
                    claim['id'] = 'imo:' + digits
            elif namespace:
                claim['id'] = namespace + ':' + compact
            yield ev.claim(key, 'identifier', claim, locator, identity=[record['id'], prop, index])
    for index, value in enumerate(dict.fromkeys(props.get('birthDate') or [])):
        yield ev.claim(key, 'birth_date', value, locator, identity=[record['id'], 'birth', index])
    for index, value in enumerate(dict.fromkeys(props.get('incorporationDate') or [])):
        yield ev.claim(key, 'incorporation_date', value, locator, identity=[record['id'], 'incorporated', index])


def _relationship(ev, record, schema, locator):
    props = record.get('properties') or {}
    source_prop, target_prop, predicate = RELATIONSHIPS[schema]
    role = '; '.join(props.get('role') or []) or None
    if schema == 'Ownership':
        kind = ' '.join((props.get('role') or []) + (props.get('ownershipType') or [])).lower()
        if 'control' in kind or 'beneficial' in kind:
            predicate = 'controls'
    attributes = {'ftm_schema': schema, 'relationship_id': 'opensanctions:' + record['id'], 'role': role}
    for name, out in (('startDate', 'start_date'), ('endDate', 'end_date'), ('date', 'date'), ('percentage', 'percentage'),
                      ('sharesCount', 'shares_count'), ('ownershipType', 'ownership_type'), ('relationship', 'relationship'),
                      ('status', 'status'), ('constituency', 'constituency')):
        values = props.get(name)
        if values:
            attributes[out] = values[0] if len(values) == 1 else values[:MAX_LIST]
    attributes['datasets'] = (record.get('datasets') or [])[:MAX_LIST]
    attributes = {k: v for k, v in attributes.items() if v is not None}
    for a in props.get(source_prop) or []:
        for b in props.get(target_prop) or []:
            yield ev.relation('opensanctions:' + a, predicate, 'opensanctions:' + b, locator,
                              identity=[record['id'], a, b], once=False, **attributes)


def _sanction(ev, record, locator):
    props = record.get('properties') or {}
    program_ids = props.get('programId') or []
    programs = []
    for code in program_ids:
        pid = 'opensanctions:program:' + re.sub(r'[^A-Za-z0-9._-]+', '-', code)
        programs.append(pid)
        program = ev.entity(pid, 'regulation', code, locator, program_id=code)
        if program:
            yield program
    attributes = {'authority': (props.get('authority') or [None])[0], 'program': (props.get('program') or [None])[0],
                  'program_ids': program_ids, 'country': props.get('country') or [], 'status': (props.get('status') or [None])[0],
                  'provisions': [short(p, 300) for p in (props.get('provisions') or [])[:MAX_LIST]],
                  'reason': short(' '.join(props.get('reason') or []), MAX_TEXT) or None,
                  'start_date': first(props, 'startDate'), 'listing_date': first(props, 'listingDate'), 'end_date': first(props, 'endDate'),
                  'source_url': first(props, 'sourceUrl'), 'sanction_id': 'opensanctions:' + record['id'],
                  'datasets': (record.get('datasets') or [])[:MAX_LIST],
                  'date_semantics': 'Sanction startDate (else listingDate/date) as published by the source list'}
    attributes = {k: v for k, v in attributes.items() if v not in (None, [], '')}
    date = next((d for d in (first(props, 'startDate'), first(props, 'listingDate'), first(props, 'date'))
                 if d and re.fullmatch(r'\d{4}-\d{2}-\d{2}', d[:10])), None)
    for entity in props.get('entity') or []:
        key = 'opensanctions:' + entity
        for pid in programs:
            relation = ev.relation(key, 'subject_to_sanctions_program', pid, locator)
            if relation:
                yield relation
        if date:
            yield ev.event('sanctions_designation', date[:10], [key, *programs], locator, [record['id'], entity], attributes=attributes)
        else:
            yield ev.claim(key, 'sanctions_listing', attributes, locator, identity=[record['id'], entity])
