# Dataset catalog

Each child directory is a dataset, whether imported, computed, or materialized as
a graph. `dataset.json` is the tracked definition; raw data, staging, run receipts
and published results are created at runtime and ignored by Git.

The four `offline_example` entries are runnable with the repository fixtures.
The fourteen `requires_configuration` source families record the ingestion scope,
expected outputs and mapping requirements. They intentionally have no guessed
release URL or universal parser. Configure exact releases, adapter entrypoints
and mappings before using them. Split a family into individual datasets when
releases, formats, licensing or update schedules differ.

See the root README for commands, schema, provenance, and workstation setup.

Each definition also has a laptop `sampling` policy. Sampling is available even
when the production entrypoint still requires configuration. `sample all
--allow-network` acquires bounded excerpts; `explore DATASET` verifies and displays
the retained profile. See `docs/sampling.md` and the exploration report.
