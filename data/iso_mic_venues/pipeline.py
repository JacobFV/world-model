'Bounded selected US MIC reference records, including published Texas venue names; registration does not prove operation.'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import _iso_date, _code

def run(context):
    dataset = 'iso_mic_venues'
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
            mic = _code(row['MIC'], '[A-Z0-9]{4}', 'MIC')
            key = entity('mic:' + mic, 'trading_venue', row['MARKET NAME-INSTITUTION DESCRIPTION'], mic_category=row.get('MARKET CATEGORY CODE'), country=row.get('ISO COUNTRY CODE (ISO 3166)'), identity_basis='ISO10383 MIC; does not establish exchange operation')
            identifier(key, 'mic', mic)
            assertion(key, 'published_mic_status', row['STATUS'], source_creation_date=_iso_date(row.get('CREATION DATE')), source_last_update_date=_iso_date(row.get('LAST UPDATE DATE')), source_expiry_date=_iso_date(row.get('EXPIRY DATE')), interpretation='MIC registration status only; no operational status inferred')
            operating = _code(row['OPERATING MIC'], '[A-Z0-9]{4}', 'operating MIC')
            if operating != mic:
                target = entity('mic:' + operating, 'trading_venue', identity_basis='explicit operating MIC reference')
                identifier(target, 'mic', operating)
                assertion(key, 'mic_operating_venue', obj=target)
            if row.get('LEI'):
                target = lei_entity(row['LEI'], row.get('LEGAL ENTITY NAME') or None)
                assertion(key, 'venue_operator', obj=target, identity_basis='explicit LEI in MIC directory')
            yield from out
