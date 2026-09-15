# Typed world graph, process registry and materialization

## User requirements and inspiration
Unify all acquired real source samples into a typed graph; expand the ontology for
movement, private aircraft, oil/resources, investments and institutions. Define
heterogeneous multi-timescale processes with interchangeable deterministic,
stochastic and LLM-agent implementations. Materialize requested targets at chosen
time resolution and abstraction, within a compute budget.

IBM-1 (https://jacobfv.github.io/IBM-1/, retrieved 2026-09-15) separates state,
topology and processes P=(I,O,T,f,theta), treats implementations as replaceable,
and traces dependencies from requested outputs. We adapt that software structure;
we do not import claims of a learned or validated world predictor.

## Boundaries
Use Python stdlib and existing immutable dataset/provenance system. No additional
bulk downloads or paid model calls. Normalize all 11 acquired real source families;
report unavailable BEA/freight/USDA coverage explicitly. Keep demo data separate.
Synthetic references and aggregate cohorts are labeled, never fabricated people,
firms, ownership, aircraft owners or observed causal edges.

## Shared module contracts

### ontology.py / normalizers.py (graph task)
`ENTITY_TYPES` remains importable from model.py. Add a declarative ontology with
entity hierarchy, relation domain/range and numeric/categorical/vector variables.
`validate_typed_graph(records: list[dict]) -> dict` validates entity references,
compatible relation endpoints and ontology declarations, returning useful counts.
Unresolved references become typed synthetic reference entities, visibly labeled.
`normalizers.normalize_sample(context)` yields existing model.py compatible
entity/observation/assertion/event records from JSONL sampled raw payloads. Every
output has evidence to a contributing line. `observation.subject` names the entity
whose variable is measured; metric and unit are explicit. Existing fields remain
backward compatible. Each real dataset gets that entrypoint and suitable metadata;
no new downloads or mutation of existing sample bytes.
Observations include original data attrs, source units, valid time and acquired
knowledge time. Preserve suppressed/missing values and classification revision.
Provide `ontology.describe()` returning JSON-compatible schema for CLI discovery.

### processes.py / process_library.py (process task)
Public API intentionally uses JSON-compatible descriptors and dictionaries:
- `ProcessRegistry.register_process(spec: dict)`; spec keys id, inputs, outputs
  (port-name -> {type, unit?, required?}), topology (description), description.
- `register_implementation(spec: dict, handler: Callable)`; spec keys id,
  process_id, fidelity ('deterministic','stochastic','agent'), max_step_seconds,
  cost_per_call, description; optional min/max regimes and requires_backend.
- `describe() -> dict` process/implementation descriptors.
- `select(process_id, fidelity, step_seconds, remaining_budget, backend_available=False)
  -> dict` selects compatible implementation or raises clear ValueError.
- `predict(implementation_id, inputs, parameters, context) -> dict` validates
  ports/types/units and result, then invokes handler. Inputs are port-name ->
  {'value': JSON, 'unit': str|None}; context includes dt_seconds, time, rng
  (random.Random), state (output-port -> value), entity_id, memory, agent_backend.
  Result {'pressures':[{port, mode:'rate'|'target'|'set', value, strength,
  confidence, unit}], 'events':[], 'memory':dict, 'diagnostics':dict}.
  A numeric rate has units of the target variable per second (unit field names
  target variable's unit; mode gives time semantics). A target pressure strength
  is a nonnegative relaxation rate in inverse seconds. Rate strength is a
  nonnegative dimensionless multiplier. Set chooses discrete/categorical state.
- `combine_pressures(current, pressures, dt_seconds, value_type='number')`:
  numeric dx/dt = sum(rate*strength*confidence) + sum(k*(target-x)), stable exact
  solution for constant pressures per step; sets are exclusive with dynamics,
  highest strength*confidence wins, unequal tied targets raise. No averaging
  categorical values. Validate finite quantities, durations and compatibility.
- `process_library.default_registry() -> ProcessRegistry` with illustrative
  resource inventory flow, population growth, investment/cash flow, position
  movement, and human/business decision implementations. Typed heterogeneous
  ports include number,string,boolean,object,array/vector. Deterministic and seeded
  stochastic behavior executable offline. Agent backend is an explicitly supplied
  object with `predict(request:dict)->dict`; per-entity memory/goals/budget context
  and backend response are audited. No pretend LLM fallback and no network backend
  is invoked by default. Forecasts are labeled illustrative/unvalidated.

### materialize.py (root task)
`materialize(store, graph_ref, request, registry=None, agent_backend=None)->dict`
reads an exact normalized graph dataset ref and validates request. Request keys:
- start,end (ISO time); step_seconds; known_at; targets list of {entity,variable}
- initial_state optional list {entity,variable,value,unit,type}, explicitly scenario
  overrides rather than observations; seed; budget; max_points
- bindings list {id,process_id,entity_id,fidelity,inputs,outputs,parameters}; inputs
  port -> {entity,variable} OR {value,unit}; outputs port -> {entity,variable}.
- abstraction 'entity' or 'group'; groups list {id,members:[entity IDs]}; reducers
  per variable 'sum'|'mean'|'min'|'max' explicitly required for group outputs.
Output includes dependency plan, selected implementations, exact source ref,
request and code identity, state snapshots, per-step pressures/inputs/outputs,
agent transcripts/memory, fidelity/regime warnings and uncertainty limitations.

Materialization traces backward from targets through output bindings to input
variables and includes competing processes on a variable. Cycles are legal
feedback: simultaneous read-old/write-new steps, not a DAG cycle error. Missing
inputs fail explicitly. Query graph only filters on knowledge time and valid
interval; seed numeric/categorical variables from observations and scalar claims.
Conflicting source state requires an explicit request reconciliation policy
('error' default, or 'latest_observation'); resolution records rejected candidates.
Do not extrapolate historical validity by silently carrying expired observations.

Internal step never exceeds the smallest selected max_step_seconds; split at
process cadence boundaries if configured. Sample requested outputs at requested
step_seconds and include exact end. Budget preflight bounds evaluations and
snapshot count; do not partially publish on budget/fidelity failures. Forecast
state is distinct from evidence and stored as an immutable materialization with
input refs, request, code/registry/backend identity and output hashes. Group
aggregation reports coverage and requires explicit reducers; no invented microstate
or high-fidelity promise for coarse input. Spatial filtering/high-detail refinement
without evidence is not implemented and must be reported as unsupported.

## Validation
Behavioral tests for all adapters, typed reference/endpoint validation, aggregate
identity without fake firms, real graph topology, pressure composition and units,
seeded stochastic execution, unavailable agent backend, per-entity agent memory,
dependency pruning, coupled feedback, competing pressures, temporal clipping,
resolution invariance for constant rates, budgets, group aggregation, lineage,
and CLI integration. Existing tests must continue to pass. Build actual typed
world evidence from all available real samples and exercise a clearly labeled
forecast view on a real measured variable with explicit illustrative assumptions.
