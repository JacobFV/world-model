# Implementation and remaining empirical limits

An experimental evidence and simulation substrate. Tests establish software contracts,
not world coverage or predictive validity: the suite is 1,268 tests (263 of them skip without the optional `agents` extra and local data payloads), and it passes while 24
of the 33 current estimation attempts fail their acceptance criteria (verified 2026-09-18).

[handoff-completion.md](handoff-completion.md) and [RESUME-PLAN.md](RESUME-PLAN.md) are
historical records of the pre-acquisition state and are no longer current. The current
audit is [strategic-affordances-audit.md](strategic-affordances-audit.md).

## The main concern

**Almost nothing here is empirically validated.** Five of the 22 processes declared in
`requirements.json` — `monetary_model`, `resource_inventory`, `elections_model`,
`assets_model` and `legislative_model` — meet their own pre-registered acceptance criteria on
real data, each on the specification and series it declared. Nine of 33 current attempts pass.
All eleven model families' descriptors still say `validated: false`; validation comes only from
a passing report. `coupled_economy` needs nine components and three pass, one only on a
substituted series. Fifteen current attempts have no demonstrated forecast skill against a
persistence baseline. See [calibration-status.md](calibration-status.md) for each attempt and
its reason; the counts were re-derived from the published reports on 2026-09-18 with
`wm calibration-status --all`.

That is the honest recorded result, not an interim state waiting to be cleaned up. A
simulation kernel that runs, conserves mass and reproduces its own reference
implementation exactly is still parameterised by assumptions. Do not read a passing test,
a completed acquisition or a large record count as evidence that a model predicts
anything.

Three layers added on 2026-09-18 each end in the same place, and each says so in its own document:

* **World-state embeddings.** Eight scored attempts over five domains
  ([world-state embeddings](world-state-embeddings.md)). The encoder beats the naive baseline
  everywhere and beats a gradient-boosted model on the same subgraph only on county employment,
  where the year-clustered test is not significant. Adding the embedding to that model does not
  help. A first-release county panel removed the revision leakage that had made the places attempts
  undecidable, and the attempt then failed on skill; a 13F link-prediction task with an empty query
  node -- the design's best case -- also lost to that model. None is validated, and the stop rule
  declared with the last attempt has been kept: the encoder line is closed and only the Student-t +
  split-conformal interval head continues.
* **Natural experiments.** Eight designs over two waves ([natural experiments](natural-experiments.md)):
  three identified nulls whose bounds all contain zero and are wider than the effect worth finding,
  five failed diagnostics, and a power suite showing every design underpowered against its own
  registered plausible effect. No intervention response is identified anywhere in this repository.
* **Identity.** Mining every published bridge moved cross-source joins from 3.17% to 3.66% of
  entities ([identity coverage](identity-coverage.md)). The ceiling is structural: 2.3 million
  entities are records-as-entities that no second publisher describes.

The decision layer ([decision layer](decision-layer.md)) makes the consequence explicit: its policy
learner refuses to run on any mechanism that is not validated, and on the one process where it is
allowed to run it beats its baselines on the history it trained on and loses to fixed rules on
held-out history.

## Durable execution

The incremental scheduler preserves state, RNG streams, agent memory, inputs and
held pressures. The opt-in SQLite journal binds action outcomes to checkpoints,
deduplicates committed actions, charges failed attempts, and blocks unresolved
provider effects. Backups support verification, optional HMAC authentication and
retention without deleting quota/effect authority. See [execution-journal.md](execution-journal.md).

Remaining limits: histories and checkpoint copies consume memory. Local SQLite
coordination is not a distributed scheduler; copying a database does not create a
global quota. Provider effects cannot roll back. Exactly-once execution still
requires provider support. Superseded live writers are fenced by durable action attempt and checkpoint state.
HMAC keys must be supplied and protected separately;
checksums alone do not authenticate data, and backups are not encrypted.

## Economy and fields

