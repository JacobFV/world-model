# Data Substrate Implementation Plan

**Goal:** An offline-first, reproducible data pipeline feeding a temporal evidence graph.
**Architecture:** Immutable dataset versions and manifests are authoritative; a local
SQLite graph index is disposable. Computations and external data use one registry.
**Tech Stack:** Python 3.11+ standard library, JSON/JSONL, SQLite, unittest.
**Spec:** `docs/superpowers/specs/2026-09-15-data-substrate-design.md`

## Global constraints
- No external dataset downloads during implementation or verification.
- No simulation processes or inferred identities from aggregate counts.
- Data root is relocatable; exact inputs and source/code hashes survive relocation.

## Execution
1. [x] Write behavioral tests for publication, provenance, DAGs, validation and graph queries;
   run `python3 -m unittest discover -s tests -v` to establish the missing implementation.
2. [x] Implement `worldmodel/catalog.py`, `store.py`, `provenance.py`, `pipeline.py`.
   `Store.import_file` creates raw refs; `Runner.run` returns immutable version refs;
   `Store.verify` and `Store.lineage` verify and expose the evidence chain.
3. [x] Implement `model.py`, `transforms.py`, `graph.py`; normalize fixture evidence,
   derive the example metric, preserve claims and index bounded temporal neighborhoods.
4. [x] Add `cli.py`, dataset definitions, fixtures and `README.md`; expose catalog,
   import, fetch, plan, run, inspect, lineage, verify, graph build and neighbors.
5. [x] Run all behavioral tests, exercise the documented CLI in a temporary data root,
   verify relocation and no runtime data in the tracked catalog. Review implementation
   for integrity gaps and correct failures before reporting completion.

No commit steps: the supplied workspace is an empty directory without a Git repository.
