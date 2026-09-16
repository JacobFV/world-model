"""unitedstates/congress-legislators -> people, published ID crosswalks, dated terms, committees and memberships.

Full sharded acquisitions contain the complete JSON files (legislators current/historical, executive,
committees current/historical, current committee membership, social media). Legacy JSONL samples
(one legislator per line) keep the original sample adapter in ``sample``.
Only published identifiers create ``same_as`` links; names are aliases, never merge evidence.
"""
from datetime import date
import json
import re
from worldmodel.util import digest

NAMESPACES = ('bioguide', 'bioguide_previous', 'wikidata', 'fec', 'govtrack', 'lis', 'thomas', 'opensecrets', 'votesmart',
              'icpsr', 'icpsr_prez', 'cspan', 'house_history', 'ballotpedia', 'maplight', 'google_entity_id', 'pictorial')
SOCIAL = ('twitter', 'twitter_id', 'facebook', 'youtube', 'youtube_id', 'instagram', 'instagram_id', 'mastodon')
FILES = ('legislators-current', 'legislators-historical', 'executive', 'committees-current', 'committees-historical',
         'committee-membership-current', 'legislators-social-media')
ROLE_TYPES = {'rep': ('house', 'Representative'), 'sen': ('senate', 'Senator'),
              'prez': ('president', 'President of the United States'), 'viceprez': ('vice_president', 'Vice President of the United States')}
ORGS = {'house': ('us:congress:house', 'US House of Representatives'), 'senate': ('us:congress:senate', 'US Senate'),
        'president': ('usgov:agency:executive_office_of_the_president', 'Executive Office of the President'),
        'vice_president': ('usgov:agency:executive_office_of_the_president', 'Executive Office of the President')}
TERM_FIELDS = ('type', 'start', 'end', 'state', 'district', 'party', 'class', 'state_rank', 'how', 'caucus')
MAX_JSON_BYTES = 256 * 1024 * 1024


def full_layout(context):
    try:
        coverage = context.raw_coverage(0)
    except Exception:
        return False
    return isinstance(coverage, dict) and coverage.get('layout') == 'shards'


def file_name(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '').rsplit('/', 1)[-1]
    for candidate in FILES:
        if name == candidate + '.json':
            return candidate
    raise ValueError(f'Unrecognized congress-legislators shard: {name!r}')


def committee_id(code):
    code = code.strip().lower()
    return 'congress:committee:' + (code + '00' if len(code) == 4 else code)


def label_of(name):
    return name.get('official_full') or ' '.join(str(name[k]) for k in ('first', 'middle', 'last', 'suffix') if name.get(k))


def run(context):
    if not full_layout(context):
        yield from sample(context)
        return
    dataset = context.definition['id']
    emitted = set()
    for index, ref in enumerate(context.raw_inputs):
        shards = {file_name(s): s for s in context.raw_shards(index)}
        for name in FILES:
            shard = shards.get(name)
            if shard is None:
                continue
            if shard['bytes'] > MAX_JSON_BYTES:
                raise ValueError(f'{name}.json exceeds the in-memory JSON cap')
            with open(shard['path'], encoding='utf-8') as stream:
                payload = json.load(stream)
            observed = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
            prefix = f'shard:{shard["index"]}'

            def emit(kind, identity, locator, attributes=None, **fields):
                rid = f'{dataset}:' + digest(identity)[:40]
                if rid in emitted:
                    return None
                emitted.add(rid)
                record = {'kind': kind, 'id': rid, 'observed_at': observed, 'evidence': [{'input': ref, 'locator': locator}],
                          'attributes': {'source_dataset': dataset, 'source_file': name + '.json', **(attributes or {})}, **fields}
                return record

            if name in ('legislators-current', 'legislators-historical', 'executive'):
                for number, row in enumerate(payload):
                    yield from person_records(emit, f'{prefix}/record:{number}', row, name)
            elif name in ('committees-current', 'committees-historical'):
                for number, row in enumerate(payload):
                    yield from committee_records(emit, f'{prefix}/record:{number}', row, name)
            elif name == 'committee-membership-current':
                for code, members in sorted(payload.items()):
                    committee = committee_id(code)
                    for position, member in enumerate(members):
                        bioguide = member.get('bioguide')
                        if not bioguide:
                            continue
                        record = emit('assertion', ['membership', code, bioguide], f'{prefix}/record:0/{code}:{position}',
                                      {'rank': member.get('rank'), 'title': member.get('title'), 'side': member.get('party'),
                                       'temporal_scope': 'current membership file as of retrieval; start date not published'},
                                      subject='bioguide:' + bioguide, predicate='committee_member', object=committee)
                        if record:
                            yield record
            elif name == 'legislators-social-media':
                for number, row in enumerate(payload):
                    bioguide = (row.get('id') or {}).get('bioguide')
                    if not bioguide:
                        continue
                    for namespace in SOCIAL:
                        value = (row.get('social') or {}).get(namespace)
                        if value:
                            record = emit('assertion', ['social', bioguide, namespace, str(value)], f'{prefix}/record:{number}',
                                          {'identity_basis': 'community_published_social_account', 'validity_basis': 'current file; account dates not published'},
                                          subject='bioguide:' + bioguide, predicate='identifier_assignment',
                                          value={'namespace': namespace, 'value': str(value)})
                            if record:
                                yield record


