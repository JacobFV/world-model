# WS-E — uncertainty calibration (`interval_coverage`)

Started 2026-09-17. `interval_coverage` blocks **16** of the 35 pre-registered attempts in
[`docs/calibration-status.md`](../calibration-status.md), more than any other criterion, and
needs no new data. This file is the working record: every diagnosis, every change, the modeling
reason each change follows from, and the real verdict afterwards.

**Nothing here was widened until coverage passed.** Every change to an interval's width follows
from a stated defect — a scoring bug, heteroskedasticity across regimes, a revision component
missing from the residual, an equidispersion assumption — and is applied for that reason. Where
no principled fix exists the attempt is recorded as **still failing**, which is the intended
outcome of this method rather than a shortfall of it. No declared acceptance criterion or
pre-registered bound was relaxed.

## How a method was chosen, and what was allowed to choose it

The contract in [`docs/estimation-and-validation.md`](../estimation-and-validation.md) permits
selection on the validation window and forbids it on the holdout. So every diagnosis below rests
on one of three things, all available before the holdout is scored:

1. **The training window's own residuals** — for example the residual root-mean-square by
   decade, which measures heteroskedasticity directly.
2. **A selection-window backtest** (`train_end` → `validation_end`, evaluated at
   `validation_end`), which is the same view `validate_process` uses to select candidates.
3. **The publication process** — an annual benchmark, a rebasing, an index base change.

Where several candidate methods were admissible the declared selection rule is **lowest
validation-window CRPS**, a proper score that is blind to interval coverage. Selecting on
"coverage closest to 0.80" would be tuning to the criterion, so it was not used — and in
`interest_pass_through` the CRPS rule picks the candidate with the *worst* validation coverage of
the three trailing windows, which is the evidence that it was not.

## The two new code defects, and why each is a defect rather than a preference

### 1. A conditional input was graded against a later vintage (a scoring bug)

`rolling_origin_backtest` builds each forecast's design row from the *origin's* point-in-time
frame, then overwrites the declared conditional inputs at the target period with values from
the *evaluation* frame — the realized driver, which is the point of a conditional forecast.
But a design that reads the driver against its own lag reads one value from each vintage.

`labor_demand`'s design reads industrial production only as `log(output_t / output_{t-1})`.
INDPRO is an index the Federal Reserve rebases, and the published ALFRED vintages carry
thirteen index bases. Measured at the 2005-04 origin:

```
origin_value_at_anchor  118.527      (2005-03 INDPRO, vintage available 2005-04-15)
truth_value_at_anchor    95.3013     (2005-03 INDPRO, latest vintage at 2012-12-31)
ratio                     0.804047   <- the rebasing factor
mixed_vintage_growth     -0.2176     <- what the design row actually contained
consistent_growth        +0.000493   <- realized March-to-April growth
```

Every `labor_demand` v1 and v2 forecast was computed with a fabricated 22% collapse in
industrial production in its design row. That is why their bias ran −2,172 thousand payrolls
over 2005-2009 and decayed toward zero as the origins approached the evaluation cutoff: the
contamination is the base gap between the origin's vintage and the truth's, and that gap
closes as the two converge.

**The fix.** Components declare `conditional_rebase` per input. `ratio` (and `difference`)
carries the realized value onto the origin's vintage using the two frames' overlap at the
anchor period. This preserves the realized *movement* exactly — the movement is the
conditioning information the contract already declares — and adds nothing the origin did not
have, so no pre-origin value changes and the leakage audit stays meaningful. The default
`none` is today's behaviour.

**Blast radius, measured rather than assumed.** All seven conditional inputs in the catalog
were checked with the same instrument:

| Component | Conditional input | Overlap factor at the anchor | Declaration |
| --- | --- | --- | --- |
| `labor_demand` | `output` (INDPRO) | **0.8040** | `ratio` |
| `energy_purchasing` | `crude_price`, `policy_rate` | 1.000000, 1.000000 | `none` |
| `deposit_growth` | `policy_rate` (DFF) | 1.000000 | `none` |
| `interest_pass_through` | `policy_rate` (DFF) | 0.9846 / 1.0068 / 1.0000 — 0.003pp on a monthly mean of daily vintages | `none` |
| `policy_rule` | `price_index` (CPI) | 1.0017 — mild, and noted below | `none` |
| `policy_rule` | `real_gdp`, `potential_gdp` | 1.1286, 1.1116 — but both are read at the target period from one vintage, so the gap is internally consistent | `none` |
| `default_hazard` | `unemployment_rate` | 1.0142 / 0.9857 — read as a level at the target period, never as a ratio | `none` |

So exactly one component was affected. `policy_rule`'s inflation term is mildly contaminated
(`price_index` at *t* from the evaluation vintage against *t−4* from the origin's, giving
0.678%/quarter where the consistent figure is 0.504%/quarter); CPI revisions are small and
this is recorded as a known limitation rather than silently fixed, because the design reads
the CPI as a four-quarter ratio and rebasing it onto the origin would move a term the
attempt's declared inflation measure depends on. It is not the reason `policy_rule` fails.

### 2. Conflict counts were scored Poisson while the family simulates negative binomial

`_conflict_forecast` set `sd = sqrt(mean)`. The Hawkes recursion produces a conditional
*mean*; `sqrt(mean)` additionally asserts equidispersion. The family never asserted that:
`worldmodel.models.conflict.simulate` draws counts as
`negative_binomial(rng, lambda, dispersion)`, and `dispersion` (nb2_alpha) is a declared
family parameter with bounds `[0, 50]`. `fit_hawkes` simply never estimated it, so it stayed
at its default of `0` — the score was Poisson while the simulator was negative binomial.

Measured over the fit-plus-validation window alone (4,300 one-step country-month forecasts,
holdout untouched): the Poisson Pearson dispersion `mean[(N−λ)²/λ]` is **35.9** against the
1.0 the score assumed, and the NB2 moment estimate of α is **0.041**.

`fit_hawkes` now estimates α from its own residuals and the forecaster uses
`Var = λ + α λ²`. **α = 0 reproduces `sqrt(mean)` exactly**, so this is not a widening
factor: it is a parameter the family already declared, measured instead of defaulted. The
predictive *shape* is unchanged (still Gaussian around the intensity) because changing it
would be a second, separately arguable change.

