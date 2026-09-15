> This guide describes the original kernel. See [the current strategic systems
> guide](strategic-systems.md) for fields, additional kernels, source coverage and lifecycle support.

# Typed world graph and process materialization

## Implemented contract

The data path is now:

```
immutable sampled sources → source-specific typed evidence → world_evidence
                                                             ↓
request + process bindings + implementation registry → materialized dataset
```

`world_graph` remains the fictional demonstration. `world_evidence` contains the
11 acquired real samples. `python3 -m worldmodel unify` normalizes and validates
them, pins their versions, publishes a union and builds its SQLite index. This
command performs no downloads. All 11 samples must exist; missing data fails
explicitly. BEA input-output, freight and USDA are recorded as unavailable.

Evidence records have distinct identities from the entities they describe. Stable
publisher identifiers and explicit crosswalks join datasets; names alone do not.
Source rows, raw line references, acquisition time, reported valid time, pipeline
code snapshots and exact input hashes remain attached. Multiple descriptions and
contradictory claims survive. Geographic reference entities are labeled synthetic
when the row only supplies an identifier. Aggregate business observations refer to
cohorts, never invented individual businesses. Unmapped source fields remain in
`attributes.source_row`; their presence does not imply semantic normalization.

Inspect the executable schema with `python3 -m worldmodel ontology`. It covers
agents, organizations, facilities, jurisdictions, industries, aircraft/private
jets, flights, shipments, commodities/oil, deposits, resource flows, accounts,
securities and investments. Relations check endpoint types; variables check types
and units. Ontology coverage is broader than acquired evidence. Private-flight
tracks, investment positions and ownership networks are not acquired merely
because their types are declared.

## Processes and interchangeable implementations

A process declares named, typed input and output ports, units and topology. An
implementation separately declares fidelity, valid time-step range, cost per
invocation and handler. Bindings connect ports to entity variables or typed
literal inputs. Inputs may be numbers, vectors, strings, booleans, arrays or
objects. Cyclic dependencies are allowed.

The default registry has six process contracts, each with deterministic,
stochastic and agent implementations: population growth, resource inventory,
investment cash flow, movement, human decisions and business decisions. These
are illustrative models, not fitted predictions. Inspect them with
`python3 -m worldmodel processes`.

Handlers return pressures, events, memory and diagnostics. Numeric `rate`
pressures contribute change per second. A `target` pressure draws a variable
toward a predicted value at a strength measured in inverse seconds. Confidence
multiplies strength as a nonnegative weight. For constant pressures over a step:

```
dx/dt = sum(rate × strength × confidence)
      + sum(strength × confidence × (target - x))
```

The combiner integrates this linear equation analytically. Vector components use
the same rule. Discrete `set` pressures select the greatest weighted strength;
equal-weight contradictory sets fail. Sets cannot mix with rates or targets.
Declared output bounds fail explicitly instead of silently clipping.

Due processes read the same old state, then pressures combine simultaneously.
Each process has its own cadence; its pressure remains fixed until its next
invocation. Times, output intervals and process cadences have microsecond precision.
Output sampling and process cadence are distinct. Higher sampling
frequency alone does not make a forecast more accurate. There is no adaptive
error estimator or stiff differential-equation solver yet.

Agent implementations call a Python-injected backend implementing
`predict(request) -> prediction`, with a nonempty `identity` dictionary. Identity,
exact request/response, entity memory and diagnostics are retained. Each entity
has at most one agent memory owner. Deterministic processes cannot overwrite that
memory. No hosted LLM endpoint is bundled; CLI agent requests fail without a
backend. Cost is a declared scheduling unit, not an enforced token/dollar limit
inside an external provider. Backend authors must implement provider limits.

## Materialization operator

```python
from worldmodel.materialize import materialize
result = materialize(store, graph_ref, request, registry=None, agent_backend=None)
```

A request specifies:

