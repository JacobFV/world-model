> Historical interruption checklist. Current implementation and verification status lives in
> [handoff-completion.md](handoff-completion.md); current external dependencies live in
> [remaining-concerns.md](remaining-concerns.md). Status labels below describe the interruption.

# World model: paused work and detailed remaining specification

**Paused:** 2026-09-15, at the user's request after a provider interruption.
**Repository:** https://github.com/JacobFV/world-model
**Workspace:** `/Users/vibestartup/Code/world-model`
**Last committed implementation:** `40a3470cb24d912b9b400702c67f21660236df0e` (`main`, previously verified public and pushed).
**Next session's objective:** continue all implementation, evidence, validation and usability fronts below, within laptop limits. **Do not run full-scale GB10 workloads.**

This is the authoritative continuation handoff. It consolidates the earlier
[expansion spec](superpowers/specs/2026-09-15-laptop-expansion.md),
[expansion plan](superpowers/plans/2026-09-15-laptop-expansion.md), and
[remaining concerns](remaining-concerns.md). Proposed APIs in this document are
implementation contracts for unfinished work, not claims that those APIs exist.

## 1. User intent and standing decisions

Build a coherent, evidence-backed world substrate that can materialize economic,
geopolitical, geographic and actor-centered simulations. The foundational model
supports fields/topologies and typed graph descriptions; entities alone are not the
entire physical substrate. Processes have heterogeneous typed inputs/outputs,
multiple cadences and selectable numerical/agent fidelity. Materializations expose
inputs, outputs, abstraction and temporal resolution. Any suitable materialization
can become an environment, with actions mapped to inputs, observations to outputs,
and rewards explicitly computed from state. A single actor's point of view is one
special case, not a restriction on environments.

Continue to support growth, birth, death, mergers and territorial changes; finance,
resources, transport, public institutions, research, law and public information must
connect through the same evidence contracts. Distinguish supported schema, acquired
observations, inferred identity, scenario assumptions and calibrated mechanisms.

Standing choices:

- Code/documentation: MIT. Dataset rights stay attached to their source and cascade
  through derived artifacts, including excerpts of derived datasets. Unknown terms
  are metadata for downstream review, not a local computation gate.
- Preserve `data/<dataset>/{raw,processing,final,...}` organization. Computed datasets
  are first-class siblings of acquired datasets.
- Immutable raw receipts and derived versions retain hashes, input references, code
  snapshots/entrypoints, source metadata, acquisition time and validity information.
- Use Python stdlib for the core; Gymnasium is optional.
- Retain at most **100 rows / 1 MiB per exploratory source sample**. Temporary
  download/processing allocation is **64 MiB**, including the configured buffers.
  A full file may be downloaded and discarded if it fits the explicit allocation.
- Do not infer individual businesses from aggregate counts, a flight passenger from
  aircraft ownership, an instrument identity from a ticker alone, or a bilateral
  debt contract from an aggregate balance-sheet figure.
- Parallel work is authorized. Use disjoint file ownership and one integration owner.
- Implementation and public GitHub commit/push were previously authorized. The latest
  instruction pauses implementation for a written handoff; resume only when the user
  resumes work. Do not publish unfinished code merely to clear the working tree.

## 2. Exact state at interruption

### 2.1 Committed baseline

Release `0.2.0` includes:

- Typed evidence graph, source normalization, identity/reconciliation and coverage.
- Cadenced process registry and replay/reference materialization.
- Incremental `CheckpointEvaluator`, portable JSON state, deterministic RNG/memory,
  pressure/scheduler validation, numerical rollback and explicit live-effect limits.
- Generic environments, optional numerical Gymnasium adapter, tabular Q-learning,
  sequential vector environments and bounded perception wrappers.
- Coupled commercial-bank production/purchasing kernel; exact-cent ledger checks.
- SQLite `SpatialStore` with bounded reads, atomic support lifecycle operations,
  signed/vector storage, and nonnegative scalar numerical evolution via `FieldWorld`.
- Interactive standalone tables/plots/maps/graphs with selection, timeline, source
  inspection, layer visibility, panel reordering and specification export.
- Standalone wheels with catalog, fictional fixtures and examples; code/data rights.

Last baseline verification: **322 tests passed locally**. A clean source snapshot
ran 322 tests with six acquired-data integrations skipped, i.e. 316 passed. Installed
wheel, bundled examples, Gymnasium 1.3.0 environment checker and browser controls
passed. Those results describe the committed baseline, **not the unfinished working
state below**. See [the recorded verification](concern-resolution-verification-2026-09-15.json).

### 2.2 Uncommitted files to preserve

At the pause, `git diff --stat` showed no edits to tracked implementation files.
These new files existed:

