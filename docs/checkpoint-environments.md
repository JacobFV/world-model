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

## Limits and chunked checkpoints

Runtime bounds are named limits in `worldmodel/limits.py` (override per call,
with `use_limits`, `WORLD_MODEL_LIMITS` or CLI `--limits` on `environment`,
`surface`, `route`, `economy`, `banking` and `strategies`):

| Limit | Default | Replaces |
|---|---:|---|
| `materialize_max_points` / `materialize_max_calls` | 100,000,000 | 100,000 ceilings (request defaults stay 10,000) |
| `materialize_max_interventions` / `materialize_max_lifecycle_work` | 10,000,000 / 1e11 | 100,000 / 10,000,000 |
| `environment_max_ports`, `environment_max_steps` | 100,000 / 10,000,000 | 100 ports, 1..1,000 steps |
| `environment_max_output_bytes`, `environment_max_action_bytes` | 16 GiB / 64 MiB | 32 MiB / 64 KiB |
| `checkpoint_max_json_bytes`, `checkpoint_max_items` | 4 GiB / 1e9 values | 32 MiB / 2,000,000 |
| `journal_max_payload_bytes`, `journal_max_checkpoint_bytes`, `journal_max_storage_bytes`, `journal_max_records` | 1 GiB / 64 GiB / 4 TiB / 1e10 | ceilings equal to the persisted defaults |
| `journal_max_page_items` / `journal_max_page_bytes` | 1,000,000 / 16 GiB | 100 items / 32 MiB |
| `cli_max_input_bytes` | 4 GiB | 1 MiB local request files |

Execution-journal defaults (1 MiB payload, 32 MiB checkpoint, 64 MiB storage,
100,000 records) are unchanged and remain persisted and immutable per journal; only
the ceilings a journal may be created with are configurable.

`CheckpointEvaluator` transitions are copy-on-write: accumulated trace, snapshot and
history entries are shared with the committed state instead of deep-copied every
step, and `transaction()` keeps a reference rather than a deep copy. Environments
skip re-encoding the whole growing materialization for evaluators that declare
`bounded_output` (checkpoint evaluators validate each new prediction).

`evaluator.checkpoint(format='chunked', directory=path)` writes the same checkpoint
(identical identity and checksum) as streamed canonical JSON in zlib chunks with a
sha256 per chunk, the sha256 of the whole canonical document and the envelope
checksum in `manifest.json`. The directory is written and fsynced under a temporary
name and renamed into place, so a failed writer never replaces a previous
checkpoint. `evaluator.restore(path)` verifies every chunk and hash before parsing
and then applies the usual identity, checksum and trace validation; any mismatch
raises without changing evaluator state. `worldmodel.checkpoints` also provides
`write_checkpoint`/`read_checkpoint` for any JSON-native envelope and
`write_array_checkpoint(arrays, metadata, directory, *, overwrite=False)` /
`read_array_checkpoint(directory) -> (arrays, metadata)` for raw little-endian
int64/float64/bool/int32/uint8 buffers with per-array sha256 (numpy optional; flat
Python lists fall back to the `array` module). The numpy coupled economy uses the
array form (`ArrayEconomy.checkpoint`/`restore`), re-validating the restored state.
Measured throughput is in [scale-benchmarks.md](scale-benchmarks.md).