- `targets`: explicit `{entity, variable}` pairs.
- `start`, `end`, `step_seconds`: time range and output resolution.
- `known_at`: information cutoff, independent of modeled time.
- `mode`: `observed` or `forecast`.
- `bindings`: port connections, parameters, fidelity and optional cadence.
- `initial_state`: explicit, labeled scenario overrides when needed.
- `budget`, `max_calls`, `max_points`, `seed`: bounded execution and replay.
- `abstraction`: `entity` or `group`; groups require explicit members and
  per-variable `sum`, `mean`, `min` or `max` reducers.
- `reconciliation`: default `error`; optional `latest_observation` records its
  choice and rejected evidence. Equal-time conflicts still fail.

The operator traces backwards from targets through all connected producers and
inputs. Unreachable processes do not execute. It streams graph evidence and
retains only relevant entities and candidate values, preflights the complete
invocation budget and temporal regimes, then runs the selected implementations.
All state variables currently need an observed or explicitly supplied initial
value; processes cannot initialize missing state implicitly.

Forecast, scenario and persistence records cannot seed observational state without
an explicit scenario override. Observed views require valid evidence at every requested time and never
extrapolate. Forecasts seed from evidence valid at the start and known by the
cutoff. Unupdated inputs and targets persist under an explicit
`persistence_assumption` after the start. Forecasts do not assimilate later
observations automatically. Override values are labeled `scenario_assumption`.
Increasing temporal fidelity cannot create missing historical observations.

Group reducers report exact members and coverage of those requested members;
coverage is not population representativeness. Arbitrary spatial refinement,
automatic individual/cohort substitution and latent microstate generation remain
unsupported. Unknown request dimensions fail rather than being ignored.

The result is another immutable dataset at `data/<requested dataset>/final/<hash>`.
`view.json` contains snapshots, plan, reconciliation and complete execution trace;
`records.jsonl` exposes snapshots as observations or unitless literal assertions. Its manifest pins the graph,
registry, handler hashes, code snapshot, parameters and backend identity. Seeded
local stochastic runs replay exactly; external agent traces support audit and manual replay; no automatic saved-response
backend is bundled, and providers are not assumed to regenerate identical outputs.

## Run the examples

```sh
python3 -m worldmodel unify
python3 -m worldmodel neighbors geo:US:state:06
python3 -m worldmodel materialize world_evidence --request examples/observed-population.json
python3 -m worldmodel materialize world_evidence --request examples/california-population.json
python3 -m worldmodel view california_population_scenario
python3 -m worldmodel verify california_population_scenario
```

The population scenario starts from the sampled July 2024 California estimate,
assumes 1% annual growth, updates daily and emits approximately monthly values.
Its September 2026 information cutoff deliberately makes this a retrospective
scenario, not a historical backtest or a claim about actual 2025 population.

## Remaining strategic capabilities

The system supports evidence-backed neighborhood exploration, typed aggregate
constraints, explicit scenario propagation, competing pressures, entity/group
views, heterogeneous process substitution, budgets and replayable lineage.

It still lacks calibrated causal models, parameter fitting, uncertainty
calibration, policy/action search, objectives and utility functions, constrained
optimization, equilibrium/market clearing, automatic entity resolution beyond
explicit identifiers, cross-source unit/currency conversion, full source
coverage, live aircraft/resource/investment feeds, automatic model selection by
predictive skill, intervention semantics and counterfactual identification.
Events are returned and audited but do not yet mutate graph topology. Large
workstation execution also needs partitioned columnar storage, distributed
scheduling and benchmarked resource limits. The current union/index build loads
bounded graph records; it is not a demonstrated full-world deployment.

## Inspiration

[IBM-1](https://jacobfv.github.io/IBM-1/) motivates separating process contracts
from implementations and materializing a requested portion of a modeled world.
This implementation adopts those interface ideas; it does not claim to implement
IBM-1's learned representations or training system.
