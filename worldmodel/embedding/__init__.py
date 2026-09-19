"""World-state embeddings over dated subgraphs of the evidence catalog.

``county_panel`` (stdlib) assembles a dated county panel and county graph from published
normalized datasets, attaching to every value the date on which it became public.
``tensors`` (numpy), ``model`` and ``train`` (torch) and ``assay`` (numpy, torch, lightgbm)
build as-of subgraphs, train the root-readout encoder and score it under a pre-registered
protocol. Everything past ``county_panel`` needs the optional ``embed`` extra::

    pip install "worldmodel-substrate[embed]"

The actor domains live in ``actors`` (13F positions), ``votes`` (roll-call defection), ``newpos``
(13F link prediction) and ``domains/`` (campaign contributions, bank distress); each supplies the
runner in ``actors_assay`` with tasks, a subgraph template and its own data audit.

See docs/world-state-embeddings.md for the design, the leakage rules and the results,
including the ones that fail.
"""
