# Environments and materialization surfaces

An environment wraps a materialization's **inputs, outputs and state-derived reward**.
It has no special actor type. A single subject, several entities, an aggregate group,
or a field-backed temporal view can use the same interface. Selecting one subject is
an ordinary materialization choice; output selection and permitted controls remain
explicit.

## Runtime interface

```python
from worldmodel.environments import Environment, TemporalEvaluator

evaluate = TemporalEvaluator(store, graph_ref, materialization_request,
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

The reset/step tuple interface supports a reinforcement-learning loop. A Gymnasium
wrapper, vectorized environments and an RL training algorithm are **not bundled**.
No training result or validated strategic policy is claimed by the example episode.

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

The adapter pins graph evidence, process implementations, cadence and seed, then
replays the entire input history for each episode prefix. This retains the process
state, seeded randomness and scheduling that would be lost by restarting unrelated
one-step forecasts. Environment steps must align with every active process cadence.
Actions apply before process calls at their scheduled boundary. Results expose
`input_interventions` and the normal process trace.

Replay is bounded but costs more than a resumable simulator: a six-step example with
two hourly processes uses 42 process calls across prefixes. It is suitable for small
experiments and API integration, not workstation-scale RL training yet. Live agent
backends are rejected by this adapter because replay would repeat their calls and
side effects. Checkpointed execution is the next performance extension.

Read-only observed materializations can use empty actions. Forecast controls require
actual input ports; wrapping a view cannot invent geopolitical or economic mechanisms.
Changes to the existing economy replay kernel's entire initial configuration are
rejected because they would rewrite its previous economic history. That kernel needs
an explicit dynamic policy input before it can serve as a closed-loop economic policy
environment. Changed field/work inputs receive conservative execution preflight.

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
lineage. The final cashflow example ends at $1,468 for each entity; rewards are the
explicit normalized changes, not a claim that these are good real-world policies.

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
boundaries. The initial surfaces are static; interactive brushing, layer controls,
animated playback and a diagram editor remain future work.

The HTML is stored in a verified report artifact and exported to the requested path.
The exported file is a convenience copy; the immutable artifact is the authoritative
version. Open [the episode surface](generated/environment.html) or
[the sampled world surface](generated/world.html) locally in a browser.