The bank-ledger economy now includes explicit labor limits, daily interest and
policy-rate transmission, inventory costing, bounded demand/price feedback and
funded collateral recovery. Each committed step checks accounting and work bounds.
See [economy-mechanisms.md](economy-mechanisms.md).

Signed scalar and vector transport/diffusion now supports atomic dated spatial
lifecycle changes, closed-boundary conservation, planar polygon selection and
explicit refinement/coarsening. Timeline views retain actual active topology and
source details. See [field-dynamics.md](field-dynamics.md) and
[spatial-surfaces.md](spatial-surfaces.md).

Explicit process input-time, conservation and aggregation contracts plus a replayable
cross-domain example are described in [process-composition.md](process-composition.md).

These are synthetic mechanisms. Parameters are assumptions, not estimated causal
responses. The field solver is bounded and local, with explicitly supplied topology,
coordinate systems and component frames. It does not infer navigable water, roads,
terrain or geodetic physics from coordinates. Spatial support lifecycle and an
institution's legal lifecycle are distinct contracts.

## Evidence and coverage

113 of 125 declarations publish a normalized stage, totalling about 1.56 billion records
(48.5 GiB gzipped) built from 90.0 GiB of acquired raw data (re-counted 2026-09-18 from the
latest normalized manifests and `wm budget`). All dataset-specific
transformations live in each dataset directory; shared code provides parsing, provenance,
graph and execution mechanics. Acquired artifacts are ignored by Git; tracked code and
compact manifests make the work reproducible. Observations, source claims, reviewed
assertions and synthetic scenarios remain distinguishable.

Record counts are the weakest kind of evidence about a system. They say what was
downloaded and parsed. They say nothing about whether a metric means the same thing in
two datasets, whether a join is defensible, or whether any model over them predicts.

Cross-source joins now have explicit machinery; see
[identity-units-crosswalks.md](identity-units-crosswalks.md).

- **Units.** Units and dimensions are checked. Currency conversion and deflation require
  dated series references.
- **Codes and crosswalks.** Country, county and NAICS codes are dated, and crosswalks
  conserve totals.
- **Identifier links.** Deterministic links come from published mappings.
- **Probabilistic matching.** Matches carry score, method, features and reviewer status.
- **Beliefs.** The belief table handles retractions, staleness, unknown versus false, and
  conflict reports.
- **Graph.** The index supports resolved neighborhoods, paths and centrality queries.

Remaining limits:

- **Unvalidated match quality.** Probabilistic precision and recall were measured only on
  fictional data. Without published-identifier labels, EM over-links look-alike names.
  Real use needs labeled samples and review.
- **Crosswalk weights are now acquired, and that is not the same as correct.**
  `census_relationship_files` (300,090 weighted rows), `cbsa_delineations` (23,742) and
  `trade_concordances` (233,598) are published, so apportionment no longer refuses for
  lack of weights. The weights carry their own vintage: a 2020 ZCTA-to-county
  relationship file does not apportion a 2010 geography, and nothing checks that for you.
  Split rows still refuse apportionment unless weights or an explicit equal-split
  assumption (with an error bound) are supplied.
- **Uncalibrated beliefs.** Source reliability priors are policy inputs, not calibrated
  posteriors.
- **Coverage is not measured.** There is no coverage estimator. A record count is not
  representativeness: 52.3 million Companies House rows are complete for the UK and say
  nothing about anywhere else; 78.9 million 13F rows cover one reporting regime and one
  asset class.
- **Base years and units in vintage data.** FRED/ALFRED rebases chained-dollar and index
  series at benchmark revisions, so each vintage is denominated in the base period current
  at that vintage, and some series change scale outright. 189 of 849 panel series change
  units across vintages. Every FRED observation now carries `attributes.source_units`,
  `attributes.unit_multiplier` and `base_period`, and the `unit` token is per vintage.
  **Compare levels only within one `base_period`**, or use growth rates and gaps. A
  point-in-time frame is single-vintage and therefore single-base and safe; any
  construction that combines periods from *different* vintages (a first-release series,
  for example) is not, and mixing bases there produced an output gap of +22.5% before it
  was caught.
