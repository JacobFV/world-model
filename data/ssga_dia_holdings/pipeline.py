"""Dated SPDR fund positions from published holdings workbooks.

Positions are fund holdings, not issuer ownership or index membership. A published
identifier becomes ``cusip:<id>`` only when it is a check-digit-valid 9-character CUSIP;
otherwise it stays a publisher-scoped instrument reference. Cash is not a security.
"""
from datetime import date, datetime, timedelta
import re
from worldmodel.source_records import sampled_rows, number
from worldmodel.util import digest


def cusip_valid(value):
    if not re.fullmatch(r'[0-9A-Z*@#]{8}[0-9]', value or ''):
        return False
    total = 0
    for i, char in enumerate(value[:8]):
        if char.isdigit():
            v = int(char)
        elif char.isalpha():
            v = ord(char) - 55
        else:
            v = {'*': 36, '@': 37, '#': 38}[char]
        if i % 2:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(value[8])


def _rows(context):
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            for locator, row in context.raw_rows(index):
                yield index, locator, row, receipt, True
        else:
            for _, line, row, receipt in ((i, l, r, rc) for i, l, r, rc in sampled_rows(context) if i == index):
                yield index, 'line:' + str(line), row, receipt, False


def run(context):
    seen = set()
    for index, locator, row, receipt, full in _rows(context):
        metadata = row['_context']
        fund_ticker = metadata['ticker']
        if not re.fullmatch(r'[A-Z]{2,5}', fund_ticker or ''):
            raise ValueError('Unexpected fund identity')
        day = datetime.strptime(metadata['date'].removeprefix('As of '), '%d-%b-%Y').date()
        valid_from, valid_to = day.isoformat(), (day + timedelta(days=1)).isoformat()
        base = {'observed_at': receipt['retrieved_at'], 'evidence': context.raw_evidence(locator, index)}
        attrs = {'source_dataset': 'ssga_dia_holdings', 'snapshot_date': valid_from,
                 'coverage': 'published_full_holdings' if full else 'bounded_sample'}
        prefix = 'ssga_holdings:' + digest([context.raw_inputs[index], locator])[:24]

        def entity(key, typ, label):
            if key in seen:
                return key
            seen.add(key)
            yield_rows.append({**base, 'kind': 'entity', 'id': 'ssga_holdings:entity:' + key, 'entity_id': key,
                               'entity_type': typ, 'label': label or key, 'attributes': dict(attrs)})
            return key

        yield_rows = []
        fund = entity('ssga:fund:' + fund_ticker, 'investment_fund', metadata['fund'])
        identifier = row.get('Identifier')
        if not identifier:
            raise ValueError('Holding lacks published instrument identifier')
        cash = row['Name'] == 'US DOLLAR' and row['Ticker'] == '-' and row['Local Currency'] == 'USD'
        if cash:
            security = entity('ssga:instrument:' + digest([identifier, row['Local Currency']]), 'cash_balance', row['Name'])
        elif cusip_valid(identifier):
            security = entity('cusip:' + identifier, 'security', row['Name'])
        else:
            security = entity('ssga:instrument:' + digest([identifier, row['Local Currency']]), 'security', row['Name'])
        yield_rows.append({**base, 'kind': 'assertion', 'id': prefix + ':identifier', 'subject': security,
                           'predicate': 'published_instrument_identifier',
                           'value': {'publisher_field': 'Identifier', 'value': identifier,
                                     'namespace': 'cusip' if security.startswith('cusip:') else 'ssga_published_identifier',
                                     'ticker': row['Ticker'], 'sedol': row.get('SEDOL'), 'currency': row['Local Currency']},
                           'attributes': dict(attrs)})
        position = entity(f'ssga:{fund_ticker}:position:' + digest([valid_from, security]), 'investment_position', None)
        for predicate, target in (('position_holder', fund), ('position_instrument', security)):
            yield_rows.append({**base, 'kind': 'assertion', 'id': f'{prefix}:{predicate}', 'subject': position,
                               'predicate': predicate, 'object': target, 'valid_from': valid_from, 'valid_to': valid_to,
                               'attributes': dict(attrs)})
        shares = number(row['Shares Held'])
        weight = number(row['Weight'])
        # Short index-futures overlays publish small negative weights; shares are always non-negative.
        if shares < 0 or not -100 <= weight <= 100:
            raise ValueError('Invalid shares/portfolio percent')
        for metric, value, unit in ((('position_cash' if cash else 'position_shares'), shares, 'USD' if cash else 'shares'),
                                    ('portfolio_weight', weight, 'percent')):
            yield_rows.append({**base, 'kind': 'observation', 'id': f'{prefix}:{metric}', 'subject': position, 'metric': metric,
                               'value': int(value) if float(value).is_integer() else value, 'unit': unit,
                               'valid_from': valid_from, 'valid_to': valid_to,
                               'dimensions': {'snapshot_date': valid_from, 'fund': fund_ticker}, 'attributes': dict(attrs)})
        yield from yield_rows