## Where a principled fix does not exist, and why

Four of the sixteen cannot be fixed by any interval method, and the reason is worth stating
precisely rather than as "not enough data".

- **`population_growth_rate.census_pep`** — 4 holdout forecasts. `minimum_test_forecasts`
  (4 < 8) fails whatever the interval does, and PEP publishes only 2010-2024, so at most 7
  origins can ever exist. Worse, the selection window yields **zero** forecasts (under the
  strict policy nothing is visible by 2018-12-31, because the published PEP vintages start in
  2020), so any interval choice would have to be made on the holdout itself — which is
  tuning. And the observed coverage is 2 of 4: a Wilson 95% interval on 2/4 runs
  **[0.150, 0.850]**, so the number does not distinguish a calibrated interval from a broken
  one. Lengthening the series is WS-D's problem, not WS-E's.
- **`deposit_rate_pass_through.fred_realtime`** — 16 holdout forecasts against a declared 24,
  so `minimum_test_forecasts` fails structurally (SNDR starts 2021-04). Its in-sample residual
  scale is *correct*: 0.00573 against a measured 2020s residual rms of 0.00540. The
  under-coverage (0.438, Wilson 95% **[0.231, 0.668]**) comes from the point forecast being
  worse than persistence (MAE 0.0131 against 0.0056), which no predictive distribution repairs.
- **`cash_balance.sec_companyfacts`** (5 issuers) — the selection window yields **0 or 1**
  usable forecast per issuer (3-4 origins skipped each because a required concept was not yet
  filed), so there is no pre-holdout evidence on which to declare anything. The recorded
  diagnosis stands — coverage 1.00 on three issuers because a level-sigma estimated on noisy
  quarterly differences is far too wide — but coverage is not the binding defect:
  `beats_persistence_dm` fails on all five, `cash_conversion` is statistically
  indistinguishable from zero everywhere, and two issuers breach declared bounds.
- **`regional_model.cbp_state_sectors`** (and `_v2`, `_v3`) — with a single holdout year all
  51 forecasts share one draw of the common component, so `interval_coverage` here is one
  Bernoulli trial and not a coverage estimate at all. v1's 51/51 has a Wilson 95% interval of
  **[0.930, 1.000]** and v2's 50/51 **[0.897, 0.997]**; neither is a measurement of 0.80.
  `regional_model.qcew_state_sectors` exists precisely to make the criterion evaluable, and it
  is where the regional question is settled.

## The clearest test that the criterion was not gamed: regional

`regional_model.qcew_state_sectors` (v1, `per_unit_year_mean`) fails `interval_coverage` from
below at 0.597. I registered `per_unit_year_draw` to widen the common term, on the a-priori
argument that the forecast error contains next year's own year effect, so the predictive
variance should be `between × (1 + 1/Y)` and not `between / Y`.

Then I measured, on the selection window, under the rule declared before any WS-E run:

| method (212 forecasts, 2015-2018, nominal 0.80) | coverage | mean sd | **CRPS** |
| --- | --- | --- | --- |
| `per_unit_year_mean` (what v1 already declares) | 0.6321 | 0.01371 | **0.010294** |
| `per_unit_year_draw` | 0.9340 | 0.02496 | 0.010591 |
| `pooled_year_draw` | 0.9340 | 0.02541 | 0.010857 |

**A wider interval that would have passed was available, and I did not take it.**
`per_unit_year_draw` reaches 0.9340 in validation, comfortably inside 0.80 ± 0.15; run on the
holdout it reaches **0.7579 — a pass**. It loses on *validation* CRPS — a proper score — so the
declared rule selects `per_unit_year_mean`, which is what v1 already uses, and `regional_model`
therefore **still fails** on the long panel.

**The rule was wrong here, by its own metric, and that is the more interesting result.** On the
holdout, `per_unit_year_draw`'s CRPS is **0.016269** against `per_unit_year_mean`'s **0.016934** —
the rejected candidate is better on coverage *and* on the proper score, once the answer is
visible. That is knowable only after the fact and does not license selecting it. The reason is
that the validation window (2015-2018) is entirely pre-pandemic while the holdout (2019-2024) is
not, so validation could not price the regime the holdout contains.

See the next section: this is one of two places the selection rule itself failed.

The a-priori argument was also wrong, and the evidence refuting it needed no holdout. The
shift-share shock is built from *realized* other-region industry employment at the target year,
a declared conditional input, so `log(others_after / others_before)` already contains the target
year's common component; what remains to forecast is closer to an estimated level than to a
fresh draw. On the synthetic shift-share panel in `tests/test_estimation_intervals.py` — 16
years, 30 regions, a genuine common year shock of sd 0.004, region noise spanning twentyfold —
`per_unit_year_mean` attains coverage **0.8000** against a nominal 0.80 while
`per_unit_year_draw` over-covers at 0.8417 and `pooled_year_draw` at 0.8667. The fifth wave's
choice was right and my correction to it was not a correction.

Both attempts were still run and published, as **rejected candidates on the record** rather
than as the answer, and the rejection was written into the plan before either holdout was
scored (`run_note` on `regional_model.qcew_state_sectors_v2`).

What actually blocks the long regional panel is not a variance component. v1's coverage by year
is 0.85 (2019), **0.04 (2020)**, 0.38 (2021), 0.45 (2022), 0.98 (2023), 0.89 (2024): outside the
pandemic the intervals are roughly right, and the 2020-2022 common component is not something
nineteen pre-pandemic year effects can price. `no_revision_leakage` also fails, and not for any
reason an interval method reaches: QCEW publishes no vintages, so its row dates are not
information times. (I first wrote that as "no published employment source in this catalog carries
vintages". That was true of CBP and QCEW and wrong in general — see the correction under Counts,
below: WS-B built one the same day.)

## The selection rule failed to generalize on two of six choices

The rule declared before any WS-E attempt ran — lowest validation-window CRPS — made a
non-trivial choice on six attempts. It generalized on four and **failed on two**, and both
failures have the same cause, in opposite directions.