| File | State | Next action |
| --- | --- | --- |
| `worldmodel/execution_journal.py` | Implemented by runtime worker, frozen | Independent review, rerun tests, integrate |
| `tests/test_execution_journal.py` | 12 new tests | Rerun with checkpoint tests |
| `docs/execution-journal.md` | Detailed runtime API and limitations | Keep; update after integration |
| `tests/test_field_dynamics.py` | Test-first spatial specification only | Implement its missing module/API |
| `docs/superpowers/specs/2026-09-15-laptop-expansion.md` | Initial continuation design | Keep as prior planning context |
| `docs/superpowers/plans/2026-09-15-laptop-expansion.md` | Initial work breakdown | Superseded in detail by this handoff |

This handoff itself is another new file. **Do not delete the incomplete spatial tests
or mistake their import failure for a regression in the committed release.** They
import `worldmodel.field_dynamics`, which does not exist yet, and specify
`SpatialStore.evolve_timeline`, which also does not exist yet.

Worker state:

- Runtime worker: completed. Reported **27 targeted tests passed**: 12 journal and
  15 checkpoint tests. Root has read the files/status but has not independently
  rerun that new targeted suite in this paused handoff turn.
- Spatial worker: interrupted while preparing test-first implementation. Only the
  new test file was present; no `field_dynamics.py` or `spatial_store.py` edit exists.
- Economy worker: provider returned “Selected model is at capacity.” No economy
  implementation edits were present. Its proposed design is captured below.
- No workers should continue implementation while the user has paused the task.

### 2.3 Data reconnaissance performed, not yet integrated

Two bounded exploratory workbook downloads succeeded under `/tmp/wm-source-probes`:

| Probe | Downloaded bytes | Observed structure |
| --- | ---: | --- |
| `dia_holdings.xlsx` | 19,190 | Fund/ticker metadata in rows 1–2, holdings date in B3, table headers on row 5 |
| `dia_prices.xlsx` | 30,240 | **Premium/discount**, not price: table headers on row 4, dated rows below |

These are temporary research downloads, **not catalog samples, normalized evidence,
or committed data**. Temporary paths may disappear. Reacquire through the normal
bounded sampling pipeline once the adapter/config is complete. Do not interpret the
second filename as evidence of market prices.

Verified primary-source discovery:

- [State Street DIA fund page](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-dow-jones-industrial-average-etf-trust-dia)
  links a daily holdings workbook, NAV history and premium/discount history.
- Holdings endpoint:
  `https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-dia.xlsx`
- Premium/discount endpoint:
  `https://www.ssga.com/library-content/products/fund-data/etfs/us/pdhist-us-en-dia.xlsx`
- NAV endpoint discovered, **not yet downloaded or inspected**:
  `https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-dia.xlsx`
