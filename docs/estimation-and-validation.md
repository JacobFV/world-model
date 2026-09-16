# Estimation and validation

`worldmodel.estimation` turns process parameters into data products. It declares
exactly which public series each process family needs, fits parameters using only
information available by a knowledge cutoff, scores out-of-sample forecasts
against naive baselines on a frozen holdout, and records the outcome as an
immutable report. A process is marked `validated` only when explicit, declared
acceptance criteria pass. Nothing becomes validated by default.

All numerics are pure Python (standard library only). NumPy, if installed, is used
only to invert large matrices, and results stay plain Python floats.
`backend()` reports which path ran.

## Quick start

```python
from worldmodel.estimation import ObservationSet, estimator_for, validate_process, attach_calibration
from worldmodel.process_library import default_registry
from worldmodel.store import Store
from worldmodel.cli import reference

store = Store('data')
refs = [reference('fred_policy_rate', store), reference('fred_macro_panel', store)]
data = ObservationSet.from_store(store, refs)

estimator = estimator_for('interest_pass_through')
estimate = estimator.fit(data, cutoff='2024-12-31', vintage_policy='strict')
print(estimate.parameters)           # spread, pass_through, adjustment_speed, ...
print(estimate.to_dict()['process_parameters'])  # {'mechanisms.interest.spread': ...}

report = validate_process(estimator, data, train_end='2012-12-31', validation_end='2018-12-31',
                          cutoff='2024-12-31', data_inputs=refs)
registry = default_registry()
record = attach_calibration(registry, report)   # re-verifies the report and re-evaluates the criteria
registry.calibration('coupled_economy')          # missing/failing components
```

CLI (JSON output; artifacts are published unless `--no-publish` is given):

```sh
wm estimation-requirements coupled_economy          # what data agents must normalize
wm estimate coupled_economy --component interest_pass_through \
   --data fred_policy_rate --data fred_macro_panel --cutoff 2024-12-31
wm validate coupled_economy --component interest_pass_through \
   --data fred_policy_rate --data fred_macro_panel \
   --train-end 2012-12-31 --validation-end 2018-12-31 --cutoff 2024-12-31
wm calibration-status calibration_reports@<version>
```

`interest_pass_through` reads the prime rate (DPRIME) from `fred_macro_panel`, not from a
separate dataset; `wm estimation-load` prints the dataset and metric each requirement
resolves to. Published reports live in `calibration_reports`, and
`wm calibration-status` accepts a *validation report* version — handed one of the
`estimate` artifacts in the same dataset it returns a misleading
`Validation report content does not match report_id`.

Other options: `--vintage-policy retrospective`, `--series cash.subject=sec:cik:0000320193`
(requirement overrides), `--option ecm_lags=2`, `--options-file topology.json`
(field topology), `--criteria criteria.json`, `--horizon`, `--window rolling --window-size N`
and `--refit-every`.

## Package map

| Module | Contents |
| --- | --- |
| `data` | `SeriesRequirement`, `ObservationSet` (point-in-time selection, `visible`, `subset`, `frame`), `Frame`, `Series`, `LeakageError` |
| `spec` | `ParameterSpec` (unit, bounds, prior, `maps_to` process parameter path), `Estimate`, the `Estimator` protocol |
| `regression` | OLS/WLS with nonrobust, HC0–HC3 or Newey–West HAC covariance; 2SLS with first-stage partial F and Sargan test; Wald test; delta method |
| `timeseries` | AR(p), ARIMA-lite (CSS; MA terms by bounded Nelder–Mead), VAR(p), AIC/BIC lag selection on a common sample, forecasts with psi-weight/MA(∞) variances |
| `adjustment` | Partial adjustment and single-equation error-correction models (long-run response, half-life, delta-method SEs) |
| `discrete` | Logit/cloglog binomial GLM (binary or fractional; quasi-ML sandwich), grouped discrete-time hazard, Poisson GLM, PPML gravity with optional fixed effects |
| `growth` | Exponential (log-linear) and logistic growth |
| `kalman` | Local-level filter, RTS smoother, MLE over variances, missing observations |
| `simulation` | Simulated method of moments (common random numbers, sandwich SEs with (1+1/S), J test), rejection ABC, and adapters that treat configuration evaluators (`simulate_coupled_economy`, `materialize_composition`) and `Environment` episodes as black boxes |
| `bootstrap` | iid, moving-block, circular-block and stationary bootstrap with seeds and failure counts |
| `validation` | Baselines, MAE/RMSE/MASE, Gaussian and ensemble CRPS, pinball loss, interval coverage, log score, Brier score and skill, calibration curves, Diebold–Mariano (HLN-corrected), rolling-origin backtests, `validate_process`, publication helpers |
| `acceptance` | Declared criteria and `evaluate_criteria` |
| `families` | Estimators for each process component, loaded from `requirements.json` |
| `registry` | `calibration_record`, `attach_calibration`, `load_calibrations` |
| `synthetic` | Fictional datasets with known true parameters for every component |

