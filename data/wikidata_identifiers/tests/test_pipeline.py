"""The dataset's acceptance tests live with the identity layer they exist for.

``tests/test_wikidata_bridge.py`` covers this pipeline's two guards - the entity typing that keeps
IMO's ship and company number series apart, and the page-sequence check that catches a query the
service truncated - alongside the bridge that reads what it publishes. They belong together: a
value shape and the refusal it feeds are one behaviour, and splitting them would let one change
without the other noticing.
"""
