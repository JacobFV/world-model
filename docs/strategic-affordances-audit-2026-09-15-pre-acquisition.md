> **Historical record — superseded.** This is the audit as written before full-scale
> acquisition, the estimation layer and the model families existed. Its numbers (18
> dataset declarations, 11 sampled sources, 762 sample records, zero real graph edges,
> no estimation or calibration) describe a state the repository has left. It is kept
> unedited so the change is auditable. The current audit is
> [strategic-affordances-audit.md](strategic-affordances-audit.md); the current
> per-session record is [session-2026-09-15-summary.md](session-2026-09-15-summary.md).
>
> The older header of this file read: "Historical pre-kernel audit. The subsequent typed
> graph, process registry and materializer are described in [the current strategic systems
> guide](strategic-systems.md), including updated supported capabilities and remaining gaps."

# Strategic affordances audit (pre-acquisition)

Audited 2026-09-15 against the then-current Python implementation, dataset declarations,
retained sample reports, published dataset pointers, and SQLite graph contents.

## Finding

We currently support **auditable evidence acquisition, inspection, and custom
computations**. We do not yet support an integrated real-world strategic decision
workflow. The largest immediate gap is connecting real observations into a
consistent world state; having more hardware or more raw data will not fill it.

### Verified state

- 18 dataset declarations: 14 real source families and four offline/derived examples.
- 11 real source families sampled, with 762 records in their latest samples.
- BEA and USDA are blocked by missing keys; the freight download failed to connect.
- All 14 production source definitions have `entrypoint: null` and no final version.
- Published `world_graph` contains seven fictional records: two entity descriptions,
  two literal assertions, and three observations. It has zero entity-object edges.
- Graph traversal is implemented and exercised in tests, but real relationship
  networks are not present in the published graph.
- The happiness example demonstrates a user-written formula, not a validated
  outcome measure, learned world model, or policy optimization method.

This separates **working infrastructure**, **available sample evidence**, and
**implemented strategic analysis**. A place to store a law, a confidence value,
or a process type does not implement legal reasoning, probabilistic inference,
or process execution.

## Working affordances

| Affordance | What can actually be done | Boundary |
| --- | --- | --- |
| Acquire evidence economically | Fetch source-specific API subsets or bounded full files; retain complete small samples | Samples are narrow and nonrandom; production pagination/incremental ingestion is absent |
| Inspect evidence | Examine source fields, examples, types, missingness, identifiers and source quirks | Profiles are basic; no coverage estimator or general statistical-analysis UI |
| Audit claims and calculations | Trace exact input versions/records, code snapshots, parameters and output checksums | Sample originals were intentionally discarded; their hashes do not restore the original files |
| Represent multiple accounts of the world | Store entities, observations, literal/relationship assertions, and events; retain conflicting evidence | No identity resolver, truth-selection policy or current-belief materialization |
| Keep aggregates distinct from entities | Store population, employment and establishment totals as dimensional observations | No disaggregation, latent-state inference or aggregate constraint solver |
| Ask temporal evidence questions | Filter valid periods and source observation times; preserve different versions | No event-driven state transitions; observation-time filtering is not historical installation availability |
| Inspect local graph neighborhoods | Query bounded incident assertions up to six hops, with provenance | Requires normalized edges; no real edges loaded, weighted path analysis, centrality, flow or graph-query language |
| Build custom metrics | Write Python transforms over exact input datasets; publish outputs under the same contract | Each formula/join must be implemented; no general metric DSL, scoring interface or model registry |
| Vary calculation assumptions | Rerun a transform with different parameters while pinning input versions | Manual parameter variation is not a causal counterfactual, automatic sensitivity study, or optimization |
| Rebuild and relocate results | Versioned artifacts and a disposable SQLite projection work across data-root paths | Single-machine execution; no partition scheduler, distributed storage, incremental index or scale benchmark |

## Strategic questions and current coverage

“Sample evidence” below means a person or new analysis code can inspect those rows.
It does not mean the question is implemented as a graph query or supported as a
reliable recommendation.