## Point-in-time discipline

Every fit and forecast takes a `cutoff`. An observation is usable only if both its
valid time and its availability time are at or before the cutoff. Availability
is resolved in this order:

1. **Real-time vintages.** `realtime_start` (ALFRED), `vintage_date`,
   `published_at`, `released_at` or `available_at` on the record or in
   `attributes`. When several vintages of one period are available, the latest
   is used and superseded vintages are counted.
2. **`vintage_policy='strict'` (default).** Availability is the acquisition time
   `observed_at`. A current-vintage download cannot feed a historical cutoff.
3. **`vintage_policy='retrospective'`.** Availability is the period end plus the
   requirement's `publication_lag_days`. This controls timing leakage but not
   revision leakage, and the audit marks `revision_leakage_possible`.

Aggregation to the model frequency (mean/last/sum) includes only periods that
are complete by the cutoff. Each estimate's `data_audit` records, per series:
vintage modes, vintages, maximum availability, excluded missing values, the
superseded-vintage count, evidence inputs and record ids (a digest when there
are more than 2,000). `Series.audit()` raises `LeakageError` if any point is
later than the cutoff.

Validation adds these checks:

- Model selection receives `ObservationSet.visible(validation_end)`, a copy that
  physically omits later information.
- Each holdout origin's cutoff is the first-publication time of the anchor row.
  If the target period was already published at that time, the origin is skipped.
- A forecast design that reads the target at forecast time raises `LeakageError`.
- Scoring targets after the evaluation cutoff raises `LeakageError`.

## Validation protocol

`validate_process(estimator, data, train_end, validation_end, cutoff, candidates=None, criteria=None, ...)`:

1. **Selection.** Each candidate is backtested on `(train_end, validation_end]`
   using only information available by `validation_end`. The lowest validation
   MSE wins; ties go to declaration order. The selection is hashed and frozen,
   and `refit_after_selection` is false.
2. **Holdout.** The selected estimator is backtested on
   `(validation_end, cutoff]` with an expanding or rolling window, refitting
   every `refit_every` origins. Actual values are the latest vintages available
   by `cutoff`. Changing holdout values cannot change the selection; this is
   tested.
3. **Scoring.** The model and each baseline (persistence, drift, historical mean,
   optional seasonal naive) are scored with MAE, RMSE, MASE, bias, Gaussian
   CRPS, pinball loss at 10/50/90%, log score, and 80% interval coverage and
   width. Brier score, Brier skill and calibration curves are added for
   probability targets. Diebold–Mariano tests (squared and absolute loss,
   one-sided, Harvey–Leybourne–Newbold correction) compare the model with each
   baseline.
4. **Final estimate.** The estimator is refit at `cutoff`. Its content-addressed
   `estimate_id` is part of the report.
5. **Acceptance.** Criteria are evaluated and the report gets a content digest
   `report_id`. `publish_validation_report` publishes it through
   `worldmodel.artifacts.publish_report`, pinning inputs, code and rights.

Conditional forecasts are labeled. Components with declared
`conditional_inputs` (for example the policy rate for pass-through) use realized
values of those drivers at the target time. They test the mechanism given its
inputs, not the ability to forecast those inputs. The gravity component uses a
cross-sectional next-year holdout (`GravityEstimator.backtest`) with the same
report schema.

