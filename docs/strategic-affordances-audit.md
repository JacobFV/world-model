# Strategic affordances audit

Audited 2026-09-15 against the working tree: `wm catalog`, `wm budget`, the published
stage manifests under `data/*/manifests/`, `docs/calibration-status.md`, `wm models list`
and `wm estimation-load`. Every number below was re-derived from those sources, not
carried over from the previous audit.

> **Supersedes** [strategic-affordances-audit-2026-09-15-pre-acquisition.md](strategic-affordances-audit-2026-09-15-pre-acquisition.md),
> which is kept unedited. That audit reported 18 dataset declarations, 11 sampled source
> families holding 762 records in total, "all 14 production source definitions have
> `entrypoint: null`", a published `world_graph` of seven fictional records with zero
> entity-object edges, and no estimation, calibration, political or market models. All of
> those statements are now false. Most of the *capability* gaps it described — no decision
> contract, no causal identification, no value-of-information, no coverage estimator — are
> still open, and are restated below.

## Finding

The evidence gap it identified is largely closed; the **validation** gap is not, and it
is now the binding constraint.

We hold roughly 1.31 billion normalized records across 107 published datasets and can
acquire, version, verify, join, convert, resolve and query them with provenance intact.
We have an estimation and validation layer, and we have run it on that real data. The
result is that **one** of 22 declared processes meets its own pre-registered acceptance
criteria. Two of 24 attempt runs pass, one of them only on a substituted series that
contradicts its declared one. Eleven model families remain `validated: false` by their own
descriptors. More data will not fix this; the bottleneck moved from acquisition to
specification, identification and measurement.

The previous audit's core distinction still holds and should still be read literally:
a place to store a law, a confidence value or a process type does not implement legal
reasoning, probabilistic inference or process execution. Having 89 million bilateral
trade rows does not implement a trade model that forecasts anything.

### Verified state

| Quantity | Value | How it was derived |
| --- | --- | --- |
| Dataset declarations | 121 (116 source, 5 derived) | `wm catalog` |
| Declarations with a published normalized stage | 107 | `data/*/manifests/stages/normalized/latest.json` |
| Normalized records | 1,312,082,961 | sum of `outputs[*].rows` in each latest normalized manifest |
| Normalized output on disk | 36.9 GiB gzipped | sum of `outputs[*].bytes` |
| Raw acquired bytes | 94,196,568,082 (87.7 GiB) | `wm budget` → `totals.used` |
| Fair-share pool | 107,374,182,400 (100 GiB), max 5% per dataset | `wm budget` → `total`, `max_share` |
| Pre-registered estimation attempts | 24 | `docs/calibration-status.md` summary table |
| Attempt runs that pass every declared criterion | 2 (plus 1 superseded) | same |
| Registry processes validated | 1 of 22 (`monetary_model`) | `wm estimation-requirements`, `docs/calibration-status.md` |
| Published calibration artifacts | 100 (50 validation reports, 50 estimates) | `data/calibration_reports/artifacts/final/` |
| Estimation components loadable / blocked | 13 / 2 | `wm estimation-load` |
| Model families loadable / blocked / non-estimable | 5 / 5 / 1 | `wm estimation-load` |
| Model families declaring themselves validated | 0 of 11 | `wm models list` |
| Tests | 839 discovered | `unittest` discovery over `tests/` |
| Datasets whose rights metadata requires redistribution review | 75 of 107 | `rights.redistribution_review_required` in each manifest |

The 14 declarations with no published normalized stage, and why:

| Declaration | Status | Blocker |
| --- | --- | --- |
| `acled` | `awaiting_credentials` | account approval, not a technical gap |
| `global_fishing_watch` | `awaiting_credentials` | account approval |
| `wto_timeseries` | `awaiting_credentials` | optional API key not held |
| `bts_airline_t100` | `manual_download_required` | interactive form download |
| `epa_aqs_daily` | `full_acquisition_configured` | download in flight |
| `market_corporate_actions` | `full_acquisition_configured` | download in flight |
| `contract_candidates`, `reviewed_obligations`, `market_obligations` | `ready_for_authorized_file_import` | require a supplied, authorized file; nothing is inferred |
| `calibration_reports` | `published_by_estimation_layer` | published as `final`, not `normalized` |
| `demo_countries`, `demo_graph`, `rando_joes_happiness_index`, `world_graph` | `offline_example` | fictional fixtures, deliberately tiny |
| `strategic_scenarios` | `synthetic_example` | fictional scenario carriers |
| `world_evidence` | `sample_only` | superseded by the unified-graph rebuild in progress |

Two further declarations are published but incomplete by their own status:
`mit_election_returns` (`partial_manual_download_required`: statewide president and
senate only; House and county returns sit behind a guestbook-gated Dataverse download)
and `lda_lobbying` (`partial_acquisition_in_progress`).

