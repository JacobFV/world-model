# World Kernel Implementation Plan

**Goal:** Connect real evidence to a typed graph and implement bounded, multi-fidelity process materialization.
**Architecture:** Immutable normalized evidence feeds explicitly parameterized process implementations; requested views trace dependencies and produce auditable scenario artifacts.
**Tech Stack:** Python standard library, JSON/JSONL, existing Store/Runner/SQLite.
**Spec:** `docs/superpowers/specs/2026-09-15-world-kernel.md`.

- [x] Task 1: ontology and normalization. Own ontology.py, normalizers.py, model.py,
  real source dataset definitions, tests/test_normalization.py. Normalize all
  available real samples with typed entities/relations and raw-line evidence.
- [x] Task 2: process abstraction and library. Own processes.py, process_library.py,
  tests/test_processes.py. Implement declared ports, fidelity selection, pressure
  composition and illustrative deterministic/stochastic/agent implementations.
- [x] Task 3: materialization. Own materialize.py and tests/test_materialize.py.
  Trace target dependency closure, validate state, integrate simultaneous pressure
  updates with internal substeps, support group views and immutable publication.
- [x] Task 4: integration. Own cli.py, integration tests and documentation. Add
  ontology/process listing, unify and materialize commands; build the real graph,
  run a reproducible illustrative scenario, review all interfaces and verify suite.

Ruling: no Git worktree/commit workflow because the workspace has no Git repository.
Ruling: existing authorization covers implementation; no additional design approval pause.
Shared-file scan: graph and process tasks write disjoint modules. Materialization
consumes the explicit dict interfaces above. CLI/config integration is root-owned;
normalizer updates only existing real dataset entrypoints, not the demo definitions.

Verification: 71 tests pass. Real sample union validates 3,320 records, 933 entities,
1,164 relations and 1,168 observations. The 365-call population scenario publishes
14 snapshots and recursively verifies. See docs/kernel-verification-2026-09-15.json.
