# The two `not_run` attempts: profiled, sped up, run

2026-09-18. `legislative_model.voteview` and `market_abm_model.alpaca` were recorded `not_run` with
reason `compute_budget` (30 minutes wall clock per attempt). This file records what the time was
actually spent on, what changed in the code and why no output changed, the measurements against the
budget, and the two verdicts. Registration: `not_run_wave` and the last two entries of `attempts` in
[`real_data_plan.json`](../../worldmodel/estimation/real_data_plan.json), committed (6cf509d) before
either attempt touched real data.

## The budget claim was never measured

Neither `not_run` note carries a timing, and there was no catalog loader for either family, so neither
attempt could have been run. Measured on fixtures of the real sizes with the **pre-change code**, both
fit the budget comfortably:

| Fixture (synthetic, real size) | Pre-change `validate_process` | After | Same `report_id` |
| --- | ---: | ---: | --- |
| Senate: 104 members, 949 roll calls on 252 dates, 95,715 positions, 50/70/100% splits | 75.1 s | 61.7 s | yes (`fcf65265e257…`) |
| SPY-sized: 2,264 closes, 107 windows of 21 returns, grid of 4, 10 holdout runs | 69.0 s | 29.3 s | yes (`2707d66a958c…`) |

So the honest reading of the old record is "not run because no loader existed and the cost was
assumed", not "measured over budget". The `not_run` entries are kept as recorded, with a
`reattempted_by` pointer.

## Where the time went (cProfile, before)

**legislative**, one refit at a Senate-sized origin: 2.8 s of 3.1 s per holdout target is the fit.
`estimate_discipline` takes 55% of it — a one-coefficient Newton loop over ~50,000 votes through the
general k-coefficient `_newton` (list comprehensions, `zip`, a `sum()` generator and a 1×1 Gaussian
elimination per row) — and `estimate_ideal_points` about 30% (numpy sweeps 0.44 s, screening, power
initialization and the final log-likelihood pass with a `sum()` generator per vote). The holdout
forecaster itself is 0.19 s per target.

**market_abm**: 97% of the time is the Walrasian price search. Every step bisects ~102 candidate prices,
and at each one `_theta` was called for every agent — 196 million calls for 14 simulated paths — although
chartist and noise allocations do not depend on the price. The SMM refit at each window origin is cheap
once `simulation_horizon_steps` covers the sample: each configuration is one cached path and every origin
reads a prefix of it (paths are prefix-consistent, tested).

## What changed, and why it is output-identical

- `legislative._newton1` / `_newton2`: the one- and two-coefficient Newton steps written out operation
  for operation. A one-term `sum(t * v ...)` is `0.0 + t * v`, and a two-term one is `(0.0 + a) + b` —
  Python 3.12's compensated float `sum()` adds a correction that is exactly zero for two terms (the
  Fast2Sum error of the one addition). Checked on two million random pairs, and in the tests.
  `_dot` does the same for the offsets and the log-posterior; three or more terms still call `sum()`.
- `estimate_discipline`: the Fisher information computes the logistic once per row instead of twice
  (same value) and sums the same terms in the same order.
- `market_abm._theta_function`: per step, chartist and noise allocations come from `_theta` itself once;
  fundamentalists are re-evaluated per candidate price with `math.log(fundamental / price)` computed once
  per price rather than once per agent — the same expression on the same operands. `_walrasian` reads
  holdings once per search (they cannot change during it) and sums the same terms in agent order.

Nothing about either model changed: the estimators, objectives, grids, iteration limits, tolerances,
seeds and the numpy/Python backend choice are untouched. `tests/legacy_legislative.py` and
`tests/legacy_market_abm.py` freeze the pre-change modules, and `tests/test_estimation_not_run_speed.py`
requires **bit-identical** results (compared as IEEE-754 byte strings, so even `0.0` versus `-0.0` would
fail): Newton steps for one, two and three coefficients; `_dot` against `sum()`; `legislative.fit` on
both backends in one and two dimensions; the holdout forecasts; `market_abm.simulate` for both clearing
rules, both modes and four agent configurations; and whole validation reports of both families, including
`report_id`. After the change, cProfile puts a Senate-sized refit at 0.85 s (from 2.8 s) and a simulated
path about 3× faster.

