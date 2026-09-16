"""GLEIF Level 2 relationship records and reporting exceptions.

Relationships are accounting-consolidation, fund-management, sub-fund, feeder and
international-branch links between LEIs: no equity stake or financial exposure is implied.
RELATIONSHIP_PERIOD dates become the validity interval; accounting periods stay attributes.

Reporting exceptions (6.36M rows) are restricted: per-LEI claims are emitted only for the
ULTIMATE parent category with an informative reason (NATURAL_PERSONS, NO_LEI, NON_PUBLIC,
and the legal-obstacle/consent reasons). NON_CONSOLIDATING and NO_KNOWN_PERSON rows, and all
DIRECT-category rows, appear only in aggregate count observations (raw keeps every row).
Legacy JSONL samples (GLEIF API relationship records) keep the original behaviour.
"""
from collections import Counter
import csv
import io
import json
import re
import zipfile
from worldmodel.util import digest
from worldmodel.source_helpers import _iso_date, _code

PREDICATES = {'IS_DIRECTLY_CONSOLIDATED_BY': 'directly_consolidated_by', 'IS_ULTIMATELY_CONSOLIDATED_BY': 'ultimately_consolidated_by',
              'IS_FUND-MANAGED_BY': 'fund_managed_by', 'IS_SUBFUND_OF': 'subfund_of', 'IS_FEEDER_TO': 'feeder_fund_of',
              'IS_INTERNATIONAL_BRANCH_OF': 'international_branch_of'}
EXCEPTION_CLAIMS = {'NATURAL_PERSONS', 'NO_LEI', 'NON_PUBLIC', 'CONSENT_NOT_OBTAINED', 'BINDING_LEGAL_COMMITMENTS',
                    'DETRIMENT_NOT_EXCLUDED', 'DISCLOSURE_DETRIMENTAL', 'LEGAL_OBSTACLES'}
LEI = re.compile(r'[A-Z0-9]{18}[0-9]{2}')


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _csv(path):
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith('.csv')]
        if len(members) != 1:
            raise ValueError('Expected exactly one CSV member in GLEIF ZIP')
        with archive.open(members[0]) as stream:
            reader = csv.reader(io.TextIOWrapper(stream, encoding='utf-8-sig', newline=''))
            header = next(reader)
            yield members[0], header, None, None
            last = 1
            for values in reader:
                start, last = last + 1, reader.line_num
                yield members[0], header, start, values


def _node(node_id, node_type):
    if node_type == 'LEI' and LEI.fullmatch(node_id):
        return 'lei:' + node_id
    return 'gleif:node:' + digest([node_type, node_id])[:32]


def _full(context, index, receipt):
    observed = receipt['retrieved_at']
    for shard in context.raw_shards(index):
        rows = _csv(shard['path'])
        member, header, _, _ = next(rows)
        prefix = f'shard:{shard["index"]}/member:{member}'
        col = {name: i for i, name in enumerate(header)}
        if 'Relationship.RelationshipType' in col:
            yield from _relationships(context, index, observed, prefix, rows, col)
        elif 'Exception.Category' in col:
            yield from _exceptions(context, index, observed, prefix, rows, col, shard)
        else:
            raise ValueError(f'{prefix}: unrecognized GLEIF Level 2 CSV header')


def _relationships(context, index, observed, prefix, rows, col):
    seen = set()
    get = lambda row, name: row[col[name]] if name in col else ''
    for _, _, line, row in rows:
        kind = get(row, 'Relationship.RelationshipType')
        if kind not in PREDICATES:
            raise ValueError(f'{prefix}/line:{line}: unsupported relationship type {kind}')
        child = _node(get(row, 'Relationship.StartNode.NodeID'), get(row, 'Relationship.StartNode.NodeIDType'))
        parent = _node(get(row, 'Relationship.EndNode.NodeID'), get(row, 'Relationship.EndNode.NodeIDType'))
        key = f'gleif_rr:{child}:{PREDICATES[kind]}:{parent}'
        if key in seen:
            key = f'{key}:line:{line}'
        seen.add(key)
        periods = {}
        for n in range(1, 6):
            ptype = get(row, f'Relationship.Period.{n}.periodType')
            if ptype:
                periods.setdefault(ptype, {'start': _iso_date(get(row, f'Relationship.Period.{n}.startDate')[:10] or None),
                                           'end': _iso_date(get(row, f'Relationship.Period.{n}.endDate')[:10] or None)})
        quantifiers = [{'method': get(row, f'Relationship.Quantifiers.{n}.MeasurementMethod'),
                        'amount': get(row, f'Relationship.Quantifiers.{n}.QuantifierAmount'),
                        'units': get(row, f'Relationship.Quantifiers.{n}.QuantifierUnits')}
                       for n in range(1, 6) if get(row, f'Relationship.Quantifiers.{n}.MeasurementMethod')]
        qualifiers = [{'dimension': get(row, f'Relationship.Qualifiers.{n}.QualifierDimension'),
                       'category': get(row, f'Relationship.Qualifiers.{n}.QualifierCategory')}
                      for n in range(1, 6) if get(row, f'Relationship.Qualifiers.{n}.QualifierDimension')]
        record = {'kind': 'assertion', 'id': key, 'subject': child, 'predicate': PREDICATES[kind], 'object': parent,
                  'observed_at': observed, 'evidence': context.raw_evidence(f'{prefix}/line:{line}', index),
                  'attributes': {'relationship_status': get(row, 'Relationship.RelationshipStatus') or None,
                                 'registration_status': get(row, 'Registration.RegistrationStatus') or None,
                                 'validation_sources': get(row, 'Registration.ValidationSources') or None,
                                 'last_update': get(row, 'Registration.LastUpdateDate')[:10] or None,
                                 'accounting_period': periods.get('ACCOUNTING_PERIOD'),
                                 'interpretation': 'GLEIF Level 2 relationship; no equity stake or financial exposure implied'}}
        if quantifiers:
            record['attributes']['quantifiers'] = quantifiers
        if qualifiers:
            record['attributes']['qualifiers'] = qualifiers
        relationship = periods.get('RELATIONSHIP_PERIOD') or {}
        if relationship.get('start'):
            record['valid_from'] = relationship['start']
        if relationship.get('end') and (not relationship.get('start') or relationship['end'] > relationship['start']):
            record['valid_to'] = relationship['end']
        record['attributes']['validity_basis'] = 'published RELATIONSHIP_PERIOD' if relationship else 'no relationship period published'
        record['attributes'] = {k: v for k, v in record['attributes'].items() if v is not None}
        yield record


