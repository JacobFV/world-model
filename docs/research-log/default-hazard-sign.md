# default_hazard — why the declared series gives the opposite sign

2026-09-18, validation track. `default_hazard` passes on two variants of a *substituted*
delinquency series (the FDIC aggregate noncurrent-loan rate, with a LAUS or a CPS unemployment
rate) and fails on its declared primary series (FRED `DRCCLACBS` credit-card delinquency with
`UNRATE`), with `unemployment_sensitivity` **+2.12 / +2.19** on the substitutes and **−1.26** on
the declared pair, and a declared-pair `persistence` of **1.0038**, outside its `[0, 1]` bound.
This file records why, and what was and was not done about it.

Everything below is in-sample fitting of the component's own estimator
(`DefaultHazardEstimator.estimate`, the fractional logit
`rate_t = Λ(b0 + ρ logit(rate_{t−1}) + b_u u_t + b_i i_{t−1})`) on frames built by the
catalog loaders at the 2025-12-31 cutoff. **No forecast was scored and no report was published
for this diagnosis.** The script is not part of the package; its numbers are reproduced here.
Because the frames run to 2025, these in-sample fits *include the holdout years* of the
existing attempts; that matters for the re-specification below and is disclosed there.

## Four candidate causes, checked one at a time

### 1. Units — ruled out

`DRCCLACBS` and `DRBLACBS` are published in percent of loans (`source_units: Percent`,
multiplier 1.0, no unit change across the 62 vintages); the FDIC rate is built as
`100 × Σ noncurrent / Σ net loans`, also percent. `UNRATE`/`LNS14000000` and `DFF` are percent.
The estimator divides all three by 100 before the logit and the linear terms, identically for
every source. The 2010Q1 values line up as expected (card 5.78%, FDIC noncurrent 5.64%,
business 3.92%, unemployment 9.83%, DFF 0.13%).

### 2. Timing and lags — ruled out as the cause of the *difference*

FRED dates a quarterly observation at the first day of the quarter (`Quarterly, End of Period`
values are stamped 2010-01-01 for 2010Q1); the FDIC loader stamps the call-report date
(2010-03-31). Both aggregate to the same model quarter, and the quarter-by-quarter table below
shows the three series moving in the same calendar quarters. The design (`u_t` contemporaneous,
a declared conditional input; `i_{t−1}` lagged) is identical for every source, so the lag
structure cannot by itself produce a sign that differs *between* sources. It does determine
*what* the coefficient measures — see cause 4.

### 3. Sample window — ruled out

The declared attempt fits 1991–2025 in real time; the substitutes fit 2010–2025
retrospectively, because the FDIC panel starts in 2010. Refitting the declared pair on the
substitutes' window does **not** restore the sign:

