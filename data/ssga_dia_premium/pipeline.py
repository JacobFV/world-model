"""Published DIA premium/discount in percentage points of NAV (publisher formula multiplies by 100)."""
from datetime import datetime, timedelta
from worldmodel.source_records import sampled_rows, number


def _rows(context):
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            for locator, row in context.raw_rows(index):
                yield index, locator, row, receipt
        else:
            for i, line, row, rc in sampled_rows(context):
                if i == index:
                    yield index, 'line:' + str(line), row, rc


def run(context):
    fund_emitted = set()
    for index, locator, row, receipt in _rows(context):
        if row['_context']['ticker'] != 'DIA':
            raise ValueError('Unexpected fund identity')
        day = datetime.strptime(row['Date'], '%d-%b-%Y').date()
        start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
        base = {'observed_at': receipt['retrieved_at'], 'evidence': context.raw_evidence(locator, index)}
        if index not in fund_emitted:
            fund_emitted.add(index)
            yield {**base, 'kind': 'entity', 'id': f'ssga_premium:{index}:fund', 'entity_id': 'ssga:fund:DIA',
                   'entity_type': 'investment_fund', 'label': row['_context']['fund'], 'attributes': {'source_dataset': 'ssga_dia_premium'}}
        raw = row.get('Premium/Discount')
        record = {**base, 'kind': 'observation', 'id': f'ssga_premium:{index}:{start}', 'subject': 'ssga:fund:DIA',
                  'metric': 'fund_premium_discount', 'unit': 'percent', 'valid_from': start, 'valid_to': end,
                  'dimensions': {'fund': 'DIA', 'frequency': 'daily'},
                  'attributes': {'source_dataset': 'ssga_dia_premium', 'interpretation': 'market close vs NAV, percentage points'}}
        if raw in (None, '', '-', '--'):
            record.update(value=None, missing_reason='source_blank')
        else:
            record['value'] = number(raw)
        yield record
