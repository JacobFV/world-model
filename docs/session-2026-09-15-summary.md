# Session summary — 2026-09-15

What changed in this working session, what was found while doing it, and what none of it
establishes. Numbers were re-derived from `wm catalog`, `wm budget` and the published
stage manifests at the end of the session.

## What changed

| Area | Before | After |
| --- | --- | --- |
| Dataset declarations | 18 | 121 (116 source, 5 derived) |
| Datasets with a published normalized stage | 0 real | 107 |
| Normalized records | 762 retained sample rows | 1,312,082,961 (36.9 GiB gzipped) |
| Raw data held | ~507 KiB of samples | 87.7 GiB, inside a 100 GiB fair-share budget |
| Acquisition | bounded 100-row samplers | `wm acquire`: paged APIs, URL lists and bulk files, resume, rate limits, `Retry-After`, `.env` credentials, sharded raw artifacts |
| Simulation caps | hardcoded toy limits | named fields of `worldmodel.limits.Limits`, overridable per call/process/command, with measured benchmarks |
| Estimation | none | `worldmodel.estimation`: OLS/WLS/2SLS, AR/ARIMA-lite/VAR, error correction, hazard/logit, PPML gravity, Kalman, SMM/ABC, block bootstrap |
| Validation | one AR(1) holdout | 24 pre-registered attempts, 100 immutable artifacts (50 validation reports, 50 estimates), vintage policy and leakage audits |
| Model families | none | 11 political/market/geopolitical families plus a multi-actor game layer |
| Joins | none | units/currency, dated country/county/NAICS codes, total-conserving crosswalks, deterministic links, Fellegi-Sunter resolution, beliefs |
| Tests | 501 | 839 (8 skipped), passing |

The largest sources by normalized record count are CEPII BACI HS1992 (269.9M),
USAspending contracts (107.0M), BACI HS2017 (89.2M), SEC 13F history (78.9M), USAspending
assistance (76.2M), FAOSTAT (52.8M), Companies House UK (52.3M) and SEC financial
statements (50.9M).

The one number that did not improve much: **registry processes meeting their own declared
acceptance criteria went from zero to one, out of 22.**

## Bugs found and fixed

### 1. The provenance catalog snapshot aborted long builds

`capture_code` snapshotted every `data/*/dataset.json` into `code.files` on every build.
Publication checks that captured sources did not change, so editing *any unrelated*
dataset declaration while a multi-hour build was running caused that build to abort at
publish time — after all the work, at the commit point.

The fix is in `worldmodel/provenance.py`: a build scoped to a dataset (`dataset_root` set)
no longer adds the catalog-wide glob. It loses nothing, because the dataset's own
declaration is already captured in `code.dataset_code` and in the manifest's `definition`.
Builds that are not dataset-scoped still snapshot the full catalog.

### 2. FRED/ALFRED vintages carried no base-year or per-vintage unit metadata

FRED rebases chained-dollar and index series at benchmark revisions, so each ALFRED
vintage is denominated in the base period current at that vintage (1992, 1996, 2009, 2017
dollars for GDPC1/GDPPOT). Every normalized record was nevertheless labelled with the one
current unit and carried no per-vintage metadata. A real-time level ratio of two such
series therefore silently mixed base years across the 1999, 2013 and 2023 benchmark
revisions, producing output gaps of +22.5% (1995Q4) and +16.1% (1999Q3).

`fred_macro_panel` and `fred_cpi` were rebuilt so every observation carries
`attributes.source_units` as published in that vintage, an applied `unit_multiplier`, and
`base_period` in both attributes and dimensions. The `unit` token is now per vintage:
GDPC1 carries eight chained-dollar tokens, INDPRO thirteen index tokens, CPIAUCSL two.
189 of 849 panel series change units across vintages.

**The scope of the contamination was narrower than first claimed, and the rerun proved
it.** A point-in-time frame takes, for each period, the latest vintage available at its
cutoff, and a vintage publishes its whole history on one base — so each frame is
internally base-consistent and the estimators read only within-frame ratios and growth
rates. Rerun with identical splits, `policy_rule`, `credit_growth` and `labor_demand`
reproduced *identical* parameters and verdicts. Only constructions that combine different
vintages across periods were actually affected, which in practice meant the monetary
family's first-release rows — and there the effect was large:

| Parameter | Contaminated | Clean |
| --- | --- | --- |
| `phi_pi` | 1.0089 | 0.3839 |
| `r_star` | −1.3625 | +0.2463 |
| `rho` | 0.9107 | 0.8547 |

`phi_pi` = 0.38 means the fitted rule does **not** satisfy the Taylor principle over
1995-2007 — the opposite of what the contaminated run implied.

### 3. TOTALSL was published in billions for some vintages and millions for others

Consumer credit outstanding changed scale mid-history in ALFRED. 11,612 of its 13,537
values were wrong by 1000× (1998-03: 1.3322 where it should be 1332.2). Fixed in the same
rebuild.

