"""Compatibility imports; dataset interpretation lives in data/<id>/pipeline.py."""
from .source_helpers import STATE_FIPS, run_local_pipeline

AVAILABLE = ('census_geography','census_population','census_business','bls_labor',
             'classifications','eia_energy','fec','sec_gleif','transport','usaspending','usgs_resources')
UNAVAILABLE = ('bea_input_output','freight','usda_agriculture')


def normalize_sample(context):
    dataset = context.definition['id']
    if dataset not in AVAILABLE + UNAVAILABLE:
        raise ValueError('No sample normalizer available: ' + dataset)
    return run_local_pipeline(context)
