# Natural experiments

Until this layer existed, no component of the repository identified an intervention response:
every estimate was reduced-form, and holdout skill says nothing about what happens if you
intervene. `worldmodel.causal` adds the machinery to identify effects from dated shocks, and the
discipline to say when it has not: every design is registered and committed before any
post-period outcome is read, every result carries its design, assumptions, pre-trend and placebo
outcomes, and the identification label follows mechanically from pre-registered acceptance
criteria. Nulls, failed pre-trends and infeasible designs are published like any other result.

**State as of 2026-09-18.** Two waves of designs have been registered, committed and run. **No
design identifies a non-zero effect.** Wave 1's four designs all failed; wave 2's four
registrations were written against what wave 1 showed, and produced three *identified nulls* -
effects bounded inside intervals that are still too wide to rule out the effects the literature
would call plausible - alongside four more failed diagnostics. A registered power suite now says,
for every design in both waves, what it could and could not have detected.

### Wave 1 (four designs, none identified)

| Study | Verdict | Label | Key numbers |
| --- | --- | --- | --- |
| FEMA first major disaster -> county QCEW employment | no causal claim: pre-trends and placebo date fail | `did_failed_diagnostics` | ATT +0.009 log points (95% CI -0.009 to +0.028), 1,204 treated counties, 46 state clusters; pre-trend Wald p = 0.032, placebo-date p = 0.013 |
| MFN tariff increase >= 2 pp -> BACI import value | no causal claim: placebo date fails | `did_failed_diagnostics` | ATT -0.087 log points (-0.131 to -0.044), 4,233 treated importer-products; pre-trend p = 0.136 passes, placebo-date effect -0.068 (p = 0.046) fails |
| OFAC designation -> 13F institutional holdings | not estimable; outcome never read | `not_estimable` | 7 of 19,776 designated parties carry a 13F-reportable identifier (gate: 30) |
| Cyclone exposure ranking vs distance to track (held out) | fails | `predictive_association` | 42 held-out storms: mean Spearman -0.019 vs -0.036, one-sided p = 0.19 |

The tariff estimate is the closest to a finding and must not be quoted as one: imports of
treated products were already falling relative to same-importer comparison products in the two
years before the increase (lead e = -3 is +0.085 relative to e = -1, and the placebo-date test
assigns a spurious -0.068 "effect" to the pre-period). That is what a protection response to
falling or surging imports, or front-running of announced increases, looks like, and it is exactly
the threat the registration named.

### Wave 2 (four registrations, three identified nulls, no non-zero effect)