Its effect was smaller than its size suggests: log growth is scale-invariant within a
single-vintage frame, so `credit_growth`'s parameters and its `fail` verdict were
unchanged. What moved was the level in which its forecast error is reported, MAE 0.0525 →
52.507 — the same numbers, in billions rather than a mixed scale. This is a good example
of a serious data defect that does not change a conclusion, and it should not be reported
as though it did.

### 4. `default_hazard` contradicts itself between primary and substitute series

`default_hazard.fdic_laus_quarterly`, fitted on FDIC call reports plus BLS LAUS, passes
every criterion with `unemployment_sensitivity` = **+2.19**.

`default_hazard.fred_primary_realtime`, fitted on the series `requirements.json` actually
declares (DRCCLACBS and UNRATE with DFF), fails: `persistence` = 1.0038 breaches its
declared [0, 1] bound (a unit root in the delinquency logit) and
`unemployment_sensitivity` is **−1.26**.

Two attempts disagree about the *sign* of the mechanism. This is not a tie to be broken by
preferring the passing one. `default_hazard` must be read as **not validated** on its
declared series, and the substitute's pass must not be cited as validating the component.
Both attempts are retained with their verdicts.

### 5. Smaller findings, recorded rather than fixed

- **Unit tokens are not stable across rebuilds.** `fred_oil_price` went from `USD/barrel`
  to `USD_per_barrel` and RRSFS gained a `_cpi_adjusted` suffix, both with identical
  values. A loader pinned to a unit string then silently selects nothing, which is how
  `energy_purchasing` first failed to load. Affected loaders now leave the unit open.
- **`wm evidence-audit` aborts** in a tree where sample payloads were pruned: it
  dereferences every dataset's retained sample manifest and stops at the first missing
  one. `wm catalog` and `wm rights` are unaffected.
- **`wm sources` reports sample status only**, so it prints `not_acquired` for datasets
  that are fully acquired through `wm acquire`.
- **`wm calibration-status` gives a misleading error on an estimate artifact.**
  `data/calibration_reports` holds 50 validation reports and 50 estimates; handed one of
  the latter, the command reports `Validation report content does not match report_id`,
  which reads like corruption and is not. All 50 validation reports re-verify: their
  digests recompute and their declared criteria re-evaluate to the recorded verdict.
- **`bls_labor` omits the national CPS series.** A national unemployment rate has to be
  constructed from the 51 seasonally adjusted state series.
- **EIA weekly flows do not close the stock identity** (fitted `flow_scale` 0.56). That is
  the publisher's own adjustment term, not a pipeline error.
- **Capital expenditure is filed year-to-date only**, so discrete quarters must be
  recovered by differencing cumulative durations.

## What a reader should not conclude

- **Not that the system is validated.** One of 22 registry processes, `monetary_model`,
  meets its declared criteria. Twenty-two of twenty-four attempts fail. `coupled_economy`
  needs nine components: seven fail and two have not been re-run against the corrected
  panel. All eleven model families declare `validated: false`. See
  [calibration-status.md](calibration-status.md).
- **Not that `monetary_model` is a good model.** Its pass rests on forecast skill against
  persistence (p = 0.059 against a 0.10 threshold) and well-calibrated intervals. Its
  structural coefficients are not credible: `phi_pi` = 0.38 violates the Taylor principle.
- **Not that 1.31 billion records means coverage.** Record counts say what was downloaded
  and parsed. There is no coverage estimator. 52.3 million Companies House rows are
  complete for the UK and say nothing about anywhere else.
- **Not that the data is joined.** The machinery for defensible joins exists; the joined
  cross-domain panels mostly do not. `influence_model` is blocked because the
  lobbying/contribution/roll-call panel has not been built, even though all three inputs
  are published locally.
- **Not that the failures are provisional.** Four components have no forecast skill at all
  against a persistence baseline. Interval coverage fails repeatedly for a specification
  reason — Gaussian intervals from in-sample residual scale against fat-tailed series —
  that more data will not fix.
- **Not that fixing the base-year bug changed the results.** It changed one family's
  parameters materially and left the rest identical. Reporting it as a broad correction
  would overstate it in the other direction.
- **Not that any estimate reaches a simulation.** Most components' `hooks` entries in
  `worldmodel/estimation/requirements.json` describe a mechanism change that has not been
  implemented. An estimated parameter with no hook cannot influence a run.
- **Not that this data can be redistributed.** 75 of 107 published datasets require
  redistribution review and at least 25 carry an outright restriction. See
  [DATA_RIGHTS.md](../DATA_RIGHTS.md) and [remaining-concerns.md](remaining-concerns.md).
- **Not that 839 passing tests say anything about the world.** They establish software
  contracts. The suite passes in exactly the same tree where most models fail their
  holdouts.

## Where the detail lives

[calibration-status.md](calibration-status.md) (attempt by attempt),
[strategic-affordances-audit.md](strategic-affordances-audit.md) (current audit),
[remaining-concerns.md](remaining-concerns.md) (limits by area),
[full-acquisition.md](full-acquisition.md) (the acquisition contract),
[estimation-and-validation.md](estimation-and-validation.md) (the estimation layer),
[scale-benchmarks.md](scale-benchmarks.md) (measured performance),
[docs/data/](data/) (per-domain dataset conventions and overlaps).
