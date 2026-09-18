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

**A wider interval that would almost certainly have passed was available, and I did not take
it.** `per_unit_year_draw` reaches 0.9340 in validation, comfortably inside 0.80 ± 0.15; on the
holdout it would very likely have converted `regional_model`'s coverage failure into a pass.
It loses on CRPS — a proper score — so the declared rule selects `per_unit_year_mean`, which is
what v1 already uses, and `regional_model` therefore **still fails** on the long panel.

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
nineteen pre-pandemic year effects can price. `no_revision_leakage` also fails structurally —
no published employment source in this catalog carries vintages, which is WS-B's problem, not
an interval problem.

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

## Summary table — the 16 attempts

Filled in as each attempt is run. Rows marked *fifth wave* were diagnosed and fixed on
2026-09-16 and are re-reported here because they are part of the 16.

## Per-attempt record

_(runs in progress)_