Every wave-2 registration names the wave-1 result it answers and discloses which outcome values
wave 1 had already read. Full designs and numbers: [Wave 2](#wave-2).

| Study and outcome | Verdict | Label | Key numbers |
| --- | --- | --- | --- |
| Heavy vs negligible storm damage inside the same FEMA declaration -> county employment | no causal claim: pre-trend fails | `did_failed_diagnostics` | ATT -0.010 log points (-0.039 to +0.018); 397 of 583 treated county-disasters contribute, 50 state clusters; pre-trend Wald p = 0.034 (bootstrap sup-t p = 0.62), placebo date p = 0.79 |
| ... same design -> county establishments | **identified null** | `quasi_experimental_did` | ATT -0.004 (-0.028 to +0.020); pre-trend p = 0.18, placebo date p = 0.69, placebo units 0.06 |
| First MFN cut >= 2 pp (anticipation 1, HS2022 concordance) -> BACI HS1992 import value | no causal claim: pre-trend and placebo date fail | `did_failed_diagnostics` | ATT -0.007 (-0.056 to +0.041); 6,121 treated importer-products, 4,518 clusters; pre-trend p = 0.027, placebo-date effect +0.054 (p = 0.003) |
| ... same design -> import quantity | **identified null** | `quasi_experimental_did` | ATT -0.009 (-0.090 to +0.072); pre-trend p = 0.55, placebo date p = 0.55 |
| OFAC country-programme wave -> target's exports to the US relative to its other exports | no causal claim: cluster placebo fails | `did_failed_diagnostics` | ATT -0.115 (-0.267 to +0.038); 16 target countries, 1,163 treated country-chapters, 221 country clusters; pre-trend p = 0.69, placebo date p = 0.94, cluster-placebo rejection 0.11 (limit 0.10) |
| ... same design -> target's imports from the US relative to its other imports | **identified null** | `quasi_experimental_did` | ATT -0.121 (-0.309 to +0.068); cluster-placebo rejection 0.10 |
| Power and negative controls for every wave-1 and wave-2 design | every design is underpowered | `design_power_analysis` | minimum detectable effect / plausible effect: 2.27 (W1 FEMA), 1.81 (W1 tariffs), 1.02 (W1 exposure), 1.67 (W2 dose), 1.39 (W2 tariffs), 1.34 (W2 sanctions); two designs reject a true null in 11.5% and 14.5% of synthetic panels |

## The pieces

| Piece | Where | What it is |
| --- | --- | --- |
| Event library | `data/event_library` (derived dataset) | typed, dated shocks with unit, date semantics, intensity (with its timing) and a locator to the source record |
| Engine | `worldmodel/causal` (stdlib only) | staggered DiD estimators, inference, pre-trend and placebo tests, result records |
| Registrations | `examples/natural-experiments/registrations/*.json` | one committed design per study |
| Runner | `examples/natural-experiments/run_studies.py` | refuses uncommitted registrations; writes `results/<study>.json` and publishes to `natural_experiment_reports` |
| Power runner | `examples/natural-experiments/run_power.py` | `export` (real panels and calibration), `simulate` (null draws, runnable on another host), `assemble` (checks digests and calibration, publishes) |
| Results | `examples/natural-experiments/results/*.json`, dataset `natural_experiment_reports` | aggregate estimates and diagnostics, content-addressed, inputs pinned |

## Event library

`data/event_library` ([README](../data/event_library/README.md)) publishes **255,012 events**
(version `dbf0c37f15cb...`; 4m24s, 1.4 GB peak RSS): 127,180 natural hazard, 68,792 disaster
policy, 33,075 sanctions and 25,965 trade policy.

| Type | Events | Unit | Date | Intensity |
| --- | ---: | --- | --- | --- |
| FEMA major disaster / emergency / fire management | 45,733 / 21,194 / 1,865 | county FIPS | declaration date (incident begin kept) | disaster PA obligation, ex post |
| Sanctions designation (OFAC 20,632; UK, UN, BIS, State 12,443) | 33,075 | listed party (+ countries) | list owner's published date | none |
| NOAA storm events, damage >= $1M or casualties | 53,410 | county or NWS zone | begin time | damage USD, ex post |
| Earthquakes, M >= 5 | 62,499 | epicentre | origin time | magnitude, ex ante |
| Tropical cyclones (NA, EP) / US county exposures (<= 100 km, >= 34 kt) | 1,679 / 9,592 | storm / county | first fix / first qualifying fix | wind, ex post / ex ante |
| MFN tariff increases / decreases (>= 0.5 pp, same HS revision) | 7,214 / 18,751 | reporter x HS6 | 1 January of the new rate's year | change in pp, ex ante |

Every event names what its date means and whether its intensity was known at the event date
(`ex_ante`) or measured afterwards (`ex_post`); ex-post intensity must never define treatment.
Excluded on purpose: OpenSanctions (its dates are crawler first-seen times), USITC HTS (one release
snapshot, no datable change), non-MFN duties (not in TRAINS), and delisted sanctions parties
(the lists are current, so every sanctions event carries a survivorship note).

## Engine (`worldmodel.causal`)

All numerics are pure Python.

| Function | Method |
| --- | --- |
| `att_gt` | Callaway and Sant'Anna (2021) group-time ATTs, unconditional, with never-treated or not-yet-treated comparisons and a universal base period `g - 1 - anticipation`. Optional exact matching on a discrete stratum (comparisons only within stratum). Every cell carries its influence function summed to clusters |
| `event_study` | treated-count-weighted event-time aggregation (leads and lags), an overall post ATT (equal-weight mean over post event times), clustered analytic SEs, a Rademacher multiplier (wild cluster) bootstrap with uniform sup-t bands, a joint Wald pre-trend test on the leads and a bootstrap sup-t pre-trend test. `cohorts=` restricts the estimated cohorts while later cohorts remain not-yet-treated comparisons |
| `stacked_did` | Cengiz et al. (2019) stacked event study: one sub-experiment per cohort (and stratum) with clean controls not treated inside the window, pooled with the weights `n_T n_C / (n_T + n_C)` that the stacked two-way fixed-effects regression implies; a unit reused as a control in several stacks is clustered, not double counted |
| `twfe_static` | the naive two-way fixed-effects coefficient (alternating projections, cluster-robust SE), kept only to show how far it moves |
| `placebo_date_test` | treated units keep only pre-treatment data and are assigned treatment `shift` periods early; a credible design finds nothing |
| `placebo_unit_test` | real treated units are removed and never-treated units receive cohorts drawn from the real distribution (within stratum); the rejection rate estimates the test's size and ranks the real estimate |
| `placebo_cluster_test` (wave 2) | the same test with treatment assigned to whole clusters (e.g. all of a country's chapters), for designs whose real treatment is a cluster-level event; a unit-level placebo would count one country as many independent experiments |
| `calibrate`, `calibrate_variogram`, `simulate_null_panel`, `null_draws`, `summarize_draws` (wave 2) | noise calibrated on a real panel's untreated cells, synthetic null panels with that panel's exact structure, and the size, diagnostic pass rates and minimum detectable effect that follow |
| `run_did_design` | runs a registration end to end and returns a `worldmodel.causal_result/1` record |
| `compare_rankings`, `score_event`, `paired_sign_flip` | per-event Spearman and top-k capture of a ranking against measured outcomes, and a paired sign-flip permutation test across events |

Synthetic validation (`tests/test_causal.py`): a homogeneous effect is recovered by every
estimator; with dynamic effects under staggered timing and no never-treated units, naive TWFE
returns **-0.25 against a true 1.2** (the wrong sign) while Callaway-Sant'Anna (within 0.05) and stacked DiD (within 0.06)
recover it; a differential pre-trend is rejected by the Wald, sup-t and placebo-date
tests; bootstrap SEs match analytic ones; 95% intervals cover in at least 85% of 40 replications;
cluster-level treatment with common shocks widens clustered SEs; stratification removes
confounding by stratum trends; an anticipation window moves the base period; the placebo-unit test
has close to nominal size; and, as a regression test for a bug the first real run exposed, SEs stay
non-zero and cover when clusters equal strata and effects vary across strata.

### Identification labels

| Label | Meaning |
| --- | --- |
| `quasi_experimental_did` | every pre-registered gating criterion passed: the ATT is identified **under the listed assumptions**, for the treated population and horizon studied |
| `did_failed_diagnostics` | a pre-trend, placebo or robustness criterion failed; the estimate is shown for transparency and is not an effect |
| `not_estimable` | too few treated units, clusters or events for the registered design |
| `predictive_association` | a held-out predictive test (the exposure ranking); never an intervention response |
| `design_power_analysis` (wave 2) | operating characteristics of a design measured on synthetic panels with no effect; says nothing about any real effect |

An unmeasured criterion fails. The label is computed from the criteria, not chosen. A
`quasi_experimental_did` result whose interval contains zero is a **null**, and a null is only as
informative as the design's minimum detectable effect, which is why wave 2 measures it.

### Registration and the commit rule

`load_registration` validates the schema (question, treatment, units, windows, controls,
outcomes, estimator, inference, placebo tests, acceptance criteria, assumptions, pinned inputs and
a statement of what outcome data were examined before registration) and refuses to run a file
that is not committed or differs from its commit. The result records the registration's SHA-256,
commit and commit time, so anyone can check that the commit precedes the result.

## Studies

Registrations: `examples/natural-experiments/registrations/`. Results:
`examples/natural-experiments/results/` and `natural_experiment_reports` (versions below).

### 1. FEMA major disasters -> county employment and establishments

*Registered design.* Units: counties in the 50 states and DC; counties with a qualifying disaster
beginning 1990-1994 are dropped (1,878) so first treatment is defined after a quiet period. Cohort:
the year of the incident begin date of the county's first physical-hazard DR declaration (COVID-19
and drought excluded) in 1995-2024; cohorts 1995-2019 are estimated, later ones serve as
not-yet-treated comparisons. Callaway-Sant'Anna within state (every comparison is to a same-state
county), clustered by state, e = -5..5, overall ATT = mean of e = 0..5. Robustness: stacked DiD,
naive TWFE, never-treated comparisons. Placebo date shift 3; placebo units 100.

