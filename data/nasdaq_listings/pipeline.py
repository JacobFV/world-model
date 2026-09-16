"""Nasdaq Trader symbol directories -> venue-scoped ticker listings.

Full acquisitions (nasdaqlisted.txt + otherlisted.txt) emit one ``ticker:<MIC>:<symbol>``
listing per non-test directory row. Listings are not issuers or securities: the
directories publish no CIK, LEI, ISIN or FIGI, so no issuer/security identity is inferred.
Legacy JSONL samples keep the original source-scoped behaviour.
"""
from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo
from worldmodel.util import digest
from worldmodel.source_helpers import _code

# otherlisted.txt "Exchange" codes (https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs).
EXCHANGE_MIC = {'A': ('XASE', 'NYSE American'), 'N': ('XNYS', 'New York Stock Exchange'),
                'P': ('ARCX', 'NYSE Arca'), 'Z': ('BATS', 'Cboe BZX Exchange'),
                'V': ('IEXG', 'Investors Exchange'), 'F': ('TXSE', 'Texas Stock Exchange')}
MARKET_CATEGORY = {'Q': 'NASDAQ Global Select Market', 'G': 'NASDAQ Global Market', 'S': 'NASDAQ Capital Market'}
FINANCIAL_STATUS = {'N': 'normal', 'D': 'deficient', 'E': 'delinquent', 'Q': 'bankrupt', 'G': 'deficient_and_bankrupt',
                    'H': 'deficient_and_delinquent', 'J': 'delinquent_and_bankrupt', 'K': 'deficient_delinquent_bankrupt'}
SYMBOL = re.compile(r'[A-Z0-9.$^=/+\-]{1,20}')


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _creation_time(path):
    with open(path, 'rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 512))
        tail = stream.read().decode('utf-8', 'replace')
    match = re.search(r'File Creation Time: (\d{2})(\d{2})(\d{4})(\d{2}):(\d{2})', tail)
    if not match:
        return None
    month, day, year, hour, minute = map(int, match.groups())
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo('America/New_York')).isoformat()


def _full(context, index, receipt):
    ref = context.raw_inputs[index]
    shards = {shard['index']: shard for shard in context.raw_shards(index)}
    snapshot = {i: _creation_time(s['path']) for i, s in shards.items()}
    venues = set()
    for locator, row in context.raw_rows(index, format='psv', strict=False):
        shard_index = int(locator.split('/', 1)[0].split(':')[1])
        shard = shards[shard_index]
        observed = shard.get('retrieved_at') or receipt['retrieved_at']
        evidence = context.raw_evidence(locator, index)
        other = 'ACT Symbol' in row
        symbol = (row.get('ACT Symbol') if other else row.get('Symbol')) or ''
        if symbol.startswith('File Creation Time:') or row.get('Test Issue') != 'N':
            continue
        if not SYMBOL.fullmatch(symbol):
            raise ValueError(f'{locator}: unexpected symbol {symbol!r}')
        if other:
            code = row.get('Exchange') or ''
            mic, venue_name = EXCHANGE_MIC.get(code, (None, None))
        else:
            code, mic, venue_name = 'Q', 'XNAS', 'NASDAQ - ALL MARKETS'
        base = {'observed_at': observed, 'evidence': evidence}
        if mic is None:
            venue = 'nasdaq_trader:exchange_code:' + re.sub(r'[^A-Za-z0-9]', '_', code or 'blank')
            scope = venue
        else:
            venue, scope = 'mic:' + mic, mic
        if venue not in venues:
            venues.add(venue)
            yield {**base, 'kind': 'entity', 'id': f'nasdaq_listings:venue:{venue}', 'entity_id': venue,
                   'entity_type': 'trading_venue', 'label': venue_name or venue,
                   'attributes': {'identity_basis': 'Nasdaq Trader directory exchange code ' + code,
                                  'unresolved_identity': mic is None}}
        listing = f'ticker:{scope}:{symbol}'
        key = f'nasdaq_listings:{scope}:{symbol}'
        attributes = {'security_name': (row.get('Security Name') or '').strip(), 'etf': row.get('ETF') == 'Y',
                      'round_lot_size': row.get('Round Lot Size'), 'directory': 'otherlisted' if other else 'nasdaqlisted',
                      'source_snapshot_time': snapshot.get(shard_index),
                      'identity_basis': 'venue-scoped ticker listing in dated directory snapshot; issuer and instrument identifiers not published',
                      'validity_basis': 'present in snapshot; listing start/end dates not published'}
        if other:
            attributes.update(cqs_symbol=row.get('CQS Symbol'), nasdaq_symbol=row.get('NASDAQ Symbol'))
        else:
            attributes.update(market_tier=MARKET_CATEGORY.get(row.get('Market Category'), row.get('Market Category')),
                              financial_status=FINANCIAL_STATUS.get(row.get('Financial Status'), row.get('Financial Status')))
        yield {**base, 'kind': 'entity', 'id': key + ':entity', 'entity_id': listing, 'entity_type': 'ticker_listing',
               'label': symbol, 'attributes': attributes}
        yield {**base, 'kind': 'assertion', 'id': key + ':venue', 'subject': listing, 'predicate': 'listing_venue',
               'object': venue, 'attributes': {'source_exchange_code': code}}
        yield {**base, 'kind': 'assertion', 'id': key + ':ticker', 'subject': listing, 'predicate': 'identifier_assignment',
               'value': {'namespace': 'ticker', 'value': symbol, 'scope': scope},
               'attributes': {'validity_basis': 'snapshot only', 'raw_artifact': ref['artifact']}}


def _sample(context, index, receipt):
    dataset = 'nasdaq_listings'
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
                      'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only',
                                     'representative': False, 'validity_basis': 'published snapshot; effective interval unknown'}, **fields}
            out.append(record)
            return record

        def entity(key, typ, label=None, **attrs):
            if key not in seen:
                seen.add(key)
                record = emit('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                record['attributes'].update(attrs)
            return key

        def assertion(subject, predicate, value=None, obj=None, **attrs):
            record = emit('assertion', [line_number, subject, predicate, value, obj, attrs], subject=subject,
                          predicate=predicate, **{'object': obj} if obj else {'value': value})
            record['attributes'].update(attrs)
            return record

        if str(row.get('Symbol', '')).startswith('File Creation Time:') or row.get('Test Issue') == 'Y':
            continue
        symbol = _code(row['Symbol'], '[A-Z0-9.$^-]+', 'Nasdaq symbol')
        token = digest([ref, symbol])
        venue = entity('mic:XNAS', 'trading_venue', 'NASDAQ - ALL MARKETS', identity_basis='Nasdaq-listed directory venue')
        assertion(venue, 'identifier_assignment', {'namespace': 'mic', 'value': 'XNAS'})
        security = entity('reference:nasdaq:security:' + token, 'security', row['Security Name'], unresolved_identity=True,
                          identity_basis='source-scoped security reference; issuer and permanent instrument ID absent')
        listing = entity('reference:nasdaq:listing:' + token, 'ticker_listing', symbol, unresolved_identity=True,
                         identity_basis='source-scoped listing snapshot; historical interval unknown')
        assertion(listing, 'identifier_assignment', {'namespace': 'ticker', 'value': symbol, 'scope': 'XNAS'})
        assertion(listing, 'listed_instrument', obj=security)
        assertion(listing, 'listing_venue', obj=venue)
        yield from out
