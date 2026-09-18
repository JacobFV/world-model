"""Long-history U.S. deposit rates from FRED/ALFRED, every real-time vintage.

SNDR (the declared deposit-rate series of the ``deposit_rate_pass_through`` component)
begins 2021-04 and cannot supply both the 36 observations that component needs to fit and
the 24 holdout forecasts it declares. This dataset publishes the longer deposit rates that
can: the two FDIC National Rates series SNDR replaced, and the St. Louis Fed's M2 own rate.
Each is a *different* series from SNDR, so an attempt that reads one declares it as a
substitution.
"""
from .fred_alfred import api_records, is_full


def run(context):
    """Stream FRED API observation shards; every observation keeps its ALFRED real-time period."""
    if not context.raw_inputs:
        raise ValueError('fred_deposit_rates: no raw artifact supplied')
    if not is_full(context):
        raise ValueError('fred_deposit_rates: requires the full sharded FRED API artifact '
                         '(wm acquire fred_deposit_rates --allow-network); no sample adapter is declared')
    series = context.parameters['series']
    yield from api_records(context, context.definition['id'], series, lambda series_id: series[series_id]['tier'])