## Working affordances

| Affordance | What can actually be done | Boundary |
| --- | --- | --- |
| Acquire evidence at scale | `wm acquire` downloads complete sources — paged APIs, URL lists and bulk files — with resume, `Retry-After` handling, per-host rate limits, credentials read from `.env`, and sharded immutable raw artifacts | A stopped run publishes `complete: false` with a stop reason; three sources are blocked on account approval and two on interactive download. Budget exhaustion is a real outcome, not an error |
| Budget the disk | One weighted max-min fair-share pool (`wm budget`), 5% ceiling per dataset, reconcilable against on-disk reality | 87.7 of 100 GiB is spent. The next large source displaces an existing one; there is no tiering or eviction policy |
| Normalize into one ontology | 107 datasets publish typed entities, observations, assertions and events with units, dimensions, valid time and observed time | Normalization is per-dataset adapter code. Cross-source semantic equivalence of a metric name is an assertion by the adapter author, not a checked property |
| Audit claims and calculations | Exact input versions, raw artifact references, record locators, code snapshots, parameters and output checksums; `wm verify DATASET/stage` re-reads payload bytes | Verification proves bytes and references, never that an adapter's mapping is scientifically right |
| Join across sources | Dated country/county/NAICS codes, total-conserving crosswalk apportionment, unit and currency conversion with dated rate/deflator series, deterministic identifier links and Fellegi-Sunter resolution with reviewable match assertions | Probabilistic match quality has only been measured on fictional data. Several crosswalk weight tables are acquisition declarations, not acquired files |
| Keep conflicting accounts | Multiple descriptions of one entity coexist; beliefs carry retraction, staleness and unknown-versus-false | Source reliability priors are policy inputs, not calibrated posteriors |
| Query the graph | Resolved neighborhoods, paths, degree centrality, PageRank and flow aggregation over an indexed projection | The unified graph is being rebuilt in this session; see [the unified graph guide](unified-graph.md) for its current contents and limits. Do not quote pre-rebuild edge counts |
| Estimate parameters from data | OLS/WLS/2SLS with robust SEs, AR/ARIMA-lite/VAR, error-correction pass-through, hazard/logit, growth, PPML gravity, Kalman local level, SMM/ABC over existing simulators, block bootstrap | Reduced-form. Conditional forecasts use realized drivers; holdout skill does not identify an intervention response |
| Validate a fit honestly | Pre-registered splits, knowledge cutoffs with vintage policy and leakage audits, rolling-origin backtests against naive baselines, proper scoring rules, Diebold-Mariano tests on a frozen holdout, immutable reports | 22 of 24 attempts fail. `calibration-status` re-derives the verdict rather than trusting the stored flag — and it should be used that way. All 50 validation reports re-verify; handed one of the 50 *estimate* artifacts it returns a misleading `does not match report_id` error |
| Simulate | Coupled bank/firm/household economy, fields and transport, exposure clearing, routing, lifecycle, 11 political/market/geopolitical families and a multi-actor game layer | Parameters are assumptions unless a calibration report says otherwise, and for ten of eleven families none does |
| Run at national synthetic scale | Named configurable limits (`worldmodel/limits.py`), optional numpy backend, measured wall time and peak RSS in [scale-benchmarks.md](scale-benchmarks.md) | Single process, single machine. No distributed or out-of-core execution, no GPU backend. Long-horizon figures are extrapolated from measured per-step throughput |

## Strategic questions and current coverage

"Evidence available" means normalized records exist locally that a person or new analysis
code can read. It does **not** mean the question is answered, that the records are joined
into one consistent state, or that any model over them has been validated.