- **Unit tokens are not stable across rebuilds.** `fred_oil_price` went from `USD/barrel`
  to `USD_per_barrel` with identical values. A loader pinned to a unit string silently
  selects nothing. Do not pin on unit strings.

DIA holdings are dated fund positions, not official index membership or issuer
ownership. NAV is not adjusted exchange price. Temporal financial crosswalks keep
issuer, security and listing identities separate. Reviewed contract inputs and
canonical market import adapters require supplied evidence and explicit policies. See
[financial-evidence-imports.md](financial-evidence-imports.md).

Federal Register, Crossref, NASA and conflict adapters preserve source status,
publication dates and reported uncertainty. Bounded samples do not establish complete
public-figure, institution, research, law or conflict coverage. Aircraft ownership
never proves a person's presence; routes and observed movements are separate.

### What is and is not acquired

`wm catalog` is the authority. [source-access-2026-09-15.json](source-access-2026-09-15.json)
records the *pre-acquisition* bounded-sample outcomes and is a historical file: the USDA,
BEA, UCDP, MarineCadastre and freight blockers it lists are resolved
(`usda_agriculture` 7.5M records, `bea_national_regional` 13.7M, `ucdp_conflicts` 575k,
`marine_ais` 7.4M, `freight` 31.1M). Adjusted daily prices are acquired under provider
terms (`alpaca_daily_bars`, `market_prices`).

Still outstanding, and none of it is a software task:

| Declaration | Blocker |
| --- | --- |
| `acled`, `global_fishing_watch` | approval-gated accounts |
| `wto_timeseries` | optional API key not held |
| `bts_airline_t100` | interactive download |
| `mit_election_returns` | House and county returns behind a guestbook-gated Dataverse download; statewide president/senate are published |
| `epa_aqs_daily`, `market_corporate_actions` | downloads in flight |
| `contract_candidates`, `reviewed_obligations`, `market_obligations` | authorized file import only; nothing is inferred from aggregates |
| `lda_lobbying` | partial acquisition in progress |

`wm evidence-audit` is currently broken in a tree where sample payloads have been pruned:
it dereferences each dataset's retained sample manifest and aborts on the first missing
one. Use `wm catalog` and `wm rights DATASET`.

## Learning and validation

Chronological train/validation/test benchmarks freeze model selection before final
scoring and retain persistence comparisons, missing values, input evidence and
vintage assumptions. A losing model remains a valid recorded outcome. Parameter and
scenario comparisons expose ambiguity rather than labeling a plausible fit causal.
See [benchmarks.md](benchmarks.md) and [structured-rollouts.md](structured-rollouts.md). The previous WTI holdout failed persistence;
that historical finding remains unchanged.

`worldmodel.estimation` ([estimation-and-validation.md](estimation-and-validation.md))
adds a general estimation and validation layer. It provides OLS/WLS/2SLS with
robust SEs, AR/ARIMA-lite/VAR, error-correction pass-through, hazard/logit,
growth, PPML gravity, Kalman local level, SMM/ABC over existing simulators, and
block bootstraps. Fits and forecasts obey a knowledge cutoff with explicit vintage
policy and leakage audits. Rolling-origin backtests score against naive baselines
with proper scoring rules and Diebold–Mariano tests on a frozen holdout, and
publish immutable reports. A registry process becomes `validated` only when
declared acceptance criteria pass for every required component.
`estimation/requirements.json` lists the exact public series each component needs.

This layer has now been run against the real catalog. [calibration-status.md](calibration-status.md)
records 61 pre-registered attempts (33 current, 28 superseded and kept) and
`data/calibration_reports/` holds 186 immutable artifacts: 93 validation reports and 93
estimates, including reruns and two reports from a track whose registrations are on another
branch (counted 2026-09-18).

