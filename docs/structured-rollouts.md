# Structured spaces and bounded scenario experiments

These tools run synthetic numerical experiments. They do not establish empirical calibration, causal identification, or performance on live systems. The core uses the Python standard library; Gymnasium and NumPy are optional adapters.

## Explicit structured declarations

`StructuredSpace(schema)` in `worldmodel.structured_spaces` accepts `number`, `integer`, `boolean`, `categorical`, `object`, `array`, and `vector`. Numbers and integers require inclusive `minimum` and `maximum`; categories require explicit scalar `choices`; objects require exact `properties`; arrays require `length` and `items`; vectors require length and numeric bounds. Optional `unit` records units and `nullable: true` enables an explicit missing-value mask.

`validate`, `flatten`, and `unflatten` preserve types, dimensions, exact object keys, and category membership. Flattening sorts object keys, preserves array order, and includes a mask immediately before each nullable payload. A missing payload has mask zero and canonical zero placeholder channels. The mask distinguishes it from a real zero measurement. `declaration()` provides channel paths, bounds, units, stable normalized `schema_hash`, and channel count. Declarations are capped at 1 MiB, depth 12, and 4,096 expanded channels; the expanded size is checked before allocating a layout. Schemas are declarations, never inferred from observations.

`structured_gymnasium_adapter(env, action_schema, observation_schema)` provides an optional Gymnasium environment. Numeric leaves map to Box/Discrete, objects to Dict, and arrays/vectors to Tuple. Nullable values map to a Dict with `mask` and `value`; missing Gym payloads use valid declared defaults and the mask preserves absence. The adapter converts native JSON values, forwards `close`, and requires reset after a failed transition or report. Install the optional dependencies with `pip install gymnasium numpy`. Existing scalar adapters remain available.

## Independent bounded workers

`run_episodes(episodes, *, factory, mode='spawn', workers=2, max_transitions=100, timeout_seconds=5, max_result_bytes=1048576, journal=None, quota='rollouts', reservation_key=None)` lives in `worldmodel.parallel_rollouts`.

Each episode declares unique `id` and `seed`, JSON `config`, and a finite list of action objects. `factory` is a trusted importable `module:function` that reconstructs the environment from config inside a fresh spawned process. Put SQLite filenames and other reconstruction settings in config; do not pass connections, environment instances, or closures. Factories are trusted code, not a security or operating-system memory sandbox.

The runner caps workers at eight, episodes at 100, requested transitions at the caller's budget, the serialized request at 1 MiB, each result at 1 MiB, and aggregate result allowance at 32 MiB. Each child has a deadline of at most 60 seconds. A timed-out child is terminated and, if needed, killed and reaped. Every completed environment is closed before its result is returned. Result order is lexical episode ID. `mode='sequential'` uses the same isolated child protocol one episode at a time, giving a deterministic reference with the same timeout guarantees.

Successful independent episodes survive another episode's failure. Errors, oversized results, and timeouts are explicit statuses; unknown completed transition counts are `null`. There are no automatic retries. Declared/detected live external backends are rejected before reset. A trusted factory can itself perform arbitrary effects, so factories must honor this numerical-only contract.

With an `ExecutionJournal`, configure the named quota first and supply a unique `reservation_key`. The entire requested transition allowance is durably reserved before any child starts. Completed keys cannot be rerun, interruptions are marked uncertain, and timeouts consume their full reservation. Journal records retain a compact result digest/status summary; callers retain the full report separately. Omitting a journal gives only the local invocation budget.

Run `python3 examples/structured-rollouts.py`: two economy episodes use four transitions, plus four for the sequential oracle. The example uses a temporary journal, verifies identical reports, and prints a nullable structured declaration. The economy is deterministic; distinct seeds identify episodes rather than introducing hidden random shocks.

## Held-out scenario selection and multiple objectives

`benchmark_scenarios(**request)` in `worldmodel.scenario_benchmark` accepts the request in `examples/scenario-benchmark.json`. Scenarios explicitly declare `train`, `validation`, or `test` before any policy fitting. Their full configurations must have distinct digests. Configurations declare economy endowments, mechanism parameters, and daily shocks; policies contain only daily policy actions. All scenarios use the same horizon.

This benchmark performs a finite search over fixed policy schedules. Training ranks a shortlist; validation chooses the final policy; only then are final test trajectories constructed. Final-test comparisons never modify selection. Baselines are explicitly named and excluded from selection. This is scenario-level generalization across declared supply, labor, interest-rate, and liquidity changes; random seeds in one deterministic configuration would not provide that separation.

Named objectives declare final-state selector paths, units, and maximize/minimize direction. Reports include every scenario/policy result, accounting balance, normalized weighted means, directional worst cases, constraint violations, all-scenario feasibility, and the Pareto front over weighted objectives. Feasibility is ranked before the selected objective, with lexical policy IDs resolving ties. Pareto comparisons do not collapse objectives into an implicit scalar. Scenario weights and hard bounds are modeling assumptions.

Run `python3 examples/scenario-benchmark.py`. Its 30-transition comparison selects `cautious`; the final-test Pareto front is `baseline,cautious`. Their weighted inventory/debt/household cash are respectively `(0,0,50)` and `(1,3.5,48)`. This illustrates a tradeoff rather than universal policy superiority. The expansion policy has `(1,10,47.5)`.

## Synthetic non-identifiability diagnostic

The same script runs `parameter_ensemble(evaluate, parameter_sets, observations, *, probes, outputs, baseline_id, tolerance=0, max_evaluations=100)`. The callable accepts parameters and a scenario input, returning numeric outputs. All fitting observations must be explicitly `synthetic_scenario` and carry selector paths and units. The grid, baseline, tolerance, probe definitions, and evaluation cap are declared before evaluation. Returned parameter bounds describe the supplied grid, not confidence intervals or a posterior.

Candidates are scored by mean absolute fitting error. Candidates within tolerance of the best score remain in the ensemble. Withheld probes report each accepted candidate's predictions and their minimum/maximum. The example has two parameter vectors that both exactly reproduce a synthetic 10% total rate: policy/spread pairs `(4%,6%)` and `(8%,2%)`. A withheld 20% policy-rate override yields 26% and 22%. Five evaluations expose fitting ambiguity and response disagreement. Neither candidate is established as a real causal mechanism.

## Verification

`python3 -m unittest tests.test_structured_spaces tests.test_parallel_rollouts tests.test_scenario_benchmark` exercises strict declarations, missing masks, preflight expansion, actual spawned workers, serial equivalence, cleanup, timeout/oversize/error statuses, durable quota reservation, held-out selection, objective aggregation, Pareto comparisons, and synthetic ambiguity. The Gym checker test runs when Gymnasium is installed and skips otherwise.

Publish the scenario benchmark through the CLI with:

```sh
wm benchmark-scenarios --request examples/scenario-benchmark.json
```