| Attempt | Validation window | What the rule chose | What the holdout said |
| --- | --- | --- | --- |
| `labor_demand` | 2005-2012, spanning the financial crisis and its benchmark revisions | **add** the PAYEMS revision component (validation CRPS 431.0 vs 440.1; coverage 0.8105 vs 0.600) | worse on both: CRPS 431.0 vs **405.1**, coverage **0.958 fail** vs 0.874 pass |
| `regional_model.qcew_state_sectors` | 2015-2018, entirely pre-pandemic | **reject** `per_unit_year_draw` (validation CRPS 0.010591 vs 0.010294) | the rejected one is better on both: CRPS **0.016269** vs 0.016934, coverage **0.758 pass** vs 0.597 fail |

The cause in both cases is that **the validation window's volatility regime differs from the
holdout's**. `labor_demand`'s validation window contains large PAYEMS benchmark revisions and its
holdout does not, so a revision component looked necessary and then over-covered.
`qcew_state_sectors`'s validation window is pre-pandemic and its holdout contains 2020-2022, so a
narrow common term looked sufficient and then under-covered.

**Neither was re-selected on holdout evidence.** Choosing a predictive distribution after seeing
the holdout is precisely what pre-registration exists to prevent, and doing it here would
manufacture two extra `interval_coverage` passes out of nothing. `labor_demand_v4` keeps its
failing coverage and `qcew_state_sectors_v2` is published as a rejected candidate.

This is a finding about the protocol, not about these two attempts, and it generalizes past WS-E:
**any rule that selects a predictive distribution on a validation window is only as good as that
window's coverage of the holdout's regimes, whatever score the rule uses.** A proper score does
not rescue it — CRPS is what failed here. Two fixes are worth pre-registering next: a validation
window chosen to span the holdout's regimes rather than merely to precede it, and a rule that
penalizes a candidate whose validation-to-holdout score gap is large (which is measurable on
earlier waves without touching any current holdout). Neither was retrofitted here.

## A criterion I think is wrong, argued rather than relaxed

`interval_coverage` is evaluated as `|coverage − 0.80| ≤ tolerance` against the raw count of
forecasts, with no reference to how many of those forecasts are independent. The record
already knows this is a problem — `credit_growth.fred_realtime_v3` passes at 0.773 while its
coverage is clustered by revision episode, and the fifth-wave notes say so — but the criterion
cannot see it. Where forecasts share a common shock (51 states in one year; 141 months
spanning perhaps a dozen benchmark revisions; 318 state-years whose 2020 collapse is one
event) the effective sample is far smaller than the count, and a pass or a fail at *n* = 51 or
*n* = 141 carries much less information than the same number at *n* = 141 independent draws.

A defensible replacement would test coverage against a confidence interval computed from the
*effective* number of independent observations — a cluster-robust binomial test, clustering on
the unit the common shock acts through (year for the regional panels, revision episode for the
benchmarked series). **I have not changed the criterion**, and no attempt below was passed or
failed by anything other than the declared rule. This is recorded as an argument for a future
pre-registered change, and the attempts that turn on it say so next to their verdicts.

## The diagnoses, one per attempt

### `interest_pass_through.fred_realtime` — miscalibration, heteroskedasticity across regimes

The only failing criterion (coverage 0.988) on an attempt that halves persistence's error at
p = 0.0011 — the single most valuable target in the sixteen. The predictive scale is an average
across monetary-policy regimes rather than a forecast of the current one. Residual root mean
square by decade at the validation cutoff, against a flat in-sample scale of **0.2217**:

| 1950s | 1960s | 1970s | 1980s | 1990s | 2000s | 2010s |
| --- | --- | --- | --- | --- | --- | --- |
| 0.1560 | 0.1796 | 0.2539 | **0.4186** | 0.1142 | 0.0816 | **0.0416** |

A tenfold spread, with the flat scale set mostly by the Volcker era — which enters every
forecast because a 2005 ALFRED vintage of DPRIME publishes its history back to 1955. Visible
before the holdout: in the selection window (2013-2017, 59 forecasts) the default over-covers
at **1.000** with mean predictive sd 0.2259 against a realized RMSE of 0.0498. In-sample
standardized errors have kurtosis 6.0 — what a rate moving in discrete 25bp steps looks like,
a spike at zero change with occasional jumps.

Declared: `empirical_trailing`, window 120 months, 40 nodes. Validation-window CRPS over the
declared grid:

| method | w=36 | w=60 | w=120 |
| --- | --- | --- | --- |
| `gaussian_in_sample` | — | 0.05714 | — |
| `gaussian_trailing` | 0.02954 | 0.02945 | 0.02944 |
| `student_t_trailing` | — | 0.03023 | — |
| `empirical_trailing` | 0.02495 | 0.02497 | **0.02207** |

The rule picks `empirical_trailing` at w=120, whose validation coverage (0.9153) is the
**worst** of the three trailing windows (0.8983 at 36, 0.8814 at 60). That is the check that
CRPS and not coverage did the selecting. No coefficient-uncertainty term was added (746
residuals against 5 coefficients make `x'Vx` negligible) and no revision term (DPRIME is not
revised) — neither is part of the diagnosis.

### `labor_demand.fred_realtime` / `_v2` — a scoring bug, then a revision component

Diagnosed as the conditional-input rebasing bug above. The correction alone (`_v3`) moves the
selection window from MAE 2,194.6 to **578.1** thousand payrolls, bias from −2,171.8 to +280.8,
and DM against persistence from **1.0000 to 0.0865**.

That leaves a second, separate defect, which is `credit_growth`'s: the scored actual is the
latest vintage while the forecast is anchored on the real-time level, so the scored error
carries the anchor's benchmark revision, which is in no in-sample residual. PAYEMS log revisions
from first release to latest have root mean square **0.00458** for 2000s periods against a model
residual scale of **0.00304** — the revision is larger than the residual it is added to. In the
selection window the corrected default under-covers at 0.600 (mean sd 414.5 against realized
RMSE 767.2, standardized errors rms 1.86).

Declared for `_v4`: the in-sample residual scale plus the revision component, window 120 mature
periods, maturity 24 months (a CES period is through both its February level benchmark and the
following year's seasonal-factor revision only after two Februaries; ten years keeps the
estimate inside the current methodology, whose 1950s log-revision rms is 0.0185 against 0.0046
in the 2000s). Validation-window CRPS:

