'Agencies, recipients, awards and transactions'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

def run(context):
    dataset = 'usaspending'
    if not context.raw_inputs:
        raise ValueError(f'{dataset}: no sample artifact supplied')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            evidence = context.raw_evidence(f'line:{number}', index)
            out = []

            def base(kind, identity, **fields):
                record = {'kind': kind, 'id': 'normalized:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': evidence, 'attributes': {'source_row': row, 'source_dataset': dataset}, **fields}
                out.append(record)
                return record

            def entity(key, typ, label=None, synthetic=False, aggregate=False, **attrs):
                if key not in seen:
                    seen.add(key)
                    r = base('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    r['attributes'].update(synthetic_reference=synthetic, aggregate=aggregate, **attrs)
                return key

            def geo(state=None, label=None):
                us = entity('geo:US', 'country', 'United States', synthetic=not (label and (not state or state in ('US', '00'))))
                if not state or state in ('US', '00'):
                    return us
                code = STATE_FIPS.get(state, state)
                key = entity('geo:US:state:' + code, 'state', label or 'US state FIPS ' + code, synthetic=not bool(label))
                rel(key, 'within', us)
                return key

            def rel(subject, predicate, obj):
                identity = ('relation', subject, predicate, obj)
                if identity not in seen:
                    seen.add(identity)
                    base('assertion', identity, subject=subject, predicate=predicate, object=obj)

            def obs(subject, metric, value, unit, start=None, end=None, **attrs):
                missing = value is None or (isinstance(value, str) and value.strip() in ('', '-', '(D)', 'D', 'S', 'N', 'NA', 'null'))
                if not missing and metric not in ('development_status', 'legal_status'):
                    value = float(value)
                    if value.is_integer():
                        value = int(value)
                r = base('observation', ['obs', number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                r['attributes'].update(source_unit=unit, **attrs)
                if missing:
                    r['missing_reason'] = 'source_missing_or_suppressed'
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                return r

            def year(y):
                return (f'{int(y):04d}-01-01', f'{int(y) + 1:04d}-01-01')
            key = entity('usaspending:award:' + row['generated_internal_id'], 'award', row['Award ID'])
            agency = entity('usaspending:agency:' + str(row['awarding_agency_id']), 'government_agency', row['Awarding Agency'])
            name = row['Recipient Name']
            recipient = entity('reference:usaspending:recipient:' + digest(name), 'organization', name, synthetic=True, identity_basis='unresolved source recipient name; not matched to LEI')
            rel(key, 'awarded_by', agency)
            rel(key, 'awarded_to', recipient)
            rel(agency, 'within', geo())
            obs(key, 'award_amount', row['Award Amount'], 'USD', acquired, None, validity_basis='award summary at acquisition; query window is not award validity')
            yield from out
