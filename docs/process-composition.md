# Explicit process composition contracts

The core supports bounded, synthetic composition across fiscal policy, resource
transfers and spatial lifecycle. These contracts declare units, information timing,
conservation and work assumptions; they do not calibrate the underlying economy.

## Run the cross-domain example

```sh
wm cross-domain --request examples/cross-domain-composition.json
```

```python
import json
from worldmodel.composition import materialize_composition

config = json.load(open('examples/cross-domain-composition.json'))
report = materialize_composition(config, fidelity='aggregate')
print(report['frames'][-1]['conservation'])
```

The example transfers a subsidy from government to buyer, settles an integer-cent
purchase with a producer, delivers integer kilograms between explicit supports,
and splits the depot at the first interval's end. The second interval explicitly
routes from an active child support. The total remains 1,000 cents and 10 kg.
Only the buyer's balance and delivery support appear in `observations`; full state
and audits are privileged materialization outputs, separate from those observations.
A missing requested observation support is `null`, never an invented zero.

`CompositionEvaluator(config, fidelity=...).step()` commits one interval atomically
and returns its frame. `result()` returns all committed frames and provenance.
Policy, payments, resource changes and lifecycle events roll back together on failure.
Standalone knowledge availability advances with execution time by default. Supply
`known_at` in configuration or as a function/evaluator keyword to impose a fixed
cutoff; when both exist, the earlier cutoff wins. The registry wrapper always
threads the outer materialization cutoff through policy and actor lifecycle
selection. The cutoff is part of checkpoint identity.
The evaluator supports context-manager cleanup and `close()`.

Fidelity `aggregate` settles each purchase with one money transfer and one resource
transfer. `unit` itemizes each kilogram, increasing work while preserving the same
fixed-price result. Neither is a calibrated behavioral kernel. Reports identify
selected fidelity, actual transfer count, conservative transfer-work estimate,
spatial work upper bound and approximations.

Limits: 20 intervals, 100 initial/selected supports, 100 accounts, 1,000 demanded kg
per interval, 20 lifecycle events per interval, 10,000 estimated transfers, 1 MiB
configuration and 8 MiB retained frames. Numerical spatial evolution has its own
100,000-work bound per interval. Prices and money are exact nonnegative integer
cents; no credit or hidden overdraft exists. Resource quantities use scalar kg and
floating-point conservation tolerance. Trade occurs at interval start; explicit
spatial changes occur at interval end. An actor lifecycle, when supplied, controls
account eligibility separately from support lifecycle. Routes naming removed
supports fail instead of silently following a guessed successor.

## Replay and process registry

`checkpoint()` emits JSON containing configuration/fidelity identity, completed
interval count and result/checkpoint hashes. `restore(checkpoint)` reconstructs a
candidate from bounded deterministic replay, verifies its result hash and swaps
state only after success. This is a portable replay checkpoint, not a promise of
constant-time restore. It invokes no external backend. Checksums detect accidental
corruption, not malicious alteration.

`register_composition_process(registry)` exposes `cross_domain_composition` through
the existing materialization and `CheckpointEvaluator` schedulers. Bind its `composition_config`
input with unit `composition-config` and `composition_state` output with unit
`composition-state`. Seed `composition_state` with `{"completed_steps":0}` or the complete
zero-step report. Set binding cadence to the configuration's `step_seconds` and
align the materialization start with its start. Every call checks the outer clock
and duration against the internal next interval. Explicit nonzero seed states must
use the matching clock. The reference handler replays a prefix of at most 20
intervals per call; its descriptor records this extra work. Tests compare final
materialization snapshots with standalone results and incremental checkpoint replay.

## One authority for transfers

`process_contracts.CouplingLedger(quantities, balances, interfaces)` declares each
quantity's unit and integer/real representation. Every account explicitly holds
every declared quantity. Each interface lists one quantity, allowed sources and
allowed targets. `apply(transfers)` validates and stages a bounded batch before
committing it. Each transfer supplies `id`, `interface`, `source`, `target`,
`quantity`, `unit` and `amount`. Reusing an ID fails, including repeated IDs in one
batch. Unauthorized endpoints, mixed units and insufficient funds also fail without
changing balances or receipts. `snapshot()` returns a detached audit representation.