| candidate | coverage | mean sd | CRPS |
| --- | --- | --- | --- |
| default | 0.600 | 414.5 | 440.1 |
| **+ revision** | 0.8105 | 659.2 | **431.0** |
| `gaussian_trailing`(60) | 0.3579 | 206.1 | 489.5 |
| `gaussian_trailing`(60) + revision | 0.7263 | 552.5 | 437.5 |
| `gaussian_trailing`(120) + revision | 0.7263 | 545.0 | 437.6 |
| `empirical_trailing`(60) + revision | 0.6737 | 519.3 | 434.9 |
| `empirical_trailing`(120) + revision | 0.6526 | 471.5 | 437.0 |

Note what the rule does here: the heteroskedasticity argument that fixed
`interest_pass_through` is *also* true of PAYEMS (residual rms 0.0065 in the 1940s against
0.0011 in the 1990s) but points the other way — a trailing scale **narrows** this interval and
drives validation coverage down to 0.358. It is rejected. The same reason does not license the
same change everywhere.

### `policy_rule.fred_realtime` / `_v2` — miscalibration, and a change the selection window did not demand

Coverage 1.000 alongside a `beats_persistence_dm` failure at p = 0.673. Residual rms by decade
against a flat 0.8520: 0.4847 / 0.4350 / 1.0824 / **1.4652** / 0.3592 / 0.5720 / **0.2227** — a
sevenfold spread, again set by the Volcker era, which a 1996 FEDFUNDS vintage publishes back to
1954.

**The honest caveat, registered in advance:** the selection window (2005-2012, 31 forecasts) does
*not* reject the default — its coverage there is 0.871, because that window contains the
financial crisis, when policy-rate errors genuinely were large (2009 RMSE 1.489 against 0.161 in
2005). So the evidence for changing anything is the residual-scale spread, not a validation
failure. Validation CRPS: `gaussian_in_sample` 0.38901; `gaussian_trailing` 0.36453 (w=40),
0.37603 (w=20); `empirical_trailing` 0.36553 (w=40); `+revision` 0.38901 — *identical* to the
default, which is independent confirmation that FEDFUNDS is unrevised and that no revision
component is justified here. Declared: `gaussian_trailing`, window 40 quarters.

### `energy_purchasing.fred_realtime` — two defects, a structural break, and a fix that cannot reach the verdict

Coverage 0.627 alongside `beats_persistence_dm` (p = 0.756) and a bounds failure (both response
parameters carry the wrong sign for the declared mechanism). Two measurable interval defects:
RRSFS log revisions have root mean square **0.0154** (2010s) and 0.0339 (2000s) with systematic
means of −0.0100 and −0.0313, against a model residual scale of **0.0087** — about twice the
residual; and mild heteroskedasticity (0.0074 / 0.0113 / 0.0052 against the flat 0.0087). The
selection window agrees: 0.661 coverage, mean sd 1,705 against realized RMSE 2,117.

Declared: `empirical_trailing` w=60 ⊕ revision (w=120, maturity 24 — the Census annual retail
trade survey benchmarks a month at the following year's revision and the CPI deflator is itself
revised). Validation CRPS: default 1198.1; +revision 1199.9; `gaussian_trailing`(60) 1232.7;
`empirical_trailing`(60)+revision **1144.5**. `energy_purchasing.fred_realtime_v2` was
registered in the fourth wave but never run, so v1 is the comparison.

### `conflict_model.ucdp_monthly` — equidispersion, which the family never assumed

Diagnosed above: Poisson Pearson dispersion **35.9** measured over 4,300 fit-plus-validation
forecasts, against the 1.0 the score assumed, while the family's own simulator draws negative
binomial. Corrected dispersion, unchanged shape.

## Regression check: no passing attempt moved

Both code changes are meant to be no-ops wherever they do not apply. Five earlier
attempts were re-run from a pristine copy of the package and **reproduce their recorded report
digests exactly** — not just their printed metrics, the content-addressed digest of the whole
report:

| Attempt | Recorded report id | Re-run | |
| --- | --- | --- | --- |
| `default_hazard.fdic_cps_quarterly` (has a conditional input, declares `none`) | `a29e4a61e35c…` | `a29e4a61e35c…` | **identical** |
| `inventory_balance.eia_weekly_v2` (uses `empirical_trailing`) | `ef9e071705b1…` | `ef9e071705b1…` | **identical** |
| `credit_growth.fred_realtime_v3` (uses `interval_revision`) | `8ecb67bb78f9…` | `8ecb67bb78f9…` | **identical** |
| `regional_model.cbp_state_sectors_v2` (uses `per_unit_year_mean`) | `9c97d892752c…` | `9c97d892752c…` | **identical** |
| `regional_model.qcew_state_sectors` (uses `per_unit_year_mean`) | `91090501d2aa…` | `91090501d2aa…` | **identical** |

One operational note for whoever runs the suite next: `worldmodel.provenance.capture_code`
hashes every `.py` under the package root and compares against the import-time snapshot, so any
*other* agent editing any file under `worldmodel/` during a `calibrate-all` run aborts every
publish with "Implementation changed after import". Two of my runs died that way. The fix used
here was to copy `worldmodel/` to a scratch directory, symlink `data/` back to the real store,
and run `python3 -m worldmodel calibrate-all` from the copy — the published artifacts land in
the real store and the provenance snapshot is stable.

## Summary table — the 16 attempts

Rows marked *fifth wave* were diagnosed and fixed on 2026-09-16 and are re-reported here
because they are part of the sixteen. "Verdict" is the overall attempt verdict; the
`interval_coverage` column is what this workstream was aimed at.

The **modeling reason** each change follows from is given in full after the table, one entry per
distinct change, because that is the column that has to bear weight: a change to an interval's
width is only legitimate if it follows from a stated defect rather than from the threshold.

