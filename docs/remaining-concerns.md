# Implementation and remaining empirical limits

The laptop implementation is an experimental evidence and simulation substrate.
Its completion record is [handoff-completion.md](handoff-completion.md); the original
interruption checklist remains in [RESUME-PLAN.md](RESUME-PLAN.md). Tests establish
software contracts, not world coverage or predictive validity.

## Durable execution

The incremental scheduler preserves state, RNG streams, agent memory, inputs and
held pressures. The opt-in SQLite journal binds action outcomes to checkpoints,
deduplicates committed actions, charges failed attempts, and blocks unresolved
provider effects. Backups support verification, optional HMAC authentication and
retention without deleting quota/effect authority. See [execution-journal.md](execution-journal.md).

Remaining limits: histories and checkpoint copies consume memory. Local SQLite
coordination is not a distributed scheduler; copying a database does not create a
global quota. Provider effects cannot roll back. Exactly-once execution still
requires provider support. Superseded live writers are fenced by durable action attempt and checkpoint state.
HMAC keys must be supplied and protected separately;
checksums alone do not authenticate data, and backups are not encrypted.

## Economy and fields

The bank-ledger economy now includes explicit labor limits, daily interest and
policy-rate transmission, inventory costing, bounded demand/price feedback and
funded collateral recovery. Each committed step checks accounting and work bounds.
See [economy-mechanisms.md](economy-mechanisms.md).

Signed scalar and vector transport/diffusion now supports atomic dated spatial
lifecycle changes, closed-boundary conservation, planar polygon selection and
explicit refinement/coarsening. Timeline views retain actual active topology and
source details. See [field-dynamics.md](field-dynamics.md) and
[spatial-surfaces.md](spatial-surfaces.md).

Explicit process input-time, conservation and aggregation contracts plus a replayable
cross-domain example are described in [process-composition.md](process-composition.md).

These are synthetic mechanisms. Parameters are assumptions, not estimated causal
responses. The field solver is bounded and local, with explicitly supplied topology,
coordinate systems and component frames. It does not infer navigable water, roads,
terrain or geodetic physics from coordinates. Spatial support lifecycle and an
institution's legal lifecycle are distinct contracts.

## Evidence and coverage

All dataset-specific transformations live in each dataset directory; shared code
provides parsing, provenance, graph and execution mechanics. Acquired artifacts are
ignored; tracked code and compact manifests make the work reproducible. Observations,
source claims, reviewed assertions and synthetic scenarios remain distinguishable.

DIA holdings are dated fund positions, not official index membership or issuer
ownership. NAV is not adjusted exchange price. Temporal financial crosswalks keep
issuer, security and listing identities separate. Reviewed contract inputs and
canonical market import adapters require supplied evidence and explicit policies. See
[financial-evidence-imports.md](financial-evidence-imports.md).

Federal Register, Crossref, NASA and conflict adapters preserve source status,
publication dates and reported uncertainty. Bounded samples do not establish complete
public-figure, institution, research, law or conflict coverage. Aircraft ownership
never proves a person's presence; routes and observed movements are separate.

Current acquisition outcomes and exact access dependencies are recorded in
[source-access-2026-09-15.json](source-access-2026-09-15.json). USDA, BEA and UCDP may
require credentials. Authorized adjusted prices, corporate-action feeds, complete
index histories and bilateral contracts have not been conjured from aggregate data.
MarineCadastre's status endpoint is now online; a bounded track export remains
unverified. Freight acquisition must retain an actual access/size failure if it
cannot complete within the configured buffer.

## Learning and validation

Chronological train/validation/test benchmarks freeze model selection before final
scoring and retain persistence comparisons, missing values, input evidence and
vintage assumptions. A losing model remains a valid recorded outcome. Parameter and
scenario comparisons expose ambiguity rather than labeling a plausible fit causal.
See [benchmarks.md](benchmarks.md) and [structured-rollouts.md](structured-rollouts.md). The previous WTI holdout failed persistence;
that historical finding remains unchanged.

Structured RL spaces, isolated numerical rollouts and held-out synthetic scenarios
are software tools. They do not establish real-world strategic usefulness.
Observation restrictions are explicit interfaces, not a security boundary or an
inferred model of human beliefs. Trusted Python factories may have effects outside
the runner; numerical workers reject known live backends but are not sandboxes.

## Distribution and operational scale

Code/documentation use MIT. Dataset and upstream rights remain separate and flow
through provenance; a derived artifact does not erase upstream obligations. Wheels
bundle declarations, transformation code and fictional fixtures, not acquired data
or credentials. Standalone views make no hidden network requests.

Full-scale GB10/Linux ARM64 execution remains deferred by user instruction. Local
verification does not establish distributed reliability, global coverage or
calibrated geopolitical prediction. Those require actual scale runs, source access,
independent ground truth and an empirical validation design.

Final local checks: [handoff verification](handoff-verification-2026-09-15.json).
