"""Cross-domain panels assembled from published datasets.

A panel here is a derived dataset: it is built by a declared pipeline stage
(``data/<panel>/pipeline.py``) that pins every input ``dataset@version``, snapshots the code
and parameters in its manifest, and carries per-row evidence locators back to the input
records. The builders in this package are pure functions over record streams so they can be
tested on fixtures and run on the full catalog with bounded memory.
"""