| # | Attempt | Diagnosis | Change | `interval_coverage` | Overall verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | `inventory_balance.eia_weekly` | miscalibration — static scale vs moving volatility | `empirical_trailing` w=52 ⊕ x'Vx (*fifth wave* `_v2`) | 0.640 → **0.764 pass** | **pass** (all six) |
| 2 | `credit_growth.fred_realtime` | **scoring bug** — graded against a revised vintage | revision variance component (*fifth wave* `_v3`) | 0.596 → **0.773 pass** | **pass** (all six) |
| 3 | `credit_growth.fred_realtime_v2` | same | same | 0.596 → **0.773 pass** | **pass** (all six) |
| 4 | `interest_pass_through.fred_realtime` | miscalibration — heteroskedasticity across policy regimes | `empirical_trailing` w=120 (`_v2`) | 0.988 → **0.735 pass** | **pass** (all six) |
| 5 | `labor_demand.fred_realtime` | **scoring bug** — mixed-vintage conditional input | `conditional_rebase: ratio` (`_v3`) | 0.245 → **0.874 pass** | fail (`beats_persistence_dm` p = 0.160) |
| 6 | `labor_demand.fred_realtime_v2` | same | same | 0.245 → **0.874 pass** | fail (same) |
| 7 | `policy_rule.fred_realtime` | miscalibration — heteroskedasticity across policy regimes | `gaussian_trailing` w=40 (`_v3`) | 1.000 → **0.660 pass** (by 0.0096) | fail (`beats_persistence_dm` p = 0.673) |
| 8 | `policy_rule.fred_realtime_v2` | same | same | 1.000 → **0.660 pass** | fail (same) |
| 9 | `energy_purchasing.fred_realtime` | both — revision component missing, plus mild heteroskedasticity | `empirical_trailing` w=60 ⊕ revision (`_v3`) | 0.627 → **0.867 pass** | fail (DM p = 0.756; bounds on `energy_response`, `rate_response`) |
| 10 | `conflict_model.ucdp_monthly` | miscalibration — Poisson equidispersion, Pearson dispersion 35.9 | NB2 dispersion the family already declares (`_v2`) | 0.596 → **0.719 pass** | fail (DM p = 0.583; `no_revision_leakage` structural) |
| 11 | `regional_model.cbp_state_sectors` | miscalibration, but **not measurable** — one Bernoulli trial | none legitimate | 1.000, not a coverage estimate | fail |
| 12 | `regional_model.cbp_state_sectors_v2` | same | none legitimate; `_v3` run as a control and fails **worse** | 0.980 → 1.000 (`_v3`), not a coverage estimate | fail |
| 13 | `regional_model.qcew_state_sectors` | **neither** — a structural break (2020-2022) | none: the proper score rejected the wider interval (`_v2` reached 0.758 and is **not** claimed) | 0.597 **still fails** | fail |
| 14 | `population_growth_rate.census_pep` | not diagnosable — 4 holdout and **0** validation forecasts | none legitimate | 0.500 **still fails** | fail (`minimum_test_forecasts` 4 < 8) |
| 15 | `deposit_rate_pass_through.fred_realtime` | point forecast worse than persistence; residual scale is correct | none legitimate | 0.438 **still fails** | fail (`minimum_test_forecasts` 16 < 24) |
| 16 | `cash_balance.sec_companyfacts` (5 issuers) | miscalibration, but **0-1 validation forecasts** per issuer | none legitimate | 1.00 on 3 issuers, **still fails** | fail 5/5 (`beats_persistence_dm` all five) |

### The modeling reason behind each change

Seven distinct changes were made across the sixteen rows. Each is stated as the defect first and
the change second, because that is the order in which they were decided.

1. **`inventory_balance` — `empirical_trailing` w=52 ⊕ `x'Vx`** *(fifth wave, re-reported)*.
   Reason: **heteroskedasticity across regimes plus fat tails.** One static scale was applied
   against error volatility that moves by a factor of three between petroleum-market regimes, and
   standardized errors were leptokurtic and left-skewed. A trailing window estimates the current
   regime's scale; empirical quantile nodes take the tail shape from the data instead of assuming
   a Gaussian. Window 52 weeks = one petroleum stock cycle.

2. **`credit_growth` — in-sample residual scale ⊕ revision component** *(fifth wave,
   re-reported)*. Reason: **the scored error contains a quantity no in-sample residual measures.**
   Under the strict real-time policy the forecast is anchored on the TOTALSL level available at
   the origin while the actual is the latest vintage, so the scored error carries the anchor's
   later benchmark revision. The revision component is the dispersion of revisions the publisher
   had *already made by the origin* — an observable, not a free parameter.

3. **`interest_pass_through` — `empirical_trailing` w=120.** Reason: **heteroskedasticity across
   monetary-policy regimes, plus discrete-step tails.** The flat in-sample scale (0.2217) is an
   average over a tenfold spread of decade residual scales (0.0416 in the 2010s, 0.4186 in the
   1980s), because a 2005 ALFRED vintage of DPRIME publishes back to 1955. The prime rate moves
   in 25bp steps, so one-step errors are a spike at zero with occasional jumps — kurtosis 6.0,
   which is why the shape is taken empirically rather than assumed. Window 120 months so the
   window spans a full tightening-and-easing cycle and gives 40 quantile nodes some tail
   resolution.

4. **`labor_demand` — `conditional_rebase: output = ratio`.** Reason: **a movement is only a
   movement when both endpoints come from one vintage.** This is not an interval change at all —
   it is a correction to the scored forecast. The design reads industrial production solely as
   `log(output_t / output_{t−1})`; INDPRO is rebased and the ALFRED vintages carry thirteen index
   bases, so taking the numerator from the evaluation vintage and the denominator from the
   origin's made that ratio a rebasing factor. Carrying the realized value onto the origin's base
   through the anchor-period overlap preserves the realized growth exactly and adds no
   information the origin lacked.

5. **`labor_demand` — plus the PAYEMS revision component (`_v4`).** Reason: **same as
   `credit_growth`'s** — the scored actual is the latest vintage while the forecast is anchored on
   the real-time level, and PAYEMS log revisions (rms 0.00458 for 2000s periods) exceed the model
   residual scale (0.00304). Maturity 24 months because a CES period is through both its February
   level benchmark and the following year's seasonal-factor revision only after two Februaries;
   window 120 months to stay inside the current benchmark methodology. **This change did not work
   on the holdout** and is recorded as such.