## Acceptance criteria

Default criteria (`acceptance.DEFAULT_CRITERIA`). Components override thresholds in
`requirements.json`:

| id | rule |
| --- | --- |
| `minimum_test_forecasts` | At least N holdout forecasts (default 12; component overrides from 8 to 30) |
| `beats_persistence_dm` | DM squared-loss p-value below 0.10 **and** lower mean loss than persistence |
| `interval_coverage` | 80% interval coverage within ±0.15 of nominal (population ±0.20) |
| `parameters_within_declared_bounds` | Every final parameter is inside its `ParameterSpec` bounds |
| `no_timing_leakage` | Zero leakage violations in selection and holdout, with at least one origin checked |
| `no_revision_leakage` | Series that can be revised must use real-time vintages. Components may allow `minor` revision classes explicitly |

Additional types: `relative_metric`, `max_metric`, `brier_skill`, `calibration_error`,
`vintage_modes` and `estimate_diagnostic` (for example the demand
component requires a first-stage F of at least 10). Unknown types fail. An
empty criteria list raises.

`ProcessRegistry.attach_calibration` rejects records whose digest does not match,
or whose `validated` flag disagrees with their acceptance results.
`register_process` rejects `validated: true`. A process is validated only when
every component listed in `requirements.json` has a passing record.
`banking_ledger` declares no components and can never be validated.
`registry.calibrated_parameters(process_id)` returns parameter paths with their
estimate ids, and `predict` diagnostics carry the calibration summary.

## Components and required series (`worldmodel/estimation/requirements.json`)

| Process / component | Method | Series (publisher id) | Parameters → process path |
| --- | --- | --- | --- |
| population_growth / population_growth_rate | log-difference drift, HAC (optional exponential and logistic diagnostics) | Census PEP vintage files (`POPESTIMATE`); FRED POPTHM alternative | growth_rate_per_year → `growth_rate_per_year` |
| resource_inventory / inventory_balance | stock-flow balance OLS | EIA WCESTUS1, WCRFPUS2, WCRIMUS2, WCREXUS2, WCRRIUS2 | flow_scale, unmeasured_net_flow → handler parameters of the same name |
| investment_cash_flow / cash_balance | cash-flow balance OLS | SEC XBRL CashAndCashEquivalentsAtCarryingValue, Revenues, CostsAndExpenses, PaymentsToAcquirePropertyPlantAndEquipment | cash_conversion, unmeasured_net_cash_flow |
| coupled_economy / interest_pass_through | ECM, HAC | FRED DPRIME, DFF | `mechanisms.interest.spread`, `.pass_through`\*, `.adjustment_speed`\* |
| coupled_economy / deposit_rate_pass_through (optional) | ECM | FDIC national savings rate (SNDR; id unverified), DFF | `mechanisms.deposit_interest.*`\* |
| coupled_economy / default_hazard | fractional-logit hazard, HC0 | FRED DRCCLACBS (CORCCACBS/DRBLACBS alternatives), UNRATE (BLS LNS14000000), DFF; FDIC call reports for bank panels | `mechanisms.default_hazard.*`\* |
| coupled_economy / deposit_growth | log-growth ADL | FRED DPSACBW027SBOG (H.8), DFF; FDIC DEP | `mechanisms.deposit_growth.*`\* |
| coupled_economy / credit_growth | AR(p) with BIC on log growth | FRED TOTALSL (G.19) | `mechanisms.credit_growth.*`\* |
| coupled_economy / demand_price_elasticity | partial-adjustment log-linear 2SLS (crude price as instrument), Fourier seasonality | EIA MGFUPUS2, GASREGW, DCOILWTICO | `mechanisms.demand_feedback.elasticity` |
| coupled_economy / price_adjustment | inventory-gap price adjustment | GASREGW, EIA WGTSTUS1, DCOILWTICO | `mechanisms.price_feedback.adjustment`, `.cost_pass_through`\* |
| coupled_economy / energy_purchasing | log-growth ADL | FRED RRSFS, DCOILWTICO, DFF | `mechanisms.demand_feedback.energy_response`, `.rate_response` |
| coupled_economy / labor_demand | log-growth ADL | BLS CES PAYEMS (LAUS state override), FRED INDPRO | `mechanisms.labor.employment_output_elasticity`\* |
| coupled_economy / policy_rule | smoothed Taylor rule | FEDFUNDS, CPIAUCSL, GDPC1, GDPPOT (ALFRED vintages) | `mechanisms.interest.policy_rule.*` (smoothing\*) |
| field_dynamics / field_diffusion_transport | pooled Euler regression, HC1 | one series per topology cell (for example EPA AQS PM2.5 county means with Census county adjacency) | `edges[*].conductance`, `edges[*].transport_rate`; decay/source\* |
| cross_domain_composition / bilateral_flow_gravity | PPML with origin/destination/year fixed effects | BTS FAF5 OD tonnage, OD distances | `routes.distance_elasticity`\* |

