'Bounded public scholarly author directory and publication affiliation years, with explicit ORCID and ROR crosswalks'
from datetime import date
import json
import re
from worldmodel.util import digest

def run(context):
    dataset = 'openalex_people'
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
            author = row.get('id', '')
            if not re.fullmatch('https://openalex.org/A[0-9]+', author):
                raise ValueError('OpenAlex person requires a published author ID')
            person = entity('openalex:' + author.rsplit('/', 1)[-1], 'person', row.get('display_name') or author, source_row=row, identity_basis='OpenAlex_disambiguated_author_record', source_updated_date=row.get('updated_date'))
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
                    role = entity('openalex:affiliation:' + digest([person, org, year]), 'role', 'Publication author affiliation', role_type='publication_affiliation', source_affiliation=affiliation, temporal_resolution='year', interpretation='publication affiliation evidence; no employment or continuous presence asserted')
                    edge(person, 'holds_role', role, start, end, validity_basis='publication_affiliation_year')
                    edge(role, 'role_in_organization', org, start, end, validity_basis='publication_affiliation_year')
            yield from out