Integer quantities conserve exactly; real quantities use 1e-12 relative/absolute
tolerance. These are local accounting guarantees. The ledger cannot detect the
same real-world transfer deliberately assigned unrelated IDs in separate ledgers;
a composed simulation must use one authority and stable transfer identities.

Registry process descriptors can declare `conserved_quantities` as a list of
`{"id":"stock","unit":"kg","ports":["source","destination"]}`. Ports must be
distinct numeric extensive outputs with matching units. Both temporal schedulers
check their sum after each update. Such outputs cannot have multiple writers;
exchanges belong in one authoritative coupling process. Intensive quantities must
be converted using explicit support measures before declaring an integral.

## Information availability and missing values

`select_timed_input(observations, contract, at=..., known_at=...)` chooses the latest
eligible record. Every record includes `value`, `unit`, `information_time` and
`valid_time`. The contract includes exact `unit`, nonnegative `lag_seconds`,
nonnegative `max_age_seconds` and `missing` (`error` or `omit`). A record is eligible
only when its information has arrived plus lag by both execution and knowledge
cutoffs, its valid time is not in the future, and its age is within the limit.
Conflicting records tied on both times require reconciliation.

A process input port can include `temporal: {lag_seconds, max_age_seconds, missing}`.
Bind that port to a literal array of these observations, using the port's unit.
The registry resolves and type-checks the selected value before invoking the handler,
then records the chosen input in `prediction.input_receipts`. Optional missing ports
may be omitted; required missing ports fail. Ordinary ports retain their existing
explicit persistence/literal behavior. There is no implicit zero imputation.

Implementations record `approximation` strings; plans expose selected fidelity,
implementation, cost per call and those statements. Older descriptors explicitly
report that no approximation statement was supplied.

## Lifecycle execution and aggregation

Both schedulers check declared actor eligibility at each prediction's execution
interval. Actor status moves only from not-started to active to ended, so two
endpoint reconstructions detect any intervening death or merger without repeating
the entire lifecycle for each event. Inactive actors fail
before invocation. Plans reject more than 10 million estimated lifecycle record
visits before reconstruction; standalone composition has a one-million-visit bound.
Existing conservative whole-view lifecycle preflight remains:
split actor views at end events instead of using stale bindings. Spatial support
IDs are not treated as actors unless explicitly declared in an actor lifecycle.

`aggregate_quantity(rows, kind='extensive'|'intensive', unit=...)` supports bounded
numeric scalar/vector rows. Extensive quantities sum; intensive values require
positive measures and use their weighted mean. Units and component shapes must
match. Identities and categorical values are rejected. Returned metadata states
that within-group distributions and member identities are lost. Fine support
allocation uses the explicit uniform-measure split utilities documented in
[field dynamics](field-dynamics.md); no observed fine structure is reconstructed
from an aggregate.

## Live backend provenance

The deterministic composition above performs no live effects. Existing agent
processes send declared observations, parameters and memory to their configured
backend and record predictions in traces. `ContractAgentBackend(provider, model=..., configuration=..., prompt=...,
tool_policy=...)` binds explicit model/version, configuration, prompt and tool
policy to its identity and each invocation while preserving observations and
memory. It rejects request attempts to override this contract and bounds input
and output payloads. Wrap this adapter in the journaled backend below. For retryable external execution use
`JournaledBackend` with `JournaledEnvironment`: provider identity and complete input
request are journaled before invocation, and output plus provider receipt are
persisted before completion. Uncertain effects require reconciliation; ordinary
checkpoint restore does not authorize retrying them. Deterministic mock backends
remain available for tests. The runtime cannot certify unrecorded configuration
inside an arbitrary third-party provider.
