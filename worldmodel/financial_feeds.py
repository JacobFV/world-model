"""Strict canonical contracts for explicitly authorized, imported financial feeds.

No acquisition, calendar inference, ticker matching or price adjustment occurs here.
"""
from copy import deepcopy
from datetime import date
import math
import re
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from .util import digest


def _exact(row,fields):
    if not isinstance(row,dict) or set(row)!=set(fields.split()):raise ValueError('Expected exact canonical fields: '+fields)


def _text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>1000:raise ValueError('Bounded explicit string required')


def _number(value,positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (positive and value==0):raise ValueError('Finite nonnegative number required')


def validate_feed_row(kind,row):
    from .model import instant,identifier
    common='source_record_id source_published_at observed_at identity '
    _exact(row,common+('quote_at valid_to price currency price_type session adjustment' if kind=='prices' else 'action_type effective_at announced_at status terms' if kind=='corporate_actions' else 'INVALID'))
    _text(row['source_record_id']);known=instant(row['observed_at']);published=instant(row['source_published_at'])
    if published>known:raise ValueError('Source publication after observation')
    ident=row['identity'];_exact(ident,'issuer_id security_id listing_id issuer_identifier instrument_identifier venue_mic ticker currency valid_from valid_to known_at source_snapshot')
    for k in ('issuer_id','security_id','listing_id'):identifier(ident[k])
    if len({ident[k] for k in ('issuer_id','security_id','listing_id')})!=3:raise ValueError('Issuer/security/listing must be distinct')
    for field,names in [('issuer_identifier',{'sec_cik','lei'}),('instrument_identifier',{'isin','figi','source_local'})]:
        item=ident[field];_exact(item,'namespace value');_text(item['value']);_text(item['namespace'])
        if item['namespace'] not in names:raise ValueError('Explicit issuer/instrument identifier required; ticker cannot resolve identity')
    _text(ident['source_snapshot']);_text(ident['ticker']);_text(ident['venue_mic']);_text(ident['currency'])
    if not re.fullmatch('[A-Z0-9]{4}',ident['venue_mic']) or not re.fullmatch('[A-Z]{3}',ident['currency']):raise ValueError('Explicit MIC and currency required')
    at=instant(row['quote_at'] if kind=='prices' else row['effective_at'])
    if not instant(ident['valid_from'])<=at<instant(ident['valid_to']) or instant(ident['known_at'])>known:raise ValueError('Point-in-time identity does not cover event or knowledge date')
    if kind=='prices':
        _number(row['price'])
        if not at<instant(row['valid_to'])<=instant(ident['valid_to']):raise ValueError('Explicit quote expiration within identity validity required')
        if row['currency']!=ident['currency'] or row['price_type'] not in ('open','close','last','nav'):raise ValueError('Invalid quote currency/type')
        session=row['session'];_exact(session,'name calendar calendar_version timezone open_at close_at')
        for k in ('name','calendar','calendar_version','timezone'):_text(session[k])
        try:ZoneInfo(session['timezone'])
        except ZoneInfoNotFoundError as exc:raise ValueError('Unknown session timezone') from exc
        if not instant(session['open_at'])<=at<=instant(session['close_at']) or instant(session['open_at'])>=instant(session['close_at']):raise ValueError('Quote outside explicit session')
        if at>published:raise ValueError('Quote timestamp after source publication')
        adj=row['adjustment'];_exact(adj,'policy as_of corporate_action_ids method version')
        if adj['policy'] not in ('unadjusted','split_adjusted','total_return') or not at<=instant(adj['as_of'])<=known:raise ValueError('Invalid adjustment policy or point-in-time cutoff')
        _text(adj['method']);_text(adj['version'])
        if not isinstance(adj['corporate_action_ids'],list) or len(adj['corporate_action_ids'])>1000 or len(set(adj['corporate_action_ids']))!=len(adj['corporate_action_ids']):raise ValueError('Bounded distinct action identifiers required')
        for action in adj['corporate_action_ids']:_text(action)
        if adj['policy']=='unadjusted' and adj['corporate_action_ids']:raise ValueError('Unadjusted quote cannot declare applied actions')
    else:
        if instant(row['announced_at'])>published or row['status'] not in ('announced','confirmed','cancelled'):raise ValueError('Invalid action status/announcement date')
        typ=row['action_type'];terms=row['terms']
        if typ=='split':
            _exact(terms,'numerator denominator');_number(terms['numerator'],True);_number(terms['denominator'],True)
        elif typ=='dividend':
            _exact(terms,'amount currency ex_date record_date payment_date');_number(terms['amount'])
            _text(terms['currency'])
            if not re.fullmatch('[A-Z]{3}',terms['currency']):raise ValueError('Dividend currency required')
            dates=[date.fromisoformat(terms[k]) for k in ('ex_date','record_date','payment_date')]
            if dates[1]>dates[2]:raise ValueError('Dividend payment precedes record date')
        elif typ in ('merger','share_class_change'):
            _exact(terms,'successor_issuer_id successor_security_id exchange_ratio')
            identifier(terms['successor_issuer_id']);identifier(terms['successor_security_id']);_number(terms['exchange_ratio'],True)
        elif typ=='delisting':
            _exact(terms,'reason');_text(terms['reason'])
        else:raise ValueError('Unsupported corporate action type')
    return deepcopy(row)


def feed_records(context,kind):
    """Canonical file rows into dated typed claims; import receipt carries input rights."""
    from .contract_extraction import bounded_rows
    seen=set();entities=set()
    for index,locator,raw in bounded_rows(context):
        row=validate_feed_row(kind,raw)
        if row['source_record_id'] in seen:raise ValueError('Duplicate source record ID; use revision-specific IDs')
        seen.add(row['source_record_id']);ident=row['identity']
        base={'observed_at':row['observed_at'],'valid_from':ident['valid_from'],'valid_to':ident['valid_to'],
              'evidence':context.raw_evidence(locator,index)}
        for field,typ in [('issuer_id','organization'),('security_id','security'),('listing_id','ticker_listing')]:
            key=(ident[field],ident['valid_from'],ident['valid_to'],row['observed_at'])
            if key not in entities:
                entities.add(key)
                yield dict(base,kind='entity',id='financial_feed:'+digest([context.definition['id'],key]),entity_id=ident[field],entity_type=typ,label=ident[field])
        for subject,predicate,obj in [(ident['issuer_id'],'issuer_security',ident['security_id']),(ident['listing_id'],'listing_security',ident['security_id'])]:
            yield dict(base,kind='assertion',id='financial_feed:'+digest([context.definition['id'],row,predicate]),subject=subject,predicate=predicate,object=obj)
        from datetime import timedelta
        from .model import instant
        instant_at=row['quote_at'] if kind=='prices' else row['effective_at']
        point_base=dict(base,valid_from=instant_at,valid_to=row['valid_to'] if kind=='prices' else (instant(instant_at)+timedelta(microseconds=1)).isoformat())
        yield dict(point_base,kind='assertion',id='financial_feed:'+digest([context.definition['id'],row['source_record_id'],row]),
            subject=ident['security_id'],predicate='security_price_quote' if kind=='prices' else 'corporate_action_terms',unit=None,value=row,
            attributes={'coverage':'authorized supplied rows only','identity_policy':'explicit source point-in-time mapping; no ticker-only resolution'})
