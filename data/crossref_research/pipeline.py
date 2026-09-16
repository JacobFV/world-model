"""Publisher-deposited DOI records; no affiliation tenure or name-based identity inference.

Full acquisitions are cursor-paged Crossref /works JSONL shards. Affiliations without a
ROR identifier stay literal claims on the author (no organization entity is invented
from a name); ROR-identified affiliations point at ``ror:<id>``. Funders are only linked
when Crossref publishes a funder DOI.
"""
from worldmodel.source_records import Emitter, sampled_rows
from worldmodel.util import digest


def run(context):
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index)


def _date(parts):
    try:
        values = (parts or {}).get('date-parts')[0]
    except (TypeError, IndexError):
        return None
    if not values or values[0] is None:
        return None
    return '-'.join(f'{int(v):02d}' if i else f'{int(v):04d}' for i, v in enumerate(values[:3]))


def _full(context, index, receipt):
    observed = receipt['retrieved_at']
    retrieved_day = observed[:10]
    entities = set()
    works = set()
    for locator, row in context.raw_rows(index, format='jsonl'):
        doi = (row.get('DOI') or '').lower().strip()
        if not doi:
            raise ValueError(f'{locator}: Crossref work without DOI')
        if doi in works:
            continue  # cursor pages can repeat a work when the index changes during paging
        works.add(doi)
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        paper = 'doi:' + doi
        key = 'crossref:' + doi
        title = '; '.join(row.get('title') or [])[:500] or doi
        yield {**base, 'kind': 'entity', 'id': key + ':entity', 'entity_id': paper, 'entity_type': 'research_paper',
               'label': title, 'attributes': {'work_type': row.get('type'), 'subjects': (row.get('subject') or [])[:10]}}
        yield {**base, 'kind': 'assertion', 'id': key + ':metadata', 'subject': paper, 'predicate': 'publication_metadata',
               'value': {'doi': doi, 'issued': _date(row.get('issued')), 'published': _date(row.get('published')),
                         'publisher': row.get('publisher'), 'container_title': (row.get('container-title') or [None])[0],
                         'issn': (row.get('ISSN') or [])[:4], 'author_count': len(row.get('author') or []),
                         'omitted_authors': max(0, len(row.get('author') or []) - 100)},
               'attributes': {'source': 'Crossref REST API /works'}}
        if isinstance(row.get('is-referenced-by-count'), int):
            yield {**base, 'kind': 'observation', 'id': key + ':citations', 'subject': paper, 'metric': 'citation_count',
                   'value': row['is-referenced-by-count'], 'unit': 'citations', 'valid_from': retrieved_day,
                   'dimensions': {'citation_source': 'crossref_is_referenced_by'},
                   'attributes': {'validity_basis': 'cumulative count at retrieval'}}
        for i, author in enumerate((row.get('author') or [])[:100]):
            name = ' '.join(str(author.get(k) or '') for k in ('given', 'family')).strip() or author.get('name') or 'unnamed author'
            if author.get('ORCID'):
                person = 'orcid:' + author['ORCID'].rstrip('/').rsplit('/', 1)[-1]
            else:
                person = 'crossref:author:' + digest([doi, i, name])
            if person not in entities:
                if person.startswith('orcid:'):
                    entities.add(person)
                yield {**base, 'kind': 'entity', 'id': f'{key}:author:{i}:entity', 'entity_id': person, 'entity_type': 'researcher',
                       'label': name, 'attributes': {'identity_basis': 'published ORCID' if person.startswith('orcid:') else 'work-scoped author position; no name merge'}}
            yield {**base, 'kind': 'assertion', 'id': f'{key}:author:{i}', 'subject': person, 'predicate': 'authored', 'object': paper,
                   'attributes': {'sequence': author.get('sequence'), 'position': i}}
            for j, affiliation in enumerate((author.get('affiliation') or [])[:20]):
                ror = next((x.get('id') for x in affiliation.get('id') or [] if str(x.get('id-type', '')).upper() == 'ROR'), None)
                attrs = {'tenure': 'unknown; publication metadata only', 'doi': doi}
                if ror:
                    org = 'ror:' + ror.rstrip('/').rsplit('/', 1)[-1]
                    if org not in entities:
                        entities.add(org)
                        yield {**base, 'kind': 'entity', 'id': f'{key}:affiliation:{i}:{j}:entity', 'entity_id': org,
                               'entity_type': 'institution', 'label': affiliation.get('name') or org, 'attributes': {'identity_basis': 'published ROR'}}
                    yield {**base, 'kind': 'assertion', 'id': f'{key}:affiliation:{i}:{j}', 'subject': person,
                           'predicate': 'research_affiliation', 'object': org, 'attributes': attrs}
                elif affiliation.get('name'):
                    yield {**base, 'kind': 'assertion', 'id': f'{key}:affiliation:{i}:{j}', 'subject': person,
                           'predicate': 'research_affiliation_name', 'value': affiliation['name'][:500], 'attributes': attrs}
        for j, funder in enumerate((row.get('funder') or [])[:50]):
            if not funder.get('DOI'):
                continue
            org = 'doi:' + funder['DOI'].lower()
            if org not in entities:
                entities.add(org)
                yield {**base, 'kind': 'entity', 'id': f'{key}:funder:{j}:entity', 'entity_id': org, 'entity_type': 'organization',
                       'label': funder.get('name') or org, 'attributes': {'identity_basis': 'Crossref Funder Registry DOI'}}
            yield {**base, 'kind': 'assertion', 'id': f'{key}:funder:{j}', 'subject': paper, 'predicate': 'funded_by', 'object': org,
                   'attributes': {'awards': (funder.get('award') or [])[:20], 'asserted_by': funder.get('doi-asserted-by')}}


def _sample(context, index):
    emit = Emitter(context)
    for i, line, row, receipt in sampled_rows(context):
        if i != index:
            continue
        emit.at(i, line, row, receipt)
        doi = row['DOI'].lower()
        paper = emit.entity('doi:' + doi, 'research_paper', '; '.join(row.get('title', [])))
        emit.claim(paper, 'publication_metadata', {'doi': doi, 'issued_date_parts': row.get('issued', {}).get('date-parts'),
            'published_print': row.get('published-print'), 'published_online': row.get('published-online'), 'publisher': row.get('publisher'),
            'url': row.get('URL'), 'licenses': row.get('license', []),
            'omitted_authors': max(0, len(row.get('author', [])) - 100),
            'omitted_references': max(0, len(row.get('reference', [])) - 100),
            'omitted_affiliations': sum(max(0, len(a.get('affiliation', [])) - 20) for a in row.get('author', [])[:100])})
        for n, author in enumerate(row.get('author', [])[:100]):
            key = 'orcid:' + author['ORCID'].rsplit('/', 1)[-1] if author.get('ORCID') else 'crossref:author:' + digest([doi, n, author])
            person = emit.entity(key, 'researcher', ' '.join(str(author.get(k, '')) for k in ('given', 'family')))
            emit.relation(person, 'authored', paper)
            for affiliation in author.get('affiliation', [])[:20]:
                if not affiliation.get('name'):
                    continue
                org = emit.entity('crossref:affiliation:' + digest([doi, n, affiliation]), 'academic_institution', affiliation['name'])
                emit.relation(person, 'research_affiliation', org)['attributes']['tenure'] = 'unknown; publication metadata only'
        for reference in row.get('reference', [])[:100]:
            if reference.get('DOI'):
                target = emit.entity('doi:' + reference['DOI'].lower(), 'publication')
                emit.relation(paper, 'cites', target)
        yield from emit.rows