Every series entry declares its metric, unit, model and native frequency,
`source_series`, aggregation, publication lag, revision class and source URLs.
`bank_energy_business` requires the pass-through and energy-purchasing
components.

\* Added by a simulator hook (see the table below). Each component's `hooks` list in
`requirements.json` records the requested change, and `hook_status: "implemented"`
with `hook_implementation` says where it lives. `process_library` also accepts
`flow_scale`, `unmeasured_net_flow`, `cash_conversion`, `unmeasured_net_cash_flow`
and `estimate_id`. All defaults reproduce the previous behavior.

## Simulator hooks and calibration binding

Every hook is optional, and leaving it out keeps results bit-identical (the frozen
`tests/legacy_coupled_economy.py` parity test still passes). The coupled-economy
hooks run in both the pure-Python reference and `coupled_economy_numpy.ArrayEconomy`,
and the field hooks run in both `field_arrays` backends. Parity is tested step by
step in `tests/test_estimation_hooks.py`.

| Component | Hook (simulator parameter) | Default | Python / numpy |
| --- | --- | --- | --- |
| interest_pass_through | `mechanisms.interest.pass_through`, `.adjustment_speed`, `.impact_pass_through`; persisted `loan_rate` | absent: loan rate = policy_rate + spread | implemented / identical |
| deposit_rate_pass_through | `mechanisms.deposit_interest.{spread, pass_through, adjustment_speed, impact_pass_through, day_count}`, paid from bank equity (`banking` kind `deposit_interest`) | absent: no deposit interest | implemented / identical |
| default_hazard | `mechanisms.default_hazard.{intercept, persistence, unemployment_sensitivity, rate_sensitivity}` plus `seed`, `hazard`, `unemployment`, `period_days` (91.3125) | absent: no hazard defaults | implemented / identical (counter-based draws) |
| deposit_growth | `mechanisms.deposit_growth.{mean_growth_per_month, persistence, rate_semi_elasticity}`: households keep opening deposits grown at the target rate | absent: no retention | implemented / identical |
| credit_growth | `mechanisms.credit_growth.{mean_growth_per_month, persistence, base_credit_limit}`: generates `credit_limit` for firm actions that omit it | absent: omitted limit = 0 | implemented / identical |
| price_adjustment | `mechanisms.price_feedback.cost_pass_through` (firm `last_unit_cost`) | absent: no cost term | implemented / identical |
| labor_demand | `mechanisms.labor.employment_output_elasticity` scales `labor_capacity` by (output/previous output)^elasticity | absent: capacity unchanged | implemented / identical |
| policy_rule | `mechanisms.interest.policy_rule.smoothing` (daily weight smoothing^(1/period_days), period 91.3125) | absent: unsmoothed rule | implemented / shared function |
| field_diffusion_transport | `edges[*].conductance`, `edges[*].transport_rate`, `fields.<name>.decay_rate`, `fields.<name>.source_rate` | 0: closed system | implemented / bit-identical |
| bilateral_flow_gravity | `gravity_demand.distance_elasticity` (`routes.distance_elasticity`) with `routes`, `origin_effects`, `destination_effects` | absent: explicit step routes | implemented (Python only; the spatial backend result is identical) |

