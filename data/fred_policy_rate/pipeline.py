'DFF: 2025-01-01 through 2025-03-31; dated historical sample, current vintage, not real-time historical availability'
import json
import math
from worldmodel.util import digest
from worldmodel.source_helpers import SERIES, next_day, month_end

def _sample_run(context):
    dataset = 'fred_policy_rate'
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
            day = row.get('observation_date', row.get('DATE'))
            if not day:
                raise ValueError('FRED date field missing')
            found = False
            for series, (metric, unit) in SERIES.items():
                if series not in row:
                    continue
                found = True
                key = entity('fred:' + series, 'economic_series', series, series_id=series, source_url='https://fred.stlouisfed.org/series/' + series)
                observation(key, metric, row[series], unit, day, month_end(day) if series == 'CPIAUCSL' else next_day(day), source_series=series, vintage='acquired_current_vintage', interpretation='market breakeven includes risk/liquidity premia; not pure expected inflation' if series == 'T10YIE' else 'reported series level')
            if not found:
                raise ValueError('No recognized FRED series columns')
            yield from out


def run(context):
    """Full sharded artifacts stream through fred_alfred; sample/single payloads keep the sample adapter."""
    if getattr(context, 'raw_coverage', None) is None or not _full(context):
        yield from _sample_run(context)
        return
    from .fred_alfred import api_records
    series = context.parameters['series']
    yield from api_records(context, context.definition['id'], series, lambda series_id: 'vintages')


def _full(context):
    coverage = context.raw_coverage()
    return not coverage['sampled'] and coverage['layout'] == 'shards'
