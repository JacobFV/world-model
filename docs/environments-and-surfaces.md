# Environments and materialization surfaces

An environment wraps a materialization's **inputs, outputs and state-derived reward**.
It has no special actor type. A single subject, several entities, an aggregate group,
or a field-backed temporal view can use the same interface. Selecting one subject is
an ordinary materialization choice; output selection and permitted controls remain
explicit.

## Runtime interface

```python
from worldmodel.environments import Environment, CheckpointEvaluator

evaluate = CheckpointEvaluator(store, graph_ref, materialization_request,
                             step_seconds=3600, max_total_calls=10000)
env = Environment(evaluate, environment_spec)
observation, info = env.reset(seed=17)
observation, reward, terminated, truncated, info = env.step({"spend": 0.02})
```

`Environment` also accepts any callable `evaluate(input_history, seed)` returning a
materialization with a chronological `snapshots` list. This is the adapter boundary
for other execution engines and materialization formats. Each history item contains
`inputs`: routed `{binding, port, value, unit}` entries. A custom evaluator must implement
deterministic replay or equivalent transactional state semantics and its own execution
limits; a callable alone does not establish provenance or reproducibility.

Each snapshot exposes `{time, entity, variable, value, unit}` and may carry evidence,
origin and other metadata. Group IDs work as selectors just like individual entity IDs.
There is no implicit visibility or information-access model: select only the outputs
an agent should receive. `info` contains episode status, not the underlying graph.
`env.materialization` is a separate, privileged inspection API.

The reset/step tuple interface supports the bundled bounded RL helpers and optional
Gymnasium adapter described below. An example episode or a toy training result does
not establish a validated strategic policy.

## Specification

```json
{
  "actions": {
    "spend": {
      "binding": "cash_flow", "port": "expenditure",
      "type": "number", "unit": "USD/second",
      "minimum": 0, "maximum": 0.1
    }
  },
  "observations": {
    "balance": {"entity": "org:example", "variable": "cash", "unit": "USD"}
  },
  "reward": {
    "terms": [{
      "selector": {"entity": "org:example", "variable": "cash", "unit": "USD"},
      "mode": "delta", "weight": 1, "scale": 100
    }]
  },
  "max_steps": 6
}
```

Actions must exactly match the declared names. Numeric actions require finite bounds;
other supported types are strings, booleans, objects, arrays and vectors. Optional
`choices` further restrict values. Payloads are limited to 64 KiB. In the temporal
adapter, destinations must be existing literal process input ports. Connected state
variables are not implicitly exposed as writable controls; a process must explicitly
provide the input you want to control.

Observations select materialized outputs. Optional `path`, such as
`["snapshot", "cash"]`, traverses an object or array. The selector's `unit` checks the
outer output unit; it does not infer or validate a nested field's physical unit. Prefer
separate typed outputs for financially or physically meaningful reward components.
Missing/null outputs, conflicting values, invalid paths and mismatched units fail.

Reward is `sum(weight * value_or_delta / scale)`, with explicit positive scales.
`delta` compares successive materialized states; `value` uses the new state's level.
Scales normalize heterogeneous metrics into the declared objective score. Missing
state does not become zero. An optional `termination` uses a numeric selector,
`operator` (`gte` or `lte`) and threshold `value`. Horizon and execution-budget limits
truncate the episode. A budget-rejected action is not added to history; the environment
returns its previous observation, zero reward and a truncation reason.

## Temporal execution

The CLI defaults to `CheckpointEvaluator`; `--backend replay` explicitly selects
`TemporalEvaluator`, the full-prefix reference. The checkpoint evaluator pins graph,
implementations, cadence and the full request horizon. It persists state, independent
RNG streams, memory, literal inputs, held predictions and scheduler deadlines. Only due
handlers execute. Observation intervals may fall between process updates; actions still
must align with the affected binding's cadence. Discrete `end_of_step` implementations
may emit only `set` pressures. Results retain cumulative snapshots, intervention records
and traces; they are copied in memory, so execution is incremental without promising
constant-memory episodes.

Numerical failures and Environment observation/reward validation roll back the pending
transition. Work limits are checked before handler calls; failed attempts still consume
the current session's attempt budget. Trusted JSON checkpoints use `.checkpoint()` and
`.restore(checkpoint)`, binding graph/request/registry/source identity and validating
state, RNG and scheduler contracts. Nested tuple values and nonstring object keys are
rejected because JSON would change their meaning. `.publish()` returns an immutable
materialization reference with source provenance; the CLI publishes episode prefixes
without repeating their process calls. See [checkpoint details](checkpoint-environments.md).

Live backends require explicit injection and identity. Attempts are audited. A failure
after a live call blocks further steps until explicit reset; external effects cannot
roll back. Live-backend checkpoint restore is rejected because exactly-once external
execution is not guaranteed. The replay adapter rejects live backends entirely.

