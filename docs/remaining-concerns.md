# Implemented mitigations and remaining concerns

This repository is an experimental evidence and simulation substrate. Implementation
checks establish software behavior, not complete world coverage or predictive validity.
The concerns below distinguish working mechanisms from unresolved empirical and
operational requirements.

## Temporal execution and environments

**Implemented:** `CheckpointEvaluator` persists state, RNG streams, agent memory,
literal inputs, held pressures and next-due times. The environment CLI defaults to
this incremental evaluator; `--backend replay` retains the reference implementation.
Numerical and output-validation failures roll back the transition. Trusted JSON
checkpoints bind graph, request, registry and source identity and validate scheduler
and pressure contracts on restore. Nonaligned observations preserve process cadence;
end-of-step implementations emit discrete `set` pressures only.

**Remaining:** cumulative snapshots, traces and transaction state still occupy memory
and are copied. Checkpoint checksums are not authentication. Live backend effects
cannot roll back: attempts are audited, a failed live transition blocks further
steps, and live-backend restore is rejected. Exactly-once external execution across
processes, global quota enforcement and a distributed scheduler are not implemented.

## Coupled economic mechanisms

**Implemented:** `coupled_economy` connects firms and households to one commercial-bank
ledger. Explicit step policies control lending, production, purchases, repayment and
bankruptcy. Transfers settle through deposits and bank reserves; each committed step
checks accounting. Conservative cumulative transaction, posting and ledger-work budgets
reject oversized scenarios before transfers. The process registry exposes state feedback and a separate dynamic
policy input, so the checkpoint environment can change policy without rewriting prior
steps. The older cash-funded and replay economy kernels remain separate examples.

**Remaining:** this is a closed synthetic economy, not reconstructed business accounts.
Labor limits, price discovery, interest, collateral recovery, inventory valuation,
central-bank responses and empirical behavioral calibration remain absent or outside
this kernel's scope. Accounting consistency does not establish realistic incentives,
market equilibrium or a useful real-world policy.

## Spatial state and lifecycle

**Implemented:** `SpatialStore` persists explicit supports, topology and field values
in SQLite. Bounded selections and lifecycle batches support birth, death, merge and
split, with atomic transactions, conservation checks and explicit external inputs.
Cartesian and latitude/longitude axis conventions are declared. Signed scalar and
vector values can be stored and conserved componentwise.

**Remaining:** numerical evolution delegates bounded nonnegative scalar selections to
`FieldWorld`. This is not a distributed field solver, adaptive mesh, geodetic PDE
solver or general vector-field dynamics engine. Coordinates locate supports; they do
not infer polygons, terrain or missing topology. Explicit coordinate pairs support
spherical geodetic distance; this is not road or navigable-water distance. Boundary cuts require
explicit handling. Spatial lifecycle edits do not automatically mutate every arbitrary
coupled temporal model.

## RL and first-person observations

**Implemented:** explicit scalar space declarations, an optional Gymnasium adapter,
synchronous vector environments, and bounded tabular Q-learning with disjoint training
and evaluation seeds and a fixed-action baseline. The perception wrapper implements
allowlists, masks, reporting delays, seeded numeric noise and bounded delivered-observation
memory. The toy benchmark is an offline integration example.

**Remaining:** the vector adapter is local and sequential. A partial batch failure
requires reset, not distributed rollback. The numerical Gym adapter supports explicitly
bounded scalar observations and continuous scalar actions, not arbitrary nested ports.
Perception is an interface contract, not a security sandbox or inferred human beliefs.
Toy rewards and held-out seeds do not validate strategic behavior outside the simulator.

## Evidence, identity and validity

**Implemented:** bounded source acquisition and access inventories, evidence-preserving
identity references, dated reconciliation policies with retained candidates and audit,
unit- and dimension-aware claim selection, bounded sensitivity sweeps, holdout gates, accounting checks and explicit model-assessment gates.
These mechanisms expose uncertainty; they do not remove it.

**Remaining:** real bilateral obligations, complete ownership, supplier dependencies,
adjusted security histories, corporate actions and index constituents remain incomplete.
The bounded Apple SEC asset and issuer samples now work with the configured contact
header. Other sources may require credentials, licensed access or a release-specific adapter. Roads are local subsets; airport references are not flight or passenger
movements. Samples are bounded and nonrepresentative. Sparse source-scoped IDs do not
establish complete real-world entity coverage. Independent temporal crosswalks, benchmarks
and mechanism calibration remain necessary. The previously documented descriptive WTI
holdout did not beat persistence; no calibrated geopolitical simulator is claimed.

## Distribution, rights and visual inspection

**Implemented:** wheels bundle read-only catalog declarations, fictional fixtures and
examples. Writable runtime data is configured independently; `wm resources` reports
paths. Code and documentation use MIT. Derived reports and materializations retain
source rights metadata recursively, including unspecified or restricted terms.
Interactive standalone HTML adds linked entity selection, filtering, time playback,
source inspection, panel visibility/reordering and specification export.

**Remaining:** Git retains the bounded samples and artifact history; wheels do not
bundle that data. Standalone browser exports and indexes remain local. Fixture
coverage does not verify access to a current
external feed. MIT does not relicense third-party data; rights inventories do not decide
license compatibility or grant redistribution rights. Unknown terms remain visible and
require review for redistribution, without blocking local computation. Maps have no
remote basemap or reconstructed terrain. Graph playback does not infer historical
topology. Visual editing changes panel layout/specification, not source data or a general
canvas diagram. ARM64/GB10 deployment and nonlocal filesystem backends need their own
operational verification.

See [environments and surfaces](environments-and-surfaces.md),
[checkpoint details](checkpoint-environments.md), [reference backbone](reference-backbone.md),
[strategic systems](strategic-systems.md), and [code/data rights](../DATA_RIGHTS.md).

See [the verification record](concern-resolution-verification-2026-09-15.json) for exact local checks and artifact references.