Remaining limits:

- **Five validated processes, each narrowly.** `monetary_model`, `resource_inventory`,
  `elections_model`, `assets_model` and `legislative_model` pass every declared criterion on
  the specification and series they declared; several have failing attempts beside them that
  stay on the record. The passing monetary fit rests on forecast skill against persistence
  (p = 0.059, against a 0.10 threshold) and well-calibrated intervals — **not** on credible
  structural coefficients. The legislative pass conditions on a third of the chamber's votes on
  the same roll call and pools member-votes that share roll calls. `coupled_economy` needs nine
  components and three pass, one only on a substitute. Everything else is unvalidated.
- **Skill, not intervals, is now the most common failure.** Fifteen of the 24 failing current
  attempts do not beat persistence. Interval coverage fails on nine. Fat-tailed predictive
  distributions — Student-t by maximum likelihood, empirical residual quantiles and rolling
  split-conformal on pre-origin out-of-sample errors — were pre-registered against every attempt
  failing coverage on 2026-09-18 and resolved none: where the tails were the issue, the
  validation windows were too short to choose between methods, or the validation window's
  regime reversed on the holdout (`labor_demand`, for the second time), and where they were not,
  the interval was wide because the point forecast was poor. **Selection on a validation window
  is only as good as that window's coverage of the holdout's regimes**; that is the protocol
  finding to act on, and no rule that fixes it has been pre-registered yet.
- **`default_hazard` still does not validate, and the sign flip is explained.** The FDIC
  substitute passes with `unemployment_sensitivity` = +2.12/+2.19; the declared credit-card
  series fails with −1.26 and persistence 1.0038. Units, lags and the sample window were ruled
  out; the cause is what each series measures (card delinquency is purged by charge-off and
  falls while unemployment is high; the FDIC noncurrent stock tracks its level). A business-loan
  re-specification, closer to the firm-default mechanism the hook implements, beats persistence
  and fails on coverage. See [research-log/default-hazard-sign.md](research-log/default-hazard-sign.md).
- **Two components and two families remain blocked on data** (`wm estimation-load`):
  county PM2.5 with a topology (`epa_aqs_daily`), FAF OD distances, several consecutive years of
  bilateral flows (`trade`), and an unbuilt lobbying/contribution/roll-call panel (`influence`,
  being built on another track). `sanctions_model` is non-estimable by declaration: it is a
  legal-rule determination.
- **The two attempts recorded `not_run` have run.** The compute-budget reason was never
  measured — neither family had a loader. `legislative_model.voteview` ran in 70 s and passes;
  `market_abm_model.alpaca` ran in 106 s and fails four criteria.
- **Simulator hooks bind estimates by declared assumption.** Every component with a `hooks`
  entry in `requirements.json` (ten) now declares `hook_status: implemented`, so an estimate can
  reach a simulation, but the mapping from quarterly or monthly coefficients to daily mechanisms
  is an assumption (`period_days` cadences), and binding an estimate that failed its holdout is
  not prevented by the hook — only by `registry.calibrated_parameters(require_validated=True)`.
- **Estimates are reduced-form.** Conditional forecasts use realized drivers, and
  holdout skill does not identify intervention responses. Identification labels
  (`correlational`, `predictive_association`, `descriptive_time_series`) are part of the
  record and should be read literally.
- **Real-time vintages exist now, and their pitfalls are real.** `fred_macro_panel` and
  `fred_cpi` carry full ALFRED vintages, so revised series can be read point-in-time. See
  the base-year and unit-token warnings above before comparing any levels.

Structured RL spaces, isolated numerical rollouts and held-out synthetic scenarios
are software tools. They do not establish real-world strategic usefulness.
Observation restrictions are explicit interfaces, not a security boundary or an
inferred model of human beliefs. Trusted Python factories may have effects outside
the runner; numerical workers reject known live backends but are not sandboxes.

