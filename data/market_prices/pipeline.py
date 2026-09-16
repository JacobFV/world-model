"""Daily US equity bars from Massive grouped aggregates, plus strict authorized-feed imports.

Full acquisitions (one grouped-daily JSON per weekday) emit, per ticker per trading day,
``close_price_split_adjusted`` (USD/share; open/high/low/VWAP/trade count as attributes)
and ``volume_split_adjusted`` (shares) on the consolidated-tape listing ``ticker:US:<T>``.
Grouped aggregates publish no venue, issuer or instrument identifier: join to
``ticker:<MIC>:<T>``, CIK and FIGI through market_corporate_actions' ticker directory, with
dates, never by name. Ticker symbols are reused over time. Canonical JSONL imports keep the
strict contract in worldmodel.financial_feeds (no network access, no inferred adjustment).
"""
from datetime import date, timedelta
import json
import math
import re
from worldmodel.financial_feeds import feed_records

TICKER = re.compile(r'[A-Za-z0-9.\-]{1,16}')


def run(context):
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            yield from feed_records(context, 'prices')
            return
    listings = set()
    for index in range(len(context.raw_inputs)):
        yield from _grouped(context, index, context.raw_receipt(index), listings)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _grouped(context, index, receipt, listings):
    for shard in context.raw_shards(index):
        day = ((shard.get('request') or {}).get('params') or {}).get('date')
        if not day:
            raise ValueError(f'shard {shard["index"]}: grouped aggregate request date missing')
        date.fromisoformat(day)
        with open(shard['path'], 'rb') as stream:
            payload = json.load(stream)
        if payload.get('status') not in ('OK', 'DELAYED'):
            raise ValueError(f'shard {shard["index"]}: provider status {payload.get("status")!r}')
        observed = shard.get('retrieved_at') or receipt['retrieved_at']
        valid_to = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        adjusted = bool(payload.get('adjusted'))
        for number, bar in enumerate(payload.get('results') or []):
            ticker = bar.get('T') or ''
            if not TICKER.fullmatch(ticker) or not _finite(bar.get('c')):
                continue
            listing = f'ticker:US:{ticker}'
            locator = f'shard:{shard["index"]}/record:{number}'
            base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
            if listing not in listings:
                listings.add(listing)
                yield {**base, 'kind': 'entity', 'id': f'massive:listing:{ticker}', 'entity_id': listing, 'entity_type': 'ticker_listing',
                       'label': ticker, 'attributes': {'identity_basis': 'consolidated US tape symbol in Massive grouped daily aggregates; symbol reuse possible'}}
            dims = {'adjustment': 'split_adjusted' if adjusted else 'unadjusted', 'source': 'massive_grouped_daily', 'frequency': 'daily'}
            attrs = {k: bar[s] for k, s in (('open', 'o'), ('high', 'h'), ('low', 'l'), ('vwap', 'vw'), ('transactions', 'n')) if _finite(bar.get(s))}
            yield {**base, 'kind': 'observation', 'id': f'massive:{day}:{ticker}:close', 'subject': listing,
                   'metric': 'close_price_split_adjusted' if adjusted else 'close_price_unadjusted', 'value': bar['c'], 'unit': 'USD/share',
                   'valid_from': day, 'valid_to': valid_to, 'dimensions': dims, 'attributes': attrs}
            if _finite(bar.get('v')):
                yield {**base, 'kind': 'observation', 'id': f'massive:{day}:{ticker}:volume', 'subject': listing,
                       'metric': 'volume_split_adjusted' if adjusted else 'volume_unadjusted', 'value': bar['v'], 'unit': 'shares',
                       'valid_from': day, 'valid_to': valid_to, 'dimensions': dims, 'attributes': {}}
