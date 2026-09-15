"""Strict authorized dated corporate-action import; successor identities stay explicit."""
from worldmodel.financial_feeds import feed_records

def run(context):
    yield from feed_records(context,'corporate_actions')