`worldmodel.estimation.binding.parameter_bindings(*sources)` accepts `Estimate`
objects, verified estimate dicts, calibration records from `attach_calibration` or
`calibration_record`, `registry.calibrated_parameters(...)` mappings, or lists of
these. Digests are recomputed, and conflicting values for one path raise. Paths come
from each parameter's `maps_to`. Three fitted parameters have no `maps_to`, so the
binder maps them itself, and only when the estimate reports a standard error:
`impact_pass_through` and the field `decay_rate`/`source_rate`.

```python
record = attach_calibration(registry, report)
state = initialize_economy(config, calibration=record)          # or simulate_coupled_economy(..., calibration=...)
state['calibration']['bindings']['mechanisms.interest.pass_through']['record_id']
parameter_provenance(state)          # {path: {'status': 'estimated', record_id, estimate_id} | {'status': 'assumed', value}}
FieldWorld(world, calibration=estimate, calibration_field='pm')
materialize_composition(config, calibration=gravity_estimate)
registry.predict('coupled_economy.deterministic', inputs, {'calibration': record}, context)
```

A binding only fills a mechanism block that is already configured. For example,
`default_hazard` still needs its assumed `seed`, initial `hazard` and `unemployment`.
Paths with no configured block are listed under `unbound`. The source of every bound
value is recorded:

- coupled economy: `state['calibration']`
- fields: result `calibration`
- composition: result `calibration`
- economy: result `calibration`

Simulators that declare `illustrative: true` report `parameter_provenance` in their
diagnostics. These are `coupled_economy`, `bank_energy_business` and the
`process_library` processes, and `field_dynamics` does the same. `banking_ledger`
has no behavioral parameters and reports `{}`.

End-to-end tests (`EndToEndCalibrationTests`) fit synthetic data with known
parameters, attach or bind the calibration, run the simulator, and compare against
the data-generating process:

- interest pass-through: the loan-rate path after a policy change
- default hazard: the next-quarter hazard and realized default rates
- field decay: the cell trajectories over 60 days

## Political, market and geopolitical model families

`worldmodel.models` families (legislative, elections, influence, trade, sanctions,
conflict, assets, market_abm, commodities, monetary, regional) are registered in
`default_registry()` as `<family>_model`, alongside `taylor_rule_policy_rate`,
`two_region_migration` and `conflict_event_intensity`. Each family has a component
`<family>_model_parameters` in `requirements.json`. Its `family_requirements` and
parameters are copied from `models.parameter_hooks` (a test fails on drift).

`ModelFamilyEstimator(family)` does three things:

- **Fit.** It wraps `models.fit(family, data, cutoff)` in an `Estimate`. Scalar
  parameters keep their units and bounds and map to `parameters.<name>`; family
  process handlers merge these into `config.parameters`. Structured estimates are
  kept too, and every fit window is audited against the cutoff.
- **Holdout scoring.** It scores rolling one-step forecasts from each family's holdout
  forecaster (`monetary` and `conflict` are built into `model_families.py`; the others are
  module-level `holdout_forecaster` declarations, contract in `requirements.json` →
  `model_family_contract`). Each origin refits at the previous target time, the forecast
  function receives history rows before the target and a data view restricted to rows at
  or before the target time, and declared conditional inputs are labeled.
- **Restriction.** `visible_data` restricts native mappings to rows at or before the
  selection cutoff.

Forecasters may declare `baselines` (reference forecasts they compute with the same
conditional inputs; each is scored and Diebold–Mariano tested like the naive baselines),
`probability: True` (adds Brier score, Brier skill against the historical mean and a
calibration curve) and prediction `group`s. Only rows of the primary `target` group enter
`metrics` and acceptance; other groups are scored under `test.secondary`. A forecast that
raises `ValueError` is recorded in `skipped`.