- [BEA input-output landing page](https://www.bea.gov/itable/input-output) links
  concordance and interactive tables. The concordance URL was discovered but not
  sampled:
  `https://www.bea.gov/sites/default/files/2023-10/BEA-Industry-and-Commodity-Codes-and-NAICS-Concordance.xlsx`
- BEA supply/use bulk-download leads still require verification against BEA itself;
  no replacement for the API-key-dependent source was configured.

The State Street page distinguishes fund holdings from index holdings and attaches
source-specific reproduction terms. Preserve those terms; do not relabel an ETF's
holdings as official index membership or redistribute its raw workbook with MIT code.

## 3. Resume procedure and ownership

### First commands

```sh
cd /Users/vibestartup/Code/world-model
git status --short
git log -1 --oneline
python3 -m unittest tests.test_execution_journal tests.test_checkpoint_runtime -q
```

Read this handoff, then `docs/execution-journal.md` and
`tests/test_field_dynamics.py`. Do not run a broad suite expecting it to pass until
the missing spatial implementation is supplied. Do not reset or clean the worktree.

### Parallel decomposition

| Workstream | Exclusive implementation files | Dependencies |
| --- | --- | --- |
| Runtime | `execution_journal.py`, new journaled-runtime helper, journal tests | Existing checkpoint protocol |
| Economy | `banking.py`, `coupled_economy.py`, optional mechanism helper, economy tests | Existing exact-cent ledger |
| Spatial | New `field_dynamics.py`, `spatial_store.py`, spatial tests | Existing support lifecycle |
| Root/integration | Sampling/source adapters, benchmarks, CLI, ontology, packaging, shared docs | Coordinate merges with workers |
| Second wave RL/UI | New structured-space/rollout adapters; surface adapter/UI files | Assign after a slot frees |

Only root changes shared registry, ontology, CLI dispatch, release version and Git.
Agents report actual files changed, test results and limitations, then freeze. Use
reviewers on completed independent streams before final integration. The provider
capacity failure was operational; no alternate model has been selected in this handoff.

### Source snapshot constraint

`worldmodel.__init__` snapshots all package Python source hashes on import.
`capture_code` rejects publication if package sources change during that process.
Do source work first, freeze all workers, then start fresh Python processes for
acquisition/publication and installed-wheel checks. Previously published artifacts
remain valid because they retain their original source snapshots.

## 4. R1 — Review and finish durable runtime integration

**Present implementation:** `ExecutionJournal(path, *, limits=None)` in
`worldmodel/execution_journal.py`; see its dedicated documentation.

Implemented APIs:

- `append(stream, value, *, key=None)`; `history(stream, *, after=0, limit=100, max_bytes=...)`.
- `configure_quota(name, limit)`; `reserve(name, units, key)`; `quota(name)`;
  `reservations(name, *, after=0, limit=100)`.
- `save_checkpoint(session, checkpoint, *, history_events=(), expected_sequence=None)`;
  `load_checkpoint(session, *, sequence=None, expected_identity=None)`.
- `save_evaluator(session, evaluator, **kwargs)` and `load_evaluator(...)`.
- `begin_effect(key, request, *, quota=None, units=1)`;
  `mark_uncertain(key, reason)`; `complete_effect(key, result, *, provider_receipt=None)`;
  `effects(*, after=0, limit=100, max_bytes=...)`.

### R1a. Independent correctness review

Review transaction boundaries, idempotency equality, cursor ordering, reserved effect
history namespaces and size accounting. Reproduce concurrent claims with independent
SQLite connections. Reopen must not reset quotas. Tampered payloads must fail before
runtime restoration. Inspect exceptional paths for partial quota/effect/history writes.

Defaults are 1 MiB ordinary JSON payload, 32 MiB checkpoint, 64 MiB logical payload
storage and 100,000 records. These are **logical payload caps**, not physical disk
quotas: database pages, indexes and WAL consume extra space.

Acceptance:

- Rerun the worker's 27 tests and add regressions for any review findings.
- Lost-response/repeated calls never consume a second reservation or execute a
  pending/uncertain effect again.
- Failed checkpoint/history transaction leaves neither partial record.
- A stale expected checkpoint sequence rejects the writer.

### R1b. Usable environment integration

Add an opt-in journaled episode adapter, preferably in a separate
`worldmodel/journaled_environment.py`, plus CLI arguments in `environment_cli.py`:
`--journal PATH`, `--session ID`, and explicit `--resume`.

Contract:

1. Bind session to graph reference, materialization request, environment spec,
   registry/source identity and seed; reject mismatches.
2. Reserve a conservative process-work bound before a new transition. Charge failed
   attempts monotonically; a checkpoint restore never refunds consumed work.
3. Give each action a stable ID and content hash. Repeated identical submissions
   return the committed outcome; different content under one ID is rejected.
4. For pure numerical execution, stage transition output and checkpoint together;
   persist before exposing success. If persistence fails, restore the prior in-memory
   checkpoint or mark the session unusable until explicit recovery.
5. For external effects, record pending before invocation and pass the same key to
   a provider that supports idempotency. Ambiguous completion stays unresolved.
6. Publish immutable episode reports with the journal/checkpoint audit reference,
   graph inputs and exact request. Do not treat the mutable SQLite file as an
   immutable artifact without exporting a verified snapshot.

The current `save_evaluator` runs after an in-memory transition. It does **not** make
that earlier transition atomic with storage. Close this gap in the opt-in adapter;
do not claim it is already solved.

Tests: crash/lost-response simulation at each boundary, failed persistence followed
by retry, resume without duplicate process calls, stale action ID, quota exhaustion,
malformed stored environment state, and equality with uninterrupted execution.

### R1c. Retention, storage and authentication

Add an explicit SQLite backup/export operation with checksum manifest; test reopen
from the exported backup. Separate usage metadata for logical payload, database and
WAL bytes. Add bounded checkpoint/history retention only if quota/effect authority
survives pruning. Pruning must never enable a previously attempted external action.

If checkpoints must cross an untrusted boundary, add optional HMAC envelope signing
with a key supplied outside datasets. Otherwise continue to require trusted storage;
hashes alone are not authentication. Do not add secrets to provenance snapshots.

Deferred outside this pass: distributed scheduling, networked filesystem guarantees,
and full-scale GB10 execution.

## 5. E1 — Extend the coupled economy consistently

**Files:** `banking.py`, `coupled_economy.py`, optional `economy_mechanisms.py`, tests,
new `examples/economy-policy-feedback.json`, and `docs/economy-mechanisms.md`.
**Status:** design only; existing implementation untouched.

Preserve `initialize_economy`, `step_economy(state, policy, shock=None)` and
`simulate_coupled_economy`. Absent options must retain current behavior and tests.
Do not rewrite prior policies to implement a current action.

### E1a. Labor constraints

Optional household `labor_capacity` and firm `labor_per_unit`; use explicit time
units tied to the step. Production consumes worker capacity as well as finance and
physical capacity. Allocate shared labor deterministically by stable firm ID initially;
report requested, allocated and unmet labor so order dependence is visible.

Tests: one worker shared by two firms, zero capacity, fractional input validation,
capacity changing next step, finance and labor constraints together, and no inventory
creation for unproduced units.

### E1b. Loan interest and monetary transmission

Optional mechanism object:

```json
{
  "interest": {
    "policy_rate": 0.04,
    "spread": 0.02,
    "day_count": 365,
    "insufficient": "defer"
  }
}
```

Rates are annual fractions; duration is explicit. Validate finite ranges. Round with
the existing exact-cent money convention. A prospective per-step `policy_rate`
override affects that step onward only.

Add an `interest` bank transaction: debit borrower deposit, credit bank equity;
principal is unchanged. Same-bank interest does not create an interbank reserve
transfer. Update coupled accounting identities to include interest explicitly.
Insufficient payment must follow a declared `defer` or `bankrupt` policy. Deferred
unpaid amounts can initially be memorandum arrears, clearly distinguished from booked
principal/assets; never silently capitalize or erase them.

Add optional bounded policy-rate feedback from explicit inflation/output-gap inputs,
with declared reference rate, targets, response coefficients and min/max rate. Direct
policy overrides take precedence and are recorded. Coefficients are scenario
assumptions until independently calibrated.

Tests: zero/positive rate, exact rounding, changed rate, spread, insufficient deposit,
interest versus principal repayment order, reserve/deposit/equity identities and
bounded rule response. Reject NaN/infinity, ambiguous duration and invalid day count.

### E1c. Inventory valuation, price and demand feedback

Track inventory quantity separately from inventory cost. Use declared weighted-average
cost initially; distinguish revenue, cash surplus, cost of goods sold and inventory
write-down. Existing `profit` is currently a cash-oriented metric, so do not silently
change its meaning: introduce clearly named accounting outputs or version the metric.

Optional bounded price adjustment may respond to target versus actual inventory and
recent unmet demand. Optional demand elasticity may respond to relative price and
explicit expected energy/rate shocks. Record the equation, parameters, lag convention,
clamping and units. Demand changes must not create purchasing power.

Tests: stock accumulation, sale below/above cost, inventory destruction, no division
by zero, bounded price response, lagged expectations and cash-constrained purchases.

### E1d. Explicit collateral recovery

Initial proposed recovery is a funded sale, not an unexplained noncash bank asset:
`collateral_sale = {buyer, units, unit_price}` during a declared default. Transfer goods
to a funded eligible buyer, repay principal from proceeds, then write off remaining
principal through existing equity-loss accounting. Explicitly record recovery value,
principal recovered and loss. Do not infer market value from book value.

Tests: insufficient buyer cash, missing buyer, excess units, interbank settlement,
partial/full recovery, zero recovery, repeated default and goods conservation.

### E1e. Work bounds and integrated policy example

Extend conservative preflight budgets to include interest transactions, labor loops,
recovery and new history fields. Do not reintroduce the household-by-firm purchase
cross-product blowup. Run old examples plus a short scenario combining energy/rate
shock, labor limits, production, credit, spending and default.

Acceptance: balanced ledger and conserved goods every step; policy action changes
future state without mutating history; identical seeded runs match. This proves
mechanical consistency, not empirically realistic strategy.

## 6. S1 — Implement signed/vector field dynamics and timed lifecycle

**Status:** only `tests/test_field_dynamics.py` exists. Read it before implementation.
**Files:** new `field_dynamics.py`, targeted `spatial_store.py`, tests,
`examples/spatial-timeline.json`, `docs/field-dynamics.md`.

### S1a. Numerical API

```python
evolve_fields(world, request, *, coordinate_system, component_frames)
```

Return `state`, `execution`, per-field `conservation`, assumptions and calibration
status. Do not mutate the input world.

Request fields: `duration_seconds`, `step_seconds`, `boundary="closed"`, explicit
`edge_units`, `max_substeps`, `max_work`. Extensive values are quantities; intensive
values are densities weighted by cell measure. For vectors, require a matching
`fixed_global` component frame and axis count. Reject undefined local tangent-frame
rotations; latitude/longitude coordinates alone do not define vector transport.

Diffusive flux across an edge is conductance times the difference in intensive
values. Advection uses the declared directed edge and transport rate. Apply equal
and opposite extensive changes, component by component. Convert intensive fields
through support measure. Signed values are allowed; do not clip them to zero.

Compute a stable outgoing-fraction bound and subdivide steps (test contract <=0.9).
Preflight cumulative work before evolution and reject oversized requests.

Existing tests specify exact examples:

- Signed `[-2, 2]` across equal supports becomes `[-1, 1]` after the declared quarter
  step; vector components diffuse consistently.
- Directed transport moves negative components in the source-to-target direction.
- Unequal measures preserve the weighted intensive integral.
- High conductance subdivides instead of generating new extrema.
- Wrong frames/units/open-boundary requests/nonfinite values reject.

### S1b. Atomic spatial timeline

```python
SpatialStore.evolve_timeline(request)
```

Request: `start`, `end`, `sample_seconds`, numerical step/budgets, boundary and frame
metadata, optional selected cells/explicit boundary-cut acceptance, and
`events=[{"time": ..., "events": [existing lifecycle operations]}]`.

Advance to each event boundary exactly, apply its lifecycle batch, then emit that
time's `after_events` frame. Return full bounded frames, projected snapshots, final
state, cumulative conservation/work, start/end and source revision lineage. Persist
both final field values and support topology. The entire requested timeline is one
transaction: a late invalid event rolls back earlier evolution, lifecycle and audit.

Store an explicit continuation clock. A subsequent request must begin at the prior
end; reject overlapping or backward implicit replay. Preserve existing scalar
`FieldWorld`/`SpatialStore.materialize` behavior.

Tests already specify split at 0.5 seconds, birth/merge/death with explicit external
input, conservation across topology changes, full rollback on late error, boundary
cut handling, bounded frames/snapshots/work and continuation timing.

Implementation caution: reuse transaction-aware internal lifecycle helpers; do not
nest independent commits inside the outer timeline transaction.

### S1c. Geometry and abstraction follow-up

Add explicit cell polygon/boundary support only with validated coordinates and a
clear CRS. Plan point-in-polygon, adjacency and bbox intersection separately from
centroid lookup. Record when geometry is simplified. Adaptive refinement initially
uses explicit split/merge criteria and conservation tests, not automatic claims of a
general mesh solver. Global curved-surface/vector PDEs remain a separate future task.

## 7. L1 — Structured RL spaces, concurrent rollout and generalization

**Status:** not started in this continuation. Existing `rl.py` supports bounded tabular
learning and scalar Dict/Box Gymnasium adaptation; `VectorEnvironment` is sequential.

### L1a. Declared structured spaces

Create `structured_spaces.py` with schema-to-space conversion and strict value
validation. Support finite bounded numeric scalars, booleans, categorical choices,
fixed-length arrays/vectors and nested objects. Require explicit lengths, keys,
choices and bounds; reject open-ended objects as numerical spaces. Preserve units
and field paths in the declaration. Do not infer a space from one observed sample.

Expose a stdlib declaration first, then optional Gymnasium Dict/Tuple/Discrete/Box
mapping. Provide flatten/unflatten with stable ordering and an explicit schema hash.
Masked `None` observations require an explicit mask channel or declared optional
encoding; never coerce them into a meaningful zero.

Tests: nested round trip, scalar type distinctions, dimensional mismatch, invalid
category, nonfinite values, copied outputs, declared mask semantics, source-free
import without Gymnasium, and installed Gymnasium checker.

### L1b. Bounded concurrent episodes

Create `parallel_rollouts.py` using independent processes for CPU-bound numerical
episodes. Prefer serializable worker configuration/factories over closures that fail
under spawn. Each worker reconstructs its own environment and SQLite connection.
Keep result order deterministic by episode ID; partition seeds explicitly.

Configure worker count, maximum episodes, transitions and per-result bytes. Terminate
or clearly mark failed workers; do not pretend partial batches rolled back. Charge
shared work before dispatch through the journal if enabled. Keep a sequential mode
as a reference oracle. Do not place live external agents in an automatically retried
worker pool.

Tests: serial/parallel equality, distinct seeds, out-of-order completion, worker error,
timeout cleanup, oversize result, quota exhaustion and no orphan processes.

### L1c. Held-out scenarios and policy comparisons

Extend `examples/train-materialization.py` or add a separate scenario benchmark.
Split scenario configurations before training: interest regimes, initial liquidity,
labor capacities and supply disruptions. Keep final test configurations untouched
by model/policy selection. Report results per scenario and aggregate with explicit
weights, including baseline and worst-case outcomes.

The current 60-transition example changes seeds on one deterministic economy. It is
an integration test, not scenario generalization. Preserve that caveat until this
new benchmark runs. Use <=100 transitions for an initial smoke; larger laptop runs
need an explicit bounded request.

Rewards remain explicit graph-state criteria. Add multiple named objectives and
Pareto/constraint reporting rather than hiding tradeoffs in one unlabeled score.

## 8. D1 — Expand bounded evidence acquisition and finance references

### D1a. Improve workbook sampling first

Current `sampling.xlsx_rows` assumes the first worksheet row is the header. It already
bounds XML bytes and does not extract ZIP files, but cannot correctly parse the DIA
workbooks discovered above.

Add explicit configuration:

- `header_row`: physical worksheet row number.
- Optional selected-column mapping and explicit start/end row conditions.
- `context_cells`: selected metadata cell references, retained in a reserved context
  object for every sampled row, including the holdings date.
- Preserve worksheet member and physical source-row locator in sample provenance.

Validate header uniqueness, source cells, shared-string references and metadata name
collisions. Respect one cumulative uncompressed XML budget. Reject unexpected DTD or
entity declarations; do not fetch external XML resources. Stop at the configured row
and retained-byte cap. Do not let footer/disclaimer rows become entities.

Tests use tiny generated fictional XLSX archives: metadata-before-header, sparse cells,
duplicate headers, missing sheet, duplicate/colliding context, oversized XML, malformed
shared strings and footer exclusion. No real workbook should be committed as a test
fixture unless its redistribution terms are resolved.

### D1b. DIA holdings as investment evidence

Proposed dataset: `data/ssga_dia_holdings/dataset.json`, source normalizer in a new
`finance_sources.py`, fictional shape tests and `reference_build` integration.

Known header row 5 has Name, Ticker, Identifier, SEDOL, Weight, Sector, Shares Held and
Local Currency. B3 contains the snapshot date. Inspect identifier documentation before
calling the generic Identifier field a CUSIP. Do not infer issuer identity from names.

Produce:

- Fund entity with explicitly sourced identity.
- Source-scoped security references, with published identifiers as claims.
- Dated investment-position entities connecting fund and instrument.
- Shares and weight observations with explicit units and snapshot time.
- Source evidence on every entity/assertion/observation.

Do not equate fund holdings to the official Dow index constituent list. Do not infer
corporate ownership percentage from portfolio weight. A published snapshot establishes
holdings at that date, not an unlimited valid interval.

Use <=100 rows/1MiB, retain publisher terms/attribution/status, and build through the
normal immutable sample pipeline after source freeze. Test closed typed graph,
quantity parsing, dates, footer exclusion, missing identifiers and unit distinctions.

### D1c. NAV and premium/discount histories

Inspect the NAV workbook before defining its parser. Distinguish NAV per fund share,
exchange price, premium/discount, distributions and adjusted total return. The already
probed `pdhist` workbook contains premium/discount observations only. Verify whether
its decimal is percentage points or a fraction against source documentation before
assigning the unit.

Create separate datasets/metrics as needed. Keep at most 100 dated observations;
explicitly select a useful interval rather than silently relying on workbook order.
Do not relabel NAV as security market-price history or derive adjusted returns without
corporate-action/distribution evidence.

### D1d. BEA and freight alternatives

BEA input-output still requires `BEA_API_KEY` in its current catalog config. Investigate
BEA's own downloadable supply/use/requirements tables as a no-key alternative. The
NAICS concordance is classification evidence, not an input-output matrix. If a bounded
workbook works, keep release year, industry/commodity revision, monetary units and
producer/purchaser-price conventions in every normalized observation.

FAF historical state ZIP still failed at its configured ORNL URL. Verify the current
primary publisher's link and release before changing it. Probe response size within
the shared buffer. If too large, locate a documented filtered/table export; do not
silently raise the cap or claim flows from road topology alone.

Acceptance: either a verified bounded source and typed normalization, or a specific
recorded URL/access/size failure. Never fill missing rows with invented data.

### D1e. Remaining access-dependent datasets

| Area | Known gap | Next concrete work |
| --- | --- | --- |
| USDA agriculture | Missing `USDA_NASS_API_KEY` | Validate adapter with fictional shape; use supplied key when available or verified public bounded tables |
| Marine AIS | Ordering service unavailable; bulk tracks not verified under budget | Find official bounded geographic/time export or a verified small archive; distinguish vessel tracks from people |
| Adjusted security prices | Feed/adjustment policy absent | Configure authorized feed, quote currency/session/calendar, splits/dividends and point-in-time IDs |
| Corporate actions | Licensed daily-list feed not configured | Define dated event schema and import adapter; ingest only with actual source access |
| Index membership | Complete official history absent | Dated publisher snapshots/constituent changes; ETF holdings remain separate |
| Bilateral obligations | Aggregate SEC balances insufficient | Filing-specific contract extraction and reviewed counterparty/schedule/currency/seniority evidence |

Do not solicit credentials unnecessarily; document exact environment-variable names
and continue independent work. Do not put keys or private provider responses in Git.

## 9. D2 — Identity, obligations and broader domain coverage

### D2a. Temporal financial identity

Extend explicit dated crosswalks among issuer CIK/LEI, instrument identifiers, listing
venue MIC, ticker and currency. Model ticker reuse, delisting, merger successor and
share-class changes. Equivalence evidence must identify what is equivalent: issuer,
security and listing are different entities.

Test overlapping contradictory assignments, missing validity intervals, ambiguous
same-name issuers and snapshot-local IDs. Reconciliation must preserve all candidates
and the policy decision; no destructive name-based merge.

### D2b. Contract extraction pipeline

Design a bounded source-document adapter with document hash, accession/URL, section
and character/table locator. Extract candidate parties, amounts, currencies, dates,
rate terms, maturity, collateral and seniority into claims, not immediate canonical
obligations. Attach extraction method/model/version and confidence. Require explicit
counterparty resolution and completeness status before a claim enters an exposure
simulation. Unknown schedules/parties stay unknown.

Tests: fictional agreements with amendments, tables, missing amount, uncertain party,
multiple currencies, undated clauses and superseded terms. Publish extracted claims
and reviewed mappings as separate adjacent datasets. Avoid pretending every SEC
balance sheet can reconstruct the banking network.

### D2c. Transport and movement

Separate network geometry, route feasibility, capacity, timetables and observed
movements. Roads/ports/airports already have partial support; complete coverage is not
acquired. Add dated multimodal transfers, travel-time uncertainty, closures and capacity
constraints, with explicit local geographic bounds.

Movement events require evidence of the moving entity. Aircraft registration or a
corporate association is not proof a person was aboard. Missing passenger/crew links
remain unresolved. Test temporal route continuity and impossible transfers.

### D2d. Institutions, conflict, research, law and information

Existing schema declarations cover many of these concepts, but acquisition and
cross-domain processes remain sparse. Work through these as source-specific adapters:

- Public offices and figures: dated office terms, jurisdictions, appointments and
  affiliation changes; separate a person from the office they hold.
- Conflict: dated aggregate conflict events, participants, territorial claims and
  uncertain reported effects; retain contradictory sources and reporting lags.
- Education/research: institutions, affiliations, publications, grants and citations;
  distinguish affiliation date from publication date and resolve identifiers explicitly.
- Law: statutes, regulations, amendments, effective dates, cases and rulings; distinguish
  proposed/enacted/effective/repealed status and source jurisdiction.
- Public information: posts/publications, authorship, platform, publication/revision
  time and cited source; a post is a claim/event, not established world truth.

For each: one bounded primary-source sample, a fictional adapter fixture, closed typed
schema validation, provenance, a coverage statement and one meaningful materialized
query. Do not count adding entity-type names as completed evidence integration.

## 10. V1 — Descriptive benchmarks and empirical validation

**Status:** not started. Existing `calibration.fit_ar1` and `validation.assess_holdout`
are available. Prior WTI holdout failed persistence; preserve that finding.

Proposed new module `benchmarks.py`:

```python
benchmark_series(rows, train_end, validation_end, *, candidates=None, max_rows=100000)
```

### V1a. Dataset contract and separation

Require one explicit series identity and unit. Sort dated rows, reject duplicates
unless reconciled first, preserve excluded missing values. Partition into training,
validation and final test by time. Require minimum usable observations in all three.
Record whether values are retrospective revisions or actual information vintages.

Candidate baseline set: persistence, training mean, bounded drift and AR(1). Fit on
training, select using validation MAE with a deterministic tie-break favoring simpler
models, then freeze selection before final test. If refitting on training+validation,
record that operation and do not inspect final targets while fitting.

Emit every prediction with input/target evidence and information cutoff; use only
observations available before its target time. One-step rolling inputs are allowed
when clearly labeled; do not claim a fixed-horizon forecast from them.

### V1b. Scoring and diagnostics

Report selected and baseline MAE, signed bias, counts, missing-data policy, error
quantiles and simple bounded uncertainty intervals. Use numerically stable finite
summaries. If metrics are unrepresentable, fail explicitly. Do not optimize a published
final holdout repeatedly and still call it unseen.

Tests: changing final test values cannot change selected model; future input leakage
fails; units/identity/duplicate errors fail; persistence wins on an appropriate fixture;
constant and large-finite series remain well-defined; insufficient data fails.

### V1c. Mechanism validation and parameter uncertainty

Keep accounting invariants separate from behavioral calibration. Fit optional economy
parameters only from suitable evidence and identify observed versus latent quantities.
Report parameter bounds, sensitivity and whether multiple parameter sets fit equally
well. Run withheld periods/scenarios and compare to explicit simple baselines.

A process is not causally calibrated because a forecasting score improves. Policy
response validation needs a specified empirical design or external benchmark; until
then label response coefficients as assumptions. Expose model disagreement in views
and compare strategies across parameter/scenario ensembles.

CLI: add `benchmark-series` and artifact publication using verified inputs; retain
`assess-model` failure outcomes rather than raising merely because a model loses.

## 11. U1 — Spatial and temporal surfaces

**Status:** not started in this continuation. Existing surfaces already support
interactive time/entity filtering and bounded SVG/table rendering.

### U1a. Spatial result adapter

Add `spatial_surfaces.py` to convert `SpatialStore`/timeline results into existing
surface inputs without inventing coordinates. Preserve cell IDs, measures, frame time,
field units, component frames, revision/hash and lifecycle audit. Distinguish a point
support map from polygon territory. Use a selected vector component/magnitude only
when explicitly requested and label the transformation.

Add panels for scalar field color, vector arrows, topology edges and lifecycle table.
Choose bounded node/edge/frame counts. Return omission counts and missing-geometry
status rather than failing into misleading empty geography.

### U1b. True temporal topology

Current graph playback does not reconstruct historical graph topology. Extend surface
payloads with explicit edge/entity validity or timeline frames from materialization.
At a time cutoff, use the correct active support/edge set. Do not infer birth/death from
absence in a truncated sample. Test merge/split playback and source selection after
an entity disappears.

### U1c. Export and interactive verification

Export the exact view specification plus source/materialization reference. Keep user
layout changes separate from source claims. Optional locally supplied basemap/geometry
must carry source and CRS; no hidden network calls in standalone reports.

Tests: escaping hostile labels, finite color/vector scales, missing coordinates,
large-view truncation, correct timeline topology and preserved provenance. Browser
check: selection, slider/playback, layers, source inspector and exported spec. Inspect
actual screenshot and DOM; unit tests alone are insufficient for interface behavior.

## 12. P1 — Process abstraction and cross-domain composition

The registry already supplies multiple deterministic/numerical/agent implementations.
Remaining work is to make richer components composable without losing semantics:

- Declare conserved quantities and coupling interfaces where multiple kernels exchange
  money, goods, labor or spatial mass; reject double application of the same transfer.
- Specify process input information time, valid time, lag, unit and missing-value policy.
  An unavailable observation must not silently become zero.
- Record fidelity selection, estimated work and any approximation used by a requested
  materialization. Compare simple and expensive kernels on shared fixtures.
- Add lifecycle eligibility at process execution boundaries: unborn/dead/merged entities
  cannot keep acting under stale bindings. Distinguish spatial support lifecycle from
  agent/institution lifecycle; they are related but not interchangeable.
- Define coarse/fine aggregation operators with extensive/intensive semantics and
  documented information loss. Do not average money, rates and identities uniformly.
- For live human/business/government agent backends, bind model/config/prompt/tool inputs,
  observations and output receipts to provenance. Integrate effect journaling before
  allowing retryable external execution. Keep deterministic mock backends for tests.

Acceptance: at least one short cross-domain materialization combining economic policy,
resource flow and spatial/lifecycle change, with conservation audits, explicit inputs,
limited observations and replay/checkpoint equivalence where the contracts allow it.

## 13. Release integration and verification checklist

Complete after implementation streams are frozen. Root owns these steps.

1. Review each agent's changes and actual test output; do not rely on a success message.
2. Update ontology/registry/CLI integrations and fictional examples. Validate all new
   source records against the typed graph and all declared process units/types.
3. Resolve incomplete `test_field_dynamics.py`; then run the complete suite:
   `python3 -m unittest discover -s tests -q`.
4. Export a clean staged source snapshot and run the same suite. Acquired-data tests
   may skip explicitly; new fictional contract tests must run offline.
5. Build a wheel with `pip wheel --no-deps --no-build-isolation .`; install in a fresh
   virtual environment outside the checkout. Ensure examples/catalog/fixtures exist
   and runtime data writes elsewhere. Exclude compiled bytecode from text provenance.
6. Run `wm resources`, `wm demo`, `wm verify world_graph`, bundled local sampling,
   new economy/spatial/journal examples, structured Gymnasium checker and training smoke.
7. Acquire only finalized bounded source samples in fresh processes. Build and recursively
   verify reference graph and new reports; record actual access failures.
8. Run checkpoint/replay or serial/parallel comparisons on compatible models. Test
   reopen/resume and budget exhaustion using the journal.
9. Run final held-out benchmarks without tuning against their reported final results.
10. Render standalone surfaces; inspect browser interactions and screenshots.
11. Write a new verification JSON with commands, versions, counts, artifact references,
    input/source hashes, limits, failed benchmarks and remaining access gaps. Existing
    local artifact hashes are not downloadable public data releases.
12. Update README and remaining-concerns by final behavior, not this conversation's
    chronology. Select the next release version based on the actual interface changes.
13. `git diff --check`; inspect staged files for acquired data, SQLite/WAL files,
    credentials and temporary probes. Keep only code, docs, configs and fictional fixtures.
14. Commit and push under the existing authorization, then verify remote HEAD matches
    local HEAD and the working tree is clean. Do not mark unfinished or externally
    blocked empirical work as complete.

## 14. What remains outside a software-only completion claim

Even after every implementation task above passes:

- Complete world coverage is not established by a catalog or a sample.
- Public-source access and licensed feeds may remain unavailable.
- A consistent ledger and stable solver do not establish realistic economics or
  geopolitical prediction.
- Provider-side exactly-once semantics cannot be manufactured by a local journal.
- A local SQLite implementation does not establish distributed reliability.
- Full-scale GB10/Linux ARM64 validation is explicitly postponed by the user.

Report those boundaries directly and preserve the next actionable dependency. The
next session should finish the pending implementation, not erase these distinctions.
