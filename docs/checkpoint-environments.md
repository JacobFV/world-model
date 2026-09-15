# Incremental temporal environments

`worldmodel.environments.CheckpointEvaluator` implements the same callable
`evaluate(history, seed)` protocol as `TemporalEvaluator`. Pass it to `Environment`
with the same actions, observation selectors, reward, and horizon specification.
Its constructor takes `(store, graph_ref, request, step_seconds, registry=None,
max_total_calls=10000, agent_backend=None)`.

An empty history explicitly resets a session. Each subsequent call must append
exactly one item. State, independent per-binding RNG streams, agent memory, held
pressures, literal inputs, and next-due times persist. Process handlers execute
only when due; earlier prefixes are never executed again. Observations may fall
between process updates, including discrete end-of-step updates. Literal input
changes must still align with the affected binding's cadence. The full request
horizon pins implementation selection and the last prediction's duration.

```python
import json
from worldmodel.environments import CheckpointEvaluator

evaluator = CheckpointEvaluator(store, graph_ref, request, step_seconds=1)
evaluator([], seed=7)
evaluator([{'inputs': []}], seed=7)
checkpoint = json.loads(json.dumps(evaluator.checkpoint()))

resumed = CheckpointEvaluator(store, graph_ref, request, step_seconds=1)
resumed.restore(checkpoint)
result = resumed([{'inputs': []}, {'inputs': []}], seed=7)
artifact_ref = resumed.publish()
```

Checkpoint envelopes bind graph, request, registry implementation descriptors,
source file hashes, backend identity, and configured call budget. JSON RNG state
uses `random.Random.setstate`; no pickle or executable deserialization is used.
Integrity checks detect corruption, not intentional forgery: checkpoints must
come from a trusted source. Restore verifies the graph and unchanged runtime
source. `.publish()` writes a normal provenance-bearing materialized artifact,
including the input graph's dataset rights. In-memory evaluation does not publish
an artifact on every step.

Numerical failures roll back state, RNG, memory, inputs, and scheduler changes.
Environment output/reward validation participates in that transaction. Handler
attempts consume the current session's work budget even if their state rolls
back. Deterministic/stochastic checkpoint restoration restores committed work;
it does not provide a global quota service across independent session instances.
The full-horizon plan, group output counts, per-transition call/cost counts, and
registered work estimators are checked before handlers execute.

Live backends must be explicitly injected with an identity and `predict` method.
Each live handler attempt records binding, time, input digest, status, and returned
prediction digest; committed traces retain inputs, outputs, and memory. If a
transition fails after a live attempt, further steps are blocked until an explicit
reset. Read `.audit` before deciding how to handle external effects. External
calls cannot be rolled back, and live-backend checkpoint restore is rejected
because this runtime cannot guarantee exactly-once behavior across processes.
An explicit reset begins a new episode and may repeat external work.

This removes repeated process execution, not all history-copy costs: snapshots,
traces, and transaction state are copied in memory and remain subject to configured
horizon/output budgets. Predictions remain illustrative and uncalibrated. Existing
`materialize` and `TemporalEvaluator` remain the replay reference; materialize's
short final discrete update now commits at the actual horizon.