| Declared pair (DRCCLACBS + UNRATE), window | n | ρ (SE) | b_u (SE) |
| --- | ---: | --- | --- |
| 1991–2025 (the attempt) | 138 | 1.0038 (0.0219) | **−1.259** (0.287) |
| 1991–2009 | 75 | 0.9897 (0.0420) | +0.126 (0.473) |
| **2010–2025 (the substitutes' window)** | 62 | 0.9473 (0.0306) | **−1.550** (0.456) |
| 2010–2019 | 39 | 0.9142 (0.0190) | −0.910 (0.405) |
| 1991–2016 (to the attempt's `train_end`) | 103 | 1.0003 (0.0244) | −0.918 (0.350) |

| FDIC noncurrent + CPS, window | n | ρ (SE) | b_u (SE) |
| --- | ---: | --- | --- |
| 2010–2025 (the passing attempt) | 62 | 0.9403 (0.0096) | **+2.118** (0.232) |
| 2010–2019 | 39 | 0.8028 (0.0919) | +5.744 (2.489) |

On the same sixty-two quarters the two left-hand sides give opposite, individually significant
signs. The window is not the explanation.

### 4. Data definitions — this is the cause

The two left-hand sides are different objects, and they move at different points of the cycle.

- **`DRCCLACBS`** is the share of credit-card balances **30+ days past due**, card loans only,
  seasonally adjusted. Card balances are charged off at 180 days past due, so the delinquent stock
  is continually purged: it is close to a *flow* of new household distress. It peaks with or
  before unemployment and falls while unemployment is still high.
- **The FDIC rate** is the share of *all* loans and leases that are **noncurrent — 90+ days past
  due or in nonaccrual**, dominated by real-estate and commercial loans whose resolution
  (foreclosure, workout) takes years. It is a slow-clearing *stock*, and it tracks the *level*
  of unemployment.

The quarter-by-quarter record (percent):

| quarter | card 30+ dpd | FDIC noncurrent | business 30+ dpd | unemployment |
| --- | ---: | ---: | ---: | ---: |
| 2009Q2 | **6.77** (peak) | — | 3.78 | 9.30 |
| 2009Q4 | 6.33 | — | 4.27 | 9.93 |
| 2010Q1 | 5.78 | 5.64 | 3.92 | 9.83 |
| 2010Q4 | 4.14 | 5.00 | 2.96 | 9.50 |
| 2011Q4 | **3.25** | 4.28 | 1.64 | **8.63** |
| 2020Q1 | 2.69 | 0.95 | 1.14 | 3.83 |
| 2020Q2 | 2.45 | 1.10 | 1.28 | **13.00** |
| 2020Q3 | **1.99** | **1.19** | 1.30 | 8.80 |
| 2021Q2 | 1.59 | 1.03 | 1.06 | 5.93 |

Card delinquency halved between 2009Q2 and 2011Q4 while unemployment stayed between 8.6% and
10%; in 2020 it *fell* from 2.69% to 1.99% while unemployment went to 13% (income support and
forbearance). The FDIC stock declined slowly after 2010 and *rose* through 2020.

Because the fitted persistence is close to one, `b_u` is identified almost entirely by how the
*change* in log-odds co-moves with the *level* of unemployment. Measured directly:

| series | corr(logit level, u) | corr(Δ logit, u level) | corr(Δ logit, Δu) |
| --- | ---: | ---: | ---: |
| DRCCLACBS (1991–2025) | +0.246 | **−0.503** | +0.086 |
| FDIC noncurrent (2010–2025) | **+0.809** | +0.001 | +0.336 |
| DRBLACBS (1987–2025) | +0.352 | −0.178 | +0.258 |
| CORCCACBS (1985–2025) | +0.365 | −0.131 | +0.197 |

Card delinquency *falls* in the quarters when unemployment is *high* (−0.50), so with ρ ≈ 1 the
contemporaneous-level coefficient comes out negative. The FDIC stock's level tracks
unemployment's level (+0.81), so its coefficient comes out positive. With ρ pinned at one on the
declared series, the sign of `b_u` is a statement about which phase of the cycle a series
peaks in, not about how much default risk unemployment adds.

The unit root is part of the same finding. Card delinquency drifts down from about 5% in the
1990s to about 2% by 2021 (a change in who holds cards and in underwriting, including the 2009
CARD Act), so over 1991–2025 the lagged log-odds absorbs a trend and ρ lands at 1.0038.

### A fifth thing the diagnosis turned up: the declared series is not the mechanism's object

The simulator hook the component binds to (`requirements.json` → `hooks`) *"draws firm
defaults"*. The declared primary series measures household credit-card distress. The FDIC
substitute is all bank loans. `requirements.json` already names `DRBLACBS` — delinquency on
business loans at all commercial banks — as the *business-loan delinquency alternative*, and it
is the only series in the catalog whose borrowers are firms. On it the relationship is positive
and significant before 2010 (`b_u` +3.15, SE 1.45, 1987–2009) and indistinguishable from zero
after (−0.10, SE 0.80, 2010–2025; +0.49, SE 0.72, full sample), with ρ 0.954 inside its bound.
The charge-off alternative `CORCCACBS` is again household card credit and was not pursued.

## Re-specification: one attempt, pre-registered with a disclosure

The principled re-specification is the one the component's own declarations point to: fit the
mechanism that draws **firm** defaults on the **business-loan** delinquency rate, keeping
everything else about the declared attempt fixed — declared `UNRATE` and `DFF`, strict
real-time vintages (`DRBLACBS` has ALFRED vintages from 2011-05, as `DRCCLACBS` does), the same
splits as `default_hazard.fred_primary_realtime`, the component's default criteria. It is
registered as `default_hazard.fred_business_loans_realtime`, a declared **substitution**
following the FDIC precedent: a pass would be a pass on business-loan delinquency, and the
declared credit-card attempt keeps its failing verdict and stays current beside it.

**Disclosure, written into the registration.** This diagnosis fitted `DRBLACBS` in-sample on
frames running to 2025, so before registering I already knew the full-sample point estimates
(ρ 0.954, `b_u` +0.49, `b_i` +1.09). `parameters_within_declared_bounds` is evaluated on the
final estimate at the cutoff, which is exactly that fit, so that criterion's outcome was
effectively known in advance and the attempt is **not blind** on it. No forecast on this series
— validation or holdout — had been computed, so `beats_persistence_dm`, `interval_coverage` and
the leakage criteria were unseen.

What was *not* done, and why:

- **Changing the design** (unemployment in changes, or lagged) would fit the card series
  better, but the design is the simulator hook's mechanism (`unemployment_sensitivity × u`);
  changing it means changing the simulator, not re-estimating it. Recorded as a follow-up.
- **Changing the declared primary series in `requirements.json`** would turn the declared
  attempt's failure into a non-event after the fact. It stays declared; its failure stands.
- **A grouped hazard on the FDIC bank panel** (the declared next step in the record) needs a
  bank-level loader and is not attempted here.

The result is recorded in [`docs/calibration-status.md`](../calibration-status.md) under the
2026-09-18 section and in the attempt's `run_note`, whichever way it went.