| Strategic affordance | Evidence available now | What prevents a complete answer |
| --- | --- | --- |
| Regional demand / market entry | State population estimates; a small CBP industry sample | Income/consumption, market prices, customer segments, competitors, representative coverage and joined industry/geography definitions |
| Industry attractiveness | Aggregate establishments, payroll, employment; partial NAICS vocabulary | Sales/output, margins, concentration, growth, comparable denominators and classification-revision crosswalks |
| Facility / site selection | California county identifiers; population context; a small road sample | Parcel/building geometry, zoning, land costs, utilities, hazards, access, local labor and comparable locations |
| Hiring / workforce strategy | Twelve national unemployment-rate observations for 2024 | Local occupation supply, wages, skills, vacancies, mobility and firm demand |
| Counterparty discovery / identity | 25 LEI records and 100 award recipients | Identifier linkage, deduplication, operating-entity versus fund distinctions, commercial coverage and live status verification |
| Ownership / control exposure | Entity/assertion schema and GLEIF identity structures/links | Actual ownership relationships, beneficial owners, stakes, subsidiaries, security mappings and control semantics |
| Production capacity / feasibility | Schema can describe facilities, commodities and process types | Named facilities, equipment, capacities, bills of materials, yields, input requirements, costs and operating constraints |
| Supplier dependencies / substitution | Industry/resource vocabularies and mineral records | Supplier-buyer edges, BEA input-output data, commodity crosswalks, inventories, lead times and substitution rules |
| Logistics / route selection | 100 OSM highway ways with tags and centers | Node connectivity, full geometry, freight OD flows, travel times, transport costs, modes and capacities |
| Energy sourcing / reliability | One state-year retail-sales observation with units | Plant/generator assets, grid topology, prices, load profiles, capacity, fuel dependence and outages |
| Mineral resource strategy | 100 historical occurrences/past producers | Active operations, owners, reserves, grade economics, output, permits, transport access and extraction costs |
| Agriculture / food exposure | Declared source and bounded query | No sample yet; yields, acreage, livestock, water, weather, input dependencies and production regions |
| Public procurement opportunity | 100 high-value contract award summaries | Solicitations, eligibility, award transactions, buyer requirements, budgets, competitors, renewal timing and win models |
| Private customer / partner strategy | Some legal names and public-award recipients | Product portfolios, customer-supplier links, purchasing needs, channels, relationships and commercial transactions |
| Capital allocation / financing | Generic account/security/contract types | Statements, cash flows, prices, valuation, financing terms, risk exposures, investment alternatives and return models |
| Political / institutional exposure | 100 committee metadata records | Donation and lobbying edges, offices, districts, votes, policy positions, institutions' authority and operational effects |
| Legal / regulatory feasibility | Generic law/jurisdiction/contract types | Laws, effective dates, permits, taxes, enforcement, obligations and executable feasibility constraints |
| International / geopolitical exposure | Namespaced location/entity scheme | Trade flows, tariffs, sanctions, alliances, currency exposures and broad international coverage |
| Environmental / physical-risk exposure | Generic event/location scheme | Emissions, weather/climate, hazard probabilities, asset exposure, vulnerability and recovery models |
| Technology / innovation strategy | Generic technology/asset types | Patents, ownership/assignments, capabilities, research links, adoption, obsolescence and technology substitution |
| Infrastructure / housing investment | Generic assets and locations | Land/building inventories, ownership, condition, construction, housing demand, utilization and project economics |
| Macro consistency / calibration | Population, one labor series and one energy observation | National/regional accounts, BEA IO, trade/finance totals and accounting or conservation constraints |

The original broader source inventory included many of these missing areas.
Mentioning a source in the conversation or defining an entity type is not the
same as having a configured ingestion pipeline or usable evidence for it.

## Missing decision capabilities across every domain

### 1. Coherent real-world state

We need source-specific normalization, unit/currency conventions, geographic and
classification vintages, stable identifiers, entity resolution, and evidence-backed
cross-source relationships. Current samples are geographically and temporally
misaligned: Alabama business rows, California counties/energy, a San Francisco
road box, national labor, and selected national entities/awards.

