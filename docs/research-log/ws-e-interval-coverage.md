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

## Summary table — the 16 attempts

Status as of the last update to this file. `superseded (fifth wave)` means the failure was
already diagnosed and fixed on 2026-09-16 and is re-reported here for completeness.

| Attempt | Diagnosis | Change | New verdict |
| --- | --- | --- | --- |
| `inventory_balance.eia_weekly` | miscalibration — static scale against moving volatility | none needed; `_v2` (fifth wave) declares `empirical_trailing` w=52 ⊕ x'Vx | **pass** as `inventory_balance.eia_weekly_v2`, coverage 0.764 |
| `credit_growth.fred_realtime` | **scoring bug** — real-time forecast graded against a revised vintage | none needed; `_v3` (fifth wave) adds the revision variance component | **pass** as `credit_growth.fred_realtime_v3`, coverage 0.773 |
| `credit_growth.fred_realtime_v2` | same | same | **pass** as `_v3` |
| _(rows below filled in as each is run)_ | | | |

## Per-attempt record

_(in progress)_