*Result* (`natural_experiment_reports@8c6c9050...`):

| Outcome | ATT (95% CI) | Pre-trend Wald p | Placebo date | Placebo units (rejection) | Stacked | TWFE | Label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| log QCEW employment | +0.009 (-0.009, +0.028) | 0.032 | +0.015, p = 0.013 | 0.03 | +0.009 (-0.009, +0.028) | -0.004 | `did_failed_diagnostics` |
| log QCEW establishments | +0.017 (-0.005, +0.039) | 0.042 | +0.009, p = 0.0006 | 0.02 | +0.012 (-0.001, +0.024) | -0.013 | `did_failed_diagnostics` |
| log CBP establishments | none | - | - | - | - | - | `not_estimable` |

The leads rise steadily (employment -0.022 at e = -5 to -0.009 at e = -2): counties hit first
were on a different growth path from same-state counties hit later, so the comparison is not
credible and no effect is claimed. **The CBP outcome was a registration error:** CBP covers
2019-2023, but the registered estimated cohorts end in 2019, so no cohort has a base year in the
data; it is recorded as registered, not repaired. Only 64 never-treated counties exist, which is
why never-treated comparisons are a robustness check rather than the design.

**Superseded run.** The first run (`@b898b92b...`) reported standard errors of about 1e-19: with
clusters equal to strata, the influence functions were centred within each state and summed to
zero in every cluster because estimated aggregation weights were treated as fixed. The engine was
fixed (commit "Include estimated-weight terms in DiD influence functions", with a regression test
that fails on the old code) and the unchanged registration re-run. Point estimates were identical;
only the inference changed. Both reports are kept.

### 2. MFN tariff increases -> imports

*Registered design.* Units: importer x HS6 (HS2017) for 49 TRAINS reporters that are BACI
importers, with positive imports in 2017 and 2018 (215,397 units). Cohort: the year of the
unit's first MFN change of at least 0.5 pp, when that change is an increase of at least 2 pp
(1,676 / 655 / 1,881 / 21 units in 2019-2022); units whose first change is a decrease or a small
increase are excluded (14,280). Callaway-Sant'Anna within importer, clustered by importer x HS2
(4,571 clusters), e = -3..3, overall = mean of e = 0..3. Placebo date shift 2.

*Result* (`natural_experiment_reports@151a9472...`):

| Outcome | ATT (95% CI) | Pre-trend Wald p | Placebo date | Placebo units | Stacked | TWFE | Label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| log import value | -0.087 (-0.131, -0.044) | 0.136 | -0.068, p = 0.046 | 0.05 | -0.108 (-0.162, -0.055) | -0.029 | `did_failed_diagnostics` |
| log import quantity | -0.121 (-0.201, -0.042) | 0.013 | -0.114, p = 0.004 | 0.06 | -0.126 (-0.217, -0.035) | -0.021 | `did_failed_diagnostics` |

