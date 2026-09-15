'One published direct accounting-consolidation parent relationship for Bloomberg Finance L.P.; not a beneficial ownership or exposure ledger.'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import _iso_date, _code

def run(context):
    dataset = 'gleif_parent_relationships'
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            out = []

            def emit(kind, identity, **fields):
                record = {'kind': kind, 'id': 'market:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': context.raw_evidence('line:' + str(line_number), index), 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only', 'representative': False, 'validity_basis': 'published snapshot; effective interval unknown'}, **fields}
                out.append(record)
                return record

            def entity(key, typ, label=None, **attrs):
                if key not in seen:
                    seen.add(key)
                    record = emit('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    record['attributes'].update(attrs)
                return key

            def assertion(subject, predicate, value=None, obj=None, **attrs):
                record = emit('assertion', [line_number, subject, predicate, value, obj, attrs], subject=subject, predicate=predicate, **{'object': obj} if obj else {'value': value})
                record['attributes'].update(attrs)
                return record

            def identifier(subject, namespace, value, **extra):
                return assertion(subject, 'identifier_assignment', {'namespace': namespace, 'value': value, **extra})

            def lei_entity(code, label=None):
                code = _code(code, '[A-Z0-9]{18}[0-9]{2}', 'LEI')
                key = entity('lei:' + code, 'organization', label, identity_basis='published LEI')
                identifier(key, 'lei', code)
                return key
            attributes = row['attributes']
            relationship = attributes['relationship']
            start, end = (relationship['startNode'], relationship['endNode'])
            if start['type'] != 'LEI' or end['type'] != 'LEI':
                raise ValueError('Relationship nodes must publish LEI identifiers')
            predicates = {'IS_DIRECTLY_CONSOLIDATED_BY': 'directly_consolidated_by', 'IS_ULTIMATELY_CONSOLIDATED_BY': 'ultimately_consolidated_by'}
            if relationship['type'] not in predicates:
                raise ValueError('Unsupported GLEIF relationship type')
            child, parent = (lei_entity(start['id']), lei_entity(end['id']))
            periods = [p for p in relationship.get('periods', []) if p.get('type') == 'RELATIONSHIP_PERIOD']
            for period in periods or [{}]:
                record = assertion(child, predicates[relationship['type']], obj=parent, relationship_status=relationship.get('status'), relationship_record_id=row['id'], registration=attributes.get('registration'), source_record_valid_from=attributes.get('validFrom'), source_record_valid_to=attributes.get('validTo'), interpretation='accounting consolidation relationship; no equity stake or financial exposure implied', relationship_period=period)
                if period.get('startDate'):
                    record['valid_from'] = _iso_date(period['startDate'])
                if period.get('endDate'):
                    record['valid_to'] = _iso_date(period['endDate'])
                if period:
                    record['attributes']['validity_basis'] = 'published RELATIONSHIP_PERIOD'
            yield from out
