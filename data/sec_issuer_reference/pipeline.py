'One issuer reference using published CIK, name and unjoined ticker/exchange metadata.'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import _iso_date, _code

def run(context):
    dataset = 'sec_issuer_reference'
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
            cik = str(row['cik'])
            if not cik.isdigit() or not 0 < int(cik) < 10 ** 10:
                raise ValueError('Invalid published CIK')
            key = entity('sec:cik:' + cik.zfill(10), 'business', row['name'], identity_basis='SEC published issuer CIK', published_tickers=row.get('tickers', []), published_exchanges=row.get('exchanges', []), former_names=row.get('formerNames', []))
            identifier(key, 'sec_cik', str(int(cik)))
            yield from out
