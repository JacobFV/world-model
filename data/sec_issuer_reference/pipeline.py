"""SEC EDGAR submissions -> issuer reference: names over time, identifiers, listings, filings.

Full acquisitions hold one submissions JSON per CIK. Issuer (``sec:cik``), listing
(``ticker:<MIC>:<symbol>``) and classification identities stay distinct; no security
identity is inferred. Former names carry their published from/to dates. Exchange names map
to MICs only where unambiguous (Nasdaq->XNAS, NYSE->XNYS); other venues stay source-scoped.
Every filing in the 'recent' array becomes a dated ``sec_filing`` event (knowledge time =
acceptance time). Legacy JSONL samples keep the original single-issuer mapping.
"""
import json
import re
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

EXCHANGE_SCOPE = {'Nasdaq': 'XNAS', 'NYSE': 'XNYS', 'CBOE': 'CBOE', 'OTC': 'OTC'}
ENTITY_TYPES = {'operating': 'business', 'investment': 'investment_fund'}
SYMBOL = re.compile(r'[A-Z0-9.\-]{1,12}')


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _full(context, index, receipt):
    listings = set()
    for locator, doc in context.raw_rows(index, format='json'):
        cik_text = str(doc.get('cik') or '')
        if not cik_text.isdigit() or not 0 < int(cik_text) < 10 ** 10:
            raise ValueError(f'{locator}: invalid CIK')
        cik = cik_text.zfill(10)
        issuer = 'sec:cik:' + cik
        shard_index = int(locator.split('/', 1)[0].split(':')[1])
        observed = receipt['retrieved_at']
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        key = 'secsub:' + cik
        business = (doc.get('addresses') or {}).get('business') or {}
        attributes = {'sic': doc.get('sic') or None, 'sic_description': doc.get('sicDescription') or None,
                      'sec_entity_type': doc.get('entityType') or None, 'filer_category': doc.get('category') or None,
                      'state_of_incorporation': doc.get('stateOfIncorporation') or None, 'fiscal_year_end': doc.get('fiscalYearEnd') or None,
                      'business_state': business.get('stateOrCountry') or None, 'business_city': business.get('city') or None,
                      'website': doc.get('website') or None, 'identity_basis': 'SEC published issuer CIK'}
        yield {**base, 'kind': 'entity', 'id': key + ':entity', 'entity_id': issuer,
               'entity_type': ENTITY_TYPES.get(doc.get('entityType'), 'organization'), 'label': doc.get('name') or issuer,
               'attributes': {k: v for k, v in attributes.items() if v is not None}}
        yield {**base, 'kind': 'assertion', 'id': key + ':cik', 'subject': issuer, 'predicate': 'identifier_assignment',
               'value': {'namespace': 'sec_cik', 'value': str(int(cik))}, 'attributes': {}}
        if doc.get('ein') and str(doc['ein']).strip('0'):
            yield {**base, 'kind': 'assertion', 'id': key + ':ein', 'subject': issuer, 'predicate': 'identifier_assignment',
                   'value': {'namespace': 'ein', 'value': str(doc['ein'])}, 'attributes': {}}
        if doc.get('lei') and re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}', str(doc['lei'])):
            yield {**base, 'kind': 'assertion', 'id': key + ':lei', 'subject': issuer, 'predicate': 'identifier_assignment',
                   'value': {'namespace': 'lei', 'value': doc['lei']}, 'attributes': {'basis': 'LEI published in SEC submissions metadata'}}
        if doc.get('sic') and str(doc['sic']).isdigit():
            yield {**base, 'kind': 'assertion', 'id': key + ':sic', 'subject': issuer, 'predicate': 'classified_as',
                   'object': 'sic:' + str(doc['sic']), 'attributes': {'classification': 'SEC-assigned SIC code', 'label': doc.get('sicDescription')}}
        state = doc.get('stateOfIncorporation') or ''
        if state in STATE_FIPS:
            yield {**base, 'kind': 'assertion', 'id': key + ':incorporated', 'subject': issuer, 'predicate': 'registered_in',
                   'object': 'geo:US:state:' + STATE_FIPS[state], 'attributes': {'source_state_of_incorporation': state}}
        former = sorted((f for f in doc.get('formerNames') or [] if f.get('name')), key=lambda f: f.get('from') or '')
        for n, item in enumerate(former):
            record = {**base, 'kind': 'assertion', 'id': f'{key}:former_name:{n}', 'subject': issuer, 'predicate': 'legal_name',
                      'value': item['name'], 'attributes': {'validity_basis': 'SEC formerNames from/to'}}
            if item.get('from'):
                record['valid_from'] = item['from']
            if item.get('to') and (not item.get('from') or item['to'] > item['from']):
                record['valid_to'] = item['to']
            yield record
        current = {**base, 'kind': 'assertion', 'id': f'{key}:name', 'subject': issuer, 'predicate': 'legal_name', 'value': doc.get('name') or issuer,
                   'attributes': {'validity_basis': 'current SEC conformed name; starts at end of last former name when published'}}
        if former and former[-1].get('to'):
            current['valid_from'] = former[-1]['to']
        yield current
        tickers, exchanges = doc.get('tickers') or [], doc.get('exchanges') or []
        for n, symbol in enumerate(tickers):
            symbol = str(symbol).upper()
            if not SYMBOL.fullmatch(symbol):
                continue
            exchange = exchanges[n] if n < len(exchanges) else None
            scope = EXCHANGE_SCOPE.get(exchange, 'SEC_UNSPECIFIED')
            listing = f'ticker:{scope}:{symbol}'
            if listing not in listings:
                listings.add(listing)
                yield {**base, 'kind': 'entity', 'id': f'{key}:listing:{n}:entity', 'entity_id': listing, 'entity_type': 'ticker_listing',
                       'label': symbol, 'attributes': {'sec_exchange': exchange, 'identity_basis': 'SEC submissions tickers/exchanges snapshot'}}
            yield {**base, 'kind': 'assertion', 'id': f'{key}:listing:{n}', 'subject': issuer, 'predicate': 'issuer_listing', 'object': listing,
                   'attributes': {'sec_exchange': exchange, 'validity_basis': 'current snapshot; listing dates not published'}}
        recent = (doc.get('filings') or {}).get('recent') or {}
        accessions = recent.get('accessionNumber') or []
        column = lambda name, i: (recent.get(name) or [None] * (i + 1))[i] if i < len(recent.get(name) or []) else None
        for i, accession in enumerate(accessions):
            filed = column('filingDate', i)
            accepted = column('acceptanceDateTime', i) or filed
            if not filed:
                continue
            attrs = {'form': column('form', i), 'accession': accession, 'filing_date': filed, 'report_date': column('reportDate', i) or None,
                     'items': column('items', i) or None, 'primary_document': column('primaryDocument', i) or None,
                     'is_xbrl': bool(column('isXBRL', i)), 'file_number': column('fileNumber', i) or None}
            yield {'kind': 'event', 'id': f'{key}:filing:{accession}', 'event_type': 'sec_filing', 'occurred_at': accepted,
                   'participants': [issuer], 'observed_at': accepted,
                   'evidence': context.raw_evidence(f'{locator}/filings/recent/{i}', index),
                   'attributes': {k: v for k, v in attrs.items() if v not in (None, '')}}


def _sample(context, index, receipt):
    dataset = 'sec_issuer_reference'
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
                      'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only', 'representative': False,
                                     'validity_basis': 'published snapshot; effective interval unknown'}, **fields}
            out.append(record)
            return record

        def entity(key, typ, label=None, **attrs):
            if key not in seen:
                seen.add(key)
                record = emit('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                record['attributes'].update(attrs)
            return key

        cik = str(row['cik'])
        if not cik.isdigit() or not 0 < int(cik) < 10 ** 10:
            raise ValueError('Invalid published CIK')
        key = entity('sec:cik:' + cik.zfill(10), 'business', row['name'], identity_basis='SEC published issuer CIK',
                     published_tickers=row.get('tickers', []), published_exchanges=row.get('exchanges', []), former_names=row.get('formerNames', []))
        emit('assertion', [line_number, key, 'identifier_assignment', {'namespace': 'sec_cik', 'value': str(int(cik))}, None, {}],
             subject=key, predicate='identifier_assignment', value={'namespace': 'sec_cik', 'value': str(int(cik))})
        yield from out
