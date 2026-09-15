# Chronological benchmarks

`benchmark_series(rows, train_end, validation_end)` accepts one identified series
with one unit, explicit dates, finite values or null, and per-row evidence.
It requires four training observations, two validation observations and two final
observations. Missing values are retained in an exclusion report. Duplicate dates
and mixed identities/units require reconciliation before fitting.

Persistence, training mean, bounded drift and bounded AR(1) are fitted on training
only. Validation MAE selects a model with a simpler-model tie break. Selection and
parameters are frozen before final scoring. Each final prediction retains input and
target evidence and information cutoff. `available_at` may specify actual availability;
future input leakage fails. Without vintages this is explicitly retrospective.

Reports contain MAE, signed bias, absolute-error quantiles, persistence comparison
and a validation-residual uncertainty band. This interval is descriptive and does
not promise statistical coverage. Decimal arithmetic avoids avoidable overflow;
unrepresentable outputs fail explicitly. Losing to persistence is a valid result.

```sh
wm benchmark-series ssga_dia_nav/normalized --metric fund_nav \
  --train-end 2026-06-15 --validation-end 2026-08-01
```

Choose cutoffs from the acquired date coverage before looking at final targets.
Rolling inputs make this a next-observation benchmark, not a fixed-horizon forecast.
No forecasting result identifies a causal policy response. Mechanism coefficients
remain scenario assumptions unless a separate empirical design supplies evidence.