Event-time estimates for value: e = -3 +0.085, e = -2 +0.017, e = 0 -0.064, e = 1 -0.114,
e = 2 -0.088, e = 3 -0.083. The post-period decline is large and consistent across estimators, but
the pre-period decline means part or all of it may be a continuation of a trend that preceded (and
perhaps prompted) the tariff. 74 treated units had a zero import year after treatment (the
extensive margin the log outcome drops). Naive TWFE (-0.029) understates the Callaway-Sant'Anna
estimate by a factor of three, as the synthetic tests predict for staggered dynamic effects.

### 3. Sanctions designations -> institutional holdings

The registration gated the design on at least 30 designated parties with an identifier that can
name a 13F-reportable security (a CUSIP/CINS-based ISIN or a US equity ticker). **Seven** of
19,776 OFAC-designated parties qualify (2 designated in 2020, 4 in 2021, 1 in 2022, all Chinese
Military-Industrial Complex or SDN entries), so the study stopped before reading any 13F row
(`natural_experiment_reports@2ce5d28f...`). The registration also records why the other sanctions
designs were rejected: `marine_ais` covers three months of 2025, so vessel designations have no
pre/post panel; no designated party or its two-hop ownership neighbourhood is an SEC registrant;
country-level designation waves coincide with the events that motivate them; OpenSanctions dates
are not designation dates. **No sanctions effect is identified.**

### 4. Exposure validation: tropical cyclones -> county employment