6. **`policy_rule` — `gaussian_trailing` w=40 quarters.** Reason: **heteroskedasticity across
   monetary-policy regimes**, identical in form to `interest_pass_through`: flat scale 0.8520
   against a sevenfold decade spread (0.2227 in the 2010s, 1.4652 in the 1980s). No empirical
   shape component, because 31 validation forecasts cannot estimate 40 quantile nodes; no revision
   component, because FEDFUNDS is not revised — and that was verified rather than assumed, by
   running the revision component and observing it reproduce the default's numbers exactly.

7. **`energy_purchasing` — `empirical_trailing` w=60 ⊕ revision component.** Reason: **both of the
   above at once.** The revision half: RRSFS log revisions (rms 0.0154 in the 2010s, systematic
   mean −0.0100) are about twice the model residual scale of 0.0087, and the real-time anchor is
   scored against the revised actual. The heteroskedasticity half: decade residual scales 0.0074 /
   0.0113 / 0.0052 against the flat 0.0087. Maturity 24 months because the Census annual retail
   trade survey benchmarks a month at the following year's revision and the CPI deflator behind
   the real series is itself revised.

8. **`conflict_model` — NB2 predictive dispersion.** Reason: **the score asserted equidispersion
   that the family itself never assumed.** `sqrt(λ)` is the standard deviation of an equidispersed
   Poisson count; the measured Pearson dispersion over the fit-plus-validation window is 35.9, and
   `worldmodel.models.conflict.simulate` already draws counts as
   `negative_binomial(rng, λ, dispersion)` with `dispersion` a declared family parameter that
   `fit_hawkes` simply never estimated. Estimating it aligns the scored predictive with the
   declared mechanism; `α = 0` reproduces the old behaviour exactly, so it is a measurement rather
   than a widening factor.

