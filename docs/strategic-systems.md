# Strategic world-model systems

## Foundation

World state can now live as fields on explicit supports and topologies. A support
can contain measured spatial cells or network nodes; adjacency describes permitted
interaction. `FieldWorld` stores extensive quantities (amount per cell) and
intensive quantities (amount per measure). Typed graphs are generated lazily for
selected cells, fields, connections and claims. Existing source evidence can
continue to be graph-shaped: neither representation requires forcing everything
into the other.

This adopts the field/topology/process separation described in
[IBM-1](https://jacobfv.github.io/IBM-1/). It does not implement learned universal
fields or claim complete world coverage.

The field kernel supports nonnegative scalar diffusion and directed transport,
with stable bounded substeps and conservation checks. Measures, coordinate units,
conductances and rates are explicit inputs. Both amount and integrated density
remain 100 in the coastal example. Territory claims are independent overlays:
multiple claimants can cover the same cell without adjudicating sovereignty.
Water/land tags, geographic boundaries, watersheds and water bodies are represented
separately from political claims. The projection counts its requested records
before yielding, preventing silent truncation or dangling endpoints.

Current limits: finite in-memory supports, nonnegative scalar fields, no adaptive
mesh refinement, signed/vector PDE fields, terrain reconstruction, geodetic
solver or automatic polygon overlays. Lazy graph generation does not mean the
underlying field storage is already distributed or lazy on disk.

## Numerical and agent kernels

The default registry now exposes 12 process contracts and 34 implementations.
Bindings can select `implementation_id` explicitly, independently of fidelity.

| Actor/system | Implementations and behavior |
|---|---|
| Humans | Expected utility, prospect-style loss aversion, seeded logit, explicit reward learning, aging, stochastic mortality, injected agent backend |
| Businesses | Cost-plus production or demand-sensitive price search, with cash, inputs and capacity constraints; injected agent backend |
| Governments | Bounded monetary reaction or fiscal allocation; injected agent backend |
| Bank–energy–business economy | Anticipatory inventory purchases, exogenous energy/rate shocks, cash-funded credit, production, stockouts/defaults, balanced journals |
| Commercial-bank ledger | Loan/deposit creation, repayment destruction, interbank reserve settlement and defaults against equity, using integer cents |
| Fields | Conservative diffusion and directed transport on declared topology |
| Original processes | Population growth, resource inventory, cash flow, velocity-based movement, simple human/business action selection |

All behavioral models are illustrative. A fitted descriptive time-series model is
labeled separately from causal calibration. Agent implementations require a
Python-injected backend with recorded identity and exact request/response audit;
no hosted LLM credentials or endpoint are bundled.

Daily object-state predictions become visible at the end of their step, even
when output snapshots or other processes run more frequently. Economic replay
and field evolution expose work estimators checked before calls; numerical
learning stays in explicit state rather than competing for agent memory.

The commercial-bank ledger and behavioral economy are separate mechanisms. The
latter remains a cash-funded lender; it does **not** secretly use commercial-bank
deposit creation. Linking their transaction/credit decisions into one endogenous
banking economy remains work. Likewise isolated business kernels do not settle
counterparty balances; use the accounting kernels when global conservation matters.

## Limits and scale: exposure, routing, lifecycle and contracts

Every size or work cap in these kernels is a named field of
`worldmodel.limits.Limits`. Raise one per call (`limits={"exposure_max_obligations": 50_000_000}`),
for a process (`WORLD_MODEL_LIMITS='{"transport_max_edges": 80000000}'`, or
`@limits.json`), or for a CLI command with `--limits`. A lowered limit still
rejects. Oversized requests raise `LimitExceeded` (a `ValueError`) whose message
names the limit and all three ways to raise it. Request-level defaults did not
change: route `max_expansions` 10,000 and `max_labels` 100,000,
`CouplingLedger(max_transfers=10000)`, and surface panels at 100 rows and 500
edge references. Only the ceilings became limits.

| Old hard cap | Limit (default) |
|---|---|
| exposure: 1..1,000 entities, 10,000 obligations | `exposure_max_entities` (2,000,000), `exposure_max_obligations` (20,000,000) |
| exposure: 10,000 clearing iterations, 2,000,000 work per scenario | `exposure_max_clearing_iterations` (100,000), `exposure_max_clearing_work` (2e10, summed over baseline and stress) |
| route: 100,000 nodes, 200,000 edges | `transport_max_nodes` (20,000,000), `transport_max_edges` (50,000,000) |
| route: max_expansions/max_labels ≤ 1,000,000 | `transport_max_search_labels` (1e9) |
| route: 10,000 transfers, 1,000 windows/edge, 10,000 departures/edge | `transport_max_transfers` (1e7), `transport_max_windows` (1e6), `transport_max_departures` (1e7) |
| lifecycle: 10,000 entities, 100,000 events | `lifecycle_max_entities` (1e7), `lifecycle_max_events` (1e8) |
| aggregate_quantity 10,000 rows; select_timed_input 10,000 observations | `contracts_max_rows` (2e8), `contracts_max_observations` (1e8) |
| CouplingLedger: max_transfers ≤ 100,000; 100 quantities; 1,000 accounts; 1,000 interfaces; 1,000 per batch; 1 MB payloads | `contracts_max_transfers` (1e9), `contracts_max_interfaces` (1e6, quantities and interfaces), `contracts_max_accounts` (5e7), `contracts_max_batch_transfers` (1e8), `contracts_max_payload_bytes` (1 GiB) |
| agent contract/request/output 1 MB | `agent_max_payload_bytes` (64 MiB) |
| surfaces: 100,000 rows, 20,000 chars/field, 8 MB HTML, panel ≤500 rows, graph ≤200 nodes, 500 edge references | `surface_max_rows` (5e7), `surface_max_field_chars` (1e7), `surface_max_html_bytes` (1 GiB), `surface_max_panel_rows` (1e6), `surface_max_graph_nodes` (100,000), `surface_max_graph_edges` (1e6; the per-panel default `edge_limit` stays 500) |
| structured spaces: 1,000 choices, length 1,000, 100 properties, 4,096 channels, 1 MiB schema | `spaces_max_choices` (1e7), `spaces_max_length` (1e8), `spaces_max_properties` (1e6), `spaces_max_channels` (1e9), `spaces_max_schema_bytes` (1 GiB); depth 12 stays a structural rule |
| RL: 10,000 seeds, max_steps 1,000, 1e6 transitions, 100 actions/64 KiB, 4 KiB observation, 10,000 Q states, 100 vector envs | `rl_max_seeds`, `environment_max_steps`, `rl_max_transitions`, `rl_max_actions` (count and total KiB), `rl_max_observation_bytes`, `rl_max_states`, `rl_max_vector_environments` |
| rollouts: 8 workers, 100 episodes, 1,000 actions, 1e5 transitions, 1 MiB result, 60 s timeout, 1 MiB request, 32 MiB results | `rollout_max_workers` (256), `rollout_max_episodes`, `environment_max_steps`, `rollout_max_transitions`, `rollout_max_result_bytes`, `rollout_max_timeout_seconds`, `rollout_max_request_bytes`, `rollout_max_total_result_bytes` |

Domain rules are unchanged: the exposure horizon is at most ten years, there are
32 vector components, and at most 100 conserved declarations.

**Exposure clearing.** Clearing runs on index arrays.
`stress_exposures(config, backend=..., detail=...)` keeps the old result structure
and adds an `execution` block. `backend='python'` is the reference.
`backend='numpy'` (or `'auto'`, the default when `WORLD_MODEL_BACKEND` is unset,
which uses numpy for ≥50k elements if installed) is bit-identical: operations run
in the same order, incoming payments accumulate with `np.bincount` in obligation
order, and totals use Python's own (compensated) `sum`. Tests compare both
backends with a verbatim copy of the previous dict implementation on random
networks with cycles and shortfalls. `detail='summary'` returns totals, iteration
counts, shortfall counts and the `top` largest entity shortfalls instead of one
dict per entity and obligation. `stress_exposure_arrays(...)` accepts
array-native networks (cash, borrower/lender indices, principal, rate, floating
mask, maturity in days) and can `return_arrays`.

**Routing.** `compile_network(network)` validates a network once. It parses
departures, closure/capacity windows and validity, indexes dated transfer rules
by (node, from_mode, to_mode), and builds per-source adjacency lists.
`route(compiled, request)` reuses that compiled form. `route(dict, request)`
compiles on the fly. Both return results identical to the previous
implementation (checked on random graphs with transfers, windows, departures and
closures). Search no longer deep-copies edges or re-parses timestamps in the
inner loop, scans every transfer rule, or preallocates nodes × modes frontiers.
Each (node, mode) Pareto frontier is a time/cost staircase with bisection
dominance checks, and legs are built only for the returned path.

**Contracts and lifecycle.** `CouplingLedger.apply` copies only touched accounts.
It keeps exact running totals: integers, or `Fraction`s for real quantities, so
reported totals still equal `math.fsum` over all balances. It also keeps a running
SHA-256 of the canonical receipt list, so a batch costs O(batch) instead of
O(accounts + all receipts), with unchanged atomicity. Rollout workers track
serialized frame size incrementally. Lifecycle reconstruction was already linear.

Measured on the GB10 (20-core aarch64, Python 3.12, numpy 2.5.3). These are
fit-check runs; `docs/scale-benchmarks.md` has the final sequential table:

| Benchmark | Size | Backend | Kernel s | Peak RSS | Throughput |
|---|---|---|---:|---:|---|
| exposure (`bench_exposure.py`) | 10k entities / 100k obligations | python / numpy | 0.22 / 0.03 | 0.09 / 0.05 GiB | 12M / 98M clearing updates/s |
| exposure | 100k / 1M | python / numpy | 3.5 / 0.30 | 0.57 / 0.19 GiB | 9.4M / 112M updates/s |
| exposure | 1M / 10M | python / numpy | 126 / 4.0 | 5.4 / 1.6 GiB | 2.8M / 89M updates/s |
| routing (`bench_transport.py`) | 25.6k nodes / 102k edges, 4 queries | python | 4.0 (compile 0.3) | 0.16 GiB | 210k label expansions/s |
| routing | 250k nodes / 1.0M edges, 2 queries | python | 27 (compile 3.5) | 1.4 GiB | 154k expansions/s |
| routing | 1.25M nodes / 5.0M edges, 2 queries (script default now 1) | python | 302 (setup 24) | 7.3 GiB | 93k expansions/s |

Routing memory is dominated by the input edge dicts plus compiled records (about
1.5 kB per edge including the source dict), so a 10M-edge graph needs about
15 GiB. Array-native graph input and a vectorized label search remain future work.

## Lifecycle

`materialize_lifecycle(config, at, known_at)` reconstructs births, incorporation,
growth, mergers, death and dissolution from append-only event claims. It retains
ended predecessors, successors and source events. Mergers conserve an explicitly
declared additive size when requested. Conflicting simultaneous transitions fail
until reconciled. Absence of a death record does not establish survival.

Actor kernels freeze inactive states and reject agent attempts to change their
lifecycle status. Aging/mortality kernels can emit explicitly synthetic death
events. Those events are audited; they do not automatically become observed facts.
The materializer accepts a `lifecycle` config and refuses a requested evolution
interval that crosses a known actor end. Split such intervals and use lifecycle
projection to inspect changed membership. Automatic birth/merger topology mutation
inside arbitrary coupled process runs is not yet implemented.

## Expanded vocabulary

The schema now includes conflict/war, military units and alliances; academic
institutions, researchers, projects, papers and citations; exchanges, ticker
listings, bonds, equities, derivatives and positions; posts, platforms and
publishers; statutes, regulations, cases, courts and rulings; territories,
water bodies, rivers, oceans, watersheds, land cover, boundaries and disputed
claims. These are typed declarations. There are no newly acquired legal,
academic, social-post, conflict-event or exchange-tick corpora in this build.

Related strategic domains to add next include public health, insurance, housing,
communications outages and institutional enforcement. Each needs actual evidence and its
own model tests. Elections, migration, trade, sanctions, conflict and markets have
estimable model families (next section); `wm estimation-load` reports that five of the
eleven families can now load real data, five are blocked on a missing input or an unbuilt
panel, and `sanctions` is non-estimable by declaration.

## Political, geopolitical and market model families

`worldmodel.models` registers eleven families: legislative spatial voting and passage,
elections, lobbying/campaign-finance influence, structural gravity and GE trade shocks,
sanctions ownership exposure, conflict Hawkes hazards, factor/GARCH assets, an
agent-based order-flow market, commodity balances, Taylor-rule/Nelson–Siegel rates and
regional employment/migration. Each declares parameters with units and the public
series needed to estimate them. Each implements `fit(data, cutoff) -> {estimate,
diagnostics, evidence}` plus deterministic and stochastic simulation. Accounting
identities are checked, and every family is still `validated: false` — the estimation
layer has now been run against real data and none of the eleven has met its declared
acceptance criteria ([calibration-status.md](calibration-status.md)). They
register as `<family>_model` processes, plus `taylor_rule_policy_rate`, conserved
`two_region_migration` and `conflict_event_intensity`. Registration goes through
`models.register_model_processes(registry)` or `models.registry_with_models()`;
`default_registry()` does not include them yet.

A strategic multi-actor layer (`worldmodel.models.games`) lets actors with goals,
budgets and private signals choose declared actions in these environments. It solves
finite games by pure Nash enumeration, best-response dynamics, fictitious play or
two-player support enumeration. It compares a focal actor's candidates against
best responses using the same `strategy.rank_outcomes` ranking as policy search.
See [political, geopolitical and market models](political-market-geopolitical-models.md).

```sh
python3 -m worldmodel models list
python3 -m worldmodel models simulate --request examples/models-sanctions.json
python3 -m worldmodel models game --request examples/models-trade-war-game.json
```

## Evidence actually acquired

The bounded-sample table that used to sit here (7,166 `strategic_evidence` records from
22 sampled sources, 100 rows apiece) described the pre-acquisition state and has been
removed. Those sources are now fully acquired. Run `wm catalog` for status and record
counts; the current totals are 107 published normalized datasets, about 1.31 billion
records, built from 87.7 GiB of raw data inside a 100 GiB fair-share budget.

The sources this section previously listed as blocked are acquired: SEC company facts
(`sec_company_assets`, 16.6M records, `SEC_USER_AGENT` supplied), MarineCadastre AIS
(`marine_ais`, 7.4M), BEA input-output (1.5M), freight FAF5 (31.1M) and USDA NASS (7.5M).
What is still blocked is listed in [remaining-concerns.md](remaining-concerns.md) and is
account approval, an interactive download, or an authorized file import — not a code gap.

The qualifications have not changed, and scale does not soften them: airports do not
imply flights; road topology does not imply congestion estimates; financial identity
records do not imply resolved ownership or exposure networks; and 89 million bilateral
trade rows do not imply a trade model. Source samples (the `sample` path) still retain at
most 100 rows / 1 MiB and are for schema exploration only.

## Strategic workflows

| Use | Supported operation | Qualification |
|---|---|---|
| Travel/logistics alternatives | Earliest arrival or least cost across supplied road, walking, air, sea, rail and transfer edges | Flight/ferry examples are fictional; costs, capacities and schedules supplied explicitly |
| Disruption planning | Close edges, restrict modes, change demand/capacity and reroute | Static capacity check, not reservations or congestion simulation |
| Liquidity and energy purchasing | Compare stockpiling against financing costs and energy-price stress | Explicit expectations; firms cannot read future shock schedules |
| Banking stress | Examine deposit creation, reserve settlement failures and capital losses | Accounting mechanics, no calibrated bank run or central-bank rescue model |
| Policy comparison | Finite candidates × scenarios; expected/worst-case/minimax-regret ranking and hard constraints | Candidate controls cannot alter the environment, horizon or initial endowment |
| Forecast validation | Chronological AR(1) fitting and rolling one-step holdout against persistence. Superseded for real data by [the estimation layer](estimation-and-validation.md), which adds pre-registration, vintage policy, leakage audits, proper scoring rules and DM tests | Descriptive fit, not a causal claim. On real data 22 of 24 attempts fail their declared criteria |
| Territorial/resource interaction | Conservative fields plus overlapping territorial claims | Synthetic support and coefficients in example |
| Actor heterogeneity | Assign distinct numerical implementations to different entities | Model selection is explicit; no claim of empirical superiority |
| Organizational change | Query active/ended entities and conserved merger successors at different times | Requires declared lifecycle events |

The example inventory comparison favors the anticipatory candidate under its
specified stress probabilities and assumptions. That is a conditional result,
not a general recommendation to stockpile. The real WTI holdout is more sobering:
AR(1) MAE 0.8156 versus persistence 0.7760 USD/barrel across 30 holdout observations.
The fitted model **does not beat persistence** and is flagged accordingly.

## Commands

> **These commands operate on the bounded-sample pipeline, not on the acquired catalog.**
> `wm sources` reports sample status only, so it prints `not_acquired` for datasets that
> are fully acquired through `wm acquire` — use `wm catalog` for real status.
> `wm strategic-build` joins retained samples into a `strategic_evidence` dataset; in a
> tree where sample payloads have been pruned it fails with
> `No evidence inputs available; acquire bounded samples first`, and every command below
> that names `strategic_evidence` fails with it. Re-sample first, or read the kernels
> through the examples that do not depend on it.

```sh
# Inspect sample acquisition status; no network unless explicitly requested.
python3 -m worldmodel sources
python3 -m worldmodel sources --sample --allow-network
python3 -m worldmodel strategic-build

# Real sampled road topology; supplied speeds are assumptions.
python3 -m worldmodel route --sample osm_topology --request examples/sf-road-request.json
# Fictional scheduled multimodal journey.
python3 -m worldmodel route --network examples/multimodal-network.json --request examples/multimodal-request.json

# Bank/energy scenarios and finite policy search.
python3 -m worldmodel economy --request examples/economy-market.json --seed-graph strategic_evidence --as-of 2025-03-31 --known-at 2026-09-16
python3 -m worldmodel banking --request examples/banking.json
python3 -m worldmodel strategies --request examples/strategies.json
python3 -m worldmodel calibrate strategic_evidence --metric oil_price --train-end 2025-02-14

# Fields first; graph only on request.
python3 -m worldmodel fields --request examples/fields.json
python3 -m worldmodel field-view --request examples/fields.json --view examples/field-view.json --evolve

# Numerical actors and lifecycle projections.
python3 -m worldmodel actors --request examples/actors.json --actor human --implementation human_behavior.prospect --at 2026-01-01 --known-at 2026-09-16
python3 -m worldmodel lifecycle --request examples/lifecycle.json --at 2023-01-01 --known-at 2026-09-16

# Domain processes also work through the shared temporal materializer.
python3 -m worldmodel import strategic_scenarios examples/scenario-entities.jsonl
python3 -m worldmodel run strategic_scenarios
python3 -m worldmodel materialize strategic_scenarios --request examples/actors-view.json
python3 -m worldmodel materialize strategic_scenarios --request examples/economy-view.json
python3 -m worldmodel materialize strategic_scenarios --request examples/banking-view.json
python3 -m worldmodel materialize strategic_scenarios --request examples/fields-temporal-view.json

python3 -m worldmodel report strategies_scenario
python3 -m worldmodel verify strategic_evidence
python3 -m unittest discover -s tests -v
```

Reports and graph projections are ordinary immutable derived datasets. They pin
raw configurations, source graph versions, source-row evidence, parameters and
captured code. Transport plans never assert a person boarded a vehicle. Synthetic
field/actor outputs cannot silently seed observational state.

## Workstation expansion

Keep the sample mode as development/evaluation fixtures. Source-specific bulk parsers,
licensing/access configuration and the acquisition budget exist now
([full-acquisition.md](full-acquisition.md)); partitioned geometry/time-series storage and
topology-aware region loading do not, and execution remains single-process on one machine.

The modeling priorities have narrowed to one thing: **the estimated parameters mostly do
not validate, and most of the ones that might have no simulator hook to flow into.** Every
component's `hooks` entry in `worldmodel/estimation/requirements.json` names the change
its mechanism needs; none is implemented. Coupling bank settlements to business decisions
and modeling uncertainty remain open, but a better-coupled economy built on unvalidated
parameters is not a better model of anything.
