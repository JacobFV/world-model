"""Bounded official market references; identifiers never imply issuer identity or operation."""
from datetime import date
import json
import re
from .util import digest

SOURCE_IDS = ('iso_mic_venues', 'nasdaq_listings', 'gleif_parent_relationships',
              'sec_issuer_reference', 'nasdaq_index_reference', 'market_corporate_actions',
              'market_prices', 'market_obligations')
ADAPTER_IDS = SOURCE_IDS[:4]


def schema():
    return {'entity_types': {'trading_venue': {'parent': 'institution'}},
            'relations': {
                'listing_venue': {'domain': 'ticker_listing', 'range': 'trading_venue'},
                'venue_operator': {'domain': 'trading_venue', 'range': 'organization'},
                'mic_operating_venue': {'domain': 'trading_venue', 'range': 'trading_venue'},
                'directly_consolidated_by': {'domain': 'organization', 'range': 'organization'},
                'ultimately_consolidated_by': {'domain': 'organization', 'range': 'organization'}},
            'variables': {'published_mic_status': {'type': 'string', 'unit': 'category', 'domain': 'trading_venue'}}}


def _iso_date(value):
    if not value: return None
    value = str(value)
    if len(value) == 8 and value.isdigit():
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    date.fromisoformat(value[:10])
    return value


def _code(value, pattern, name):
    value = str(value or '').strip().upper()
    if not re.fullmatch(pattern, value): raise ValueError('Invalid published '+name)
    return value