9. **`regional_model` — `per_unit_year_draw`, and why it was *not* adopted.** The proposed reason
   was that the forecast error contains next year's own common shock, so its predictive variance
   should be `between × (1 + 1/Y)` rather than `between / Y`. **That reason is wrong for this
   design**, and the evidence needed no holdout: the shift-share shock is built from realized
   other-region employment at the target year, so it already carries the target year's common
   component. On a synthetic panel with a genuine common shock, `per_unit_year_mean` attains
   0.8000 against a nominal 0.80 while `per_unit_year_draw` over-covers at 0.8417. The method was
   added to the code (WS-B's `ces_sae_realtime_v2` uses it) but **not adopted for either regional
   attempt**, and the declared selection rule rejected it on validation CRPS.

**No change was made to any of the remaining rows** — `population_growth_rate`,
`deposit_rate_pass_through`, `cash_balance` and the two CBP rows — because no defect could be
diagnosed from pre-holdout evidence. Those are recorded as still failing.

### Counts

The sixteen are *rows of the summary table*, and several rows are the same component at
different data versions (`credit_growth` v1 and v2; `labor_demand` v1 and v2; `policy_rule` v1
and v2; `cbp_state_sectors` v1 and v2). Both counts are given, because the row count is what
"16" refers to and the defect count is what was actually diagnosed.

**By cause:**

| Cause | Rows | Distinct defects |
| --- | ---: | --- |
| **Scoring bug** — a real-time forecast graded against a later vintage | **4** | **2**: `credit_growth`'s revision-vintage grading (fifth wave) and `labor_demand`'s mixed-vintage conditional input (**found here**) |
| **Genuine miscalibration** — the predictive distribution is the wrong width or shape | **6** | 5: `inventory_balance`, `interest_pass_through`, `policy_rule`, `conflict_model`, `cash_balance` |
| **Both** | **1** | `energy_purchasing` (a missing revision component *and* heteroskedasticity) |
| **Neither** — not an interval defect, or not measurable | **5** | `qcew_state_sectors` (2020-2022 structural break), `cbp_state_sectors` ×2 (one Bernoulli trial), `population_growth_rate` (4 holdout / 0 validation forecasts), `deposit_rate_pass_through` (the point forecast, not the interval) |

**The four numbers, stated plainly:**

| Question | Answer |
| --- | ---: |
| How many of the 16 were **scoring bugs**? | **4 rows / 2 distinct defects** (one found here) |
| How many were **genuine miscalibration**? | **6 rows / 5 distinct defects**, plus **1 row that is both** |
| How many were **neither** (no interval defect to fix)? | **5 rows** |
| How many **`interval_coverage` failures are now resolved**? | **10 of 16 rows** (3 fifth wave, **7 new here**) |
| How many **attempts now pass every declared criterion**? | **4 rows / 3 distinct attempts** (**1 added by WS-E**) |
| How many **still fail overall**? | **12 of 16 rows** |

**By outcome, in more detail:**

- **`interval_coverage` now passes on 10 of the 16 rows.** Three were the fifth wave's
  (`inventory_balance`, `credit_growth` ×2). Seven are new here: `interest_pass_through`,
  `labor_demand` ×2, `policy_rule` ×2, `energy_purchasing`, `conflict_model`.
- **Rows that now pass every declared criterion: 4**, which is **3 distinct attempts**:
  `inventory_balance.eia_weekly_v2` and `credit_growth.fred_realtime_v3` (fifth wave) and
  **`interest_pass_through.fred_realtime_v2`, the one WS-E added**.
- **Still failing overall: 12 of the 16 rows**, and that is the honest headline. Fixing the
  uncertainty of a model does not make the model good. On 6 of those 12 rows
  `interval_coverage` is no longer the blocker, which is the useful part: the question moves from
  "is the uncertainty wrong?" to "is there any skill?", and for five components the answer turns
  out to be no. What blocks them now:

| Blocking criterion | Rows still failing on it |
| --- | --- |
| `beats_persistence_dm` | `labor_demand` ×2 (p = 0.160), `policy_rule` ×2 (0.673), `energy_purchasing` (0.756), `conflict_model`, `cash_balance` (5 issuers) |
| `parameters_within_declared_bounds` | `energy_purchasing`, `cash_balance` |
| `no_revision_leakage` | `conflict_model`, both regional panels — no interval method reaches it; see the correction below on what "structural" turned out to mean (WS-A and WS-B own these) |
| `minimum_test_forecasts` | `population_growth_rate` (4 < 8), `deposit_rate_pass_through` (16 < 24) — **structural**, WS-D owns these |
| `interval_coverage` | `qcew_state_sectors` (0.597), `cbp_state_sectors` ×2, `population_growth_rate`, `deposit_rate_pass_through`, `cash_balance` ×3 issuers, `labor_demand_v4` |

**No process became validated.** `coupled_economy` needs nine components; WS-E adds one more
passing (`interest_pass_through`) but `labor_demand`, `policy_rule`, `energy_purchasing`,
`deposit_growth`, `deposit_rate_pass_through`, `demand_price_elasticity`, `price_adjustment` and
the declared `default_hazard` still fail. `regional_model`, `population_growth`,
`investment_cash_flow` and `conflict_model` are unchanged in status.

### Correction: "structural" was too strong about regional revision leakage

Written above and in the fifth wave's notes: *no published employment source in this catalog
carries vintages, so `regional_model` cannot pass `no_revision_leakage` whatever the intervals
do.* That was true of CBP and QCEW, which is what it was measured on, and **it was wrong as a
general claim** — WS-B built `fred_state_employment_vintages` from archived CES state-and-area
releases on the same day, and `regional_model.ces_sae_realtime` **passes `no_revision_leakage`**.
The correction is a scoping one: the criterion is unreachable *on CBP and QCEW*, not in principle.

Two things in that result belong here rather than only in WS-B's log. Their real-time panel also
**passes `interval_coverage`** (0.654 with `per_unit_year_mean`, 0.686 with `per_unit_year_draw` —
both inside 0.80 ± 0.15, and both only just), so `per_unit_year_draw`, the method added here and
*not* adopted for either of my regional attempts, is used there. And on a genuinely real-time
panel the shift-share mechanism **loses its skill**: `ces_sae_realtime` fails both
`beats_persistence_dm` and `beats_year_effect_only_dm`, where the retrospective CBP and QCEW
panels beat the mechanism-off baseline at p = 4.1e-06 and 1.2e-04. So the strong regional result
recorded under my `qcew_state_sectors_v2` entry — p = 4.3e-10 against persistence — should be read
as a result about a panel whose row dates are not information times.

## Per-attempt verdicts, with the criteria that passed

### `interest_pass_through.fred_realtime_v2` — **pass, all six criteria**

The one attempt WS-E converts into a pass. Report `0ab6414b3656…`, artifact `60dda0c3334b…`,
83 holdout forecasts (2018-2024), 0 skipped origins.

```
minimum_test_forecasts             PASS   83                        (>= 24)
beats_persistence_dm               PASS   p = 0.001111              (<= 0.10, lower loss)
interval_coverage                  PASS   0.7349                    (0.80 +/- 0.15)
parameters_within_declared_bounds  PASS   []
no_timing_leakage                  PASS   0 selection, 0 test violations
no_revision_leakage                PASS   []
```

| Metric | v1 (`gaussian_in_sample`) | v2 (`empirical_trailing` w=120) |
| --- | --- | --- |
| 80% interval coverage | 0.9880 **fail** | **0.7349 pass** |
| mean interval width | 0.5558 | 0.1356 |
| CRPS | 0.06375 | **0.04075** |
| MAE / RMSE | 0.06283 / 0.08952 | identical |
| DM p vs persistence | 0.001111 | identical |

Parameters are unchanged (`pass_through` 0.9266, `impact_pass_through` 0.5291, `spread` 0.02565,
`adjustment_speed_per_month` 0.04499); only the predictive distribution moved, so v1 and v2 are
directly comparable. CRPS — the proper score, which the criterion does not test — improves by
36%, so this is a better forecast distribution and not merely a compliant one. `coupled_economy`
needs nine components and this is one more of them passing; the process stays unvalidated.

### `labor_demand.fred_realtime_v3` / `_v4` — coverage fixed, DM still fails

`_v3` (the scoring correction alone): report `894830be6783…`, artifact `1968423d9daf…`, 143
holdout forecasts, 0 skipped.

```
minimum_test_forecasts             PASS   143                       (>= 24)
beats_persistence_dm               FAIL   p = 0.1600                (needs <= 0.10)
interval_coverage                  PASS   0.8741                    (0.80 +/- 0.15)
parameters_within_declared_bounds  PASS   []
no_timing_leakage                  PASS   0 / 0
no_revision_leakage                PASS   []
```

| Metric | v2 (bug present) | v3 (bug fixed) | persistence |
| --- | --- | --- | --- |
| MAE (thousand payrolls) | 1,328 | **499.5** | 593.2 |
| RMSE | — | 1,620.2 | 1,911.0 |
| CRPS | — | **405.1** | 479.5 |
| 80% coverage | 0.245 **fail** | **0.8741 pass** | — |
| DM p vs persistence | 0.704 | **0.160** | — |
| bias | −2,172 (selection window) | −55.9 | — |

Parameters are unchanged from v2 (`employment_output_elasticity` 0.30182, `impact_elasticity`
0.28987, `persistence` 0.03958), which is itself the proof that the defect was in the *scored
forecast* and not in the fit: the fit reads its own frame throughout, and only the backtest's
conditional injection mixed vintages. **Verdict: fail on one criterion.** The model is now
better than persistence on MAE, RMSE and CRPS and still does not clear the declared p ≤ 0.10.
That is the correct outcome: a 16% MAE improvement over a random walk on monthly payroll levels
is not significant at n = 143 with serially correlated losses, and the criterion is right to
say so.

`_v4` (v3 plus the PAYEMS revision component): report `4f3ed0b935e8…`, artifact
`754720a52b4c…`. **`interval_coverage` FAILS at 0.958** (outside 0.80 ± 0.15 by 0.008) and CRPS
is 431.0 against v3's 405.1. The declared selection rule preferred it on validation evidence
(CRPS 431.0 vs 440.1 there, coverage 0.8105 vs 0.600) and it is worse on the holdout — the
2005-2012 selection window spans the financial crisis and its benchmark revisions, while
2013-2024 has smaller PAYEMS revisions, so the component over-covers. **v3 is not
retro-selected**; both stand with their own numbers, and nothing turns on it for the verdict
because DM fails identically in both.

### `policy_rule.fred_realtime_v3` — coverage passes by 0.0096; DM unchanged

Report `f188e4198071…`, artifact `785c14227627…`, 47 holdout quarters, 0 skipped.

```
minimum_test_forecasts             PASS   47                        (>= 12)
beats_persistence_dm               FAIL   p = 0.6734                (needs <= 0.10)
interval_coverage                  PASS   0.6596                    (0.80 +/- 0.15)
parameters_within_declared_bounds  PASS   []
no_timing_leakage                  PASS   0 / 0
no_revision_leakage                PASS   []
```

Coverage moved 1.000 → 0.6596 and CRPS 0.2696 against persistence's 0.2734. Parameters
identical to v2 (`inflation_response` 1.1233, `output_response` 1.5987, `smoothing` 0.9219).
**Two things must be said next to this pass.** First, it passes by **0.0096** — the allowed band
is [0.65, 0.95] and the observed value is 0.6596, so a slightly different sample fails it; this
is a pass on the declared rule and not a robust one. Second, the trailing scale **overshot**:
the over-coverage became under-coverage, because the 2013-2024 holdout opens with a decade of
ZIRP (tiny errors) and closes with the 2022-2023 hiking cycle (large ones), and a trailing
window follows a volatility jump rather than anticipating it — the same limitation the fifth
wave recorded for `inventory_balance` in 2022. **Verdict: fail**, on one criterion instead of
two.

### `energy_purchasing.fred_realtime_v3` — coverage passes, two criteria still fail

Report `e47b4d103996…`, artifact `7709d9c616d4…`, 83 holdout forecasts, 0 skipped.

```
minimum_test_forecasts             PASS   83                        (>= 12)
beats_persistence_dm               FAIL   p = 0.7558                (needs <= 0.10)
interval_coverage                  PASS   0.8675                    (0.80 +/- 0.15)
parameters_within_declared_bounds  FAIL   ["energy_response", "rate_response"]
no_timing_leakage                  PASS   0 / 0
no_revision_leakage                PASS   []
```

Coverage 0.627 → 0.8675 with mean interval width 12,073; MAE 3,918.8 against persistence's
3,504.3, so the mechanism still has no forecast skill, and `energy_response` (−0.083) and
`rate_response` (−0.525) still carry the wrong sign for the declared mechanism. **Verdict:
fail**, on two criteria instead of three. The interval is now the right object around a point
forecast that is not.

### `conflict_model.ucdp_monthly_v2` — coverage passes on the family's own dispersion

Report `33f65fec8d70…`, artifact `76468856a59c…`, 1,200 holdout forecasts (2020-2024, 20
countries × 60 months), 0 skipped.

```
minimum_test_forecasts             PASS   1200                      (>= 12)
beats_persistence_dm               FAIL   p = 0.5829                (needs <= 0.10)
interval_coverage                  PASS   0.7192                    (0.80 +/- 0.15)
parameters_within_declared_bounds  PASS   []
no_timing_leakage                  PASS   0 / 0
no_revision_leakage                FAIL   ["country_months"]
```

| Metric | v1 (Poisson `sqrt(λ)`) | v2 (NB2 `sqrt(λ + αλ²)`) |
| --- | --- | --- |
| 80% interval coverage | 0.596 **fail** | **0.7192 pass** |
| mean interval width | 11.2 | 31.0 |
| CRPS | 11.185 | **9.994** (persistence 12.269) |
| MAE / RMSE | 12.943 / 51.223 | identical |
| DM p vs persistence | 0.58 | 0.5829 |
| fitted `dispersion` | 0 (never estimated) | **0.0566** (bounds [0, 50]) |

The mechanism parameters barely move (`self_excitation` 0.98798 against 0.988, `decay` 0.21084
against 0.2108), so this is purely the predictive dispersion. CRPS improves by 11% and now beats
persistence's, which matters: the criterion only tests coverage, and a proper score confirms the
wider interval is a genuinely better distribution rather than a compliant one.

**Verdict: fail**, on two criteria instead of three. `beats_persistence_dm` is unchanged — the
Hawkes intensity is still not significantly better than a random walk on country-month counts,
with `self_excitation` pinned at 0.988 absorbing that persistence into the excitation term — and
`no_revision_leakage` is structural: UCDP annual releases revise earlier months and the row
dates are event months, not publication dates, so no interval method can reach it.

### `regional_model.cbp_state_sectors_v3` — fails **worse**, as registered in advance

Report `764d1ca7b1db…`, artifact `aacd02f5eb75…`, 51 forecasts. `interval_coverage` **1.0000**
against v2's 0.980 — the wider common term made the over-coverage worse, which is exactly what
the registration said to expect. Mean interval width 0.1381 against v2's 0.0810.
`no_revision_leakage` also still fails (CBP publishes no vintages). Everything else passes
(`beats_persistence_dm` p = 6.2e-06, `beats_year_effect_only_dm` p = 4.1e-06,
`shift_share_elasticity` 2.3869 inside bounds), and the point forecast is unchanged (MAE
0.011558).

This row is the control for the whole workstream: one change, one stated reason, applied to two
panels, and it moves them in opposite directions relative to the threshold. A change tuned to a
threshold cannot do that.

### `regional_model.qcew_state_sectors_v2` — the rejected candidate, reported not claimed

Report `233f5714075e…`, artifact `94f3ea8bf2b2…`, 318 forecasts (2019-2024 × 53 areas).
`interval_coverage` **passes at 0.7579**, against v1's 0.597, and its holdout CRPS is 0.016269
against v1's 0.016934.

**This is not a fix and it is not counted as one.** `per_unit_year_draw` is the candidate the
declared selection rule — lowest validation-window CRPS, fixed before any WS-E attempt ran —
**rejects** for this panel (validation CRPS 0.010591 against `per_unit_year_mean`'s 0.010294),
and that rejection was written into the plan's `run_note` *before* this holdout was scored. The
attempt is published so the number is on the record, and the verdict for `regional_model` on the
long panel remains v1's: `interval_coverage` fails at 0.597 and `no_revision_leakage` fails
structurally.

Everything else in this run passes and is worth recording, because it is the strongest evidence
the shift-share mechanism has: `beats_persistence_dm` p = **4.3e-10**,
`beats_year_effect_only_dm` p = **1.2e-04**, `shift_share_elasticity` 1.2586, MAE 0.021089
against 0.030656 for the year-effect-only baseline that switches the mechanism off and 0.042199
for persistence. The mechanism is real; its uncertainty and its vintages are the problem.
