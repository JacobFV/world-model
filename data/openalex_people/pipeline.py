"""OpenAlex institutions and authors with explicit ORCID/ROR crosswalks.

Full acquisitions are cursor-paged JSONL (institution and author records). Names are
aliases, never merge evidence. "Last known institution" is an OpenAlex inference with
no published dates, so it is emitted without a validity interval. Legacy samples keep
publication-affiliation years (no continuous employment inferred).
"""
from datetime import date
import json
import re
from worldmodel.util import digest

INSTITUTION_TYPES = {'company': 'business', 'government': 'government_agency', 'education': 'academic_institution'}


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _short(url, letter):
    value = (url or '').rsplit('/', 1)[-1]
    if not re.fullmatch(letter + r'[0-9]+', value):
        raise ValueError(f'Expected OpenAlex {letter} identifier: {url!r}')
    return value


def _count(base, key, subject, metric, value, unit, day):
    if isinstance(value, int) and not isinstance(value, bool):
        record = {**base, 'kind': 'observation', 'id': f'{key}:{metric}', 'subject': subject, 'metric': metric, 'value': value,
                  'unit': unit, 'dimensions': {'source': 'openalex'}, 'attributes': {'validity_basis': 'cumulative count at source update'}}
        if day:
            record['valid_from'] = day
        return record
    return None


def _full(context, index, receipt):
    observed = receipt['retrieved_at']
    emitted = set()
    seen_records = set()
    for locator, row in context.raw_rows(index, format='jsonl'):
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        source_id = row.get('id') or ''
        day = (row.get('updated_date') or '')[:10] or None
        if day:
            date.fromisoformat(day)
        if '/I' in source_id:
            short = _short(source_id, 'I')
            key = 'openalex_people:' + short
            if key in seen_records:
                continue
            seen_records.add(key)
            entity = 'openalex:' + short
            emitted.add(entity)
            geo = row.get('geo') or {}
            yield {**base, 'kind': 'entity', 'id': key + ':entity', 'entity_id': entity,
                   'entity_type': INSTITUTION_TYPES.get(row.get('type'), 'institution'), 'label': row.get('display_name') or entity,
                   'attributes': {'openalex_type': row.get('type'), 'country_code': row.get('country_code'), 'city': geo.get('city'),
                                  'region': geo.get('region'), 'latitude': geo.get('latitude'), 'longitude': geo.get('longitude'),
                                  'source_updated_date': day, 'identity_basis': 'OpenAlex institution record'}}
            ids = row.get('ids') or {}
            for namespace, value in (('openalex', short), ('ror', (row.get('ror') or ids.get('ror') or '').removeprefix('https://ror.org/')),
                                     ('grid', ids.get('grid')), ('wikidata', (ids.get('wikidata') or '').rsplit('/', 1)[-1])):
                if value:
                    yield {**base, 'kind': 'assertion', 'id': f'{key}:id:{namespace}', 'subject': entity, 'predicate': 'identifier_assignment',
                           'value': {'namespace': namespace, 'value': str(value)},
                           'attributes': {'identity_basis': 'published_identifier_crosswalk', 'validity_basis': 'source_does_not_date_identifier'}}
            for other in sorted({_short(ancestor, 'I') for ancestor in row.get('lineage') or []}):
                if other != short:
                    yield {**base, 'kind': 'assertion', 'id': f'{key}:lineage:{other}', 'subject': entity,
                           'predicate': 'institution_lineage_ancestor', 'object': 'openalex:' + other,
                           'attributes': {'basis': 'OpenAlex lineage (ancestor order not published)'}}
            for metric, field, unit in (('works_count', 'works_count', 'works'), ('citation_count', 'cited_by_count', 'citations')):
                record = _count(base, key, entity, metric, row.get(field), unit, day)
                if record:
                    yield record
        elif '/A' in source_id:
            short = _short(source_id, 'A')
            key = 'openalex_people:' + short
            if key in seen_records:
                continue
            seen_records.add(key)
            person = 'openalex:' + short
            yield {**base, 'kind': 'entity', 'id': key + ':entity', 'entity_id': person, 'entity_type': 'person',
                   'label': row.get('display_name') or person,
                   'attributes': {'identity_basis': 'OpenAlex_disambiguated_author_record', 'source_updated_date': day}}
            ids = row.get('ids') or {}
            for namespace, value in (('openalex', short), ('orcid', (row.get('orcid') or ids.get('orcid') or '').removeprefix('https://orcid.org/')),
                                     ('scopus', ids.get('scopus'))):
                if value:
                    yield {**base, 'kind': 'assertion', 'id': f'{key}:id:{namespace}', 'subject': person, 'predicate': 'identifier_assignment',
                           'value': {'namespace': namespace, 'value': str(value)},
                           'attributes': {'identity_basis': 'published_identifier_crosswalk', 'validity_basis': 'source_does_not_date_identifier'}}
            for institution in row.get('last_known_institutions') or []:
                other = _short(institution.get('id'), 'I')
                org = 'openalex:' + other
                if org not in emitted:
                    emitted.add(org)
                    yield {**base, 'kind': 'entity', 'id': f'{key}:institution:{other}', 'entity_id': org,
                           'entity_type': INSTITUTION_TYPES.get(institution.get('type'), 'institution'),
                           'label': institution.get('display_name') or org,
                           'attributes': {'openalex_type': institution.get('type'), 'country_code': institution.get('country_code'),
                                          'identity_basis': 'OpenAlex institution reference in author record'}}
                yield {**base, 'kind': 'assertion', 'id': f'{key}:last_known:{other}', 'subject': person,
                       'predicate': 'last_known_affiliation', 'object': org,
                       'attributes': {'validity_basis': 'OpenAlex last known institution; start/end dates not published',
                                      'source_updated_date': day, 'interpretation': 'publication affiliation evidence; no employment asserted'}}
            for metric, field, unit in (('works_count', 'works_count', 'works'), ('citation_count', 'cited_by_count', 'citations')):
                record = _count(base, key, person, metric, row.get(field), unit, day)
                if record:
                    yield record
        else:
            raise ValueError(f'{locator}: unexpected OpenAlex record id {source_id!r}')