| Strategic affordance | Evidence available now | What still prevents a defensible answer |
| --- | --- | --- |
| Regional demand / market entry | ACS 5-year tables (4.5M), ACS PUMS, Census PEP (2.4M), CBP/economic census (7.9M), BEA regional (13.7M), IRS SOI migration (2.7M), LODES (3.5M) | No local price or consumption panel; no firm-level demand; no validated demand model. Geography vintages must be crosswalked per year |
| Industry attractiveness | CBP/AIES, BEA input-output (1.5M), SEC financial statements (50.9M) and companyfacts (16.6M), classifications (219k) | Margins and concentration must be constructed; comparability across NAICS revisions is a crosswalk assumption; nothing validated |
| Facility / site selection | Census geography (901k), CBSA delineations, TIGER transport (966k), OSM regional topology (98.6M across four extracts), FEMA NRI (5.8M), Aqueduct (3.1M) | No parcels, zoning, building stock, utility interconnection queues or land prices |
| Hiring / workforce | BLS labor (18.2M), BLS prices (4.2M), LODES, ACS PUMS | No vacancy or skills data; occupation supply must be derived; labor-demand estimation fails its holdout |
| Counterparty discovery / identity | GLEIF level 1 (6.2M), Companies House UK (52.3M), SEC issuer reference (4.1M), Nasdaq listings, ISO MIC, OpenAlex institutions (192k) | Coverage is not global; resolution across these is implemented but unmeasured on labelled real data |
| Ownership / control exposure | GLEIF level 2 relationships (1.9M), SEC ownership data sets (13.3M), 13F history (78.9M), OpenSanctions graph (7.2M) | Beneficial ownership below reporting thresholds is absent; 13F is long-only US equity; control semantics are not adjudicated |
| Production capacity / feasibility | EIA plant-level records inside eia_energy (15.8M), EXIOBASE 3 (6.4M), BEA IO | No bills of materials, yields, equipment or operating constraints at facility level |
| Supplier dependencies / substitution | BEA IO, EXIOBASE 3, BACI HS2017 (89.2M) and HS1992 (269.9M), Comtrade (628k), Census international trade (4.8M), FAF5 freight (31.1M) | Firm-to-firm edges do not exist in any of these; industry-level coefficients are not a supply chain |
| Logistics / route selection | OSM four-region topology (98.6M), TIGER roads/rail (966k), FAF5 OD flows (31.1M), MarineCadastre AIS (7.4M), OurAirports (708k) | BTS T-100 is a manual download; Global Fishing Watch awaits credentials; travel times and costs remain supplied assumptions |
| Energy sourcing / reliability | EIA bulk (15.8M), EIA-930 grid operations (4.6M), Ember (855k) | No grid topology; outage causation and load-profile modelling are absent |
| Mineral resource strategy | USGS MRDS and related (2.1M) | Legacy inventory; active operations, reserves, grades and permits are not in it |
| Agriculture / food exposure | USDA NASS crops (7.5M), FAS PSD (919k), FAOSTAT (52.8M), Aqueduct | No field-level yields or input dependencies; water risk is a basin-level index |
| Public procurement opportunity | USAspending contracts (107.0M) and assistance (76.2M), Federal Register (809k) | Solicitations, evaluation criteria and win models are absent; transaction records are not an opportunity pipeline |
| Capital allocation / financing | SEC statements (50.9M), companyfacts (16.6M), market prices (11.5M), Alpaca daily bars (35.9M), FDIC call reports (8.1M), FRED/ALFRED panel (7.6M) | `assets_model` and `cash_balance` both fail their holdouts. Several price sources are licensed for internal use only |
| Political / institutional exposure | Congress members (314k), Voteview roll calls (515k), BILLSTATUS (2.5M), congress.gov (31k), FEC master and itemized contributions (~16.2M), LDA lobbying (2.1M, partial), ParlGov, V-Dem (951k) | No influence panel is built; `legislative_model` and `market_abm_model` are declared but unrun on compute grounds; House district returns are gated |
| Legal / regulatory feasibility | Federal Register (809k), HTS (63k), WITS TRAINS (1.8M), OFAC (228k), OpenSanctions (792k + 7.2M graph), other lists (355k) | Sanctions determination is declared non-estimable: it is a legal-rule question, not a fitted one. Permits, taxes and enforcement are absent |
| International / geopolitical exposure | BACI, CEPII gravity (1.5M), WDI (9.0M), IMF (3.4M), OECD (1.5M), BIS (4.5M), ECB/Eurostat (16.4M), UCDP GED (575k), GDELT (17.0M), COW/NMC reference (129k) | `conflict_model` fails on all three criteria including revision leakage; `trade_model` is blocked on consecutive-year bilateral coverage; WTO awaits a key |
| Environmental / physical-risk exposure | FEMA NRI (5.8M), NOAA storm events (5.7M) and nClimDiv (6.8M), GHCN daily (13.1M) and monthly (48.9M), IBTrACS (2.5M), USGS earthquakes (4.4M), OpenFEMA (2.8M) | EPA AQS is still downloading, so `field_diffusion_transport` cannot be estimated; hazard-to-asset vulnerability functions are absent |
| Technology / innovation strategy | Crossref DoD-funded works (282k), OpenAlex institutions (192k), NASA feed (601) | No patents, assignments or adoption data |
| Infrastructure / housing investment | ACS tables, Census geography, TIGER transport, OSM | No building or land inventory, condition, or project economics |
| Macro consistency / calibration | FRED/ALFRED panel (7.6M, full real-time vintages), BEA NIPA (13.7M), BEA IO, Treasury debt (1.3M), WDI, IMF | `monetary_model` was the first validated process, joined on 2026-09-16 by `resource_inventory` and `elections_model`; its pass rests on forecast skill and interval calibration, not on credible structural coefficients (`phi_pi` = 0.38 does not satisfy the Taylor principle) |

