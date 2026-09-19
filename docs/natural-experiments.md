# Natural experiments

Until this layer existed, no component of the repository identified an intervention response:
every estimate was reduced-form, and holdout skill says nothing about what happens if you
intervene. `worldmodel.causal` adds the machinery to identify effects from dated shocks, and the
discipline to say when it has not: every design is registered and committed before any
post-period outcome is read, every result carries its design, assumptions, pre-trend and placebo
outcomes, and the identification label follows mechanically from pre-registered acceptance
criteria. Nulls, failed pre-trends and infeasible designs are published like any other result.

**State as of 2026-09-18.** Four designs were registered and committed before any outcome was
read, then run against the data on the shared machine (the FEMA study twice, see "Superseded run"). **None identifies an effect.**

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

## The pieces

| Piece | Where | What it is |
| --- | --- | --- |
| Event library | `data/event_library` (derived dataset) | typed, dated shocks with unit, date semantics, intensity (with its timing) and a locator to the source record |
| Engine | `worldmodel/causal` (stdlib only) | staggered DiD estimators, inference, pre-trend and placebo tests, result records |
| Registrations | `examples/natural-experiments/registrations/*.json` | one committed design per study |
| Runner | `examples/natural-experiments/run_studies.py` | refuses uncommitted registrations; writes `results/<study>.json` and publishes to `natural_experiment_reports` |
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

An unmeasured criterion fails. The label is computed from the criteria, not chosen.

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

## What is identified and what is not

**Identified:** nothing. No registered design passed its own diagnostics, so the repository
still holds no identified intervention response, and no simulator parameter may be bound to any
estimate above.

**Now possible that was not before:** a pre-registration and commit rule that the runner enforces;
estimators for staggered adoption that are unbiased where naive TWFE is not (tested); inference
that is clustered and bootstrapped with uniform bands; pre-trend, placebo-date and placebo-unit
falsification that can and did fail designs; labels computed from acceptance criteria; and a
versioned library of 255,012 dated shocks with explicit date semantics and ex-ante/ex-post
intensity.

**Not identified, and why:**

* Disaster effects on county employment: counties first hit after a quiet period were already on
  different paths from same-state counties hit later.
* Tariff effects on imports: a sizeable decline follows MFN increases, but a decline also precedes
  them; the design cannot separate the tariff from what prompted it.
* Sanctions effects: the designated population does not overlap the outcome data the repository
  holds.
* Exposure: hazard-based rankings of cyclone-reached counties do not predict their measured
  employment change, so an exposure ranking is not yet validated for any shock type.

## Follow-ups

* FEMA: an intensity design (damage or IHP per capita among declared counties), monthly QCEW
  (2023+) or LAUS outcomes, and a matched comparison on pre-period growth; any of these needs a new
  registration.
* Tariffs: add the decrease events (18,751 in the library), a concordance to use HS2022 years, and
  a design robust to anticipation (anticipation = 1 with the base at g - 2), each registered anew.
* Sanctions: acquire an outcome the designated population actually has (vessel AIS histories,
  bilateral trade of designated firms' sectors) before registering.
* Exposure: other outcomes (IHP registrations, storm damage), other horizons, and the storm-events
  county damage as a measured intensity, registered before scoring.

## Reproduce

```sh
# event library (pin every input so no source is rebuilt)
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 -m worldmodel run event_library --input openfema/normalized@... (eight pins, see data/event_library/README.md)
# studies (registrations must be committed)
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_studies.py --study fema_disasters_county_employment
python3 -m unittest tests.test_causal tests.test_causal_studies tests.test_causal_event_library
```