| Family | Forecast target (primary) | Method and conditional inputs | Baselines | Acceptance beyond the defaults |
| --- | --- | --- | --- | --- |
| `monetary` | Policy rate | Taylor rule given realized inflation and output gap | persistence, drift, historical mean | defaults |
| `conflict` | Country-month event count | One-step Hawkes intensity, Poisson sd | persistence, drift, historical mean | defaults |
| `legislative` | Yea/nay of unrevealed members on held-out roll calls (probability) | Origin ideal points and discipline; new roll call (a, b) fit to revealed members' votes (every third member by default); Laplace-integrated probabilities | member last vote, member yea rate, `rollcall_revealed_share` | No interval coverage (binary). ≥200 forecasts, Brier skill ≥0.05 vs member yea rate, DM vs revealed share, calibration max deviation ≤0.15 |
| `elections` | Contested district two-party share next cycle | Pooled OLS given realized fundamentals; variance σ²_national + σ²_district + x′Vx (cycle random-effects coefficient covariance). Secondary: Democratic seat count | district last share, district mean | DM vs district historical mean |
| `influence` | Next-period panel outcome per unit | βx + unit effect + drifting period effect, given realized exposure | unit last/mean outcome, `fixed_effects_only` (β = 0) | DM vs `fixed_effects_only` |
| `trade` | Next-year international bilateral flow | PPML trade costs with exporter-year/importer-year terms balanced to realized target-year exporter and importer totals; sd = cv·mean | pair last/mean flow, `frictionless_marginals` (same totals, no costs) | DM vs frictionless. GE counterfactuals are not scored |
| `assets` | Next-day log return given realized factor returns | Factor mean; GARCH(1,1) variance filtered through the origin. Secondary: unconditional return distribution | last return, mean return, `constant_volatility` (same mean) | No parameter bounds (no scalar parameters). ≥100 forecasts, DM vs historical mean, CRPS ≤0.99× `constant_volatility` |
| `market_abm` | Return sd of the next non-overlapping window | SMM fit at the origin; window-moment mean/spread across 10 seeded runs at the same elapsed steps. Secondary: absolute-return autocorrelation, excess kurtosis | previous window, mean of past windows | ≥10 windows, coverage ±0.20, DM vs historical mean |
| `commodities` | Next-period price given realized production and net imports | Fitted demand/storage clear the balance; sd = RMSE of the same one-step clearing on 36 pre-origin periods. Secondary: ending stocks | last price, mean price | defaults |
| `regional` | Next-year log employment growth per region | Leave-one-out shift-share shock from base-year shares and realized other-region industry employment; year effect by pre-origin mean | region last/mean growth, `year_effect_only` | DM vs `year_effect_only` |

Supplied baselines switch off the estimated mechanism while keeping the same inputs, so a
pass means the mechanism adds skill, not just that naive baselines are weak. Each family has
synthetic tests (`tests/test_estimation_model_families.py`): the correctly specified
synthetic fixture validates, and a misspecified or noise fixture must fail named criteria:
coin-flip votes (legislative), an omitted persistent district effect (elections), unit random
walks (influence), flows without trade costs (trade), i.i.d. idiosyncratic volatility
(assets), fundamental volatility outside the SMM configuration (market ABM), prices unrelated
to balances (commodities), and growth unrelated to industry mix (regional). Leakage tests
check that perturbing rows after a split leaves earlier forecasts unchanged, that perturbing
realized targets changes actuals but not forecasts, that the forecaster never receives rows
after the target time, and that a fit using a later cutoff raises `LeakageError`.

**Non-estimable.** `sanctions` declares `NON_ESTIMABLE`: its output is a legal-rule
determination (OFAC 50 Percent Rule least fixed point, look-through exposure, route
closure), exact given designations and ownership records, with no held-out observable to
score. Like `banking_ledger`, `sanctions_model` has no required components and can never be
validated; `validate` raises with the reason. Its `sanction_coefficient` is a gravity
regressor that the trade holdout scores when flows include a `sanction` regressor.
`two_region_migration` is also unlinked (no required components): the regional holdout
scores employment growth, and migration responses need Census PEP population components.