## Distribution and operational scale

Code/documentation use MIT. Dataset and upstream rights remain separate and flow
through provenance; a derived artifact does not erase upstream obligations. Wheels
bundle declarations, transformation code and fictional fixtures, not acquired data
or credentials. Standalone views make no hidden network requests.

**Redistribution is restricted and the restriction now has teeth.** 81 of the 113
published datasets carry `redistribution_review_required` in their manifest rights block.
At least 25 carry a source term that restricts redistribution or commercial use outright:

| Restriction | Datasets |
| --- | --- |
| Non-commercial only | `opensanctions`, `opensanctions_graph` |
| No bulk redistribution | `un_comtrade` |
| Attribution required, no resale | `wits_trains_tariffs` |
| Provider terms, personal/internal use | `alpaca_daily_bars`, `market_prices` |
| Proprietary / reference use only | `nasdaq_index_reference`, `nasdaq_listings` |
| Informational use | `ssga_dia_holdings`, `ssga_dia_nav`, `ssga_dia_premium` |
| Publisher terms, third-party series flagged per observation | `fred_*` (Moody's, S&P, CBOE, University of Michigan, Freddie Mac, Nasdaq series) |
| US government work with use restriction | `fec`, `fec_candidates`, `fec_individual_contributions*` (FEC contributor data may not be used for commercial solicitation) |

Publishing this repository's code is not publishing its data. The rights summary is a
metadata inventory, not a legal determination, and it does not adjudicate compatibility
between the terms of two inputs that were joined.

### Storage durability

**Partly closed on 2026-09-19.** There are now two external copies, both verified rather
than assumed:

* `Backup-1` (500 GB) holds `world-model-2026-09-18`, 124 GiB: raw receipts, manifests and
  derived artifacts, deliberately without the unified graph index.
* `Portable2TB` holds `world-model-full-2026-09-19`, 318 GiB: the **entire** `data/` tree,
  index included, plus a git bundle of every branch. `git bundle verify` reports a complete
  history; an `rsync --dry-run` over `data/` finds nothing missing that existed when the copy
  ran; a 120-file random sample was compared by sha256 with 0 mismatches and 0 absent.

What that still does not establish. The sample is 120 files, so byte equality is established
for the sample and, for everything else, only size and mtime agree. Both drives sit in the same
room as the machine, so this is redundancy against disk failure, not against loss of the site.
The copy is a snapshot, not a policy: everything published after it -- currently the monthly
LAUS vintages, the OpenFIGI shards and seven embedding reports -- exists in one place again
until the next run. And the destination filesystem does not preserve Unix modes, so a restore
carries the bytes and not the permissions.

The local tree is 173 GiB outside the index and 126 GiB of index. The fair-share pool is
100 GiB and 90.0 GiB of it is spent, so the next large acquisition displaces an existing one;
there is still no tiering or eviction policy to decide which.

### Scale limits and measured performance

Hardcoded toy caps (for example 1..100 coupled-economy actors, 1,000 days, 100k field
cells, 10k exposure obligations, 200k routing edges) are replaced by one named,
documented limits mechanism (`worldmodel/limits.py`; `describe_limits()` lists every
name, default and purpose). Defaults target national-scale synthetic runs; override
per call (`limits={...}`), per block (`use_limits`), with `WORLD_MODEL_LIMITS` JSON or
`@file`, or CLI `--limits`. Lowered limits still reject, and every rejection names
the limit and how to raise it. Limits bound work and retained output; they do not
guarantee that a given machine has the memory or time for any run below them.

An optional numpy backend (`pip install "worldmodel-substrate[fast]"`) accelerates
the coupled economy, field transport and exposure clearing. The pure-Python path
remains the reference. Field and exposure numpy results are bit-identical, and the
coupled economy keeps int64 cents and matches reference states and summaries exactly.
Large runs use explicit retention (`history: full|every_n|summary`), array-native
inputs (`ColumnarPolicy`, `evolve_field_arrays`, compiled networks), SQLite batch
operations, and chunked JSON or binary array checkpoints with per-chunk hashes and
atomic publication. Wall time, peak RSS and throughput for three sizes per kernel on
the GB10 (20-core aarch64, 121 GB) are recorded in
[scale-benchmarks.md](scale-benchmarks.md).

Remaining scale concerns:

* Dict/JSON APIs (process registry states, `materialize`/environment traces,
  `history='full'` records, dict `SpatialStore.select`, full timeline frames) are
  still proportional to retained state per step or frame. National-scale runs should
  use the array APIs and summary retention directly.
* The coupled-economy numpy path falls back to the exact sequential reference for
  full audit records, declared bankruptcies or collateral sales on active firms,
  balances near the int64 guard, and couplings that do not converge within 512
  fixed-point iterations. `ArrayEconomy.diagnostics` reports fallbacks.
* Execution is single-process, single-machine (numpy releases the GIL only inside
  kernels). There is no distributed/out-of-core execution, GPU backend, or
  partitioned SQLite. Benchmarks cap runs at ~20 GB RSS and 10 minutes, so
  3,650-step national runs are extrapolated from measured per-step throughput.
* Local verification does not establish distributed reliability, global coverage
  or calibrated geopolitical prediction. Those require actual scale runs on real
  data, source access, independent ground truth and an empirical validation design.
* Every acquisition, build and estimation run in this session ran on one machine, one
  process at a time. Acquisition parallelism is `--workers`, not a scheduler: there is no
  retry queue, no incremental release management and no way to resume a partially built
  catalog other than rerunning `wm acquire ... --resume` per dataset.
* Building a dataset re-reads and re-hashes bytes at several pipeline boundaries. A full
  rebuild of the catalog is an I/O-bound operation on ~90 GiB of raw input and has not
  been timed end to end.

## Grounded cognitive agents

The agent layer (see [agents.md](agents.md)) is the newest and least settled area.

* **Nothing in it is validated.** No agent is fitted to an outcome or scored against a
  baseline. Its fidelities, trust weights, slip direction, corporate thresholds and
  authority defaults are authored assumptions, declared in each module. A demonstration
  that a mechanism behaves as designed is not evidence about the world.
* **The organizational record is thinner than the cognition built on it.** No source
  publishes bylaws, so authority defaults to `UNDECLARED` and most institutional acts
  return `Unknown`. Roles are inferred from filing titles; tenure stays `Unknown` because
  filings publish a reporting period rather than an appointment.
* **Perception is narrow where the catalog is narrow.** Only five edge predicates carry a
  legislator subject, so a legislator's percept stream is thinner than the design allows,
  and contribution amounts and lobbying contacts return `Unknown` rather than a number.
* **Informal ties are inference, not record.** Affinity is derived from published
  co-membership and marked as inference with its evidence attached. Nothing publishes
  education, so those ties are not inferable at all and are absent rather than guessed.
* **Agents do not learn.** They perceive, decide and remember, but nothing updates from
  outcomes: trust derives from published structure rather than from whether a teller
  proved right. Decisions are single-tick; there are no multi-step plans, no commitment
  and no abandonment. Agents do not age, retire or die, and there is no household — the
  economic unit that consumes — nor any use of the road network and geography the catalog
  already holds.
* **Scale is bounded by memory.** A legislator store measures about 1,548 bytes per claim
  resident, roughly 470 claims per focal agent, which is ~11,800 focal agents at 8 GiB.
  Beyond that, agents must live as compact state or cohorts.

[handoff verification](handoff-verification-2026-09-15.json) is the pre-acquisition local
check record and is historical. Current checks: 1,201 tests with 8 skips with the optional
`agents` extra installed and 1,145 with 228 skips without it, `wm catalog`, `wm budget`,
and per-dataset `wm verify DATASET/normalized`.
