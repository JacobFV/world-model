'100 largest FDIC-insured banks by reported assets at2024-12-31; monetary source values in thousands USD'
import json
import math
from worldmodel.util import digest
from worldmodel.source_helpers import SERIES, next_day, month_end

def run(context):
    dataset = 'fdic_bank_financials'
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    seen = set()
    record_ids = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            out = []

            def base(kind, identity, **fields):
                r = {'kind': kind, 'id': 'strategic:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': context.raw_evidence('line:' + str(line_number), index), 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only'}, **fields}
                if r['id'] not in record_ids:
                    record_ids.add(r['id'])
                    out.append(r)
                return r

            def entity(key, typ, label=None, **attrs):
                if key not in seen:
                    seen.add(key)
                    r = base('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    r['attributes'].update(attrs)
                return key

            def relation(subject, predicate, obj, **attrs):
                r = base('assertion', [line_number, subject, predicate, obj, attrs], subject=subject, predicate=predicate, object=obj)
                r['attributes'].update(attrs)

            def observation(subject, metric, value, unit, start=None, end=None, scale=1, **attrs):
                missing = value is None or str(value).strip() in ('', '.', 'NA', 'N/A', 'null')
                if not missing:
                    value = float(value) * scale
                    if not math.isfinite(value):
                        raise ValueError('Nonfinite source measurement')
                    if value.is_integer():
                        value = int(value)
                r = base('observation', [line_number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                if missing:
                    r['missing_reason'] = 'source_missing'
                r['attributes'].update(attrs)
            data = row.get('data', row)
            rep = str(data['REPDTE']).replace('-', '')
            day = f'{rep[:4]}-{rep[4:6]}-{rep[6:8]}'
            key = entity('fdic:cert:' + str(data['CERT']), 'bank', data['NAME'], fdic_certificate=data['CERT'])
            for field, metric in [('ASSET', 'total_assets'), ('DEP', 'bank_deposits'), ('LNLSNET', 'bank_net_loans'), ('EQ', 'bank_equity'), ('NETINC', 'bank_net_income')]:
                observation(key, metric, data.get(field), 'USD', rep[:4] + '-01-01' if field == 'NETINC' else day, next_day(day), scale=1000, source_field=field, source_unit='thousand_USD', period_basis='year_to_date' if field == 'NETINC' else 'report_date_stock')
            yield from out
