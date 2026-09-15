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
communications outages, elections, migration, food systems, climate hazards and
institutional enforcement. Each needs actual evidence and its own model tests.

## Evidence actually acquired

The joined `strategic_evidence` artifact contains 7,166 records: 1,553 canonical
entities, 2,183 relations and 3,361 observations. It includes the earlier 11 sampled
sources plus 11 newly successful sources. Samples are deliberately incomplete.

| Added source | Retained scope |
|---|---|
| FRED policy rate,2y/10y Treasury yields, 10y breakeven, WTI oil | Separate dated Q1 2025 windows, 63–90 rows each, current acquisition vintage |
| FRED CPI | 90 monthly source rows in the configured historical window |
| Treasury debt | 61 dated fiscal observations |
| FDIC bank financials | 100 bank reports; explicit conversion from thousands of USD |
| OSM topology | 100 ways with node IDs and geometry; routing adapter exposes 347 nodes and 503 directed edges |
| OurAirports | 78 California medium/large airport nodes; full CSV temporarily downloaded and discarded |
| FEC candidates |Published historical names for the two existing candidate IDs; no travel or current-office inference |

Each config records official documentation, acquisition criteria and limits.
Source samples retain at most 100 rows / 1 MiB; whole-file downloads share the 64 MiB
temporary buffer. Normalized and computed artifacts are additional persistent
storage, not limited to the raw excerpt size.

SEC assets require `SEC_USER_AGENT` and remain unacquired. Marine AIS remains
unacquired: the [AccessAIS ordering service](https://marinecadastre.gov/accessais/)
is unavailable and no bounded bulk/spatial export has been verified. Earlier
BEA input-output, freight and USDA blockers remain. Airports do not imply flights;
road topology does not imply congestion estimates; financial identity records do
not imply resolved ownership or exposure networks.

## Strategic workflows

| Use | Supported operation | Qualification |
|---|---|---|
| Travel/logistics alternatives | Earliest arrival or least cost across supplied road, walking, air, sea, rail and transfer edges | Flight/ferry examples are fictional; costs, capacities and schedules supplied explicitly |
| Disruption planning | Close edges, restrict modes, change demand/capacity and reroute | Static capacity check, not reservations or congestion simulation |
| Liquidity and energy purchasing | Compare stockpiling against financing costs and energy-price stress | Explicit expectations; firms cannot read future shock schedules |
| Banking stress | Examine deposit creation, reserve settlement failures and capital losses | Accounting mechanics, no calibrated bank run or central-bank rescue model |
| Policy comparison | Finite candidates × scenarios; expected/worst-case/minimax-regret ranking and hard constraints | Candidate controls cannot alter the environment, horizon or initial endowment |
| Forecast validation | Chronological AR(1) fitting and rolling one-step holdout against persistence | Current vintages; descriptive fit, not a causal or real-time backtest |
| Territorial/resource interaction | Conservative fields plus overlapping territorial claims | Synthetic support and coefficients in example |
| Actor heterogeneity | Assign distinct numerical implementations to different entities | Model selection is explicit; no claim of empirical superiority |
| Organizational change | Query active/ended entities and conserved merger successors at different times | Requires declared lifecycle events |

The example inventory comparison favors the anticipatory candidate under its
specified stress probabilities and assumptions. That is a conditional result,
not a general recommendation to stockpile. The real WTI holdout is more sobering:
AR(1) MAE 0.8156 versus persistence 0.7760 USD/barrel across 30 holdout observations.
The fitted model **does not beat persistence** and is flagged accordingly.

## Commands

```sh
# Inspect acquisition status; no network unless explicitly requested.
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

Keep the existing sample mode as development/evaluation fixtures. Full coverage
needs partitioned geometry/time-series storage, topology-aware region loading,
source-specific bulk parsers and licensing/access configuration. The immediate
modeling priorities are coupling bank settlements to business decisions, acquiring
production input-output dependencies, estimating behavioral responses, adding
held-out regime tests, and modeling uncertainty before widening optimization.
