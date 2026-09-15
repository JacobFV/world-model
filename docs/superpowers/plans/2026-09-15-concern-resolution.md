# Concern Resolution Implementation Plan

Goal: implement verified remedies for each item in docs/remaining-concerns.md.
Spec: ../specs/2026-09-15-concern-resolution.md
Architecture: existing evidence/materialization contracts remain; incremental execution, consistent economic state and persistent topology are independent kernels integrated through declared ports. Distribution/evidence/visuals stay separate modules.
Constraints: bounded laptop workload, immutable evidence, no fabricated data/validation, stdlib core, optional Gymnasium integration, user-selected code license. Root owns integration/commits; agents own disjoint modules.

- [x] Checkpoint execution + rollback tests; compare uninterrupted runs against replay and restored state, including RNG, memory, end-of-step and cadence boundaries.
- [x] Coupled commercial-bank economy + dynamic policy test; verify balanced deposits/loans/settlement and no rewriting historical state.
- [x] Persistent field/topology lifecycle + tests; bounded neighborhood reads, conservation, atomic rollback and reopened storage.
- [x] Standalone packaging/provenance/resources; build wheel, install outside checkout and run demo/verify; replace data-dependent unit tests with fictional shape fixtures where possible.
- [x] RL/spaces/perception; train/evaluate tiny controlled environment with disjoint seeds, test deterministic batch rollout and mask/delay/noise semantics.
- [x] Evidence and reconciliation/validation; source/rights readiness audit, bounded acquisition where feasible, audited conflict handling and explicit calibration gate.
- [x] Interactive surfaces; safe browser rendering, selection/layers/playback, evidence metadata preserved.
- [x] License on user selection, final integration/examples, independent review, full local/clean install tests, concern disposition report.

Ruling: authorizations already cover implementation/public repository updates; no additional implementation approval gate. User delegated license selection; MIT chosen with separate cascading source terms. Complete world coverage and empirical causal validity remain evidence questions and must not be claimed from tests.

Delivery: commit and push main after verification under existing authorization.
