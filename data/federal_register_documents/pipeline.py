"""Federal Register document metadata; proposed rule, rule, notice and presidential document are distinct source statuses.

Full sharded acquisitions (API ``documents.json`` records, one per JSONL line) emit per document:
* ``federalregister:{document_number}`` entity (``regulation`` for rules and proposed rules, ``law`` for
  executive orders and proclamations, ``publication`` otherwise)
* ``document_status`` (publication/effective/comment dates; effectiveness is never inferred)
* ``issued_document_by`` agency links (agencies carry ``part_of`` parents), CFR references, RINs, dockets,
  EO/proclamation numbers and topics
Legacy JSONL samples keep the sample adapter.
"""
from worldmodel.source_records import Emitter, sampled_rows

LEGAL = ('Rule', 'Proposed Rule', 'RULE', 'PRORULE')
PRESIDENTIAL_LAW = ('Executive Order', 'Proclamation')


def full_layout(context):
    try:
        coverage = context.raw_coverage(0)
    except Exception:
        return False
    return isinstance(coverage, dict) and coverage.get('layout') == 'shards'


def run(context):
    if not full_layout(context):
        yield from sample(context)
        return
    dataset = context.definition['id']
    documents, entities = set(), set()
    for index, ref in enumerate(context.raw_inputs):
        observed_default = context.raw_receipt(index)['retrieved_at']
        observed_by_shard = {s['index']: s.get('retrieved_at') or observed_default for s in context.raw_shards(index)}
        for locator, row in context.raw_rows(index, format='jsonl'):
            number = row.get('document_number')
            if not number:
                raise ValueError(f'{locator}: Federal Register record without document_number')
            if number in documents:
                continue
            documents.add(number)
            shard_index = int(locator.split('/', 1)[0].split(':')[1])
            evidence = [{'input': ref, 'locator': locator}]
            observed = observed_by_shard.get(shard_index, observed_default)

            def rec(record, **attributes):
                record.update(observed_at=observed, evidence=evidence, attributes={'source_dataset': dataset, **attributes})
                return record

            typ, subtype = row.get('type'), row.get('subtype')
            entity_type = 'regulation' if typ in LEGAL else 'law' if subtype in PRESIDENTIAL_LAW else 'publication'
            document = 'federalregister:' + number
            yield rec({'kind': 'entity', 'id': f'{dataset}:document:{number}', 'entity_id': document, 'entity_type': entity_type,
                       'label': (row.get('title') or number)[:500]},
                      document_type=typ, subtype=subtype, citation=row.get('citation'), publication_date=row.get('publication_date'),
                      start_page=row.get('start_page'), end_page=row.get('end_page'), page_length=row.get('page_length'),
                      significant=row.get('significant'), abstract=(row.get('abstract') or '')[:2000] or None,
                      correction_of=row.get('correction_of'), html_url=row.get('html_url'))
            effective = row.get('effective_on')
            yield rec({'kind': 'assertion', 'id': f'{dataset}:status:{number}', 'subject': document, 'predicate': 'document_status',
                       'value': {'published_type': typ, 'subtype': subtype, 'action': row.get('action'),
                                 'publication_date': row.get('publication_date'), 'signing_date': row.get('signing_date'),
                                 'comments_close_on': row.get('comments_close_on'), 'effective_date': effective,
                                 'effective_status': 'unknown' if not effective else 'source_effective_date',
                                 'url': row.get('html_url'), 'official_pdf': row.get('pdf_url'), 'edition': 'informational API rendition'}})
            if typ in LEGAL and effective:
                yield rec({'kind': 'assertion', 'id': f'{dataset}:effective:{number}', 'subject': document, 'predicate': 'legal_effective_date',
                           'value': effective})
            issuers = set()
            for agency in row.get('agencies') or []:
                if agency.get('id') is None or agency['id'] in issuers:
                    continue
                issuers.add(agency['id'])
                key = f"federalregister:agency:{agency['id']}"
                if key not in entities:
                    entities.add(key)
                    yield rec({'kind': 'entity', 'id': f"{dataset}:agency:{agency['id']}", 'entity_id': key, 'entity_type': 'government_agency',
                               'label': agency.get('name') or agency.get('raw_name') or key}, slug=agency.get('slug'), raw_name=agency.get('raw_name'))
                    if agency.get('parent_id') is not None:
                        yield rec({'kind': 'assertion', 'id': f"{dataset}:agency_parent:{agency['id']}", 'subject': key, 'predicate': 'part_of',
                                   'object': f"federalregister:agency:{agency['parent_id']}"})
                yield rec({'kind': 'assertion', 'id': f"{dataset}:issued:{number}:{agency['id']}", 'subject': document,
                           'predicate': 'issued_document_by', 'object': key})
            references = [{k: ref_.get(k) for k in ('title', 'part', 'chapter') if ref_.get(k) is not None} for ref_ in row.get('cfr_references') or []]
            if references:
                yield rec({'kind': 'assertion', 'id': f'{dataset}:cfr:{number}', 'subject': document, 'predicate': 'cfr_references', 'value': references})
            identifiers = [('rin', v) for v in row.get('regulation_id_numbers') or []] + [('docket', v) for v in row.get('docket_ids') or []]
            for namespace in ('executive_order_number', 'proclamation_number', 'presidential_document_number'):
                if row.get(namespace) not in (None, ''):
                    identifiers.append((namespace.replace('_number', ''), row[namespace]))
            for namespace, value in identifiers:
                yield rec({'kind': 'assertion', 'id': f'{dataset}:identifier:{number}:{namespace}:{value}', 'subject': document,
                           'predicate': 'identifier_assignment', 'value': {'namespace': namespace, 'value': str(value)}})
            if row.get('topics'):
                yield rec({'kind': 'assertion', 'id': f'{dataset}:topics:{number}', 'subject': document, 'predicate': 'federal_register_topics',
                           'value': sorted(row['topics'])})


def sample(context):
    emit = Emitter(context)
    for index, line, row, receipt in sampled_rows(context):
        emit.at(index, line, row, receipt, row['publication_date'])
        typ = row['type']; legal = typ in LEGAL
        document = emit.entity('federalregister:' + row['document_number'], 'regulation' if legal else 'publication', row['title'])
        emit.claim(document, 'document_status', {'published_type': typ, 'publication_date': row['publication_date'],
                   'effective_date': row.get('effective_on'), 'effective_status': 'unknown' if not row.get('effective_on') else 'source_effective_date',
                   'url': row['html_url'], 'official_pdf': row.get('pdf_url'), 'edition': 'informational API rendition'})
        for agency in row.get('agencies', []):
            if agency.get('id') is None: continue
            key = emit.entity('federalregister:agency:' + str(agency['id']), 'government_agency', agency['name'])
            emit.relation(document, 'issued_document_by', key)
        if legal and row.get('effective_on'): emit.claim(document, 'legal_effective_date', row['effective_on'])
        yield from emit.rows
