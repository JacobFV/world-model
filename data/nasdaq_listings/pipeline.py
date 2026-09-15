'First 100 Nasdaq-listed directory rows excluding test issues. Source-scoped instrument/listing references; no legal issuer ID supplied.'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import _iso_date, _code

def run(context):
    dataset = 'nasdaq_listings'
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
            if str(row.get('Symbol', '')).startswith('File Creation Time:') or row.get('Test Issue') == 'Y':
                continue
            symbol = _code(row['Symbol'], '[A-Z0-9.$^-]+', 'Nasdaq symbol')
            token = digest([ref, symbol])
            venue = entity('mic:XNAS', 'trading_venue', 'NASDAQ - ALL MARKETS', identity_basis='Nasdaq-listed directory venue')
            identifier(venue, 'mic', 'XNAS')
            security = entity('reference:nasdaq:security:' + token, 'security', row['Security Name'], unresolved_identity=True, identity_basis='source-scoped security reference; issuer and permanent instrument ID absent')
            listing = entity('reference:nasdaq:listing:' + token, 'ticker_listing', symbol, unresolved_identity=True, identity_basis='source-scoped listing snapshot; historical interval unknown')
            identifier(listing, 'ticker', symbol, scope='XNAS')
            assertion(listing, 'listed_instrument', obj=security)
            assertion(listing, 'listing_venue', obj=venue)
            yield from out