## Loaders

- `legislative_data(congress, chamber)` reads `roll_call_member_positions` for one chamber-Congress and
  the party code from each member's `congressional_service` assertion. Position groups map as
  `legislative.from_voteview` maps cast codes (yea, paired yea, announced yea → 1; nay, paired nay,
  announced nay → 0; present, not voting, not a member → missing). Senate 117: 949 roll calls, 91,623
  positions, 104 members, two voters without a Senate service record (vice-presidential tie-breaks), no
  member with two party codes; 1 s, 65 MB.
- `market_abm_data(symbol, …)` reads one symbol's total-return adjusted closes and attaches the attempt's
  declared SMM configuration; SPY 2016-01-04..2024-12-31 is 2,264 bars; 27 s, 43 MB (a streaming scan of
  the 3.8 GB dataset).

## Runs against the budget

Both run with `calibrate-all --attempt …` from a frozen copy of the code at 6cf509d (so the provenance
snapshot cannot change mid-run), `nice -n 10`, one at a time, under `timeout 1800`.

| Attempt | Wall clock | Peak RSS | Report | Artifacts (calibration_reports) |
| --- | ---: | ---: | --- | --- |
| `legislative_model.voteview` | 70.2 s | 190 MB | `7f567c06342f…` | report `73ccbd12b0b9…`, estimate `fa9fa8d9cac9…` |
| `market_abm_model.alpaca` | 106.3 s | 77 MB | `1f9fadd315a2…` | report `0e7bd72c9f17…`, estimate `f4c6f5924ac8…` |

## Verdicts

### `legislative_model.voteview` — pass on all eight criteria; `legislative_model` is validated

| Criterion | Observed | Declared |
| --- | --- | --- |
| `minimum_test_forecasts` | 18,725 member-votes | ≥ 200 |
| `brier_skill_vs_member_base_rate` | 0.794 | ≥ 0.05 |
| `beats_revealed_share_dm` | p < 1e-15, mean loss difference −0.170 | p ≤ 0.10 |
| `beats_persistence_dm` | p < 1e-15 | p ≤ 0.10 |
| `calibration` | max deviation 0.082 (0.7-0.8 bin) | ≤ 0.15 |
| `parameters_within_declared_bounds` | discipline 0.0277 | [−10, 10] |
| `no_timing_leakage` | 0 / 0 | 0 |
| `no_revision_leakage` | rows declared `revisions: none` | — |

Brier 0.0436. Three things belong next to the pass. (1) The forecast is conditional on the votes of every
third senator on the same roll call — that is the family's declared contract, and it is a strong input,
which is why the revealed-share baseline (not the base rate) is the one that matters; it is beaten
decisively. (2) The DM p-values pool 18,725 member-votes that share roll calls, so they overstate the
evidence; the effective sample is closer to the ~285 held-out roll calls. (3) `no_revision_leakage`
passes on the loader's declaration that a recorded position is final when the vote closes; Voteview
transcription corrections are possible and unmeasured. Calibration is systematically too cautious in the
middle-high range (predicted 0.75, observed 0.83).

### `market_abm_model.alpaca` — fail on four criteria

`beats_persistence_dm` p = 0.819, `beats_historical_mean_dm` p = 0.998, `interval_coverage` 0.222 against
0.80 ± 0.20, and `no_revision_leakage` (adjusted closes, as registered). `minimum_test_forecasts` (18
windows), parameter bounds and timing leakage pass. The SMM chooses `chartist_strength` 0 at the final
cutoff; the simulated window return sd averages 0.0104 against a realized 0.0071 over the holdout
(2023-07..2024-12), and the 10-run spread is narrow (mean 80% width 0.0041), so the intervals miss from
above. With one grid parameter and a fixed fundamental volatility of 0.01 per step, the stylized market is
simply more volatile than SPY was in 2023-2024; the family cannot match the level, let alone its changes.
This is the expected shape of failure written into the registration.
