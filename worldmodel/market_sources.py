"""Shared ontology declarations and compatibility access to dataset-local pipelines."""
from .source_helpers import _iso_date, _code, run_local_pipeline

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


def normalize(context):
    dataset = context.definition['id']
    if dataset not in SOURCE_IDS:
        raise ValueError('No acquired source adapter: ' + dataset)
    return run_local_pipeline(context)