Read-only observed materializations use empty actions. Forecast controls require actual
literal input ports. The older replay economy's initial configuration cannot change
mid-episode because that would rewrite history. The new `coupled_economy` process instead
feeds back `economy_state` and accepts a separate `economy_policy` object each step.
Its firms and households transact through one commercial-bank ledger, with accounting
checks on production, credit and settlement. It remains an illustrative closed economy;
matching accounts does not validate behavior or policy.

`SpatialStore` separately persists bounded field/topology selections and atomic lifecycle
changes in SQLite. Its Python API can reopen the same database; the `spatial` CLI builds
an immutable scenario report from explicit inputs. Cartesian/geodetic coordinate conventions
are required. General distributed PDE evolution and automatic lifecycle mutation across
arbitrary temporal models remain outside this interface.

## RL and perception adapters

The core runtime remains standard-library Python:

- `worldmodel.rl.numerical_space_spec(env, observation_bounds)` declares finite scalar
  spaces from explicit observation bounds and continuous scalar action ports.
- `VectorEnvironment(factories)` executes independent environments synchronously with
  explicit seeds. It is sequential, not a parallel worker pool; failed or ended batches
  require reset.
- `train_tabular(...)` takes explicit finite action dictionaries and an observation
  encoder. It bounds transitions and table size, separates training/evaluation seeds,
  and compares a frozen policy with a fixed-action baseline.
- `ObservationWrapper(env, allowlist, masked=..., delay=..., noise_std=..., memory_size=...)`
  in `worldmodel.perception` applies masks, reporting delays and seeded numeric noise.
  Its bounded belief memory contains delivered observations. This is an interface
  boundary, not a sandbox or a model of an actor's actual knowledge.

Install the optional adapter dependencies with `python3 -m pip install '.[rl]'` from
a checkout, or install a built wheel with its `rl` extra. Then
`worldmodel.rl.gymnasium_adapter(env, observation_bounds)` provides scalar Dict/Box
spaces. Arbitrary object/array observations and discrete action choices require another
explicit adapter. `python3 -m worldmodel rl-benchmark` runs a fictional tabular example;
its reward is a toy correctness objective, not economic or geopolitical validation.

## Runnable examples

These use existing local evidence. Their cash, revenues and controls are invented
scenario inputs attached to public identifiers, not reported financial states.

```sh
python3 -m worldmodel environment reference_evidence --request examples/environment-cashflow.json
python3 -m worldmodel environment reference_evidence --request examples/environment-subject-view.json --dataset subject_environment_episode

python3 -m worldmodel surface environment_episode --spec examples/surface-episode.json --output docs/generated/environment.html
python3 -m worldmodel surface reference_evidence --spec examples/surface-world.json --dataset world_surface --output docs/generated/world.html
```

The first episode selects two entities; the second materializes one subject using the
same machinery. Episodes retain the request, action history, selected observations,
rewards and exact materialization artifacts. Each prefix retains its graph/code/input
lineage. Rewards are explicit normalized changes, not a claim that these are good real-world
policies. These commands describe runnable examples; they do not assert that acquired
inputs or generated exports exist in a fresh installation.

## Visual surfaces

`render_surface(data, spec)` composes up to 16 panels into standalone HTML:

- `value`: a selected scalar or object, with units and state origin.
- `table`: selected snapshots and expandable evidence.
- `plot`: one numerical series over time.
- `map`: paired, explicit latitude/longitude observations in degree units.
- `graph`: abstract topology, optionally expanded from `seeds` such as `mic:XNAS`.

Values/tables/plots select `entity`, `variable`, and optional `path`. Maps select
coordinate variable names and optionally an entity. Graph seed selection follows
relations in both directions within the node limit. Compatible descriptions and
matching coordinate measurements retain their multiple sources. Conflicting
coordinates or incompatible entity types fail rather than silently reconcile.

Surfaces show omitted-row/node counts and preserve source/origin details. All external
text is escaped. No remote scripts, map tiles or dependencies are required. Maps are
coordinate plots with explicit extents, not reconstructed terrain or political
boundaries. Static HTML remains the default. Set `"interactive": true` in the surface
specification to add linked entity selection/search, time cutoff/playback, source
inspection, panel visibility/reordering and export of the edited specification.
Clicking a row, point, marker or node links the selected entity across panels. All
controls execute locally; embedded JSON escapes script boundaries and user values
are rendered with DOM text operations.

Playback retains rows through the cutoff; plots show the bounded cumulative series,
values use the last selected row in input order, and maps use each entity's latest
available paired coordinates. Graphs display the supplied static topology and do not
infer historical relationships. Panel limits and omitted counts still apply. Exported
specifications retain panel ordering and `visible` flags; this is layout editing, not
source editing or a general canvas diagram editor.

The HTML is stored in a verified report artifact and exported to the requested path.
The exported file is a convenience copy; the immutable artifact is the authoritative
version. Open [the episode surface](generated/environment.html) or
[the sampled world surface](generated/world.html) locally in a browser after generating them. Installed wheels expose the example
directory through `wm resources`; use those paths when working outside a checkout.
