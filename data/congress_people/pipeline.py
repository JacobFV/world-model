'Community-maintained public Congressional person identifiers and dated House/Senate terms; first 100 source rows only'
from datetime import date
import json
import re
from worldmodel.util import digest

def run(context):
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