Revision leakage is assumed unless the data mapping declares
`information_time: 'real_time'` (row dates are publication dates).
`attach_calibration(registry, report, process_id='taylor_rule_policy_rate')` also
validates the linked numeric processes (`conflict_event_intensity` for conflict).

```sh
wm validate monetary_model --family-data fred_panel.json \
   --train-end 2000-12-31 --validation-end 2005-12-31 --cutoff 2019-12-31
```

## Running on data

`worldmodel/estimation/loaders.py` turns published normalized datasets into these inputs.
`COMPONENT_SOURCES` declares, per component series, the dataset, its published metric and
unit, the requirement it maps to and the availability policy it supports; `BLOCKED_COMPONENTS`
and `BLOCKED_FAMILIES` name the exact series still missing and the dataset that must publish
them. Family loaders build the native mappings (`conflict`, `assets`, `commodities`,
`regional`). Loaders stream `records.jsonl.gz`, discard lines by substring before parsing,
and keep each record's dataset reference and id so estimates carry lineage.

```sh
wm estimation-load                      # catalog availability for every component and family
wm estimation-load inventory_balance    # what one loader selects, with its evidence digest
wm calibrate-all                        # every pre-registered attempt in real_data_plan.json
```

`worldmodel/estimation/real_data_plan.json` freezes each attempt (splits, loader options,
entity-selection rules, vintage policy) before any holdout is scored, and
[docs/calibration-status.md](calibration-status.md) records what happened, including the
failures. Reports are published to `data/calibration_reports/`.

For ad-hoc runs on datasets that follow the normalization contract at the top of
`requirements.json`:

```sh
wm estimation-requirements coupled_economy
wm validate interest_pass_through --data fred_policy_rate --data <prime-rate dataset> \
   --train-end 2005-12-31 --validation-end 2015-12-31 --cutoff 2025-06-30
```

Current-vintage FRED downloads have no `realtime_start`. For those, use
`--vintage-policy retrospective` for exploration only. Revised series (CPI,
payrolls, GDP, deposits, consumer credit, population) then fail
`no_revision_leakage` until ALFRED or vintage-file records are acquired.

The offline demo uses fictional synthetic data with known parameters:

```python
from worldmodel.estimation import ObservationSet, estimator_for, validate_process
from worldmodel.estimation.synthetic import component_dataset
ds = component_dataset('interest_pass_through', seed=1)
report = validate_process(estimator_for('interest_pass_through'), ObservationSet(ds['records']),
                          train_end=ds['train_end'], validation_end=ds['validation_end'], cutoff=ds['cutoff'])
print(report['validated'], report['final_estimate']['parameters'], ds['truth'])
```

Tests: `python3 -m unittest tests.test_estimation_methods tests.test_estimation_validation tests.test_estimation_families tests.test_estimation_model_families`.

## Limits

- Estimates are reduced-form and descriptive (`causally_identified: false`).
  Out-of-sample skill does not identify responses to interventions. Instruments
  such as crude prices for gasoline demand are assumptions.
- Gaussian predictive intervals come from in-sample residual scale. Multi-step
  intervals for regression components use a √h approximation.
- ECM t-statistics under unit roots are nonstandard. MA invertibility for q>1 is
  not enforced. Logistic growth is often weakly identified, and the fit flags this.
- SMM standard errors assume a locally smooth simulator. Integer-valued simulator
  parameters make the objective piecewise constant, so prefer ABC for those.
- Pooled DM tests across field cells, gravity pairs, districts, members or regions ignore
  cross-sectional dependence (for example the shared national swing within an election cycle).
- Model-family holdouts on laptop-scale synthetic fixtures have few time points (8–14 cycles,
  10 ABM windows); passing them shows the forecaster and criteria work, not that a family
  forecasts real data.
- `requirements.json` and `worldmodel/reference/**` ship as package data. See
  `[tool.setuptools.package-data]` in pyproject.toml and `MANIFEST.in`.
- Hooks map reduced-form estimates onto daily mechanisms by declared assumption:
  - Quarterly and monthly coefficients use `period_days` cadences.
  - Deposit growth is a retention rule, because the economy is closed.
  - Credit growth generates credit limits; it is not an aggregate identity.
