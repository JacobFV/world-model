"""Alpaca Market Data v2 daily bars (adjustment=all, SIP feed) -> adjusted daily price series.

Full acquisitions are JSONL lines, each a whole /v2/stocks/bars page ``{"bars": {SYM: [...]},
"next_page_token": ...}``. Per symbol per day this emits ``close_price_total_return_adjusted``
(split- and dividend-adjusted; USD/share; adjusted open/high/low/VWAP/trade count as
attributes) and ``volume_split_adjusted`` on ``ticker:US:<SYM>``. Adjustment factors are the
provider's as of retrieval; values change retroactively after later corporate actions.
The symbol universe is current S&P 500/400 members plus major ETFs (survivorship-biased).
"""
from datetime import date, timedelta
import math
import re

SYMBOL = re.compile(r'[A-Z0-9.\-]{1,12}')


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    listings = set()
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('alpaca_daily_bars requires a full acquisition artifact')
        observed = receipt['retrieved_at']
        # Bars arrive in ascending date order per symbol, so one last-date per symbol is enough
        # to drop provider repeats; a set over every bar would grow to tens of millions of keys.
        last_day = {}
        for locator, page in context.raw_rows(index, format='jsonl'):
            bars = page.get('bars') or {}
            if not isinstance(bars, dict):
                raise ValueError(f'{locator}: unexpected bars payload')
            for symbol, rows in sorted(bars.items()):
                if not SYMBOL.fullmatch(symbol):
                    continue
                listing = f'ticker:US:{symbol}'
                if listing not in listings:
                    listings.add(listing)
                    yield {'kind': 'entity', 'id': f'alpaca:listing:{symbol}', 'entity_id': listing, 'entity_type': 'ticker_listing',
                           'label': symbol, 'observed_at': observed, 'evidence': context.raw_evidence(f'{locator}/bars/{symbol}', index),
                           'attributes': {'identity_basis': 'Alpaca SIP consolidated symbol; symbol reuse possible'}}
                for i, bar in enumerate(rows or []):
                    day = str(bar.get('t') or '')[:10]
                    close = bar.get('c')
                    if not day or type(close) not in (int, float) or not math.isfinite(close):
                        continue
                    if last_day.get(symbol) == day:
                        continue  # guard against provider repeats at page boundaries
                    last_day[symbol] = day
                    key = f'alpaca:{symbol}:{day}'
                    valid_to = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                    base = {'observed_at': observed, 'evidence': context.raw_evidence(f'{locator}/bars/{symbol}/{i}', index)}
                    dims = {'adjustment': 'split_and_dividend_adjusted', 'feed': 'sip', 'source': 'alpaca_bars', 'frequency': 'daily'}
                    attrs = {k: bar[s] for k, s in (('open', 'o'), ('high', 'h'), ('low', 'l'), ('vwap', 'vw'), ('transactions', 'n'))
                             if type(bar.get(s)) in (int, float) and math.isfinite(bar[s])}
                    yield {**base, 'kind': 'observation', 'id': key + ':close', 'subject': listing, 'metric': 'close_price_total_return_adjusted',
                           'value': close, 'unit': 'USD/share', 'valid_from': day, 'valid_to': valid_to, 'dimensions': dims, 'attributes': attrs}
                    if type(bar.get('v')) in (int, float) and math.isfinite(bar['v']):
                        yield {**base, 'kind': 'observation', 'id': key + ':volume', 'subject': listing, 'metric': 'volume_split_adjusted',
                               'value': bar['v'], 'unit': 'shares', 'valid_from': day, 'valid_to': valid_to, 'dimensions': dims, 'attributes': {}}
        last_day.clear()
