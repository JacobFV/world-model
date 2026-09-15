"""Public directory identities and source-dated roles; never inferred employment or movement."""
from datetime import date
import json
import re
from .util import digest

SOURCE_IDS = ('congress_people', 'openalex_people')


def schema():
    return {'entity_types': {'role': {'parent': 'entity'}},
            'relations': {'holds_role': {'domain': 'person', 'range': 'role'},
                          'role_in_organization': {'domain': 'role', 'range': 'organization'},
                          'role_affiliation': {'domain': 'role', 'range': 'organization'}},
            'variables': {}}


def normalize(context):
    dataset = context.definition['id']
    if dataset not in SOURCE_IDS:
        raise ValueError('No public people adapter: ' + dataset)
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
                    out.append({'kind': kind, 'id': rid, 'observed_at': observed,
                                'evidence': context.raw_evidence('line:' + str(line_number), index),
                                'attributes': {'source_dataset': dataset, 'coverage': 'sample_only',
                                               **(attributes or {})}, **fields})

            def entity(key, typ, label, **attrs):
                emit('entity', ['entity', key], attrs, entity_id=key, entity_type=typ, label=label)
                return key

            def assignment(key, namespace, value):
                if value is not None and str(value).strip():
                    emit('assertion', [key, namespace, str(value)],
                         {'identity_basis': 'published_identifier_crosswalk', 'validity_basis': 'source_does_not_date_identifier'},
                         subject=key, predicate='identifier_assignment', value={'namespace': namespace, 'value': str(value)})

            def edge(subject, predicate, target, start=None, end=None, **attrs):
                fields = {'subject': subject, 'predicate': predicate, 'object': target}
                if start: fields['valid_from'] = start
                if end: fields['valid_to'] = end
                emit('assertion', [subject, predicate, target, start, end, attrs], attrs, **fields)

            if dataset == 'congress_people':
                ids = row.get('id', {})
                bioguide = ids.get('bioguide', '')
                if not isinstance(bioguide, str) or not re.fullmatch(r'[A-Z][0-9]{6}', bioguide):
                    raise ValueError('Congress person requires a published bioguide ID')
                name = row.get('name', {})
                label = name.get('official_full') or ' '.join(str(name[k]) for k in ('first', 'middle', 'last', 'suffix') if name.get(k))
                person = entity('bioguide:' + bioguide, 'person', label or bioguide,
                                source_row=row, source_authority='community_maintained_directory')
                # Identifier labels come from the documented source crosswalk, never from names.
                for namespace in ('bioguide', 'wikidata', 'fec', 'govtrack', 'lis', 'thomas', 'opensecrets', 'votesmart'):
                    values = ids.get(namespace, [])
                    for value in values if isinstance(values, list) else [values]:
                        assignment(person, namespace, value)
                        if namespace == 'fec' and value:
                            candidate = entity('fec:candidate:' + str(value), 'person', label or str(value),
                                               identity_basis='published_congress_fec_crosswalk')
                            edge(person, 'same_as', candidate, identity_basis='published_congress_fec_crosswalk')
                for alternate in [name, *row.get('other_names', [])]:
                    alias = alternate.get('official_full') or ' '.join(str(alternate[k]) for k in ('first', 'middle', 'last', 'suffix') if alternate.get(k))
                    if alias:
                        fields = {}
                        for source, target in (('start', 'valid_from'), ('end', 'valid_to')):
                            if alternate.get(source): fields[target] = alternate[source]
                        emit('assertion', [person, 'alias', alias, fields], subject=person, predicate='alias', value=alias, **fields)
                for term in row.get('terms', []):
                    chamber = {'rep': 'house', 'sen': 'senate'}.get(term.get('type'))
                    if not chamber:
                        raise ValueError('Unknown congressional term type')
                    start, end = term.get('start'), term.get('end')
                    if not start or not end or date.fromisoformat(start) >= date.fromisoformat(end):
                        raise ValueError('Congress role requires an increasing dated term')
                    org = entity('us:congress:' + chamber, 'government_agency',
                                 'US House of Representatives' if chamber == 'house' else 'US Senate')
                    role = entity('congress:role:' + digest([bioguide, term]), 'role',
                                  ('Representative' if chamber == 'house' else 'Senator') + ' for ' + str(term.get('state', 'unspecified state')),
                                  source_term=term, role_type=term['type'], jurisdiction_code=term.get('state'),
                                  district=term.get('district'), validity_basis='published_term_dates')
                    edge(person, 'holds_role', role, start, end, validity_basis='published_term_dates')
                    edge(role, 'role_in_organization', org, start, end, validity_basis='published_term_dates')
                    if term.get('party'):
                        party = entity('congress:party:' + digest(term['party']), 'organization', term['party'],
                                       identity_basis='source_scoped_party_label', legal_identity_unresolved=True)
                        edge(role, 'role_affiliation', party, start, end, validity_basis='published_term_dates')
            else:
                author = row.get('id', '')
                if not re.fullmatch(r'https://openalex.org/A[0-9]+', author):
                    raise ValueError('OpenAlex person requires a published author ID')
                person = entity('openalex:' + author.rsplit('/', 1)[-1], 'person', row.get('display_name') or author,
                                source_row=row, identity_basis='OpenAlex_disambiguated_author_record',
                                source_updated_date=row.get('updated_date'))
                assignment(person, 'openalex', author.rsplit('/', 1)[-1])
                if row.get('orcid'):
                    assignment(person, 'orcid', row['orcid'].removeprefix('https://orcid.org/'))
                for affiliation in row.get('affiliations', []):
                    institution = affiliation.get('institution', {})
                    source_id = institution.get('id', '')
                    if not re.fullmatch(r'https://openalex.org/I[0-9]+', source_id):
                        raise ValueError('OpenAlex affiliation requires an institution ID')
                    org = entity('openalex:' + source_id.rsplit('/', 1)[-1], 'institution',
                                 institution.get('display_name') or source_id, source_institution=institution)
                    assignment(org, 'openalex', source_id.rsplit('/', 1)[-1])
                    if institution.get('ror'):
                        assignment(org, 'ror', institution['ror'].removeprefix('https://ror.org/'))
                    # These years describe publication affiliations, not employment or residence.
                    for year in sorted(set(affiliation.get('years', []))):
                        if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year < 9999:
                            raise ValueError('Invalid OpenAlex affiliation year')
                        start, end = f'{year:04d}-01-01', f'{year + 1:04d}-01-01'
                        role = entity('openalex:affiliation:' + digest([person, org, year]), 'role',
                                      'Publication author affiliation', role_type='publication_affiliation',
                                      source_affiliation=affiliation, temporal_resolution='year',
                                      interpretation='publication affiliation evidence; no employment or continuous presence asserted')
                        edge(person, 'holds_role', role, start, end, validity_basis='publication_affiliation_year')
                        edge(role, 'role_in_organization', org, start, end, validity_basis='publication_affiliation_year')
            yield from out
