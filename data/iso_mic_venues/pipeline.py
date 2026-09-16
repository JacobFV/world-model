"""ISO 10383 MIC directory -> trading venue references (registration, not operation).

Full acquisitions stream the complete CSV; legacy JSONL samples use the same mapping.
"""
import json
from worldmodel.util import digest
from worldmodel.source_helpers import _iso_date, _code


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        full = 'acquisition' in (receipt.get('source') or {})
        seen = set()
        if full:
            rows = ((locator, row) for locator, row in context.raw_rows(index, format='csv'))
        else:
            rows = _sample_rows(context.raw_path(index))
        for locator, row in rows:
            yield from _venue(context, index, receipt, locator, row, seen, full)


def _sample_rows(path):
    for line_number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if line.strip():
            yield 'line:' + str(line_number), json.loads(line)


def _venue(context, index, receipt, locator, row, seen, full):
    dataset = 'iso_mic_venues'
    ref = context.raw_inputs[index]
    out = []
    common = ({'coverage': 'complete_published_list', 'validity_basis': 'published list snapshot; MIC status dates retained'}
              if full else {'source_row': row, 'coverage': 'sample_only', 'representative': False,
                            'validity_basis': 'published snapshot; effective interval unknown'})

    def emit(kind, identity, **fields):
        record = {'kind': kind, 'id': 'market:' + digest([dataset, ref, identity]), 'observed_at': receipt['retrieved_at'],
                  'evidence': context.raw_evidence(locator, index),
                  'attributes': {'source_dataset': dataset, **common}, **fields}
        out.append(record)
        return record

    def entity(key, typ, label=None, **attrs):
        if key not in seen:
            seen.add(key)
            record = emit('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
            record['attributes'].update(attrs)
        return key

    def assertion(subject, predicate, value=None, obj=None, **attrs):
        record = emit('assertion', [locator, subject, predicate, value, obj], subject=subject, predicate=predicate,
                      **{'object': obj} if obj else {'value': value})
        record['attributes'].update(attrs)
        return record

    def identifier(subject, namespace, value):
        if ('identifier', subject, namespace, value) in seen:
            return
        seen.add(('identifier', subject, namespace, value))
        assertion(subject, 'identifier_assignment', {'namespace': namespace, 'value': value})

    mic = _code(row['MIC'], '[A-Z0-9]{4}', 'MIC')
    extra = {}
    if full:
        extra = {'segment_type': row.get('OPRT/SGMT'), 'acronym': row.get('ACRONYM') or None, 'city': row.get('CITY') or None,
                 'website': row.get('WEBSITE') or None, 'legal_entity_name': row.get('LEGAL ENTITY NAME') or None}
    key = entity('mic:' + mic, 'trading_venue', row['MARKET NAME-INSTITUTION DESCRIPTION'], mic_category=row.get('MARKET CATEGORY CODE'),
                 country=row.get('ISO COUNTRY CODE (ISO 3166)'), identity_basis='ISO10383 MIC; does not establish exchange operation', **extra)
    identifier(key, 'mic', mic)
    assertion(key, 'published_mic_status', row['STATUS'], source_creation_date=_iso_date(row.get('CREATION DATE')),
              source_last_update_date=_iso_date(row.get('LAST UPDATE DATE')), source_expiry_date=_iso_date(row.get('EXPIRY DATE')),
              interpretation='MIC registration status only; no operational status inferred')
    operating = _code(row['OPERATING MIC'], '[A-Z0-9]{4}', 'operating MIC')
    if operating != mic:
        target = entity('mic:' + operating, 'trading_venue', identity_basis='explicit operating MIC reference')
        identifier(target, 'mic', operating)
        assertion(key, 'mic_operating_venue', obj=target)
    if row.get('LEI'):
        code = _code(row['LEI'], '[A-Z0-9]{18}[0-9]{2}', 'LEI')
        target = entity('lei:' + code, 'organization', row.get('LEGAL ENTITY NAME') or None, identity_basis='published LEI')
        identifier(target, 'lei', code)
        assertion(key, 'venue_operator', obj=target, identity_basis='explicit LEI in MIC directory')
    yield from out