*Registered test.* For each North Atlantic storm, candidate counties lie within 300 km of a fix
with wind >= 34 kt. Rankings built only from the track and pre-event data: *naive* = distance to
the track; *hazard* = modelled wind `V min(1, (40 km/d)^0.5)`; *exposure* = rank(hazard) +
lambda x rank(migration-weighted hazard of the county's IRS 2017-18 migration partners), lambda
chosen on 2004-2018 development storms. Outcome: the county's seasonally differenced LAUS
employment loss (months +1..+2 against -2..-1, minus the same window a year earlier). Test on
2019-2024 storms: one-sided paired sign-flip test that exposure's mean per-storm Spearman beats
naive's.

*Result* (`natural_experiment_reports@c7cb3fcd...`, label `predictive_association`): lambda = 0
was chosen (development mean Spearman 0.024 at lambda 0 and 0.25, 0.022 at 0.5, 0.019 at 1.0),
so **the migration-graph term added nothing**. On 42 held-out storms the exposure ranking's mean
Spearman is -0.019 against -0.036 for distance to track (27 of 42 storms better, p = 0.19); top-10%
capture is 0.073 against 0.080 (p = 0.89). Neither ranking predicts measured employment loss
(exposure vs zero p = 0.75). The test fails: ranking reached counties by modelled hazard is not
confirmed by county employment changes. Plausible reasons, none tested here: LAUS county
employment is partly modelled and smooths local shocks; two post months may be too short or
too long; 2020 storms fall in COVID-19 months; and the outcome may simply not move for most
counties a storm reaches.

## Wave 2

Wave 1 left four lessons: counties hit first were on different growth paths; imports of
tariff-raised products were already falling; the designated parties have no outcome in this
repository; and a hazard ranking does not predict county employment. Wave 2 registered four
designs against those lessons, each stating in its registration what wave 1 showed, what it
changes, and **which outcome values wave 1 had already read** (none is a fresh sample in the
time-window sense, and none is presented as one). All four registrations were committed at
`c0e0f80` before any wave-2 outcome was read; the power registration was amended once, visibly,
before its simulations ran (see below).

New machinery, all stdlib and additive to wave 1: extractors for NOAA county-coded damage, BEA
county population and BACI HS1992 panels (`worldmodel/causal/sources_wave2.py`); the wave-2 panel
builders and runners (`studies_wave2.py`); a **cluster-level placebo test** (`placebo_cluster_test`)
for designs whose treatment is assigned to whole clusters; and the power suite (`power.py`,
`power_designs.py`, `examples/natural-experiments/run_power.py`).

### 1. Damage dose inside a FEMA declaration -> county employment and establishments

*Why.* Wave 1 compared counties declared after a quiet period with same-state counties not yet
declared, and the leads rose steadily. This design holds the declaration fixed: treated and
control counties are **in the same disaster**, so they share the declaration, its date, its state
and the programmes it opened; only the physical damage differs. It also matches on the counties'
own earlier growth.

*Registered design.* Treated: a county declared in a severe-storm, flood or tornado DR whose
NOAA county-coded damage during the incident period is at least **$100 per resident** (BEA
population in g-1). Control: a county in the same declaration with under $1 per resident.
Declared counties between those doses are excluded. Hurricane and winter declarations are excluded
because NOAA records that damage against NWS zones (85,424 zone-coded damage rows), which cannot be
attributed to a county. A (disaster, county) pair is dropped if the county had another qualifying
DR in g-3..g-1. Strata: disaster x above/below-median growth of log employment from g-8 to g-5 -
a window that ends before every tested lead, so the pre-trend test is not mechanically passed.
Clusters: state. e = -4..4, overall = mean of e = 0..4. Placebo date shift 2; placebo units 100.

*Sample.* 15,697 (disaster, county) pairs of those types in 1998-2020; 10,034 dropped for another
disaster in g-3..g-1, 45 without population; 583 treated, 3,371 control, 1,664 in the excluded
middle; treated dose quartiles $157 / $306 / $843 per resident. 397 of the 583 treated pairs have a
control in their own disaster-and-growth-half stratum and so contribute to the estimate.

*Result* (`natural_experiment_reports@ae0b70de...`):

| Outcome | ATT (95% CI) | Pre-trend Wald p | Placebo date | Placebo units | Stacked | TWFE | Label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| log QCEW employment | -0.010 (-0.039, +0.018) | **0.034** | +0.001, p = 0.79 | 0.03 | -0.010 (-0.025, +0.005) | -0.015 | `did_failed_diagnostics` |
| log QCEW establishments | -0.004 (-0.028, +0.020) | 0.182 | +0.001, p = 0.69 | 0.06 | -0.004 (-0.015, +0.008) | -0.010 | `quasi_experimental_did` |

The employment leads are small (+0.005, +0.000, +0.003 at e = -4, -3, -2) but jointly reject at
0.034, so the registered criterion fails and no employment effect is claimed; the bootstrap sup-t
pre-trend test on the same leads does not reject (p = 0.62), which is a disagreement between two
registered diagnostics, not a licence to pick one. The establishment outcome passes every
criterion and is a **null**: heavy damage does not move establishment counts by more than about
2.7% down or 2.0% up over five years, under the listed assumptions. That interval is wider than
the effect the design called plausible - its minimum detectable effect is 0.033 log points against
a plausible 0.02 (the power suite measures the registered primary outcome, employment; the
establishment standard error, 0.0123, is almost the same, see
[the power suite](#4-power-and-negative-controls-for-every-design)).
Event-time estimates decay
(e = 0 +0.004 to e = +4 -0.014), so nothing rules out a small effect appearing later than e = 4.
The unmatched variant (strata = disaster only, non-gating) gives -0.003 (-0.023, +0.018) for
employment with a pre-trend p of 0.34; it is reported for transparency and cannot be substituted
for the registered design.

### 2. MFN tariff decreases -> imports

*Why.* Wave 1's increases failed because imports were already falling. Decreases are the
symmetric event and are dominated in this sample by broad schedule reforms (China 2019, Pakistan
2020 and 2022, Sri Lanka 2021, the UK Global Tariff 2021, Kazakhstan, Ecuador), which should be
less responsive to a single product's import trend. The design also allows one year of
anticipation, matches on earlier import growth and uses a longer pre-period.

*Registered design.* Treatment: the first MFN change of at least 0.5 pp between consecutive
reported years, when it is a **decrease of at least 2 pp**. HS2022 reporting years are read
through the UN HS2022-HS2017 correlation, one-to-one codes only (4,097), which is what makes the
2022 and 2023 cohorts datable at all. Outcomes: BACI **HS1992** imports 2009-2024 for HS2017 codes
with a one-to-one HS1992 code (3,332), so the pre-period starts in 2009. Anticipation = 1 (base
g-2, e = -1 reported separately). Strata: importer x tercile of log import growth 2009-2013, a
window that ends before every tested lead. Clusters: importer x HS2017 chapter. e = -5..3,
overall = mean of e = 0..3. Placebo date shift 3; placebo units 100.

*Sample.* Universe 258,651 importer-products with a 2018 HS2017 rate in 48 BACI importers:
238,631 never changed, 10,509 first changes are decreases of at least 2 pp, 12,460 other first
changes, 2,438 span a reporting gap. After the HS1992 and positive-import requirements the panel
holds 138,020 units (2.19 M observations), of which **6,121 are treated** (2019: 1,915; 2020:
1,442; 2021: 2,055; 2022: 512; 2023: 197) in 4,518 clusters.

*Result* (`natural_experiment_reports@ca8df84d...`):

| Outcome | ATT (95% CI) | Pre-trend Wald p | Placebo date | Placebo units | Stacked | TWFE | Label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| log import value | -0.007 (-0.056, +0.041) | **0.027** | +0.054, p = **0.003** | 0.08 | -0.011 (-0.057, +0.034) | -0.003 | `did_failed_diagnostics` |
| log import quantity | -0.009 (-0.090, +0.072) | 0.549 | +0.015, p = 0.55 | 0.08 | +0.033 (-0.087, +0.153) | -0.027 | `quasi_experimental_did` |

The value design fails for the same reason wave 1's increases did, with the sign reversed: the
placebo date puts a spurious **+0.054** on the pre-period, and the e = -5 lead is -0.040. Trade
values of products that later get cut are not on the comparison products' path. The quantity
outcome (units with a full quantity record) passes every criterion and is a **null** of
-0.009, with an interval (-0.090, +0.072) far wider than the 5% the elasticity literature would
predict. **The symmetric design does not rescue the tariff question: cutting a tariff by 2 pp or
more is not shown to move imports, and the design is not powered to show it** - its minimum
detectable effect is 0.069 log points against a plausible 0.05 on the value outcome, and the
quantity outcome's standard error is larger still (0.041 against 0.025).

### 3. OFAC country-programme waves -> trade with the United States

*Why.* Wave 1 could not give the designated parties an outcome (7 of 19,776 map to a 13F
security) and rejected country waves because they coincide with the events that motivate them.
This design gives the programme's **target country** an outcome it has - its bilateral trade -
and removes the coinciding shock by differencing within country-chapter-year: the outcome is
log(exports to the US) - log(exports to everyone else). A shock to the country's supply or to
world demand cancels; what remains is the US-specific change.

*Registered design.* Treatment: the first year with at least 5 OFAC designations under a
programme aimed at the country (an explicit programme-to-target table in the registration;
thematic, human-rights and multi-country programmes are not mapped). 16 targets have a first wave
in 2000-2021: IRQ 2003; IRN, COD, BLR 2006; SOM, PRK 2010; LBY 2011; RUS, UKR 2014; CAF, VEN 2015;
SSD 2017; NIC 2019; CHN 2020; MMR, ETH 2021. Units: exporting country x HS1992 chapter, 1995-2024.
Strata: chapter. Clusters: country. e = -5..3. Placebo date shift 2; **placebo cluster** 100
replications (whole never-targeted countries receive placebo cohorts, because unit-level placebos
would treat one country's 96 chapters as 96 independent experiments).

*Gate numbers, stated in the registration.* The alternative vessel design is closed: 2,094 OFAC
parties carry an IMO number, 390 were first designated between 2025-02-01 and 2025-11-30 (a
`marine_ais` month before and after), and **5** of those 390 appear anywhere in `marine_ais` (24
of all 2,098 OFAC IMO numbers do); the gate was 30, and no AIS activity value was read.

*Result* (`natural_experiment_reports@891589ff...`): 17,575 units, 1,163 treated country-chapters
in 16 countries, 221 country clusters.

| Outcome | ATT (95% CI) | Pre-trend Wald p | Placebo date | Placebo cluster | Stacked | TWFE | Label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| log US share of exports | -0.115 (-0.267, +0.038) | 0.693 | -0.010, p = 0.94 | **0.11** | -0.087 (-0.222, +0.048) | -0.270 | `did_failed_diagnostics` |
| log US share of imports | -0.121 (-0.309, +0.068) | 0.853 | +0.050, p = 0.38 | 0.10 | -0.115 (-0.280, +0.050) | -0.177 | `quasi_experimental_did` |

The export design is the closest thing in either wave to a positive finding and **is not one**:
the point estimate is an 11% fall in the target's exports to the US relative to its other exports,
with an interval that includes zero, and it fails its registered cluster-placebo criterion by one
replication in a hundred (0.11 against a limit of 0.10; with 100 replications the Monte Carlo
error on that rate is about 3 points, so the test is close to its limit either way). The import
side passes everything and is a **null** of -0.121 with an interval (-0.309, +0.068): with 16
treated countries this design cannot see anything smaller than about a fifth (minimum detectable
effect 0.201 log points against a plausible 0.15), and its inference is anti-conservative: on
synthetic panels with no effect it rejects 14.5% of the time.

### 4. Power and negative controls for every design

*Why.* Wave 1 published four failures without saying whether any of them could have succeeded. A
null from a design that could not have seen the effect, and a failed diagnostic from a design whose
checks reject correct designs a third of the time, mean very different things.

*Registered design.* For each covered design, rebuild its real primary-outcome panel from its
pinned inputs with its own code (the wave-1 panels must reproduce the digests their published
results recorded, and they do: `ea51a8f6...` and `3d50cc96...`), calibrate noise on its **untreated
cells only** (never-treated units and treated units before `g - anticipation`, in first
differences, so no unit effect and no post-treatment outcome enters), then re-run the registered
estimator and its placebo-date test on 200 synthetic panels that keep the real structure exactly
and carry **no effect and exactly parallel trends**. Because the estimators are linear in the
outcomes, a homogeneous effect `delta` shifts each estimate by exactly `delta` and leaves its
standard error alone, so one set of null draws gives the whole power curve. Each design's
*plausible effect* was fixed in the registration, with its source, before any wave-2 outcome was
read. Simulations ran on the second machine from the exported panels; assembly at home rebuilds
the panels, refuses to continue unless digests and calibration match, and only then publishes.

*Amendment, disclosed.* The registered noise model is a stationary AR(1) in levels, whose
differences can only be negatively autocorrelated; county employment growth is positively
autocorrelated (+0.09), so the fitted coefficient hit its bound. The registered calibration check
caught this. A second model - the untreated cells' empirical variogram fitted with AR(1),
random-walk and random-trend components - was registered as an amendment and is simulated with the
same seeds and reported beside the registered one; **verdicts still come from the registered model
and its registered fallback** (when the mean simulated standard error is not within [0.67, 1.5] of
the real one, the reported MDE is the analytic `2.80 x SE`).

*Result* (`natural_experiment_reports@3262c1dc...`, label `design_power_analysis`):

| Design (primary outcome) | Real SE | MDE at 80% power | Plausible effect | MDE / plausible | Size under the null | Both diagnostics pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| W1 FEMA first disaster -> employment | 0.0087 | 0.023 (simulated) | 0.01 | 2.27 | 0.080 | 0.73 |
| W1 MFN increases -> import value | 0.0221 | 0.091 (simulated) | 0.05 | 1.81 | **0.115** | 0.91 |
| W1 OFAC -> 13F holdings | - | not estimable (7 treated securities) | - | - | - | - |
| W1 Cyclone exposure ranking | - | 0.051 Spearman (resampled storms) | 0.05 | 1.02 | 0.087 | - |
| W2 FEMA damage dose -> employment | 0.0119 | 0.033 (analytic; calibration check failed) | 0.02 | 1.67 | 0.070 | 0.81 |
| W2 MFN decreases -> import value | 0.0245 | 0.069 (simulated) | 0.05 | 1.39 | 0.060 | 0.91 |
| W2 OFAC programme waves -> US export share | 0.0716 | 0.201 (analytic; calibration check failed) | 0.15 | 1.34 | **0.145** | 0.59 |

**Every design in both waves is underpowered against the effect its own registration called
plausible**, by factors of 1.0 to 2.3. The wave-1 FEMA design would have needed a 2.3% employment
effect to find one reliably and called 1% plausible; its power at 1% was 0.30. The exposure
ranking test is the closest to adequate (MDE 0.051 against 0.05, power 0.79 at the plausible
value) and still failed. This is the main result of wave 2: **the nulls above are mostly the
nulls of designs that could not have seen the effects anyway**, and reporting them without this
would overstate them.

Two designs also **over-reject under a true null**: the wave-1 tariff design claims an effect in
11.5% of no-effect panels and the wave-2 sanctions design in 14.5% (nominal 5%). The sanctions
design's pre-trend test rejects a correct design 37.5% of the time - with 16 treated clusters the
clustered normal approximation is simply not accurate - so its passing diagnostics are weaker
evidence than they look, and its identified null on the import side should be read with an
interval wider than the one printed. The other four measurable designs' sizes are 6.0-8.7%, close
enough to nominal for 200 replications (Monte Carlo error about 2 points).

The calibration check fired twice, in opposite directions, and both are informative about the
designs rather than about the simulator. On the FEMA dose panel the simulated SE is 0.38 of the
real one because the panel's 3,953 units are only 2,328 distinct counties (1,141 counties appear in
two or more declarations, one in seven) with an identical outcome series each time, which the
design's state-level clustering handles and the independent-unit simulation does not. On the
sanctions panel it is 1.73 times the real one: with 96 chapters per country and strong
within-country correlation, calibrated independent chapter noise is more
variable than the real chapter series. In both cases the registered fallback reports the analytic
MDE; the amended variogram model does not rescue either panel - its own check fails too - so it
reports the same analytic values (0.033 and 0.201). Where both checks pass, the two models agree
closely (wave-1 FEMA 0.023 against 0.023; wave-2 tariffs 0.069 against 0.086).

## What is identified and what is not

**Identified: three nulls, no effect.** Three wave-2 outcomes passed every pre-registered
criterion, so under their listed assumptions the repository can now bound three intervention
responses - all of them at zero:

* heavy versus negligible storm damage inside the same FEMA declaration changes county
  **establishment counts** by -0.028 to +0.020 log points (about -2.7% to +2.0%) over five years;
* an MFN cut of at least 2 pp changes **import quantity** by -0.090 to +0.072 log points (about
  -8.6% to +7.4%) over four years;
* an OFAC country-programme wave changes the target's **imports from the US relative to its other
  imports** by -0.309 to +0.068 log points (about -27% to +7%) over four years - and that interval
  is if anything too narrow, because the same design rejects a true null in 14.5% of synthetic
  panels.

These are bounds, not effects: every interval contains zero, and every one of them is wider than
the effect its own registration called plausible - the power suite puts each design's minimum
detectable effect at 1.0 to 2.3 times that plausible effect. No simulator
parameter may be bound to a non-zero effect from any estimate in this document, and no wave-1
verdict changed.

**Now possible that was not before:** a pre-registration and commit rule that the runner enforces;
estimators for staggered adoption that are unbiased where naive TWFE is not (tested); inference
that is clustered and bootstrapped with uniform bands; pre-trend, placebo-date, placebo-unit and
(wave 2) placebo-cluster falsification that can and did fail designs; labels computed from
acceptance criteria; a versioned library of 255,012 dated shocks; and a registered power suite that
states, for every design, the smallest effect it could have detected and how often it would claim
one when there is none.

**Not identified, and why:**

* Disaster effects on county employment: wave 1's counties hit first were on different paths from
  same-state counties hit later; wave 2's within-declaration dose contrast still fails its
  pre-trend criterion on employment (while passing it on establishments), and could not have
  detected the 2% effect its registration called plausible.
* Tariff effects on import value: increases and decreases both fail, symmetrically. Values of
  treated products move before the change in both directions (wave 1 placebo -0.068, wave 2
  placebo +0.054), so neither design separates the tariff from what prompted it.
* Sanctions effects: the designated parties still have no firm-level outcome here (7 of 19,776 map
  to a 13F security; 5 of 390 in-window designated vessels appear in `marine_ais`). The
  country-programme design gives them a trade outcome, but the export side fails its cluster
  placebo and both sides are too imprecise to see anything smaller than about a third.
* Exposure: hazard-based rankings of cyclone-reached counties do not predict their measured
  employment change, and the test had little chance of showing otherwise.

## Follow-ups

* **Power first.** Every design in both waves is underpowered against the effect its registration
  called plausible. A wave 3 should start from the power suite: pick outcomes and horizons whose
  minimum detectable effect is below the effect worth finding, or say in advance that the design
  can only bound.
* FEMA: the employment pre-trend fails while establishments pass on the same units, which points
  at composition (which employers are in QCEW) rather than at the design; a monthly outcome
  (QCEW monthly within quarters exists for 2023-2025, LAUS monthly from 1990) and a larger dose
  contrast (the top decile of damage per capita) are the obvious next registrations.
  **Started, not registered.** The monthly outcome now exists: `county_monthly_realtime_panel`
  (`@197658b2...`) holds 1,459,133 LAUS first releases over 3,140 county-equivalents and 255 reference
  months, each value dated by the vintage that published it. Power for the monthly top-decile design
  was measured on it *before* any design was registered, by the method above
  (`examples/natural-experiments/run_power_wave3_draft.py`), and the design was then **registered** as
  `examples/natural-experiments/registrations/fema_monthly_dose_county_employment.json` on
  2026-09-19, once `worldmodel/causal/studies_wave3.py` held a runner for it. Registering it changed
  no threshold: the six acceptance criteria are the draft's in id, type and value, and the four
  clarifications made on the way in were additive (the bounding-only horizons made machine-readable,
  which robustness variant can gate, the per-outcome matching that differs from wave 2, and two
  operational details the runner would otherwise have chosen for itself). **The study has not been
  run, and no treated-versus-control contrast has been computed for it** - the real panel's own
  leads, pre-trend and placebo tests were deliberately not run while the design was written, because
  they are contrasts too, and the runner was tested on fixtures with planted effects alone.

  | Horizon | Real SE | MDE at 80% power | Effect worth finding | MDE / worth finding | Size under the null | Verdict |
  | --- | ---: | ---: | ---: | ---: | ---: | --- |
  | 3 months | 0.0024 | 0.0096 | 0.02 | 0.48 | 0.075 | detects it |
  | 6 months | 0.0026 | 0.0116 | 0.02 | 0.58 | 0.090 | detects it |
  | 12 months | 0.0034 | 0.0148 | 0.02 | 0.74 | **0.115** | bound only |
  | 24 months | 0.0058 | 0.0193 | 0.02 | 0.97 | **0.115** | bound only |

  This is **the first design in this repository whose minimum detectable effect is below the effect its
  own registration calls worth finding** - wave 2's annual version of the same contrast had an MDE of
  0.033 against the same 0.02 - and the gain is the monthly outcome, not more counties (176 treated
  pairs against wave 2's 583). Two things keep it honest. At 12 and 24 months the design rejects a
  true null in 11.5% of no-effect panels, above the 10% limit the power registration uses, so at those
  horizons it **can only bound**. And the pre-trend window had to be chosen on measured size: a joint
  Wald test over eleven monthly leads passes only 26% of correct no-effect designs at 47 state
  clusters, so the draft gates on five leads (72%) and reports the longer window without gating on it.
  A small MDE here also does not promise a visible effect: LAUS county series are modelled and
  smoothed toward the state, and the smoothness that makes these standard errors small attenuates the
  signal too.

  **What remains is the run itself.** `python3 examples/natural-experiments/run_studies.py --study
  fema_monthly_dose_county_employment` is the whole of it, against the registration as committed. It
  is expected to take hours and to be dominated by the 100-replication placebo-unit test over a
  2,078-unit monthly panel, so it belongs on the second GB10 under a memory cap rather than on the
  shared machine. Whatever it returns is the result: the criteria are fixed, the bounding-only
  horizons are fixed, and a rejection at 12 or 24 months may not be reported as an effect.
* Tariffs: the remaining threat is anticipation longer than one year and reallocation across HS6
  lines inside a chapter; a design at chapter level, or with the tariff change as a continuous dose
  and importer-chapter-year fixed effects, would address both.
* Sanctions: a treatment that is not a year-level wave (dated Federal Register listings by sector)
  and an outcome measured monthly would raise power far more than more countries would; the trade
  panel has 16 treated clusters and cannot get many more.
* Exposure: other outcomes (IHP registrations, storm damage) and horizons, registered before
  scoring, with the MDE computed first.

## Reproduce

```sh
# event library (pin every input so no source is rebuilt)
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 -m worldmodel run event_library --input openfema/normalized@... (eight pins, see data/event_library/README.md)
# studies (registrations must be committed)
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_studies.py --study fema_disasters_county_employment
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_studies.py --study tariff_mfn_decreases_imports   # and the other wave-2 studies
# power and negative controls (simulate may run on another host; digests and calibration are checked on assembly)
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_power.py export --dir DIR
python3 examples/natural-experiments/run_power.py simulate --dir DIR --workers 10
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_power.py assemble --dir DIR
python3 -m unittest tests.test_causal tests.test_causal_studies tests.test_causal_event_library tests.test_causal_wave2
```

The BACI HS1992 extractions (5.4 GB of gzip JSONL) were run on the second machine and the caches
copied back; every other wave-2 step ran on the shared machine under a 6-12 GB memory cap.
