'Economic census, CBP, nonemployers, BDS and AIES'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

def run(context):
    dataset = 'census_business'
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
            state = geo(row['fipstate'])
            code = row['naics'].strip().rstrip('/')
            industry = entity('naics:2022:' + code, 'industry', 'NAICS 2022 ' + code, synthetic=True, classification_revision='2022')
            key = entity('cohort:cbp:2023:' + digest([row['fipstate'], code, row['lfo']]), 'business_cohort', '2023 CBP aggregate ' + row['fipstate'] + '/' + code + '/' + row['lfo'], aggregate=True, classification_revision='2022', legal_form=row['lfo'])
            rel(key, 'within', state)
            rel(key, 'classified_as', industry)
            for field, metric, unit in [('emp', 'employment', 'people'), ('est', 'establishment_count', 'establishments'), ('ap', 'annual_payroll', 'thousand_USD'), ('qp1', 'first_quarter_payroll', 'thousand_USD')]:
                value = None if row.get(field + '_nf') == 'D' else row.get(field)
                period = ('2023-03-12', '2023-03-13') if field in ('emp', 'est') else ('2023-01-01', '2023-04-01') if field == 'qp1' else year(2023)
                obs(key, metric, value, unit, *period, noise_flag=row.get(field + '_nf'), source_field=field, reference_basis='CBP March reference period for employment/establishments; reported payroll period otherwise')
            yield from out