def person_records(emit, locator, row, source):
    ids = row.get('id', {})
    bioguide = ids.get('bioguide')
    if isinstance(bioguide, str) and re.fullmatch('[A-Z][0-9]{6}', bioguide):
        person = 'bioguide:' + bioguide
    elif source == 'executive' and ids.get('govtrack'):
        person = f"govtrack:{ids['govtrack']}"
    else:
        raise ValueError(f'{locator}: congress person requires a published bioguide ID')
    name = row.get('name', {})
    label = label_of(name) or person
    bio = row.get('bio', {})
    record = emit('entity', ['entity', person, source], locator,
                  {'source_authority': 'community_maintained_directory', 'birthday': bio.get('birthday'), 'gender': bio.get('gender'),
                   'in_current_member_file': source == 'legislators-current'},
                  entity_id=person, entity_type='person', label=label)
    if record:
        yield record
    for namespace in NAMESPACES:
        values = ids.get(namespace, [])
        for value in values if isinstance(values, list) else [values]:
            if value is None or not str(value).strip():
                continue
            record = emit('assertion', ['id', person, namespace, str(value)], locator,
                          {'identity_basis': 'published_identifier_crosswalk', 'validity_basis': 'source_does_not_date_identifier'},
                          subject=person, predicate='identifier_assignment', value={'namespace': namespace, 'value': str(value)})
            if record:
                yield record
            target = {'fec': f'fec:candidate:{value}', 'icpsr': f'icpsr:{value}', 'icpsr_prez': f'icpsr:{value}'}.get(namespace)
            if target:
                record = emit('assertion', ['same_as', person, target], locator, {'identity_basis': f'published_congress_{namespace}_crosswalk'},
                              subject=person, predicate='same_as', object=target)
                if record:
                    yield record
    for alternate in [name, *row.get('other_names', [])]:
        alias = label_of(alternate)
        if alias:
            fields = {target: alternate[key] for key, target in (('start', 'valid_from'), ('end', 'valid_to')) if alternate.get(key)}
            record = emit('assertion', ['alias', person, alias, fields], locator, subject=person, predicate='alias', value=alias, **fields)
            if record:
                yield record
    for term in row.get('terms', []):
        if term.get('type') not in ROLE_TYPES:
            raise ValueError(f'{locator}: unknown term type {term.get("type")!r}')
        chamber, title = ROLE_TYPES[term['type']]
        start, end = term.get('start'), term.get('end')
        if not start or not end or date.fromisoformat(start) >= date.fromisoformat(end):
            raise ValueError(f'{locator}: role requires an increasing dated term')
        org, org_label = ORGS[chamber]
        record = emit('entity', ['entity', org], locator, entity_id=org, entity_type='government_agency', label=org_label)
        if record:
            yield record
        compact = {k: term[k] for k in TERM_FIELDS if term.get(k) is not None}
        role = 'congress:role:' + digest([person.split(':', 1)[1], compact])
        where = f" for {term['state']}" if term.get('state') else ''
        record = emit('entity', ['entity', role], locator, {'source_term': compact, 'role_type': term['type'],
                      'jurisdiction_code': term.get('state'), 'district': term.get('district'), 'validity_basis': 'published_term_dates'},
                      entity_id=role, entity_type='role', label=title + where)
        if record:
            yield record
        for predicate, subject, target in (('holds_role', person, role), ('role_in_organization', role, org)):
            record = emit('assertion', [predicate, subject, target, start, end], locator, {'validity_basis': 'published_term_dates'},
                          subject=subject, predicate=predicate, object=target, valid_from=start, valid_to=end)
            if record:
                yield record
        if term.get('party'):
            party = 'congress:party:' + digest(term['party'])
            record = emit('entity', ['entity', party], locator, {'identity_basis': 'source_scoped_party_label', 'legal_identity_unresolved': True},
                          entity_id=party, entity_type='organization', label=term['party'])
            if record:
                yield record
            record = emit('assertion', ['role_affiliation', role, party, start, end], locator, {'validity_basis': 'published_term_dates'},
                          subject=role, predicate='role_affiliation', object=party, valid_from=start, valid_to=end)
            if record:
                yield record