We also lack an explicit way to reconcile competing claims, mark stale evidence,
propagate retractions, and distinguish unknown from false. A confidence scalar
can be stored, but no source reliability model or posterior distribution exists.

### 2. A decision contract

There is no structured representation of the decision-maker, what they control,
available actions, action cost, budget, constraints, objectives, time horizon,
state-dependent feasibility or success criteria. Arbitrary JSON could contain
these fields, but there are no validated decision interfaces or evaluators.

### 3. Dynamics and causality — intentionally deferred so far

There is no transition function, process scheduler, causal model, lag structure,
feedback mechanism, counterfactual intervention or calibration procedure. Events
are records; they do not update world state. A relationship graph alone does not
establish what changes when an actor intervenes.

Processes also need conservation/accounting rules: an inventory cannot be consumed
twice, output needs inputs/capacity, money flows must reconcile, and decision
sequences consume time/resources. No such constraints are enforced today.

### 4. Uncertainty and strategic interaction

No uncertainty distributions, correlations, belief updates, missing-state models,
Monte Carlo engine or forecast calibration. No agents with goals, observations,
private information, response policies, incentives, coalitions or negotiations.
We therefore cannot estimate an opponent's response or distinguish robust actions
from actions that depend on a fragile assumption.

### 5. Planning and optimization

No candidate-action generator, feasibility search, solver, objective evaluator,
Pareto frontier, risk/return comparison, regret minimization, rollout, sequential
policy or adaptive replanning. Dataset dependency planning is an execution order
for computations, not a plan of actions in the world.

Simple static comparisons could be built before simulation, but even those need
explicit alternatives, compatible evidence and a disclosed scoring/constraint rule.

### 6. Learning what to measure next

No value-of-information calculation, experiment design, coverage map, active
sampling policy or prioritization of acquisitions by decision impact. Current
sampling discovers schemas cheaply; it does not guarantee the sample contains
the evidence needed to choose between actions.

### 7. Evaluation and a decision feedback loop

No historical replay with strict information-availability controls, forecast
backtesting, calibration metrics, policy evaluation, decision ledger, measured
outcomes, change detection, alerting or automatic refresh. File integrity tests
verify software contracts, not predictive validity or usefulness of decisions.

### 8. Operational and user-facing support

There is a CLI, but no strategic workspace for stating a decision, inspecting
alternatives, explaining trade-offs or recording an approved action. There is
no action executor. Production data operations also lack scheduling, incremental
releases, semantic data-quality checks, large-scale performance validation and
storage lifecycle management.

## Recommended build order

1. **One connected real slice:** choose one geography, sector and compatible set
   of periods. Normalize a few existing sources, preserve their aggregates, and
   produce actual linked records. More sources are not the immediate bottleneck.
2. **One explicit static decision:** define candidate actions, objective, budget,
   hard constraints and evidence requirements. Produce an auditable comparison;
   make missing information visible rather than silently imputing it.
3. **Identity, units, reconciliation and coverage:** make joins defensible and
   establish which conclusions the available evidence supports.
4. **One process family with evaluation:** add only the transitions needed for the
   chosen decision, calibrate them, and evaluate against held-out observations.
5. **Uncertainty, interaction and optimization:** compare actions across plausible
   states/process parameters, then add response models and solver-driven search.

The first meaningful milestone is a reproducible answer to a narrowly specified
real decision, with evidence and limitations attached. The current system supplies
much of the evidence bookkeeping for that milestone; it does not yet supply the
world-state integration or decision model.

## Audit references

- [Dataset catalog](../data/README.md) and `data/*/dataset.json`.
- [Actual sample coverage and findings](sample-exploration-2026-09-15.md).
- [Record contracts](../worldmodel/model.py).
- [Graph projection and queries](../worldmodel/graph.py).
- [Transform implementations](../worldmodel/transforms.py).
- [Dataset execution](../worldmodel/pipeline.py).
- `data/world_graph/index.sqlite`, queried read-only for record/edge counts.
