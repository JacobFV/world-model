"""Massive splits, cash dividends and ticker directory, plus strict authorized-feed imports.

Full acquisitions are JSONL rows from /v3/reference/splits, /dividends and /tickers:

* splits -> ``stock_split`` events on ``ticker:US:<T>`` (split_from -> split_to);
* dividends -> ``cash_dividend`` events plus ``cash_dividend_per_share`` observations valid on
  the ex-dividend date (currency/share);
* tickers -> venue-scoped listings ``ticker:<primary MIC>:<T>`` with active/delisted status,
  ``issuer_listing`` from ``sec:cik`` when Massive publishes a CIK, ``listing_security`` to
  ``figi:<composite FIGI>``, and ``primary_listing`` from the consolidated symbol for active
  tickers only. Delisted rows keep ``valid_to`` = delisting time. Symbols are reused, so no
  historical ticker is mapped to a current issuer without these dated records.

Future-dated splits/dividends are announcements as published at retrieval. Canonical JSONL
imports keep the strict worldmodel.financial_feeds contract.
"""
from datetime import date, timedelta
import math
import re
from worldmodel.financial_feeds import feed_records

TICKER = re.compile(r'[A-Za-z0-9.\-:]{1,20}')
MIC = re.compile(r'[A-Z0-9]{4}')


def run(context):
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            yield from feed_records(context, 'corporate_actions')
            return
    for index in range(len(context.raw_inputs)):
        yield from _full(context, index, context.raw_receipt(index))


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10]).isoformat()
    except ValueError:
        return None


def _full(context, index, receipt):
    observed = receipt['retrieved_at']
    entities = set()
    for locator, row in context.raw_rows(index, format='jsonl'):
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        ticker = row.get('ticker') or ''
        if not TICKER.fullmatch(ticker):
            continue
        composite = f'ticker:US:{ticker}'
        if 'split_from' in row:
            day = _day(row.get('execution_date'))
            if not day or not row.get('split_from') or not row.get('split_to'):
                continue
            yield {**base, 'kind': 'event', 'id': f'massive:split:{row.get("id") or ticker + ":" + day}', 'event_type': 'stock_split',
                   'occurred_at': day, 'participants': [composite],
                   'attributes': {'split_from': row['split_from'], 'split_to': row['split_to'], 'provider_id': row.get('id'),
                                  'status': 'announced' if day > observed[:10] else 'executed'}}
        elif 'cash_amount' in row:
            day = _day(row.get('ex_dividend_date'))
            amount = row.get('cash_amount')
            if not day or type(amount) not in (int, float) or not math.isfinite(amount):
                continue
            currency = (row.get('currency') or 'USD').upper()
            key = row.get('id') or f'{ticker}:{day}:{amount}'
            attrs = {k: v for k, v in {'declaration_date': row.get('declaration_date'), 'record_date': row.get('record_date'),
                     'pay_date': row.get('pay_date'), 'frequency': row.get('frequency'), 'dividend_type': row.get('dividend_type'),
                     'provider_id': row.get('id')}.items() if v not in (None, '')}
            yield {**base, 'kind': 'event', 'id': f'massive:dividend:{key}', 'event_type': 'cash_dividend', 'occurred_at': day,
                   'participants': [composite], 'attributes': {**attrs, 'cash_amount': amount, 'currency': currency}}
            yield {**base, 'kind': 'observation', 'id': f'massive:dividend:{key}:amount', 'subject': composite,
                   'metric': 'cash_dividend_per_share', 'value': amount, 'unit': f'{currency}/share', 'valid_from': day,
                   'valid_to': (date.fromisoformat(day) + timedelta(days=1)).isoformat(),
                   'dimensions': {'dividend_type': row.get('dividend_type') or 'unknown', 'source': 'massive_dividends'},
                   'attributes': {'basis': 'valid on ex-dividend date'}}
        elif row.get('market') and 'active' in row:
            mic = row.get('primary_exchange') or ''
            scope = mic if MIC.fullmatch(mic) else 'US_UNSPECIFIED'
            listing = f'ticker:{scope}:{ticker}'
            active = bool(row.get('active'))
            delisted = row.get('delisted_utc')
            key = f'massive:ticker:{scope}:{ticker}:{"active" if active else "delisted:" + str(delisted)}'
            if listing + str(active) + str(delisted) in entities:
                continue
            entities.add(listing + str(active) + str(delisted))
            attrs = {k: v for k, v in {'name': row.get('name'), 'security_type': row.get('type'), 'active': active,
                     'currency': row.get('currency_name'), 'delisted_utc': delisted, 'cik': row.get('cik'),
                     'composite_figi': row.get('composite_figi'), 'share_class_figi': row.get('share_class_figi'),
                     'last_updated_utc': row.get('last_updated_utc'),
                     'identity_basis': 'Massive ticker directory; primary exchange MIC'}.items() if v not in (None, '')}
            yield {**base, 'kind': 'entity', 'id': key, 'entity_id': listing, 'entity_type': 'ticker_listing', 'label': ticker, 'attributes': attrs}
            bounds = {}
            if delisted:
                bounds['valid_to'] = delisted
            yield {**base, 'kind': 'assertion', 'id': key + ':ticker', 'subject': listing, 'predicate': 'identifier_assignment',
                   'value': {'namespace': 'ticker', 'value': ticker, 'scope': scope}, **bounds,
                   'attributes': {'validity_basis': 'delisting time published; listing start not in directory' if delisted else 'active at retrieval'}}
            cik = str(row.get('cik') or '')
            if cik.isdigit() and int(cik) > 0:
                yield {**base, 'kind': 'assertion', 'id': key + ':issuer', 'subject': 'sec:cik:' + cik.zfill(10), 'predicate': 'issuer_listing',
                       'object': listing, **bounds, 'attributes': {'basis': 'CIK published in Massive ticker directory'}}
            figi = row.get('composite_figi') or ''
            if re.fullmatch(r'BBG[A-Z0-9]{9}', figi):
                yield {**base, 'kind': 'assertion', 'id': key + ':security', 'subject': listing, 'predicate': 'listing_security',
                       'object': 'figi:' + figi, **bounds, 'attributes': {'figi_type': 'composite', 'share_class_figi': row.get('share_class_figi')}}
            if active and scope != 'US_UNSPECIFIED':
                yield {**base, 'kind': 'assertion', 'id': key + ':primary', 'subject': composite, 'predicate': 'primary_listing',
                       'object': listing, 'attributes': {'validity_basis': 'active ticker at retrieval'}}
