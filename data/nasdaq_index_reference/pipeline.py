"""Nasdaq-100 constituent snapshot (nasdaq.com list endpoint) and methodology document.

Membership is asserted only for the published snapshot date; quotes are delayed
reference values at the snapshot time. Nasdaq index data is proprietary: reference use.
"""
from datetime import datetime, timedelta
import json
import re
from zoneinfo import ZoneInfo

INDEX = 'index:nasdaq:NDX'


def _money(text):
    if text in (None, '', 'NA', 'N/A'):
        return None
    value = float(str(text).replace('$', '').replace(',', ''))
    return int(value) if value.is_integer() else value


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('nasdaq_index_reference requires a full acquisition artifact')
        yield from _full(context, index, receipt)


def _full(context, index, receipt):
    documents, constituents = [], []
    for shard in context.raw_shards(index):
        url = (shard.get('request') or {}).get('url') or ''
        with open(shard['path'], 'rb') as stream:
            head = stream.read(5)
        (documents if head == b'%PDF-' or url.endswith('.pdf') else constituents).append(shard)
    if not constituents:
        raise ValueError('No constituent JSON shard in artifact')
    observed = receipt['retrieved_at']
    first = constituents[0]
    yield {'kind': 'entity', 'id': 'nasdaq_index:entity:NDX', 'entity_id': INDEX, 'entity_type': 'economic_series',
           'label': 'NASDAQ-100 Index', 'observed_at': observed, 'evidence': context.raw_evidence(f'shard:{first["index"]}', index),
           'attributes': {'index_symbol': 'NDX', 'provider': 'Nasdaq', 'rights': 'proprietary; reference use only'}}
    for shard in documents:
        doc = 'nasdaq_index:document:' + shard['sha256'][:16]
        yield {'kind': 'entity', 'id': doc + ':entity', 'entity_id': doc, 'entity_type': 'publication',
               'label': 'Nasdaq-100 Index Methodology', 'observed_at': shard.get('retrieved_at') or observed,
               'evidence': context.raw_evidence(f'shard:{shard["index"]}', index),
               'attributes': {'url': (shard.get('request') or {}).get('url'), 'sha256': shard['sha256'], 'bytes': shard['bytes'],
                              'format': 'pdf', 'parsed': False}}
        yield {'kind': 'assertion', 'id': doc + ':methodology', 'subject': INDEX, 'predicate': 'methodology_document', 'object': doc,
               'observed_at': shard.get('retrieved_at') or observed, 'evidence': context.raw_evidence(f'shard:{shard["index"]}', index),
               'attributes': {}}
    for shard in constituents:
        with open(shard['path'], 'rb') as stream:
            payload = json.load(stream)
        data = payload.get('data') or {}
        rows = ((data.get('data') or {}).get('rows')) or []
        if not rows:
            raise ValueError('Nasdaq list payload has no rows')
        stamp = datetime.strptime(re.sub(r'\s+', ' ', data['date']).strip(), '%b %d, %Y %I:%M %p').replace(tzinfo=ZoneInfo('America/New_York'))
        day = stamp.date().isoformat()
        next_day = (stamp.date() + timedelta(days=1)).isoformat()
        seen_at = shard.get('retrieved_at') or observed
        for number, row in enumerate(rows):
            symbol = row.get('symbol') or ''
            if not re.fullmatch(r'[A-Z0-9.]{1,10}', symbol):
                raise ValueError(f'Unexpected NDX symbol {symbol!r}')
            locator = f'shard:{shard["index"]}/record:{number}'
            base = {'observed_at': seen_at, 'evidence': context.raw_evidence(locator, index)}
            listing = f'ticker:XNAS:{symbol}'
            key = f'nasdaq_index:{day}:{symbol}'
            yield {**base, 'kind': 'entity', 'id': key + ':listing', 'entity_id': listing, 'entity_type': 'ticker_listing',
                   'label': symbol, 'attributes': {'security_name': row.get('companyName'),
                                                   'identity_basis': 'NDX constituents are Nasdaq-listed; issuer identity not published'}}
            yield {**base, 'kind': 'assertion', 'id': key + ':member', 'subject': listing, 'predicate': 'index_constituent',
                   'object': INDEX, 'valid_from': day, 'valid_to': next_day,
                   'attributes': {'validity_basis': 'membership observed in dated snapshot only', 'snapshot_time': stamp.isoformat()}}
            for field, metric, unit in (('marketCap', 'market_capitalization', 'USD'), ('lastSalePrice', 'last_sale_price', 'USD/share')):
                value = _money(row.get(field))
                record = {**base, 'kind': 'observation', 'id': f'{key}:{metric}', 'subject': listing, 'metric': metric,
                          'value': value, 'unit': unit, 'valid_from': stamp.isoformat(), 'dimensions': {'source': 'nasdaq.com list-type nasdaq100'},
                          'attributes': {'source_field': field, 'source_text': row.get(field), 'quote_basis': 'delayed reference value at snapshot time'}}
                if value is None:
                    record['missing_reason'] = 'source_blank'
                yield record
