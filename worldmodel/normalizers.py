"""Sample-era normalizer entry point, kept for the bounded-sample contract tests.

Dataset interpretation lives in ``data/<id>/pipeline.py`` and the catalog-scale unified graph is
built by :mod:`worldmodel.unify` from *published* stage outputs, not from samples. The two names
below described which adapters existed when the graph was a union of eleven bounded samples:

* :data:`AVAILABLE` - the eleven datasets that had a working bounded-sample adapter. It is not a
  statement about the catalog: 105 datasets now have published normalized outputs
  (``python3 -m worldmodel unify-scope --inventory``).
* :data:`UNAVAILABLE` - datasets whose sources could not be sampled at the time. All three now
  have full published outputs; the tuple survives because ``worldmodel/strategic_build.py``
  reports it as a historical coverage gap.

Neither constant is consulted by ``unify``. Scope selection is
:data:`worldmodel.unify.PROFILES`, ``--datasets``, ``--domain`` and ``--exclude``.
"""
from .source_helpers import STATE_FIPS, run_local_pipeline

AVAILABLE = ('census_geography','census_population','census_business','bls_labor',
             'classifications','eia_energy','fec','sec_gleif','transport','usaspending','usgs_resources')
UNAVAILABLE = ('bea_input_output','freight','usda_agriculture')


def normalize_sample(context):
    dataset = context.definition['id']
    if dataset not in AVAILABLE + UNAVAILABLE:
        raise ValueError('No sample normalizer available: ' + dataset)
    return run_local_pipeline(context)