## What is still missing

### 1. Validation, not evidence

Twenty-two of twenty-four pre-registered attempts fail. The failures are not a
bookkeeping problem; they are the finding. `coupled_economy` needs nine components:
seven fail and two have not been re-run against the corrected panel. Four components have
no forecast skill at all against persistence. Interval coverage fails repeatedly because Gaussian intervals from
in-sample residual scale are too narrow for fat-tailed series. `default_hazard` passes on
a substitute series and fails on its declared primary series with the **opposite sign** on
unemployment sensitivity — two attempts that disagree about the direction of a mechanism,
which is a reason to trust neither.

Nothing in this repository should be described as a calibrated world model. One process
meets its own criteria on one family of macro series.

### 2. A coherent joined state

The machinery for defensible joins exists (units, dated codes, crosswalks, resolution,
beliefs). What does not exist is a *built* cross-domain panel. `influence_model` is
blocked precisely because the lobbying/contribution/roll-call panel has not been
constructed, even though all three inputs are published locally. The same is true of
most cross-domain questions in the table above.

### 3. A decision contract

Unchanged from the previous audit. There is still no structured representation of a
decision-maker, controllable actions, action cost, budget, constraints, objectives,
horizon or success criteria that is validated end to end. The policy-comparison and game
layers rank supplied candidates under supplied assumptions; they do not elicit or check
the decision itself.

### 4. Causal identification

Estimates are reduced-form and labelled as such (`correlational`,
`predictive_association`, `descriptive_time_series`). Instrumental-variable fits carry
their untested exclusion assumption in the record. No component identifies an
intervention response, and holdout skill does not establish one.

### 5. Coverage that is known rather than assumed

There is no coverage estimator that says what fraction of a population a dataset
represents. Record counts are not coverage: 52 million Companies House rows are complete
for the UK and say nothing about anywhere else; 78.9 million 13F rows cover one reporting
regime.

### 6. Value of information

No experiment design, no acquisition prioritisation by decision impact. The 100 GiB
budget is currently allocated by declared `desired_bytes` and `priority`, which are
author estimates, not measured value.

### 7. Operational scale and redundancy

Single machine, single process. No scheduler, no incremental release management, no
storage lifecycle policy, no second copy. 87.7 GiB of acquired raw data exists in exactly
one place and is excluded from Git by design.

### 8. Redistribution

75 of 107 published datasets carry `redistribution_review_required`. At least 25 carry a
source term that restricts redistribution or commercial use outright — including
OpenSanctions (non-commercial only), UN Comtrade (no bulk redistribution), WITS TRAINS
(attribution required, no resale), Alpaca and Massive (personal/internal use), Nasdaq
reference lists (reference use only) and FRED third-party series (flagged per
observation). The rights inventory is metadata, not a legal determination. Nothing here
authorises republishing the acquired data.

## Recommended build order

1. **Build one cross-domain panel that is currently blocked only on assembly.** The
   influence panel (LDA + FEC + Voteview) is the clearest case: every input is published
   locally and the component is blocked on the join, not on data.
2. **Fix the interval problem before adding models.** Interval coverage is the single
   most common failing criterion. Fat-tailed residuals need a distribution that admits
   them, not another mechanism.
3. **Resolve the `default_hazard` sign contradiction.** Re-specify, pre-register, and
   keep both prior attempts visible.
4. **Measure resolution quality on labelled real data.** Until then, every
   cross-source join through probabilistic matching carries unmeasured error.
5. **Publish coverage statements per dataset** so a record count stops being read as
   representativeness.
6. **Only then widen acquisition**, and pay for it from a budget that reflects measured
   decision value rather than declared desired bytes.

The first meaningful milestone remains a reproducible answer to a narrowly specified real
decision, with evidence and limitations attached. The evidence bookkeeping for that is now
genuinely in place. The validated behavioural content is one monetary policy rule.

## Audit references

- [Dataset catalog](../data/README.md), `wm catalog`, and `data/*/dataset.json`.
- [Full acquisition](full-acquisition.md) and `wm budget`.
- [Estimation and validation](estimation-and-validation.md).
- [Calibration status on real data](calibration-status.md) — the attempt-by-attempt record.
- [Political, geopolitical and market models](political-market-geopolitical-models.md).
- [Identity, units and crosswalks](identity-units-crosswalks.md).
- [Scale benchmarks](scale-benchmarks.md) and `worldmodel/limits.py`.
- [Remaining concerns](remaining-concerns.md) and [this session's summary](session-2026-09-15-summary.md).
- The superseded [pre-acquisition audit](strategic-affordances-audit-2026-09-15-pre-acquisition.md).