def _sample(context, index, receipt):
    dataset = 'openalex_people'
    ref = context.raw_inputs[index]
    observed = receipt['retrieved_at']
    seen = set()
    for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        out = []

        def emit(kind, identity, attributes=None, **fields):
            rid = 'people:' + digest([dataset, ref, line_number, identity])
            if rid not in seen:
                seen.add(rid)
                out.append({'kind': kind, 'id': rid, 'observed_at': observed, 'evidence': context.raw_evidence('line:' + str(line_number), index),
                            'attributes': {'source_dataset': dataset, 'coverage': 'sample_only', **(attributes or {})}, **fields})

        def entity(key, typ, label, **attrs):
            emit('entity', ['entity', key], attrs, entity_id=key, entity_type=typ, label=label)
            return key

        def assignment(key, namespace, value):
            if value is not None and str(value).strip():
                emit('assertion', [key, namespace, str(value)], {'identity_basis': 'published_identifier_crosswalk', 'validity_basis': 'source_does_not_date_identifier'},
                     subject=key, predicate='identifier_assignment', value={'namespace': namespace, 'value': str(value)})

        def edge(subject, predicate, target, start=None, end=None, **attrs):
            fields = {'subject': subject, 'predicate': predicate, 'object': target}
            if start:
                fields['valid_from'] = start
            if end:
                fields['valid_to'] = end
            emit('assertion', [subject, predicate, target, start, end, attrs], attrs, **fields)

        author = row.get('id', '')
        if not re.fullmatch('https://openalex.org/A[0-9]+', author):
            raise ValueError('OpenAlex person requires a published author ID')
        person = entity('openalex:' + author.rsplit('/', 1)[-1], 'person', row.get('display_name') or author, source_row=row,
                        identity_basis='OpenAlex_disambiguated_author_record', source_updated_date=row.get('updated_date'))
        assignment(person, 'openalex', author.rsplit('/', 1)[-1])
        if row.get('orcid'):
            assignment(person, 'orcid', row['orcid'].removeprefix('https://orcid.org/'))
        for affiliation in row.get('affiliations', []):
            institution = affiliation.get('institution', {})
            source_id = institution.get('id', '')
            if not re.fullmatch('https://openalex.org/I[0-9]+', source_id):
                raise ValueError('OpenAlex affiliation requires an institution ID')
            org = entity('openalex:' + source_id.rsplit('/', 1)[-1], 'institution', institution.get('display_name') or source_id, source_institution=institution)
            assignment(org, 'openalex', source_id.rsplit('/', 1)[-1])
            if institution.get('ror'):
                assignment(org, 'ror', institution['ror'].removeprefix('https://ror.org/'))
            for year in sorted(set(affiliation.get('years', []))):
                if isinstance(year, bool) or not isinstance(year, int) or (not 1 <= year < 9999):
                    raise ValueError('Invalid OpenAlex affiliation year')
                start, end = (f'{year:04d}-01-01', f'{year + 1:04d}-01-01')
                role = entity('openalex:affiliation:' + digest([person, org, year]), 'role', 'Publication author affiliation', role_type='publication_affiliation',
                              source_affiliation=affiliation, temporal_resolution='year',
                              interpretation='publication affiliation evidence; no employment or continuous presence asserted')
                edge(person, 'holds_role', role, start, end, validity_basis='publication_affiliation_year')
                edge(role, 'role_in_organization', org, start, end, validity_basis='publication_affiliation_year')
        yield from out