def committee_records(emit, locator, row, source):
    code = row.get('thomas_id')
    if not code:
        return
    committee = committee_id(code)
    attrs = {'chamber': row.get('type'), 'thomas_id': code, 'house_committee_id': row.get('house_committee_id'),
             'senate_committee_id': row.get('senate_committee_id'), 'url': row.get('url'),
             'jurisdiction': (row.get('jurisdiction') or '')[:1000] or None, 'current': source == 'committees-current',
             'identity_basis': 'THOMAS committee code; congress.gov systemCode is lowercase code + 00 (subcommittee: + suffix)'}
    record = emit('entity', ['entity', committee], locator, attrs, entity_id=committee, entity_type='institution', label=row.get('name') or code)
    if record:
        yield record
    if row.get('congresses'):
        record = emit('assertion', ['congresses', committee], locator, subject=committee, predicate='active_in_congresses',
                      value=sorted(row['congresses']))
        if record:
            yield record
    for sub in row.get('subcommittees', []):
        if not sub.get('thomas_id'):
            continue
        child = committee_id(code + sub['thomas_id'])
        record = emit('entity', ['entity', child], locator, {'thomas_id': code + sub['thomas_id'], 'current': source == 'committees-current'},
                      entity_id=child, entity_type='institution', label=sub.get('name') or child)
        if record:
            yield record
        record = emit('assertion', ['part_of', child, committee], locator, subject=child, predicate='part_of', object=committee)
        if record:
            yield record
        if sub.get('congresses'):
            record = emit('assertion', ['congresses', child], locator, subject=child, predicate='active_in_congresses', value=sorted(sub['congresses']))
            if record:
                yield record


def sample(context):
    dataset = 'congress_people'
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        observed = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            out = []

            def emit(kind, identity, attributes=None, **fields):
                rid = 'people:' + digest([dataset, ref, line_number, identity])
                if rid not in seen:
                    seen.add(rid)
                    out.append({'kind': kind, 'id': rid, 'observed_at': observed, 'evidence': context.raw_evidence('line:' + str(line_number), index), 'attributes': {'source_dataset': dataset, 'coverage': 'sample_only', **(attributes or {})}, **fields})

            def entity(key, typ, label, **attrs):
                emit('entity', ['entity', key], attrs, entity_id=key, entity_type=typ, label=label)
                return key

            def assignment(key, namespace, value):
                if value is not None and str(value).strip():
                    emit('assertion', [key, namespace, str(value)], {'identity_basis': 'published_identifier_crosswalk', 'validity_basis': 'source_does_not_date_identifier'}, subject=key, predicate='identifier_assignment', value={'namespace': namespace, 'value': str(value)})

            def edge(subject, predicate, target, start=None, end=None, **attrs):
                fields = {'subject': subject, 'predicate': predicate, 'object': target}
                if start:
                    fields['valid_from'] = start
                if end:
                    fields['valid_to'] = end
                emit('assertion', [subject, predicate, target, start, end, attrs], attrs, **fields)
            ids = row.get('id', {})
            bioguide = ids.get('bioguide', '')
            if not isinstance(bioguide, str) or not re.fullmatch('[A-Z][0-9]{6}', bioguide):
                raise ValueError('Congress person requires a published bioguide ID')
            name = row.get('name', {})
            label = name.get('official_full') or ' '.join((str(name[k]) for k in ('first', 'middle', 'last', 'suffix') if name.get(k)))
            person = entity('bioguide:' + bioguide, 'person', label or bioguide, source_row=row, source_authority='community_maintained_directory')
            for namespace in ('bioguide', 'wikidata', 'fec', 'govtrack', 'lis', 'thomas', 'opensecrets', 'votesmart'):
                values = ids.get(namespace, [])
                for value in values if isinstance(values, list) else [values]:
                    assignment(person, namespace, value)
                    if namespace == 'fec' and value:
                        candidate = entity('fec:candidate:' + str(value), 'person', label or str(value), identity_basis='published_congress_fec_crosswalk')
                        edge(person, 'same_as', candidate, identity_basis='published_congress_fec_crosswalk')
            for alternate in [name, *row.get('other_names', [])]:
                alias = alternate.get('official_full') or ' '.join((str(alternate[k]) for k in ('first', 'middle', 'last', 'suffix') if alternate.get(k)))
                if alias:
                    fields = {}
                    for source, target in (('start', 'valid_from'), ('end', 'valid_to')):
                        if alternate.get(source):
                            fields[target] = alternate[source]
                    emit('assertion', [person, 'alias', alias, fields], subject=person, predicate='alias', value=alias, **fields)
            for term in row.get('terms', []):
                chamber = {'rep': 'house', 'sen': 'senate'}.get(term.get('type'))
                if not chamber:
                    raise ValueError('Unknown congressional term type')
                start, end = (term.get('start'), term.get('end'))
                if not start or not end or date.fromisoformat(start) >= date.fromisoformat(end):
                    raise ValueError('Congress role requires an increasing dated term')
                org = entity('us:congress:' + chamber, 'government_agency', 'US House of Representatives' if chamber == 'house' else 'US Senate')
                role = entity('congress:role:' + digest([bioguide, term]), 'role', ('Representative' if chamber == 'house' else 'Senator') + ' for ' + str(term.get('state', 'unspecified state')), source_term=term, role_type=term['type'], jurisdiction_code=term.get('state'), district=term.get('district'), validity_basis='published_term_dates')
                edge(person, 'holds_role', role, start, end, validity_basis='published_term_dates')
                edge(role, 'role_in_organization', org, start, end, validity_basis='published_term_dates')
                if term.get('party'):
                    party = entity('congress:party:' + digest(term['party']), 'organization', term['party'], identity_basis='source_scoped_party_label', legal_identity_unresolved=True)
                    edge(role, 'role_affiliation', party, start, end, validity_basis='published_term_dates')
            yield from out
