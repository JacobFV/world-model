"""Actor domains for the pre-registered embedding assay.

Each module here builds a fixed-template dated subgraph per sample and a binary target, in the
shape :mod:`worldmodel.embedding.actors_assay` expects (see :mod:`worldmodel.embedding.votes` for
the pattern this follows). Nothing in a domain module runs a model; the runner does that.

* :mod:`~worldmodel.embedding.domains.fec` -- campaign contributions: will a giving committee give
  to the same candidate committee again in the next election cycle?
* :mod:`~worldmodel.embedding.domains.fdic` -- bank distress: does a declared distress marker appear
  in a bank's next quarterly call report?

Both need the optional ``embed`` extra (numpy).
"""
