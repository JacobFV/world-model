"""Evidence products over the unified graph: entity dossiers, screening, place briefings and a local API.

Each product turns one of the cross-dataset queries in ``examples/graph-queries`` into something a
non-author can run, without weakening the graph's conventions: every edge names its dataset,
asserted and inferred identity are never mixed, every answer carries
``what_this_does_not_establish``, and the rights of every contributing dataset travel with it.
See docs/products.md. Standard library only.
"""
from .base import ProductError  # noqa: F401
