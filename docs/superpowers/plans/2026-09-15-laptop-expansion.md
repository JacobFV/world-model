# Laptop Expansion Implementation Plan

> Agentic workers use subagent-driven-development for independent components; root integrates and verifies.

**Goal:** Continue every documented front with bounded, reviewable improvements.
**Architecture:** Add optional local operational adapters and richer numerical kernels while retaining the evidence/materialization contracts. Source data, fitted descriptions and synthetic scenarios remain distinct.
**Tech stack:** Python stdlib, SQLite, standalone HTML/SVG; optional Gymnasium.
**Spec:** ../specs/2026-09-15-laptop-expansion.md

## Global constraints

No full-scale GB10 work. Samples <=100 rows/1MiB,64MiB temporary download buffer. No fabricated evidence or causal validation. Tests before new behavior; no competing Git mutations. Root commits and pushes after source freeze and verification.

## Runtime operations

Files: new worldmodel/execution_journal.py, tests/test_execution_journal.py; targeted checkpoints.py integration if needed.
Interface: SQLite ExecutionJournal with bounded append/checkpoint/history, transactional quota reservation, explicit idempotency-key effects and uncertain completion recovery.
- [ ] Test reopen, concurrent budget reservation, duplicate effects, uncertain effects, payload limits and corruption.
- [ ] Implement atomic storage and optional checkpoint integration without replaying external effects.
- [ ] Run focused tests and document contracts/limits in docs/execution-journal.md.

## Economic mechanisms

Files: worldmodel/banking.py, worldmodel/coupled_economy.py, dedicated tests and examples/economy-policy-feedback.json.
Interface: existing step_economy(state,policy,shock) with explicit optional mechanism configuration; preserve absent-option behavior.
- [ ] Test labor rationing, exact-cent interest/settlement and changed policy transmission without historical rewrite.
- [ ] Implement explicit ledger treatment and bounded optional price/demand behavior; distinguish uncalibrated assumptions.
- [ ] Verify conservation, defaults, malformed inputs and existing scenarios.

## Spatial dynamics

Files: new worldmodel/field_dynamics.py, tests/test_field_dynamics.py; targeted spatial_store.py integration, example spatial timeline.
Interface: bounded evolution over declared signed/vector fields and dated topology events, returning conservation and execution audits.
- [ ] Test signed and vector conservation, advection direction, stable steps, boundary cuts and timed lifecycle changes.
- [ ] Implement componentwise dynamics with explicit coordinate/frame limitations.
- [ ] Verify rollback and old nonnegative scalar behavior.

## RL and scenario evaluation

Files: new worldmodel/structured_spaces.py and/or parallel_rollouts.py, tests; root coordinates ownership after first wave.
Interface: declared nested port spaces and bounded independent episode rollouts, deterministic seed/result order.
- [ ] Test structured round trips, malformed dimensions, optional dependency boundary, concurrent failures and caps.
- [ ] Implement adapters without changing generic Environment action semantics.
- [ ] Evaluate held-out scenario configurations, not just seeds.

## Evidence and validation

Files: selected data/*/dataset.json, source normalizers/fixtures, new worldmodel/benchmarks.py and tests.
Interface: primary-source bounded samples; benchmark_series(rows,train_end,validation_end) preserving prediction evidence and final holdout separation.
- [ ] Verify source documentation, access and bounds before acquisition; update explicit failure statuses.
- [ ] Test duplicate/units/temporal leakage and model selection independent of final test targets.
- [ ] Implement baseline selection and held-out scoring, preserving failures as results.

## Visual and release integration

Files: bounded surface adapter(s), examples, README, remaining concerns and verification report.
- [ ] Test explicit spatial-to-surface mapping and interactive controls; inspect standalone browser output.
- [ ] Freeze all sources; run local and clean-source suites, installed wheel/Gym smoke and integrated examples.
- [ ] Record exact outcomes and residual data/empirical limits, commit and push.
