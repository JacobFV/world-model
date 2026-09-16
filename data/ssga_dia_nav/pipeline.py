"""DIA NAV per fund share, shares outstanding and total net assets; no market-price inference."""
from datetime import datetime, timedelta
from worldmodel.source_records import sampled_rows, number

FIELDS = [('NAV', 'fund_nav', 'USD/share'), ('Shares Outstanding', 'fund_shares_outstanding', 'shares'),
          ('Total Net Assets', 'fund_net_assets', 'USD')]


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
            yield {**base, 'kind': 'entity', 'id': f'ssga_nav:{index}:fund', 'entity_id': 'ssga:fund:DIA',
                   'entity_type': 'investment_fund', 'label': row['_context']['fund'], 'attributes': {'source_dataset': 'ssga_dia_nav'}}
        for key, metric, unit in FIELDS:
            raw = row.get(key)
            record = {**base, 'kind': 'observation', 'id': f'ssga_nav:{index}:{start}:{metric}', 'subject': 'ssga:fund:DIA',
                      'metric': metric, 'unit': unit, 'valid_from': start, 'valid_to': end,
                      'dimensions': {'fund': 'DIA', 'frequency': 'daily'},
                      'attributes': {'source_dataset': 'ssga_dia_nav', 'source_field': key}}
            if raw in (None, '', '-', '--'):
                record.update(value=None, missing_reason='source_blank')
            else:
                value = number(raw)
                if value < 0:
                    raise ValueError('Negative fund value')
                record['value'] = int(value) if value.is_integer() else value
            yield record