def _exceptions(context, index, observed, prefix, rows, col, shard):
    counts = Counter()
    for _, _, line, row in rows:
        lei, category = row[col['LEI']], row[col['Exception.Category']]
        reasons = [row[col[f'Exception.Reason.{n}']] for n in range(1, 6) if row[col[f'Exception.Reason.{n}']]]
        for reason in reasons or ['UNSPECIFIED']:
            counts[(category, reason)] += 1
        if category != 'ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT' or not EXCEPTION_CLAIMS.intersection(reasons):
            continue
        if not LEI.fullmatch(lei):
            raise ValueError(f'{prefix}/line:{line}: invalid LEI')
        yield {'kind': 'assertion', 'id': f'gleif_repex:{lei}:ultimate', 'subject': 'lei:' + lei, 'predicate': 'parent_reporting_exception',
               'value': {'category': 'ultimate_accounting_consolidation_parent', 'reasons': reasons}, 'observed_at': observed,
               'evidence': context.raw_evidence(f'{prefix}/line:{line}', index),
               'attributes': {'interpretation': 'declared reason no ultimate parent LEI is reported; NATURAL_PERSONS means no consolidating entity above'}}
    subject = 'gleif:golden_copy:repex'
    yield {'kind': 'entity', 'id': 'gleif_repex:summary', 'entity_id': subject, 'entity_type': 'economic_series',
           'label': 'GLEIF reporting exception golden copy', 'observed_at': observed,
           'evidence': context.raw_evidence(f'{prefix}', index), 'attributes': {'aggregate': True}}
    for (category, reason), count in sorted(counts.items()):
        yield {'kind': 'observation', 'id': f'gleif_repex:count:{category}:{reason}', 'subject': subject,
               'metric': 'reporting_exception_count', 'value': count, 'unit': 'lei_records', 'observed_at': observed,
               'dimensions': {'exception_category': category, 'exception_reason': reason},
               'evidence': context.raw_evidence(f'{prefix}', index), 'attributes': {'aggregate': True}}


def _sample(context, index, receipt):
    dataset = 'gleif_parent_relationships'
    ref = context.raw_inputs[index]
    acquired = receipt['retrieved_at']
    seen = set()
    for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        out = []

        def emit(kind, identity, **fields):
            record = {'kind': kind, 'id': 'market:' + digest([dataset, ref, identity]), 'observed_at': acquired,
                      'evidence': context.raw_evidence('line:' + str(line_number), index),
                      'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only', 'representative': False,
                                     'validity_basis': 'published snapshot; effective interval unknown'}, **fields}
            out.append(record)
            return record

        def entity(key, typ, label=None, **attrs):
            if key not in seen:
                seen.add(key)
                record = emit('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                record['attributes'].update(attrs)
            return key

        def assertion(subject, predicate, value=None, obj=None, **attrs):
            record = emit('assertion', [line_number, subject, predicate, value, obj, attrs], subject=subject, predicate=predicate,
                          **{'object': obj} if obj else {'value': value})
            record['attributes'].update(attrs)
            return record

        def lei_entity(code, label=None):
            code = _code(code, '[A-Z0-9]{18}[0-9]{2}', 'LEI')
            key = entity('lei:' + code, 'organization', label, identity_basis='published LEI')
            assertion(key, 'identifier_assignment', {'namespace': 'lei', 'value': code})
            return key

        attributes = row['attributes']
        relationship = attributes['relationship']
        start, end = relationship['startNode'], relationship['endNode']
        if start['type'] != 'LEI' or end['type'] != 'LEI':
            raise ValueError('Relationship nodes must publish LEI identifiers')
        predicates = {'IS_DIRECTLY_CONSOLIDATED_BY': 'directly_consolidated_by', 'IS_ULTIMATELY_CONSOLIDATED_BY': 'ultimately_consolidated_by'}
        if relationship['type'] not in predicates:
            raise ValueError('Unsupported GLEIF relationship type')
        child, parent = lei_entity(start['id']), lei_entity(end['id'])
        periods = [p for p in relationship.get('periods', []) if p.get('type') == 'RELATIONSHIP_PERIOD']
        for period in periods or [{}]:
            record = assertion(child, predicates[relationship['type']], obj=parent, relationship_status=relationship.get('status'),
                               relationship_record_id=row['id'], registration=attributes.get('registration'),
                               source_record_valid_from=attributes.get('validFrom'), source_record_valid_to=attributes.get('validTo'),
                               interpretation='accounting consolidation relationship; no equity stake or financial exposure implied',
                               relationship_period=period)
            if period.get('startDate'):
                record['valid_from'] = _iso_date(period['startDate'])
            if period.get('endDate'):
                record['valid_to'] = _iso_date(period['endDate'])
            if period:
                record['attributes']['validity_basis'] = 'published RELATIONSHIP_PERIOD'
        yield from out
