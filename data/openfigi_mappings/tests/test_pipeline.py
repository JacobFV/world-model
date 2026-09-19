"""The dataset's acceptance tests live with the identity layer they exist for.

``tests/test_openfigi_bridge.py`` covers this pipeline's two guards - the FIGI level that decides
which rows of an answer can be identity at all, and the positional completeness check that catches
an answer with a different number of results than its request had jobs - alongside the bridge that
reads what it publishes, the listing mapping that meets ``sec_issuer_reference``, and the
declaration's own limits. They belong together: a value shape and the refusal it feeds are one
behaviour, and splitting them would let one change without the other noticing.
"""
