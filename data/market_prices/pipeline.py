"""Strict authorized canonical quote import; no network access or inferred adjustments."""
from worldmodel.financial_feeds import feed_records

def run(context):
    yield from feed_records(context,'prices')