def normalize(context):
    dataset = context.definition['id']
    if dataset not in ADAPTER_IDS: raise ValueError('No acquired source adapter: '+dataset)
    if not context.raw_inputs: raise ValueError('Source sample artifact required')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip(): continue
            row = json.loads(line)
            out = []
            def emit(kind, identity, **fields):
                record = {'kind': kind, 'id': 'market:'+digest([dataset, ref, identity]),
                          'observed_at': acquired, 'evidence': context.raw_evidence('line:'+str(line_number), index),
                          'attributes': {'source_dataset': dataset, 'source_row': row,
                                         'coverage': 'sample_only', 'representative': False,
                                         'validity_basis': 'published snapshot; effective interval unknown'}, **fields}
                out.append(record)
                return record
            def entity(key, typ, label=None, **attrs):
                if key not in seen:
                    seen.add(key)
                    record = emit('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    record['attributes'].update(attrs)
                return key
            def assertion(subject, predicate, value=None, obj=None, **attrs):
                record = emit('assertion', [line_number, subject, predicate, value, obj, attrs],
                              subject=subject, predicate=predicate, **({'object': obj} if obj else {'value': value}))
                record['attributes'].update(attrs)
                return record
            def identifier(subject, namespace, value, **extra):
                return assertion(subject, 'identifier_assignment', {'namespace': namespace, 'value': value, **extra})
            def lei_entity(code, label=None):
                code = _code(code, r'[A-Z0-9]{18}[0-9]{2}', 'LEI')
                key = entity('lei:'+code, 'organization', label, identity_basis='published LEI')
                identifier(key, 'lei', code)
                return key
            if dataset == 'iso_mic_venues':
                mic = _code(row['MIC'], r'[A-Z0-9]{4}', 'MIC')
                key = entity('mic:'+mic, 'trading_venue', row['MARKET NAME-INSTITUTION DESCRIPTION'],
                             mic_category=row.get('MARKET CATEGORY CODE'), country=row.get('ISO COUNTRY CODE (ISO 3166)'),
                             identity_basis='ISO10383 MIC; does not establish exchange operation')
                identifier(key, 'mic', mic)
                assertion(key, 'published_mic_status', row['STATUS'],
                          source_creation_date=_iso_date(row.get('CREATION DATE')),
                          source_last_update_date=_iso_date(row.get('LAST UPDATE DATE')),
                          source_expiry_date=_iso_date(row.get('EXPIRY DATE')),
                          interpretation='MIC registration status only; no operational status inferred')
                operating = _code(row['OPERATING MIC'], r'[A-Z0-9]{4}', 'operating MIC')
                if operating != mic:
                    target = entity('mic:'+operating, 'trading_venue', identity_basis='explicit operating MIC reference')
                    identifier(target, 'mic', operating)
                    assertion(key, 'mic_operating_venue', obj=target)
                if row.get('LEI'):
                    target = lei_entity(row['LEI'], row.get('LEGAL ENTITY NAME') or None)
                    assertion(key, 'venue_operator', obj=target, identity_basis='explicit LEI in MIC directory')
            elif dataset == 'nasdaq_listings':
                if str(row.get('Symbol', '')).startswith('File Creation Time:') or row.get('Test Issue') == 'Y': continue
                symbol = _code(row['Symbol'], r'[A-Z0-9.$^-]+', 'Nasdaq symbol')
                # Snapshot-local identities prevent silent instrument merges when symbols are reused.
                token = digest([ref, symbol])
                venue = entity('mic:XNAS', 'trading_venue', 'NASDAQ - ALL MARKETS', identity_basis='Nasdaq-listed directory venue')
                identifier(venue, 'mic', 'XNAS')
                security = entity('reference:nasdaq:security:'+token, 'security', row['Security Name'],
                                  unresolved_identity=True, identity_basis='source-scoped security reference; issuer and permanent instrument ID absent')
                listing = entity('reference:nasdaq:listing:'+token, 'ticker_listing', symbol,
                                 unresolved_identity=True, identity_basis='source-scoped listing snapshot; historical interval unknown')
                identifier(listing, 'ticker', symbol, scope='XNAS')
                assertion(listing, 'listed_instrument', obj=security)
                assertion(listing, 'listing_venue', obj=venue)
            elif dataset == 'gleif_parent_relationships':
                attributes = row['attributes']; relationship = attributes['relationship']
                start, end = relationship['startNode'], relationship['endNode']
                if start['type'] != 'LEI' or end['type'] != 'LEI': raise ValueError('Relationship nodes must publish LEI identifiers')
                predicates = {'IS_DIRECTLY_CONSOLIDATED_BY': 'directly_consolidated_by',
                              'IS_ULTIMATELY_CONSOLIDATED_BY': 'ultimately_consolidated_by'}
                if relationship['type'] not in predicates: raise ValueError('Unsupported GLEIF relationship type')
                child, parent = lei_entity(start['id']), lei_entity(end['id'])
                periods = [p for p in relationship.get('periods', []) if p.get('type') == 'RELATIONSHIP_PERIOD']
                for period in periods or [{}]:
                    record = assertion(child, predicates[relationship['type']], obj=parent,
                                       relationship_status=relationship.get('status'), relationship_record_id=row['id'],
                                       registration=attributes.get('registration'),
                                       source_record_valid_from=attributes.get('validFrom'),
                                       source_record_valid_to=attributes.get('validTo'),
                                       interpretation='accounting consolidation relationship; no equity stake or financial exposure implied',
                                       relationship_period=period)
                    if period.get('startDate'): record['valid_from'] = _iso_date(period['startDate'])
                    if period.get('endDate'): record['valid_to'] = _iso_date(period['endDate'])
                    if period: record['attributes']['validity_basis'] = 'published RELATIONSHIP_PERIOD'
            elif dataset == 'sec_issuer_reference':
                cik = str(row['cik'])
                if not cik.isdigit() or not 0 < int(cik) < 10**10: raise ValueError('Invalid published CIK')
                key = entity('sec:cik:'+cik.zfill(10), 'business', row['name'],
                             identity_basis='SEC published issuer CIK', published_tickers=row.get('tickers', []),
                             published_exchanges=row.get('exchanges', []), former_names=row.get('formerNames', []))
                identifier(key, 'sec_cik', str(int(cik)))
            yield from out
