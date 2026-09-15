# Strategic Systems Implementation Plan

> Agentic workers: use subagent-driven-development for independent tasks and focused review.

**Goal:** Build runnable transport, finance-energy-business and strategic decision workflows with bounded standardized evidence.
**Architecture:** Independent domain kernels consume explicit state and return auditable results; source adapters feed typed evidence; CLI publishes exact artifacts.
**Tech Stack:** Python stdlib, JSON/JSONL, SQLite, Store/Runner.
**Spec:** docs/superpowers/specs/2026-09-15-strategic-systems.md

## Global Constraints

Python standard library, existing immutable Store/Runner provenance. Retain at most 100 source rows / 1 MiB per new sample; shared 64 MiB temporary disk buffer. No unbounded downloads. Synthetic scenarios explicitly labeled. Source/model availability and calibration status independently reported. Existing examples/tests remain compatible. No Git repository; no commits/worktree deletion.

## Tasks

- [x] A: transport.py, tests/test_transport.py, examples/multimodal-{network,request}.json. Write failing connectivity/direction/schedule/closure/capacity tests, implement bounded routing and traveler events, run tests and report exact API.
- [x] B: economy.py, economy_processes.py, tests/test_economy.py, examples/economy.json. Write failing accounting conservation/credit/inventory/anticipation tests, implement simulator and registry adapter, test baseline vs shocks and report output schema.
- [x] C: strategic_sources.py, tests/test_strategic_sources.py, new data source configs. Verify official URLs, define explicit sample limits, test normalization with concrete representative rows. Root performs final pulls once files stable.
- [x] D: strategy.py, calibration.py, artifacts.py, tests/test_strategy.py, tests/test_calibration.py. Assert known finite policy rankings, hard budget rejection and chronological holdout; implement and verify.
- [x] E: ontology.py, process_library.py, cli.py, strategic_build.py, README/docs. Integrate modular domains and sources. Run bounded acquisition, graph builds, route/economy/strategies/calibration examples. Review cross-domain invariants, run final suite, publish verification summary and current affordances.

## Coordination

A/B/C write disjoint files; root owns D/E and existing shared files. No worker spawns agents. Source hashing requires fresh Python processes after any edits; postpone full-suite verification and real publications until modules stable. Maintain progress here across continuations. Domain workers provide test evidence and specific limitations for root review.

- [x] F: banking.py exact-cent reserve/loan/deposit accounting and daily registry adapter. Tested deposit creation/destruction, settlement, insolvency.
- [x] G: fields.py, tests/test_fields.py, examples/fields.json. Field/topology substrate, conservative dynamics, lazy bounded projection and territory overlaps.
- [x] H: actor_kernels.py, tests/test_actor_kernels.py, examples/actors.json. Distinct numerical human/business/government implementations, explicit state and valid regimes.
- [x] I: lifecycle.py, institutional_schema.py, tests/test_lifecycle.py. Append-only entity lifecycle materialization and institutional/geographic schema extension.
- [x] J: integrate G/H/I CLI/process selection/materialization, verify actual acquisitions and all scenarios, document model vs data vs calibration separately.

Final verification:159 tests pass. All15 published demonstration/evidence artifacts recursively verify.
See docs/strategic-verification-2026-09-15.json for hashes, source gaps and held-out results.
