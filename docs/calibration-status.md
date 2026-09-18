# Calibration status on real data

What the estimation layer produced when it was run on the normalized datasets published
in this catalog on 2026-09-15, 2026-09-16 and 2026-09-17. Every attempt was pre-registered in
[`worldmodel/estimation/real_data_plan.json`](../worldmodel/estimation/real_data_plan.json)
— splits, loader options, entity-selection rules and acceptance criteria were frozen
before any holdout was scored. Nothing below was re-specified after seeing a test result.

**Eight current attempt runs pass (ten including two superseded ones), and four of the 22
processes are validated** — reconciled 2026-09-17, after all seven waves.
The counts come from the 75 published validation reports in
[`data/calibration_reports/`](../data/calibration_reports/README.md), not from this file; the
Summary table below now agrees with them row for row. The unit is the pre-registered attempt id
(the five `cash_balance` issuers are one attempt), and *current* means not superseded by the plan:
**51 attempts registered, 30 current and 21 superseded**, plus the two recorded as `not_run` on
compute budget. Of the 30 current attempts, **8 pass and 22 fail**.

`monetary_model.fred_realtime_v2` (fourth wave) was the first process to validate. Two joined it
after the interval work: `inventory_balance.eia_weekly_v2` and `elections_model.medsl_house_districts`
each meet every declared criterion and each is the only required component of its process, so
`resource_inventory` and `elections_model` are **validated** as well. The fourth is `assets_model`,
on WS-A's real-time FX panel (`assets_model.fred_fx_realtime`) — a different declared estimand from
the equity attempt, which keeps its failing verdict. `monetary_model` and `assets_model` are the two
whose required component also carries *failing* current attempts (`cpi_okun_proxy_v2` and
`okun_unrate_realtime_v2`; `alpaca_daily`): each is validated on the specification and series it
declared, and the failures beside it are recorded below rather than averaged away.
`credit_growth.fred_realtime_v3` and `interest_pass_through.fred_realtime_v2` also pass, and
`default_hazard` passes on two variants of a *substituted* delinquency series but fails on its
declared primary series, so it must still be read as not validated on the series
`requirements.json` names. `coupled_economy` needs nine components and has **three passing — one of
them only on a substitute — and six failing**.
`regional_model` fails on the short CBP panel, on the long QCEW panel, and on the real-time CES SAE
panel — but the *reason* changed in the seventh wave: on the real-time panel it passes
`no_revision_leakage` and `interval_coverage` and fails on skill. Every failure below
is a result too, recorded with its reason.

**What blocks the 22 failing current attempts**, one count per criterion:
`beats_persistence_dm` 14, `parameters_within_declared_bounds` 7, `interval_coverage` 7,
`no_revision_leakage` 6, `beats_year_effect_only_dm` 2, `volatility_crps_skill` 1,
`minimum_test_forecasts` 0. Against the same measure on 2026-09-16 (23 current attempts, 6 passing)
two of those counts went **up**: `beats_persistence_dm` 9 → 14 and
`parameters_within_declared_bounds` 6 → 7. That is the honest shape of this push. Ten attempts that
cleared a data or scoring problem — three `deposit_rate_pass_through` on panels long enough to
score, `labor_demand_v4`, `policy_rule_v3`, `energy_purchasing_v3`, `conflict_model_v2`,
`monetary_model.okun_unrate_realtime_v2` and the two real-time `regional_model.ces_sae` runs — were
then graded on a skill test that the earlier failure had made untestable, and failed it. Five older
skill failures were retired with their superseded attempts, leaving a net +5. The new
`beats_year_effect_only_dm` count is the same effect: a criterion no regional attempt could reach
until it had vintaged rows.

**No attempt now fails `minimum_test_forecasts`** (panel-length wave, 2026-09-17). The two that did
were data-quantity failures, and five new attempts on longer panels clear the criterion — while
adding no passes: `population_growth_rate` fails `interval_coverage` on both its sources and
`deposit_rate_pass_through` fails `beats_persistence_dm` on the declared SNDR series and on both
substitute series. Lengthening a panel revealed a real failure that n = 16 could not test.

**`interval_coverage` blocked 16 of the rows this file then carried, and now blocks 6 of those 16**
(WS-E, 2026-09-17, no new data); across all 30 current attempts, including the ones registered after
WS-E, it blocks 7.
`interest_pass_through.fred_realtime_v2` became a full pass, the seventh current passing run of the
eight.
`labor_demand`, `policy_rule`, `energy_purchasing` and `conflict_model` had their coverage failure
removed and still fail on skill or on declared bounds, which moves them from "the uncertainty is
wrong" to "there is no demonstrated edge over a random walk". Two code defects were found and
fixed: a real-time forecast was being graded with a **mixed-vintage conditional input**, which put
a fabricated 22% collapse in industrial production into every `labor_demand` design row, and
conflict counts were scored **Poisson** while the family's own simulator draws negative binomial.
No threshold was relaxed; the declared selection rule twice rejected the wider interval that would
have passed, and both cases are recorded. No process became validated.

*Validated* here means exactly one thing: every declared acceptance criterion passed on a
holdout that was untouched until it was scored. It does not mean the model is right.

```sh
python3 -m worldmodel calibrate-all                        # rerun every attempt
python3 -m worldmodel calibrate-all --attempt inventory_balance.eia_weekly
python3 -m worldmodel estimation-load inventory_balance    # inspect what a loader selects
python3 -m worldmodel estimation-load                      # catalog availability per component/family
python3 -m worldmodel calibration-status calibration_reports@<version>   # re-verify a published report
```

`calibration-status` reads a published report back, recomputes its digest, re-evaluates the
declared criteria and reports the resulting process state — it does not trust the stored
`validated` flag.

Reports and estimates are immutable artifacts in
[`data/calibration_reports/`](../data/calibration_reports/README.md); each pins its input
dataset versions, the loader's evidence digest, the code snapshot and the rights inherited
from the sources.

## Summary

Fifty-one rows, one per pre-registered attempt id, checked row for row against the published
validation reports on 2026-09-17: the failing-criteria column now matches
`acceptance.results` in every case. Two corrections came out of that check.
`energy_purchasing.fred_realtime_v2` had a published report from the fourth wave and **no row here**,
which is why the earlier counts in this file said 35 attempts and 16 `interval_coverage` failures
where the reports said 33 registered (35 with the two `not_run`) and 17; its row is below.
`default_hazard.fdic_laus_quarterly` is marked **pass** without a superseded tag, but the plan
supersedes it, which is why it counts among the two superseded passes and not the eight current ones.
`monetary_model.okun_unrate_realtime` is the one row with no report: it raised during fitting, so no
criterion was ever evaluated, and it is excluded from every pass/fail count below.

| Attempt | Process / component | Data (dataset@version, window) | n test | Verdict | Failing criteria |
| --- | --- | --- | --- | --- | --- |
| `inventory_balance.eia_weekly` | resource_inventory / inventory_balance | eia_energy@f5cd9308, weekly 1991-02..2024-12 (1,767 obs) | 258 | **fail** | interval_coverage |
| `demand_price_elasticity.eia_monthly` | coupled_economy / demand_price_elasticity | eia_energy@f5cd9308 + fred_oil_price@847b0f93, monthly 1990-09..2024-10 (409) | 106 | **fail** | parameters_within_declared_bounds |
| `price_adjustment.eia_monthly` | coupled_economy / price_adjustment | eia_energy@f5cd9308 + fred_oil_price@847b0f93, monthly 1991-10..2024-11 (398) | 106 | **fail** | parameters_within_declared_bounds |
| `cash_balance.sec_companyfacts` (5 issuers) | investment_cash_flow / cash_balance | sec_company_assets@68335122, quarterly 2008-2025 | 14–23 each | **fail** (5/5) | beats_persistence_dm (all), interval_coverage (3), bounds (2) |
| `population_growth_rate.census_pep` | population_growth / population_growth_rate | census_population@d621c962, annual 2010-2024 (14) | 4 | **fail** (superseded) | minimum_test_forecasts, interval_coverage |
| `conflict_model.ucdp_monthly` | conflict_model | ucdp_conflicts@65486acc + vdem@9ca2ac85, 20 countries × 300 months (6,000 rows) | 1,200 | **fail** | beats_persistence_dm, interval_coverage, no_revision_leakage |
| `assets_model.alpaca_daily` | assets_model | alpaca_daily_bars@a825e6f0 + fred_policy_rate@2a3197b9, 5 symbols, daily 2016-2024 | 1,890 | **fail** | volatility_crps_skill, no_revision_leakage |
| `commodities_model.eia_weekly_balance` | commodities_model | eia_energy@f5cd9308 + fred_oil_price@847b0f93, weekly 2010-2024 (782) | 20 | **fail** | beats_persistence_dm, bounds, no_revision_leakage |
| `regional_model.cbp_state_sectors` | regional_model | census_business@5b0995c7, 51 states × NAICS sectors, 2019-2023 | 51 | **fail** | interval_coverage, no_revision_leakage |
| `default_hazard.fdic_laus_quarterly` | coupled_economy / default_hazard | fdic_bank_financials@6283087f + bls_labor@50917b81 + fred_policy_rate@2a3197b9, quarterly 2010-2025 (62 obs) | 19 | **pass** | none |
| `monetary_model.cpi_okun_proxy` | monetary_model | fred_cpi@c92b26cd + fred_policy_rate@2a3197b9 + bls_labor@50917b81, monthly 1990-2024 (420) | 120 | **fail** (superseded) | beats_persistence_dm, no_revision_leakage |
| `interest_pass_through.fred_realtime` | coupled_economy / interest_pass_through | fred_macro_panel@b395bda0 (DPRIME) + fred_policy_rate@2a3197b9, monthly 1955-2024 (830) | 83 | **fail** | interval_coverage |
| `deposit_rate_pass_through.fred_realtime` | coupled_economy / deposit_rate_pass_through (optional) | fred_macro_panel@b395bda0 (SNDR) + DFF, monthly 2021-2026 (62) | 16 | **fail** (superseded) | minimum_test_forecasts, beats_persistence_dm, interval_coverage |
| `default_hazard.fred_primary_realtime` | coupled_economy / default_hazard | fred_macro_panel@b395bda0 (DRCCLACBS, UNRATE) + DFF, quarterly 1991-2025 (138) | 23 | **fail** | parameters_within_declared_bounds |
| `deposit_growth.fred_realtime` | coupled_economy / deposit_growth | fred_macro_panel@b395bda0 (DPSACBW027SBOG) + DFF, monthly 1973-2024 (622) | 59 | **fail** | beats_persistence_dm |
| `credit_growth.fred_realtime` | coupled_economy / credit_growth | fred_macro_panel@b395bda0 (TOTALSL), monthly 1943-2024 (975) | 141 | **fail** | interval_coverage |
| `energy_purchasing.fred_realtime` | coupled_economy / energy_purchasing | fred_macro_panel@b395bda0 (RRSFS) + fred_oil_price + DFF, monthly 1992-2024 (393) | 83 | **fail** | beats_persistence_dm, interval_coverage, bounds |
| `labor_demand.fred_realtime` | coupled_economy / labor_demand | fred_macro_panel@b395bda0 (PAYEMS, INDPRO), monthly 1939-2024 (1,029) | 143 | **fail** | beats_persistence_dm, interval_coverage |
| `policy_rule.fred_realtime` | coupled_economy / policy_rule | fred_macro_panel@b395bda0 (FEDFUNDS, CPIAUCSL, GDPC1, GDPPOT), quarterly 1955-2024 (277) | 47 | **fail** | beats_persistence_dm, interval_coverage |
| `monetary_model.fred_realtime_quarterly` | monetary_model | fred_macro_panel@b395bda0 (FEDFUNDS, GDPC1, GDPPOT) + fred_cpi@c92b26cd, quarterly 1995-2024 (120) | 40 | **pass** (superseded) | none |
| `policy_rule.fred_realtime_v2` | coupled_economy / policy_rule | fred_macro_panel@7dcce89c, quarterly 1955-2024 (277) | 47 | **fail** | beats_persistence_dm, interval_coverage |
| `credit_growth.fred_realtime_v2` | coupled_economy / credit_growth | fred_macro_panel@7dcce89c (TOTALSL corrected), monthly 1943-2024 (975) | 141 | **fail** | interval_coverage |
| `labor_demand.fred_realtime_v2` | coupled_economy / labor_demand | fred_macro_panel@7dcce89c (INDPRO all bases), monthly 1939-2024 (1,029) | 143 | **fail** | beats_persistence_dm, interval_coverage |
| `energy_purchasing.fred_realtime_v2` | coupled_economy / energy_purchasing | fred_macro_panel@7dcce89c (RRSFS) + fred_oil_price@a42622a0 + DFF, monthly 1992-2024 (393) | 83 | **fail** (superseded) | beats_persistence_dm, interval_coverage, bounds |
| `monetary_model.fred_realtime_v2` | monetary_model | fred_macro_panel@7dcce89c + fred_cpi@34fe03f5, quarterly first releases, base-paired (106) | 36 | **pass** | none |
| `inventory_balance.eia_weekly_v2` | resource_inventory / inventory_balance | eia_energy@f5cd9308, weekly 1991-02..2024-12 (1,767) | 258 | **pass** | none |
| `credit_growth.fred_realtime_v3` | coupled_economy / credit_growth | fred_macro_panel@7dcce89c (TOTALSL), monthly 1943-2024 (975) | 141 | **pass** | none |
| `regional_model.cbp_state_sectors_v2` | regional_model | census_business@5b0995c7, 51 states × NAICS sectors, 2019-2023 | 51 | **fail** | interval_coverage, no_revision_leakage |
| `regional_model.qcew_state_sectors` | regional_model | bls_labor@ffd7f43a (QCEW private, 53 areas × 20 sectors, 2005-2024) | 318 | **fail** | interval_coverage, no_revision_leakage |
| `elections_model.medsl_house_districts` | elections_model | mit_election_returns@017e0d4f + fec_candidates@a46824fd + fec@93722c10 + fred_macro_panel@7dcce89c, 13 cycles (5,476 district-cycles) | 774 | **pass** | none |
| `default_hazard.fdic_cps_quarterly` | coupled_economy / default_hazard | fdic_bank_financials@6283087f + bls_labor@ffd7f43a (LNS14000000) + DFF, quarterly 2010-2025 (62) | 19 | **pass** | none |
| `default_hazard.fdic_laus_quarterly_v2` | coupled_economy / default_hazard | same on the completed bls_labor@ffd7f43a | 19 | **pass** | none |
| `monetary_model.cpi_okun_proxy_v2` | monetary_model | fred_cpi@34fe03f5 + DFF + bls_labor@ffd7f43a (LAUS states), monthly 1990-2024 (420) | 120 | **fail** (superseded) | beats_persistence_dm, no_revision_leakage |
| `assets_model.fred_fx_realtime` | assets_model | fred_macro_panel@7dcce89c (DEXJPUS, DEXUSUK, DEXCAUS, DEXSZUS, DEXUSAL, DEXUSEU, DFF first releases), daily 2014-03..2024-12 (13,480 bars) | 1,875 | **pass** | none |
| `monetary_model.okun_unrate_realtime` | monetary_model | fred_macro_panel@7dcce89c (CPIAUCSL, UNRATE, DFF first releases), monthly 2005-06..2024-12 (235) | — | **failed to fit** | smoothing not below one (unidentified) |
| `monetary_model.okun_unrate_realtime_v2` | monetary_model | fred_macro_panel@7dcce89c (CPIAUCSL, UNRATE, FEDFUNDS first releases), monthly 1996-12..2024-12 (337) | 120 | **fail** | beats_persistence_dm, parameters_within_declared_bounds |
| `population_growth_rate.census_pep_v2` | population_growth / population_growth_rate | census_population@063a1413 (20 PEP vintages), annual 2000-2025 (26) | 9 | **fail** | interval_coverage |
| `population_growth_rate.fred_popthm` | population_growth / population_growth_rate | fred_macro_panel@7dcce89c (POPTHM, 325 ALFRED vintages), annual 1959-2025 (67) | 16 | **fail** | interval_coverage |
| `deposit_rate_pass_through.fred_realtime_v2` | coupled_economy / deposit_rate_pass_through (optional) | fred_macro_panel@7dcce89c (SNDR) + DFF, monthly 2021-04..2026-07 (64), splits recut | 24 | **fail** | beats_persistence_dm, interval_coverage |
| `deposit_rate_pass_through.savnrnj_substitute` | coupled_economy / deposit_rate_pass_through (optional) | fred_deposit_rates@ec9e94d6 (**SAVNRNJ substitute**) + DFF, monthly 2009-05..2021-02 (142) | 38 | **fail** | beats_persistence_dm |
| `deposit_rate_pass_through.m2own_substitute` | coupled_economy / deposit_rate_pass_through (optional) | fred_deposit_rates@ec9e94d6 (**M2OWN substitute**) + DFF, monthly 1959-02..2019-05 (724) | 29 | **fail** | beats_persistence_dm, interval_coverage |
| `interest_pass_through.fred_realtime_v2` | coupled_economy / interest_pass_through | fred_macro_panel@7dcce89c (DPRIME) + DFF, monthly 1955-2024 (830) | 83 | **pass** | none |
| `labor_demand.fred_realtime_v3` | coupled_economy / labor_demand | fred_macro_panel@7dcce89c (PAYEMS, INDPRO), monthly 1939-2024 (1,029) | 143 | **fail** | beats_persistence_dm |
| `labor_demand.fred_realtime_v4` | coupled_economy / labor_demand | same | 143 | **fail** | beats_persistence_dm, interval_coverage |
| `policy_rule.fred_realtime_v3` | coupled_economy / policy_rule | fred_macro_panel@7dcce89c (FEDFUNDS, CPIAUCSL, GDPC1, GDPPOT), quarterly 1955-2024 (277) | 47 | **fail** | beats_persistence_dm |
| `energy_purchasing.fred_realtime_v3` | coupled_economy / energy_purchasing | fred_macro_panel@7dcce89c (RRSFS) + fred_oil_price + DFF, monthly 1992-2024 (393) | 83 | **fail** | beats_persistence_dm, parameters_within_declared_bounds |
| `conflict_model.ucdp_monthly_v2` | conflict_model | ucdp_conflicts@65486acc + vdem@9ca2ac85, 20 countries × 300 months (6,000) | 1,200 | **fail** | beats_persistence_dm, no_revision_leakage |
| `regional_model.qcew_state_sectors_v2` | regional_model | bls_labor@ffd7f43a (QCEW private, 53 areas × 20 sectors, 2005-2024) | 318 | **fail** (rejected candidate) | no_revision_leakage |
| `regional_model.cbp_state_sectors_v3` | regional_model | census_business@5b0995c7, 51 states × NAICS sectors, 2019-2023 | 51 | **fail** | interval_coverage, no_revision_leakage |
| `regional_model.ces_sae_realtime` | regional_model | fred_state_employment_vintages@07d42b0a (CES SAE first releases, 51 states × 10 supersectors, 2012-2025) | 306 | **fail** | beats_persistence_dm, beats_year_effect_only_dm |
| `regional_model.ces_sae_realtime_v2` | regional_model | same panel, `per_unit_year_draw` intervals | 306 | **fail** | beats_persistence_dm, beats_year_effect_only_dm |

Baseline names below: *persistence* = last value, *drift* = linear extrapolation,
*mean* = historical mean, plus each family's supplied mechanism-off baseline. All
Diebold-Mariano (DM) p-values are one-sided squared-loss tests with the HLN correction:
small p means the model has lower loss.

## Components

### resource_inventory / inventory_balance — fail (interval coverage)

*Superseded by `inventory_balance.eia_weekly_v2` (fifth wave), which passes on the same data and
splits with a declared predictive distribution. This record is kept unchanged.*

Weekly U.S. crude balance, `retrospective` vintage policy (EIA bulk records carry only an
acquisition timestamp; the requirement's 5-day publication lag is applied, and the
component allows the `minor` revision class).

| Parameter | Estimate | SE | Maps to |
| --- | --- | --- | --- |
| `flow_scale` | 0.5612 | 0.0280 | `flow_scale` |
| `unmeasured_net_flow` | 1.7499 barrels/s | 0.1800 | `unmeasured_net_flow` |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (thousand barrels) | 3,755.4 | 4,399.0 | 4,406.7 | 107,443 |
| RMSE | 4,731.4 | 5,787.6 | 5,790.3 | 113,406 |
| CRPS | 2,730.2 | 3,194.6 | 3,200.0 | 74,176 |
| DM p vs model | — | 0.00093 | 0.00088 | 1.2e-62 |

80% interval coverage 0.640 (nominal 0.80, tolerance ±0.15), mean width 8,285, bias −1,832,
0 skipped origins, 258 holdout forecasts (2020-2024).

**Verdict: fail.** The only failing criterion is `interval_coverage`: the Gaussian interval
from the in-sample residual scale is too narrow for weekly stock changes, which are
fat-tailed (hurricanes, SPR transfers, the 2020 demand collapse). The mechanism itself has
real skill — it beats persistence and drift at p < 0.001 — and `flow_scale` 0.56 says the
published weekly flows explain only about half of the reported stock change, the rest being
the balancing item EIA itself reports as "adjustment".

### coupled_economy / demand_price_elasticity — fail (declared parameter bounds)

Monthly gasoline demand, 2SLS with the crude price as the excluded cost shifter,
`retrospective` policy (the two EIA series have no vintages; DCOILWTICO has real ALFRED
vintages).

| Parameter | Estimate | SE | Declared bounds | Inside? |
| --- | --- | --- | --- | --- |
| `elasticity` (long run) | −0.1242 | 0.0210 | [0, 4] | no |
| `short_run_elasticity` | +0.0232 | 0.0090 | [−4, 0] | no |
| `persistence` | 0.8134 | 0.0534 | [0, 0.999] | yes |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (thousand b/d) | 253.9 | 290.3 | 290.7 | 594.2 |
| RMSE | 375.7 | 407.6 | 408.2 | 710.9 |
| CRPS | 188.5 | 211.9 | 212.1 | 402.4 |
| DM p vs model | — | 0.063 | 0.061 | 3.9e-11 |

Coverage 0.802, first-stage partial F 5,232 (the declared `strong_instrument` criterion
passes easily), 106 holdout forecasts (2016-2024).

**Verdict: fail.** The sign convention is the problem, not the fit: the estimated
short-run price response is *positive* (+0.023) and the derived long-run `elasticity`,
which the process expects as a positive magnitude, comes out negative. In plain terms,
higher retail prices in the same month are associated with slightly *higher* gasoline
volumes once last month's volume and seasonality are controlled — a simultaneity result
that the crude-price instrument does not remove, because crude shocks move retail prices and
refinery runs together. Forecast skill against persistence is marginal (p = 0.063).

### coupled_economy / price_adjustment — fail (declared parameter bounds)

Monthly retail gasoline price adjustment toward an inventory-gap target.

| Parameter | Estimate | SE | Declared bounds | Inside? |
| --- | --- | --- | --- | --- |
| `adjustment` (per day) | −0.0035 | 0.0016 | [0, 1] | no |
| `adjustment_per_month` | −0.1072 | 0.0474 | [0, 5] | no |
| `cost_pass_through` | 0.4336 | 0.0644 | [0, 1.5] | yes |
| `drift_per_month` | 0.0012 | 0.0019 | [−0.1, 0.1] | yes |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (USD/gal) | 0.085 | 0.125 | 0.125 | 0.799 |
| RMSE | 0.121 | 0.176 | 0.176 | 0.984 |
| CRPS | 0.063 | 0.094 | 0.094 | 0.558 |
| DM p vs model | — | 0.00024 | 0.00025 | 7.4e-12 |

Coverage 0.840, 1 skipped origin, 106 holdout forecasts.

**Verdict: fail.** Cost pass-through is sensible and precisely estimated (0.43 of a crude
move within the month), and the forecasts clearly beat persistence, but the inventory-gap
coefficient has the wrong sign for the declared mechanism: prices rise when gasoline stocks
are *above* their trailing mean. Monthly stocks are strongly seasonal (builds in winter
coincide with weak prices), so the trailing-12-month gap is picking up seasonality rather
than scarcity. Fixing this needs a seasonally adjusted gap, which is a re-specification and
would have to be filed as a new pre-registered attempt.

### investment_cash_flow / cash_balance — fail on all five issuers

Quarterly SEC companyfacts, `strict` policy: `observed_at` is the filing date, so
availability is genuinely real-time, and restatements enter as later observations. Quarterly
flows are recovered by differencing as-filed cumulative durations (which is how capital
expenditure is reported) and by annual-minus-three-quarters for the fourth quarter.

| Issuer (CIK) | Quarters fit | n test | `cash_conversion` (SE) | `unmeasured_net_cash_flow` (SE) | MAE (model / persistence) | DM p | Coverage | Failing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CNX Resources (0001070412) | 68 | 23 | 0.0094 (0.0235) | 0.157 (2.73) | 2.46e7 / 2.14e7 | 0.93 | 1.00 | DM, coverage |
| Stanley Black & Decker (0000093556) | 31 | 14 | 0.0628 (0.1148) | −1.730 (5.58) | 7.21e7 / 7.30e7 | 0.70 | 1.00 | DM, coverage |
| Valaris (0000314808) | 32 | 16 | 0.0090 (0.0345) | 1.840 (4.31) | 1.42e8 / 1.37e8 | 0.81 | 0.94 | DM |
| Enterprise Products (0001061219) | 58 | 20 | −0.0132 (0.0531) | 2.106 (5.82) | 4.52e8 / 4.50e8 | 0.38 | 0.80 | DM, bounds |
| Pitney Bowes (0000078814) | 68 | 16 | −0.1220 (0.2610) | 0.245 (2.05) | 8.33e7 / 7.86e7 | 0.60 | 1.00 | DM, coverage, bounds |

**Verdict: fail (5/5).** `cash_conversion` — the share of (revenue − costs − capex) that
lands in the cash account within the quarter — is statistically indistinguishable from zero
everywhere, and negative for two issuers (outside the declared [0, 2] bounds). The
identity the component assumes is too coarse for real filers: financing flows, acquisitions,
working capital and short-term investment reclassifications dominate the quarterly change in
cash. The random-walk baseline is never beaten. Coverage of 1.00 on three issuers shows the
predictive intervals are far too wide for the same reason (the level-sigma is estimated on
noisy differences). 5–7 origins per issuer were skipped because a required series was not yet
filed at the origin.

### population_growth / population_growth_rate — fail (too few forecasts)

*Superseded by `population_growth_rate.census_pep_v2` and joined by
`population_growth_rate.fred_popthm` (panel-length wave). Both clear `minimum_test_forecasts` and
both still fail `interval_coverage`. This record is kept unchanged.*

Census PEP national July-1 population, vintages 2020 and 2021-2024, `strict` policy:
`attributes.released_at` gives real publication dates, so the audit reports `real_time`
vintages and no revision leakage.

| Parameter | Estimate | SE |
| --- | --- | --- |
| `growth_rate_per_year` | 0.006777 | 0.000472 |
| `annual_growth_fraction` | 0.006800 | 0.000476 |

Holdout (2019-2024): 4 forecasts, MAE 1.28e6 people vs persistence 3.36e6 and drift 1.36e6,
CRPS 1.07e6 vs 3.02e6, DM vs persistence p = 0.020, coverage 0.50, 2 skipped origins.

**Verdict: fail.** `minimum_test_forecasts` (4 < 8) and `interval_coverage` (0.50). This was
expected and declared in advance: the published PEP vintages only cover 2010-2024, and the
component needs 8 observations before it can fit at all, leaving at most 7 possible origins.
The drift estimate itself is reasonable (0.68%/yr). A longer real-time series (POPTHM ALFRED
vintages from `fred_macro_panel`, or older PEP vintage files) would make this testable.

## Model families

### assets_model — fail (volatility skill, revision leakage)

Five pre-declared mega-caps (AAPL, MSFT, JNJ, XOM, KO) with SPY's excess return as the
supplied market factor and DFF/252 as the daily risk-free rate; GARCH(1,1) variance filtered
to each origin; refit every 21 origins; holdout 2023-07..2024-12.

| Metric | Model | `constant_volatility` | Persistence | Mean |
| --- | --- | --- | --- | --- |
| MAE (log return) | 0.0079699 | 0.0079699 | 0.0122854 | 0.0086118 |
| RMSE | 0.010948 | 0.010948 | 0.016393 | 0.011707 |
| CRPS | 0.0058384 | 0.0058620 | 0.0092784 | 0.0065267 |
| DM p vs model | — | degenerate (identical means) | 3.6e-47 | 4.2e-05 |

1,890 forecasts, coverage 0.841, bias 0.0006.

**Verdict: fail.** Conditional on realized factor returns the factor model beats persistence
and the historical mean decisively, and its 80% intervals are well calibrated. It fails the
two criteria that matter for this family: `volatility_crps_skill` requires CRPS ≤ 0.99× the
same means with constant volatility, and the GARCH filter delivers only 0.996× — on this
sample the conditional-variance path adds almost nothing over the unconditional variance; and
`no_revision_leakage`, because total-return adjusted closes are restated by later corporate
actions, so the row dates are not information times. An unadjusted or point-in-time price
feed would make the second criterion evaluable.

### commodities_model — fail (no skill, bounds, revision leakage)

Weekly U.S. crude balance sheet (production, refiner net input as consumption, net imports,
ending stocks) with the WTI weekly mean price; holdout 2021-2024, refit every 4 origins.

| Parameter | Estimate | SE | Bounds | Inside? |
| --- | --- | --- | --- | --- |
| `demand_elasticity` | +0.0013 | 0.0115 | [−5, 0] | no |
| `supply_elasticity` | −0.1031 | 0.0107 | [0, 5] | no |
| `storage_elasticity` | 0.2556 | 0.0212 | [0, 20] | yes |
| `target_stocks_to_use` | 3.3253 | — | [0, 10] | yes |

20 scored forecasts out of 208 possible origins: **189 origins were skipped** because the
market-clearing step refuses to run when the fitted demand elasticity is non-negative or the
storage elasticity is negative. Where it did clear, MAE was 50.3 USD/bbl against 1.47 for
persistence (DM p = 1.0) and the bias was −50.3.

**Verdict: fail.** The weekly petroleum balance does not identify a downward-sloping demand
curve: weekly refinery runs and prices move together with demand, so the OLS demand slope is
essentially zero with the wrong sign, and the lagged-price supply slope is negative. The
model is not usable as a price mechanism at this frequency; a monthly or annual balance with
a genuine supply shifter (USDA PSD or EIA monthly with an instrument) is the declared next
step.

### regional_model — fail (interval coverage, revision leakage)

*Superseded by `regional_model.cbp_state_sectors_v2` and joined by
`regional_model.qcew_state_sectors` (fifth wave); both still fail. This record is kept unchanged.*

County Business Patterns employment by state and NAICS sector, 2019-2023 (base-year shares
2019, validation target 2022, holdout target 2023 = 51 state forecasts).

`shift_share_elasticity` 2.387 (cluster-robust SE 0.840, n = 204 state-years, year fixed
effects, leave-one-out shocks).

| Metric | Model | `year_effect_only` | Persistence | Mean |
| --- | --- | --- | --- | --- |
| MAE (log growth) | 0.011558 | 0.023979 | 0.023150 | 0.024309 |
| RMSE | 0.013899 | 0.026587 | 0.028199 | 0.027560 |
| CRPS | 0.014032 | 0.018272 | 0.026610 | 0.017953 |
| DM p vs model | — | 4.1e-06 | 6.2e-06 | 3.7e-06 |

**Verdict: fail.** The shift-share mechanism genuinely adds skill: it halves the MAE of the
year-effect-only baseline that switches the mechanism off, at p = 4e-06. It fails
`interval_coverage` (1.00 against a nominal 0.80 — the predictive spread built from
between-year effect variance is far too wide with only three usable growth years) and
`no_revision_leakage` (CBP is published once a year with no vintages in this dataset, so row
dates are not information times). A longer panel (QCEW from `bls_labor`, 2014-2025) is the
declared fix.

### conflict_model — fail (no skill against persistence, coverage, revision leakage)

UCDP GED state-based monthly event counts (release 26.1) for the 20 countries with the most
events in the *training* window 2000-01..2015-12, with the previous year's V-Dem polyarchy
as the background covariate; Hawkes fit refit every 12 origins; holdout 2020-01..2024-12
(60 months × 20 countries = 1,200 forecasts). Neighbours are omitted: no contiguity dataset
is published.

| Parameter | Estimate | Note |
| --- | --- | --- |
| `self_excitation` | 0.988 | at the stability boundary (branching ratio ≈ 1) |
| `neighbor_excitation` | 0 | no neighbour graph supplied |
| `decay` | 0.2108 | monthly retention of the excitation kernel |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (events/country-month) | 12.944 | 13.071 | 13.111 | 54.387 |
| RMSE | 51.223 | 50.919 | 51.009 | 159.78 |
| CRPS | 11.185 | 12.269 | 12.295 | 43.174 |
| DM p vs model | — | 0.58 | 0.56 | 8.0e-09 |

Coverage 0.596, mean width 11.2, bias −0.53, 0 skipped origins.

**Verdict: fail.** The Hawkes intensity is slightly better than persistence on MAE and CRPS
but not significantly (DM p = 0.58), its Poisson intervals are far too narrow for
over-dispersed conflict counts (coverage 0.60), and `no_revision_leakage` fails because UCDP
annual releases revise earlier months and the row dates are event months, not publication
dates. Self-excitation pinned at 0.988 says the fit is absorbing near-random-walk persistence
into the excitation term. The negative-binomial alternative (`model: negative_binomial`) and a
published contiguity graph are the declared next steps.

Two mechanical corrections were needed before this attempt could run at all, both made before
any result existed and both recorded in the plan: the split boundaries were written as
`YYYY-MM` (rejected by `validate_process`), and the loader emitted event counts as floats
while the Hawkes fit requires integers.

## Second wave (fred_cpi and bls_labor landed)

`fred_cpi` published with **full ALFRED vintages** (CPIAUCSL 1947-2026, 3,362 vintage records,
`realtime_start`/`realtime_end` on every observation) and `bls_labor` published with payrolls
and LAUS, both current-vintage only. `fred_macro_panel` had acquired but not published, so
GDPC1, GDPPOT, INDPRO, FEDFUNDS, TOTALSL, RRSFS, DPRIME, SNDR, DPSACBW027SBOG and DRCCLACBS
were still unavailable. Two further attempts were pre-registered with their splits and then
run; the nine earlier attempts were not rerun because every dataset version behind them was
unchanged.

### coupled_economy / default_hazard — **pass** (all six criteria)

Quarterly fractional-logit hazard, `retrospective` policy (FDIC and LAUS carry no vintages;
the component allows the `minor` revision class; DFF resolves `real_time`). Two substitutions
were declared as overrides in the plan *before* the run, and both appear in the estimate's
data audit:

- `delinquency_rate` → **aggregate noncurrent-loan rate** of FDIC-insured banks,
  `100 × Σ bank_noncurrent_loans / Σ bank_net_loans` per report date. The declared primary
  series (FRED DRCCLACBS credit-card delinquency) is unpublished; `requirements.json` names
  FDIC call reports as the bank-panel alternative.
- `unemployment_rate` → **constructed national rate**,
  `100 × Σ unemployed / Σ labor_force` over the 51 seasonally adjusted LAUS state series.
  `bls_labor` publishes no national CPS series (LNS14000000 is absent from this build).

| Parameter | Estimate | SE |
| --- | --- | --- |
| `hazard_intercept` | −0.4301 | 0.0466 |
| `persistence` | 0.9382 | 0.0094 |
| `unemployment_sensitivity` | 2.1853 | 0.2263 |
| `rate_sensitivity` | 1.6492 | 0.2848 |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (percentage points) | 0.0289 | 0.0419 | 0.0821 | 1.2684 |
| RMSE | 0.0394 | 0.0531 | 0.0919 | 1.2807 |
| CRPS | 0.0233 | 0.0345 | 0.0538 | 0.7597 |
| DM p vs model | — | 0.020 | 0.0017 | 3.0e-12 |

19 holdout forecasts (2021-2025), 0 skipped origins, 80% coverage 0.947, bias +0.0233.
Sample 2010Q2-2025Q3 (62 quarters). Report `65dc57bb…`, artifact `1d166627…`.

**Verdict: pass — with three caveats that belong next to it.** (1) The criteria are the
component's declared defaults, unmodified, and the holdout was untouched until scored, so the
pass is real; `coupled_economy` still has eight components missing, so the *process* remains
unvalidated. (2) Coverage passes by 0.003 (0.947 against 0.80 ± 0.15) — it would fail on a
slightly different sample. (3) The fitted object is aggregate bank loan distress, not consumer
credit-card default: the parameters bind to `mechanisms.default_hazard.*`, so anyone using
them should read them as "noncurrent loans of all FDIC-insured banks respond to unemployment
and the policy rate", not as a card-delinquency model. Re-running against DRCCLACBS when
`fred_macro_panel` publishes is the declared next step, and would be a new attempt.

### monetary_model — fail (no skill against persistence, revision leakage)

Monthly Taylor rule, holdout 2015-2024 (120 forecasts, refit every 12 origins). Inflation is
read **point-in-time** from the new CPI vintages: month *t* uses its first ALFRED release and
the base month *t−12* uses the latest vintage available at that same release date, so no later
revision enters a row. The policy rate is the monthly mean of first-published DFF. The output
gap is the blocker: with GDPC1/GDPPOT unpublished it is an Okun proxy, `−2 × (u − u*)`, where
u is the constructed LAUS national rate and u* its trailing 120-month mean computed only from
earlier months.

| Parameter | Estimate | SE |
| --- | --- | --- |
| `rho` (smoothing) | 0.9774 | 0.0051 |
| `phi_pi` | 0.7517 | — |
| `phi_y` | 0.9327 | — |
| `r_star` | −1.9899 | — |
| `policy_shock_sd` | 0.1982 | — |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (percent) | 0.1818 | 0.0929 | 0.1068 | 2.0992 |
| RMSE | 0.2634 | 0.1861 | 0.1905 | 2.2505 |
| CRPS | 0.1436 | 0.0904 | 0.0935 | 1.3292 |
| DM p vs model | — | 1.00 | 1.00 | 6.6e-37 |

Coverage 0.692, bias +0.086, 0 skipped origins.

**Verdict: fail.** The rule is roughly twice as bad as a random walk on the monthly policy
rate (DM p ≈ 1.0), which is what a smoothed quarterly rule should look like at monthly
frequency, and `phi_pi` = 0.75 is below 1, so the fitted rule does not satisfy the Taylor
principle over 1990-2014 — a finding about the proxy-gap specification, not a bug.
`no_revision_leakage` also fails because the unemployment input carries no vintages. When
`fred_macro_panel` publishes GDPC1/GDPPOT/UNRATE vintages this attempt should be superseded by
a real-time-clean one with the declared gap.


## Third wave (fred_macro_panel landed: every required FRED series with ALFRED vintages)

`fred_macro_panel` published 7.57M records covering DPRIME, MPRIME, SNDR, DRCCLACBS, UNRATE,
DPSACBW027SBOG, TOTALSL, RRSFS, PAYEMS, INDPRO, FEDFUNDS, CPIAUCSL, GDPC1, GDPPOT and DFF —
**every observation carries `realtime_start`/`realtime_end`**, so all nine attempts below ran
under the **strict real-time policy** and `no_revision_leakage` passed in every one of them.
Splits were pre-registered inside each series' vintage era (nothing is visible under the
strict policy before a series entered ALFRED: DFF/DPRIME 2005-06, DRCCLACBS 2011-05,
DPSACBW027SBOG 2012-08, RRSFS 2001-06, TOTALSL/FEDFUNDS 1996-12, GDPC1 1992-12, GDPPOT 1994-01,
SNDR 2021-04).

### monetary_model — **pass** (all six criteria; the process is validated)

Quarterly Taylor rule from first-release vintages only (the rule's own frequency: `rho` is a
quarterly smoothing parameter). Holdout 2015-2024, 40 quarters, refit every 4 origins.

| Parameter | Estimate | SE |
| --- | --- | --- |
| `rho` (quarterly smoothing) | 0.9107 | 0.0213 |
| `phi_pi` | 1.0089 | — |
| `phi_y` | 1.0981 | — |
| `r_star` | −1.3625 | — |
| `policy_shock_sd` | 0.4250 | — |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (percent) | 0.2331 | 0.2666 | 0.3002 | 1.8706 |
| RMSE | 0.3293 | 0.4597 | 0.4727 | 2.0661 |
| CRPS | 0.1870 | 0.2331 | 0.2419 | 1.2159 |
| DM p vs model | — | 0.036 | 0.026 | 2.0e-11 |

Coverage 0.925, bias +0.092, 0 skipped origins. Artifact `4347efe4…`.

**Verdict: pass — but superseded, and its parameter reading was wrong.** This run was made on
the build where every chained-dollar vintage carried one unit label, and its gap needed a
trailing-mean workaround. On the rebuilt data (fourth wave) the same attempt still passes but
`phi_pi` falls from 1.009 to **0.384**, so the claim that the fitted rule "just satisfies the
Taylor principle" was an artifact of the contaminated gap. Read
`monetary_model.fred_realtime_v2` instead.

### The eight component attempts — all fail, but mostly on intervals, not on mechanism

| Component | n | Key estimates (SE) | Model vs persistence MAE | DM p | Coverage | Failing |
| --- | --- | --- | --- | --- | --- | --- |
| interest_pass_through | 83 | pass_through 0.9266 (0.0535), impact 0.5291 (0.0402), spread 0.0256 (0.0023), speed/mo 0.045 (0.012) | 0.0628 vs 0.1126 | **0.0011** | 0.988 | interval_coverage |
| credit_growth | 141 | persistence_sum 0.8226 (0.042), mean growth 0.0072/mo (0.001) | 0.0525 vs 0.0570 | **0.0022** | 0.596 | interval_coverage |
| default_hazard (primary) | 23 | persistence 1.0038 (0.0219), unemployment_sensitivity −1.2593 (0.2866), rate_sensitivity 0.2393 (0.3116) | 0.1244 vs 0.1448 | **0.033** | 0.870 | bounds (persistence > 1) |
| deposit_growth | 59 | persistence −0.1949 (0.105), rate_semi_elasticity 0.045 (0.091) | 147.1 vs 120.9 | 0.946 | 0.831 | beats_persistence_dm |
| labor_demand | 143 | employment_output_elasticity 0.3018 (0.0772), impact 0.2899 (0.0958) | 1,328 vs 593 | 0.704 | 0.245 | DM, coverage |
| energy_purchasing | 83 | energy_response −0.083 (0.043), rate_response −0.525 (0.529) | 3,919 vs 3,504 | 0.756 | 0.627 | DM, coverage, bounds |
| policy_rule | 47 | inflation_response 1.1233 (0.359), output_response 1.5987 (0.460), smoothing 0.9219 (0.022) | 0.3262 vs 0.2165 | 0.673 | 1.000 | DM, coverage |
| deposit_rate_pass_through | 16 | pass_through 0.0782 (0.0018), speed/mo 0.2393 (0.083) | 0.0131 vs 0.0056 | 0.871 | 0.438 | min_forecasts (16<24), DM, coverage |

Reading these honestly:

- **Three beat their naive baselines with sensible parameters.** `interest_pass_through` is the
  clearest: prime-rate pass-through of 0.93 with a 0.53 within-month impact and a 4.5%/month
  adjustment speed is textbook, and it halves persistence's error (p = 0.001). It fails only
  because its 80% intervals cover 98.8% of outcomes — the in-sample residual scale is far too
  wide for a rate that moves in discrete steps. `credit_growth` (p = 0.002) fails the mirror
  problem: intervals too narrow (0.596). *Fifth wave: `credit_growth` passes once the data-revision
  component is added; `interest_pass_through` was not re-attempted, and its over-coverage has the
  same shape as the `regional` one — a variance component that is right in form but far too large.*
- **`default_hazard` on its declared primary series fails where the substitute passed.** It beats
  persistence (p = 0.033) with good coverage, but `persistence` = 1.0038 breaches the declared
  [0, 1] bound (a unit root in the delinquency logit), and `unemployment_sensitivity` is
  **negative** (−1.26) — the opposite sign to the +2.19 the FDIC/LAUS substitute produced. The two
  attempts disagree about the mechanism's sign, which is a reason to trust neither until the
  specification is revisited (*fifth wave: the disagreement is not the unemployment series — the
  published national CPS rate gives +2.12 — so it is the delinquency measure or the sample*); the
  substitute's pass should not be read as validating the
  declared component.
- **Four have no forecast skill at all** (`deposit_growth`, `labor_demand`, `energy_purchasing`,
  `policy_rule`): monthly deposit and payroll levels are near-random walks that an ADL in growth
  rates cannot beat, and the smoothed Taylor rule loses to persistence on the quarterly rate for
  the same reason it did at monthly frequency. `energy_purchasing` also has both response
  parameters the wrong sign for the declared mechanism.
- **`deposit_rate_pass_through` is data-starved as predicted**: SNDR starts 2021-04, giving 16
  origins against a declared 24.


## Fourth wave (base-year bug fixed; what it did and did not change)

`fred_macro_panel` (7dcce89c) and `fred_cpi` (34fe03f5) were rebuilt so every observation
carries `attributes.source_units` as published in that vintage, a per-vintage
`unit_multiplier`, and `base_period` in both attributes and dimensions. The `unit` token is now
per vintage: GDPC1 carries eight chained-dollar tokens, INDPRO thirteen index tokens, CPIAUCSL
two. Two input defects were corrected — 189 of 849 panel series change units across vintages,
and TOTALSL had been published in billions for some vintages and millions for others, so
**11,612 of its 13,537 values changed** (1998-03: 1.3322 → 1332.2).

Because the unit token is now per vintage, selecting on the requirement's unit would keep only
vintages on that base (GDPC1: 382 records from 2023-09). The loaders therefore accept whatever
unit a vintage published (`SeriesSource.unit = None`), apply the requirement's unit as a label,
and keep `published_unit` and `base_period` in the record's attributes.

### The important correction: the component estimates were *not* contaminated

I flagged in the third wave that `policy_rule` "reads the levels directly, so its estimate
carries the same contamination". **That was wrong, and the rerun proves it.** A point-in-time
frame takes, for every period, the latest vintage available at its cutoff, and a vintage
publishes its whole history on one base — so each frame is internally base-consistent, and the
estimators only read within-frame ratios and growth rates. Rerunning with identical splits:

| Attempt | v1 → v2 parameters | v1 → v2 test | Verdict |
| --- | --- | --- | --- |
| `policy_rule` | identical (inflation_response 1.1233, output_response 1.5987, smoothing 0.9219) | identical (n=47, MAE 0.3262, coverage 1.000, DM 0.673) | fail, unchanged |
| `credit_growth` | identical (persistence_sum 0.8226, mean growth 0.0072/mo) | MAE 0.0525 → **52.507** (the same numbers in billions rather than the buggy mixed scale), coverage 0.596, DM 0.0022 | fail, unchanged |
| `labor_demand` | identical (elasticity 0.3018, impact 0.2899) | identical (n=143, MAE 1,328, coverage 0.245, DM 0.704) | fail, unchanged |

So the TOTALSL 1000× error changed only the *level* in which `credit_growth`'s error is
reported, not its parameters or its verdict, because log growth is scale-invariant within a
single-vintage frame. The four attempts whose series were verified **value-identical** between
the two builds — `interest_pass_through`, `deposit_rate_pass_through`,
`default_hazard.fred_primary_realtime`, `deposit_growth` (DPRIME, DFF, SNDR, DRCCLACBS, UNRATE,
DPSACBW027SBOG, PAYEMS, FEDFUNDS all unchanged) — keep their third-wave verdicts without a
rerun.

### monetary_model — pass on clean data, and the parameters move a lot

This is where base mixing genuinely mattered, because the loader picks each period's *first
release* from a different vintage rather than reading one snapshot. The gap is now
`100 × (GDPC1 / GDPPOT − 1)` with both levels taken from the newest base on which **both**
series had published by the target quarter's first release (CBO lags BEA at a rebasing, so it is
often the previous base), and inflation takes both CPI endpoints from one base. The trailing-mean
workaround is gone and the gap is a true level: −10.8% (2020Q2) to +4.0%, against −9.7…+9.0
under the workaround and +22.5 before it.

| Parameter | v1 (contaminated) | v2 (clean) |
| --- | --- | --- |
| `phi_pi` | 1.0089 | **0.3839** |
| `phi_y` | 1.0981 | 1.0405 |
| `rho` | 0.9107 (0.0213) | 0.8547 (0.0337) |
| `r_star` | −1.3625 | +0.2463 |
| `policy_shock_sd` | 0.4250 | 0.4572 |

| Metric | Model | Persistence | Drift | Mean |
| --- | --- | --- | --- | --- |
| MAE (percent) | 0.2573 | 0.2962 | 0.3358 | 1.9770 |
| RMSE | 0.3673 | 0.4967 | 0.5121 | 2.1452 |
| CRPS | 0.2115 | 0.2577 | 0.2678 | 1.2661 |
| DM p vs model | — | 0.0585 | 0.043 | 1.5e-11 |

36 holdout quarters (106 rows survive the base-pairing requirement; 14 quarters are dropped where
no common base existed at the first release), coverage 0.861, bias −0.0008, 0 skipped origins.
All six criteria pass, so `monetary_model` was the first validated process (the fifth wave adds
`resource_inventory` and `elections_model`). Artifact
`4b7e1066…`.

**What changed substantively:** `phi_pi` = 0.38 means the fitted rule does **not** satisfy the
Taylor principle over 1995-2007 — the opposite of what the contaminated run suggested — and
`r_star` is now a plausible +0.25 instead of −1.36. The pass rests on forecast skill against
persistence (p = 0.059, close to the 0.10 threshold) and on well-calibrated intervals, not on
the structural coefficients being credible.


## Fifth wave (interval calibration, the elections family, and the completed `bls_labor`)

Three strands ran on 2026-09-16. Every attempt below was pre-registered in the plan —
including the diagnosis it rests on and the interval method it declares — before any
holdout was scored.

### What changed in the code, and what it did not change

Predictive distributions are now an explicit, declared choice rather than a fixed
assumption (`worldmodel/estimation/intervals.py`): `gaussian_in_sample` (the default),
`gaussian_trailing`, `student_t_trailing` and `empirical_trailing`, plus two independent
variance components that can be added to any of them — coefficient uncertainty `x'Vx`
and the dispersion of the revisions a publisher has already made by the origin. The
`regional` forecaster declares `pooled_year_draw` or `per_unit_year_mean`. No fudge
factor was introduced: each method is estimated from information available at the forecast
origin, and which one an attempt uses is frozen in `real_data_plan.json`.

**The default path is unchanged, and the earlier verdicts stand.** With
`interval_method` left at its default the estimator returns no predictive specification,
`predict` takes the identical old branch, and no forecast row carries a `predictive` field,
so reports are byte-identical. This was checked by rerunning the three superseded attempts'
holdouts directly: `inventory_balance.eia_weekly` reproduced coverage 0.6395 on 258
forecasts with MAE 3,755.44; `credit_growth.fred_realtime_v2` reproduced coverage 0.5957 on
141 with MAE 52.507; `regional_model.cbp_state_sectors` reproduced coverage 1.000 on 51 with
MAE 0.011558 — each matching the number recorded above. The `Point.first_value` field added
to support the revision component is read only by that component and appears in no audit or
digest.

### resource_inventory / inventory_balance — **pass** (was: interval coverage)

**Diagnosis.** v1 failed `interval_coverage` alone (0.640) while beating persistence at
p = 0.00093, so the mechanism was informative and the uncertainty was not. The failure was
not "intervals too narrow" but *a static scale against a moving one*. Over the holdout the
mean predictive sd moved only 3,121 → 3,380 while the realized one-step RMSE moved
4,473 / 3,082 / 6,113 / 5,838 / 3,243 across 2020-2024, and yearly coverage ran
0.60 / 0.83 / 0.42 / 0.54 / 0.82. Inside the *training* window the same expanding in-sample
Gaussian **over**-covers (0.894-0.908 against a nominal 0.80), because 1991-1995 residuals
(sd 4,563) inflate it relative to 2006-2010 (sd 1,562). Standardized holdout errors also
have kurtosis 4.3 and are left-skewed (10% quantile −2.28, 90% +1.09). So: heteroskedasticity
across regimes first, tail shape second; not in-sample optimism (n = 1,500 against two
coefficients) and not serial correlation.

**Declared method.** `empirical_trailing`, window 52 weeks (one year, the petroleum stock
cycle), 40 quantile nodes, plus `x'Vx`. The scale is the root mean square of the last 52
one-step residuals; the shape is the empirical quantiles of residuals standardized by their
own trailing scale. Chosen on training-window evidence only: over 1991-2019 the trailing
Gaussian attains 0.789-0.796 and the trailing empirical 0.797-0.808 at a nominal 0.80.

| Metric | v1 (gaussian_in_sample) | v2 (empirical_trailing) |
| --- | --- | --- |
| 80% interval coverage | 0.640 **fail** | **0.764 pass** |
| mean interval width | 8,285 | 11,982 |
| CRPS | 2,730.2 | 2,723.4 |
| MAE / RMSE | 3,755.4 / 4,731.4 | identical |
| DM p vs persistence (squared / absolute) | 0.00093 | 0.00093 / 0.0037 |
| coverage by year 2020…2024 | 0.60 0.83 0.42 0.54 0.82 | 0.75 0.85 0.54 0.81 0.88 |

Parameters are unchanged (`flow_scale` 0.5612 ± 0.0280, `unmeasured_net_flow` 1.7499 ± 0.1800);
only the predictive distribution moved. The predictive sd now tracks the regime
(4,060 / 3,601 / 4,310 / 6,346 / 4,545 by year) instead of sitting flat, and the standardized
shape is visibly asymmetric (10%/90% nodes −1.47 / +1.15 against Gaussian ∓1.28).
**Verdict: pass — all six criteria.** `resource_inventory` requires only this component, so
the process is validated. 2022 still under-covers locally (0.54): a trailing window cannot
anticipate a volatility jump, only follow it. Report `ef9e071705b1…`, artifact `5850d01bae2e…`.

### coupled_economy / credit_growth — **pass** (was: interval coverage)

**Diagnosis.** v2 failed `interval_coverage` alone (0.596) while beating persistence at
p = 0.0022. The scored error is not the error the residuals measure. Under the strict
real-time policy the forecast is anchored on the TOTALSL level available at the origin while
the actual is the latest vintage available at the evaluation cutoff, so the scored error
carries the anchor's later revision. TOTALSL log revisions from first release to latest have
sd 0.0177 for 2013+ periods and 0.0348 for 1997-2012 (mean +0.070), against a model residual
scale of 0.0057. Realized standardized holdout errors have root mean square 3.73, close to the
ratio of the revision scale to the residual scale (0.0177 / 0.0057 ≈ 3.1), and coverage bunches by year (0.00 in 2016 and 2022,
1.00 in 2014, 2018, 2019 and 2024) rather than scattering, which is one persistent revision
episode per benchmark rather than model noise. Excess kurtosis is negligible (3.78), so tails
are not the problem.

**Declared method.** The in-sample residual scale plus an independent revision component: the
standard deviation of the log revisions *already made by the origin*, over the most recent 60
periods at least 12 months old. Twelve months is the shortest age at which a G.19 period has
been through one annual benchmark; five years keeps the estimate inside the current revision
regime instead of averaging over the 2000s benchmark that moved the level about 5%. The mean
revision is reported and deliberately not used to shift the point forecast, so systematic
revision bias stays visible in the bias statistic instead of being absorbed into the mechanism.

| Metric | v2 | v3 (+ revision component) |
| --- | --- | --- |
| 80% interval coverage | 0.596 **fail** | **0.773 pass** |
| mean interval width (billion USD) | 57.2 | 245.2 |
| MAE / RMSE | 52.51 / 75.74 | identical |
| DM p vs persistence (squared / absolute) | 0.0022 | 0.0022 / 0.0011 |
| predictive scale at the 2024 refit | residual 0.00557 | residual 0.00557 ⊕ revision 0.01178 = 0.01303 |

Parameters unchanged (`persistence_sum` 0.8226 ± 0.0420, `mean_growth_per_month` 0.00724 ±
0.00103). The revision diagnostic at that refit: 60 mature periods, mean revision +0.0097,
largest +0.0272. **Verdict: pass — all six criteria, with a caveat that belongs next to it.**
Coverage passes *on average* while remaining badly clustered by year (0.33, then 1.00 for
2014-2020, then 0.42 / 0.08 / 0.50 / 1.00): 141 monthly forecasts contain perhaps a dozen
independent revision episodes, so the coverage statistic has far fewer effective observations
than its count suggests. The interval is now the right *object*; its sampling error is larger
than `n = 141` implies. Report `8ecb67bb78f9…`, artifact `ae5704206691…`.

### regional_model — **fail** on both panels, in opposite directions

**Diagnosis (CBP).** v1 failed `interval_coverage` at 1.000 while halving the
year-effect-only baseline error at p = 4.1e-06. The predictive sd of 0.0540 decomposes into a
pooled within-year residual sd of 0.0201 and a between-year term of 0.0502. The between-year
term is the variance of *three* year effects (−0.013, +0.071, +0.050) whose dispersion is the
2020 collapse and the 2021 rebound, added at full size as if next year were a fresh draw. The
within-year part is pooled across states whose own residual sds run from 0.0009 to 0.047 — a
factor of fifty — so one scale is simultaneously far too wide for stable states and too narrow
for volatile ones. Realized 2023 cross-state error sd was 0.0115 with a common component of
+0.0078.

**Declared method.** `per_unit_year_mean`: each state's own residual mean square shrunk toward
the pooled one by a single prior observation, plus the sampling variance of the *estimated*
year level (between-year variance / Y) instead of the variance of a fresh draw.

| Attempt | n | MAE (log growth) | year_effect_only MAE | DM p | coverage | width | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cbp_state_sectors` (v1) | 51 | 0.011558 | 0.023979 | 4.1e-06 | 1.000 | 0.1385 | fail |
| `cbp_state_sectors_v2` | 51 | 0.011558 | 0.023979 | 4.1e-06 | **0.980** | 0.0810 | fail |
| `qcew_state_sectors` | 318 | 0.021089 | 0.030656 | 1.2e-04 | **0.597** | 0.0370 | fail |

**Verdict: fail, both.** On CBP the interval narrowed by 42% and coverage moved 1.000 → 0.980,
still outside 0.80 ± 0.15, and `no_revision_leakage` still fails because CBP publishes no
vintages. The deeper point, which was declared in advance: with a single holdout year all 51
forecasts share one draw of the common component, so `interval_coverage` on this design is one
Bernoulli trial and not a coverage estimate at all.

`qcew_state_sectors` was registered to make the criterion evaluable — the rebuilt `bls_labor`
carries QCEW annual averages back to 1990, and the attempt declares 2005-2024, giving twenty
years and nineteen growth years instead of four and three, with a six-year holdout
(2019-2024 × 53 areas = 318 forecasts).
The mechanism holds up on the long panel: `shift_share_elasticity` 1.2586 (cluster-robust SE
0.3269, n = 1,007 state-years, base-year shares 2005), MAE 0.0211 against 0.0307 for
year-effect-only at p = 1.2e-04 and 0.0422 for persistence at p = 4.3e-10. Coverage, however,
now fails from the *other* side, 0.597, and the year-by-year pattern says exactly why:
0.85 (2019), **0.04 (2020)**, 0.38 (2021), 0.45 (2022), 0.98 (2023), 0.89 (2024). Outside the
pandemic the intervals are roughly right; the common component of 2020-2022 is not something
nineteen pre-pandemic year effects can price. `no_revision_leakage` also fails, and this is
structural rather than incidental: **no published employment source in this catalog carries
vintages**, so `regional_model` cannot pass that criterion on CBP or QCEW whatever the
intervals do.

> **Correction, 2026-09-17.** The sentence above is right about CBP and QCEW and was wrong
> about the world, and it was read here as meaning the criterion could never be evaluated for
> this process. It can. ALFRED archives the BLS CES State and Area state-by-supersector series
> with 229 real-time vintages from 2007-06-19, `fred_state_employment_vintages` publishes them,
> and `regional_model.ces_sae_realtime` **passes `no_revision_leakage`** below. The trap that
> hid it: FRED serves the same BLS series under a short alias and under the structured BLS id
> and ALFRED coverage differs between them — `SMU48000003000000001` answers *"does not exist in
> ALFRED"* where `TXMFGN` has the full archive. Every source checked, with URLs and statuses, is
> in [`docs/research-log/ws-b-employment-vintages.md`](research-log/ws-b-employment-vintages.md).

The declared next step, which is a *new* attempt and not a re-specification of this one: keep
the per-unit within-year scale but restore the fresh-draw between-year term
(`between × (1 + 1/Y)`) rather than the sampling variance of the mean, since the QCEW holdout
shows the common component behaves like a draw and not like an estimated level. That was not
run here, because changing it after seeing this result would be tuning.
Reports `9c97d892752c…` / `91090501d2aa…`, artifacts `e9c2cc3c35c4…` / `974b6f334590…`.

### elections_model — **pass** (all seven criteria); the process is validated

`mit_election_returns` published 33,805 U.S. House district rows, so the family is no longer
blocked. `worldmodel/estimation/loaders.py::elections_data` builds one row per district-cycle
general election (no primaries, no specials) from four datasets, and the split is by **election
year**: fit through 2016, validate 2018-2020, test 2022-2024. Each holdout origin refits on
cycles up to and including the previous one, so no future election informs a past prediction.

Two declared substitutions, both recorded in the plan before the run:

- **district presidential lean → lagged normalized House lean.** No published dataset here
  gives presidential vote by congressional district (presidential returns are county and
  statewide, and counties do not nest inside districts). `pvi` is the district's two-party
  Democratic share in its most recent *contested* cycle within three cycles, minus the national
  two-party Democratic House share of that same cycle.
- **A229RX0 → DSPIC96.** `fred_macro_panel` does not publish real disposable income per
  capita. `econ` is twelve-month real disposable personal income growth read from the ALFRED
  vintage available **on election day**, with both endpoints on one base period — so the
  economic fundamental is the number a real-time observer had, not today's revised value
  (2000: +3.97%, 2008: +0.03%, 2020: +5.47%, 2022: −2.89%, 2024: +3.13%).

5,476 district-cycles over 13 cycles (2000-2024), 4,805 contested; 175 rows dropped for having
no contested lean within three cycles. Incumbency comes from the FEC candidate master
(`CAND_ICI`): +1 for a Democratic incumbent and no Republican one, −1 in the mirror case, 0 for
an open or ambiguous seat.

| Parameter (final fit, cutoff 2024-12-31, n = 4,805) | Estimate | Cluster-robust SE |
| --- | --- | --- |
| `pvi` | 0.5308 | 0.0631 |
| `incumbent` | 0.0408 | 0.0083 |
| `midterm_x_president` | −0.0136 | 0.0061 |
| `fundraising` (log receipts ratio) | 0.00844 | 0.00102 |
| `econ_x_president` | −0.00094 | 0.00099 |
| `const` | 0.5017 | 0.0046 |
| `sigma_national` | 0.0181 | — |
| `sigma_district` | 0.0605 | — |

| Metric (774 holdout district forecasts, 2022 and 2024) | Model | Persistence | District mean | Incumbent-party-holds | Drift |
| --- | --- | --- | --- | --- | --- |
| MAE (two-party share) | 0.04359 | 0.04199 | 0.08215 | 0.07937 | 0.04746 |
| RMSE | 0.06076 | 0.06932 | 0.10785 | 0.09992 | 0.07561 |
| CRPS | 0.03264 | — | — | — | — |
| DM p vs model (squared) | — | **0.0032** | 1.1e-32 | 1.4e-40 | 3.6e-05 |
| DM p vs model (absolute) | — | 0.86 | 4.5e-48 | 1.1e-60 | 0.012 |

80% coverage 0.877 (0.820 in 2022, 0.934 in 2024), mean width 0.168, bias −0.0005, 0 skipped
origins. Report `8fa3a1dc22a6…`, artifact `de194e061f15…`.

**Verdict: pass — all seven criteria, and four things that must be said next to it.**

1. **The model beats previous-margin persistence on squared loss and not on absolute loss.**
   MAE is slightly worse (0.0436 against 0.0420) while RMSE is clearly better (0.0608 against
   0.0693): the fundamentals model avoids the large misses that persistence makes where a seat
   swings, and pays for it with a little extra error in the many safe seats. The declared
   criterion is the squared-loss DM test (p = 0.0032); on absolute loss p = 0.86. Both are
   reported because reporting only the one that passes would be dishonest.
2. **The economy term is insignificant and has the wrong sign** (−0.00094 ± 0.00099): in this
   specification, real income growth under a Democratic president is associated with a very
   slightly *lower* Democratic share. Nothing in the pass depends on it; it should not be read
   as a fundamentals result. The work is being done by `pvi` (0.53), incumbency (+4.1 points)
   and the midterm penalty (−1.4 points).
3. **The seat count, the declared secondary target, is worse than the naive rules.** 2022:
   predicted 192.3 Democratic seats (80% interval 169-216) against 205 actual; 2024: predicted
   219.2 (203-235) against 213. MAE 9.5 seats against 4.5 for incumbent-party-holds and 4.6 for
   the historical mean. Seats are scored under `secondary` and never enter acceptance — which is
   the declared design, but it means this model should not be used to forecast a majority.
4. **Fundraising is a conditional input, not a forecast.** FEC weball receipts are cycle totals
   whose coverage ends after election day (2000: 2001-06-30 … 2024: 2025-01-30), so the holdout
   tests the mechanism given realized fundraising, not the ability to forecast it, and the
   report labels the forecast `conditional_on_realized_inputs`. Receipts are summed over all of
   a party's candidates in a district-cycle, including primary losers; five district-cycles with
   a negative party total were clamped to zero (a correction recorded in the plan, made before
   any result existed). Fusion-party lines in New York and Connecticut are not credited to the
   major party they endorse, which is how MEDSL publishes them.

### `bls_labor` rebuilt: which verdicts moved (none) and what the rebuild settled (the sign question)

`bls_labor` was republished at `ffd7f43a2761` with 60,872,022 rows. The version four attempts
had used, `50917b81`, came from an incomplete download holding 18,183,421 rows — about 30% of
the data. The four affected published versions are `1d1666274ab3…` and `67438abce213…` (the
report and estimate of `default_hazard.fdic_laus_quarterly`) and `eb00e7c49161…` and
`f781a9557dcb…` (the same pair for `monetary_model.cpi_okun_proxy`). Both attempts were
re-registered with identical splits, criteria, overrides and loader options and rerun.

| Attempt | Verdict on `50917b81` | Verdict on `ffd7f43a` | Movement |
| --- | --- | --- | --- |
| `default_hazard.fdic_laus_quarterly` → `_v2` | pass | **pass** | none: intercept −0.4301 → −0.43009, persistence 0.9382 → 0.93821, unemployment_sensitivity 2.1853 → 2.18535, rate_sensitivity 1.6492 → 1.64919; MAE 0.0289, coverage 0.947, DM 0.020 all reproduce |
| `monetary_model.cpi_okun_proxy` → `_v2` | fail | **fail** | none: `phi_pi` 0.7517, `rho` 0.9774, MAE 0.1818, coverage 0.692, DM ≈ 1.0 all reproduce |

That both reproduce to the printed precision is the evidence, and the inference from it is
that the seasonally adjusted LAUS state levels behind the constructed national rate were
already complete in the interim build: what the missing 70% held was series these two attempts
never read (QCEW, OEWS, the CES state and metro panel) and one they could not — the national
CPS series, which was absent entirely.
Both v2 reports were also read against newer versions of their unpinned inputs
(`fred_policy_rate@966a0099`, `fred_cpi@34fe03f5`) because only `bls_labor` was pinned, which
the plan's run notes record; that the numbers still reproduce exactly is independent evidence
that those rebuilds changed metadata and not values.

**The rebuild also settled the open sign question, and the answer is not the comfortable one.**
The completed build carries the national CPS unemployment rate (`LNS14000000`, 943 monthly
observations 1948-2026) that the partial build omitted, so one of the two substitutions behind
the only passing `default_hazard` attempt could be removed. `default_hazard.fdic_cps_quarterly`
uses the published national series directly, with the same splits:

| Parameter | LAUS aggregate (v2) | Published CPS (`LNS14000000`) | Declared primary series (DRCCLACBS + UNRATE) |
| --- | --- | --- | --- |
| `hazard_intercept` | −0.4301 (0.0466) | −0.4165 (0.0477) | — |
| `persistence` | 0.9382 (0.0094) | 0.9404 (0.0096) | 1.0038 (0.0219) — **outside [0,1]** |
| `unemployment_sensitivity` | **+2.1853** (0.2263) | **+2.1176** (0.2318) | **−1.2593** (0.2866) |
| `rate_sensitivity` | 1.6492 (0.2848) | 1.6366 (0.2876) | 0.2393 (0.3116) |
| Verdict | pass (6/6) | **pass (6/6)** | fail (parameter bounds) |

The CPS attempt passes every criterion (19 holdout quarters 2021-2025, MAE 0.0290 against
0.0419 for persistence, DM p = 0.021, coverage 0.947, 0 skipped origins; report
`a29e4a61e35c…`, artifact `87c11480d3a4…`). **The sign contradiction survives the fix.**
Unemployment sensitivity is +2.12 with the published national CPS rate and +2.19 with the
constructed LAUS aggregate, against −1.26 on the declared primary pair — so the disagreement
was never the unemployment series. What differs is the *left-hand side*: the FDIC aggregate
noncurrent-loan rate of all bank loans versus FRED's credit-card delinquency rate, over
different samples (2010-2025 retrospective versus 1991-2025 real-time). The passing attempts
therefore say "noncurrent loans of FDIC-insured banks rise with unemployment and the policy
rate", which is economically sensible, and they do **not** validate the declared
`default_hazard` component, whose own series produce the opposite sign and a unit root. A
grouped hazard on the FDIC bank panel with the declared credit-card series as a second
equation is the declared next step; it was not run here.

## Panel-length wave (the two `minimum_test_forecasts` failures)

Two attempts failed a criterion that says nothing about a model: the test window did not contain
enough forecasts to score. `population_growth_rate.census_pep` had n = 4 against a declared 8 and
`deposit_rate_pass_through.fred_realtime` had n = 16 against a declared 24. Five attempts were
pre-registered (`real_data_plan.json`, key `panel_length_wave`) and run on 2026-09-17.
**`minimum_test_forecasts` now passes in all five. Nothing else newly passes**, and one criterion
that had been unevaluable is now a real failure. Full working: [WS-D research
log](research-log/ws-d-long-panels.md).

### population_growth_rate — still fail, now on one criterion instead of two

Two sources, both real-time, both pre-registered with splits taken from vintage coverage.

`census_population` was extended from **two PEP vintages to twenty** — every national/state vintage
the Census server still serves in the machine-readable ALLDATA layout (V2004-V2007, V2011-V2025)
plus the 2000-2010 national intercensal series — so the national annual series runs 2000-2025
(26 observations) instead of 2010-2024 (14). 0.81 MiB of new raw data. `population_growth_rate.fred_popthm`
needed **no acquisition at all**: the fix the record above named, POPTHM ALFRED vintages, was already
in `fred_macro_panel@7dcce89c` (811 monthly periods 1959-2026, 325 vintages from 1999-07-30), and
`requirements.json` names POPTHM as a source for the same series.

| | `census_pep` (recorded) | `census_pep_v2` | `fred_popthm` |
| --- | --- | --- | --- |
| Frame at the cutoff | 14, annual 2010-2024 | 26, annual 2000-2025 | 67, annual 1959-2025 |
| Splits (train / val / test) | ≤2016 / 2017-18 / 2019-24 | ≤2015 / 2016 / 2017-25 | ≤2005 / 2006-09 / 2010-25 |
| n test forecasts | 4 | **9** | **16** |
| `growth_rate_per_year` (SE) | 0.006777 (0.000472) | 0.006652 (0.000401) | 0.006427 (0.000645) |
| MAE (people) | 1.28e6 | 1.10e6 | 7.82e5 |
| persistence / drift MAE | 3.36e6 / 1.36e6 | 2.51e6 / 1.05e6 | 2.98e6 / 7.42e5 |
| CRPS | 1.07e6 | 8.79e5 | 6.13e5 |
| DM p vs persistence | 0.020 | 0.0094 | 3.6e-07 |
| 80% coverage (0.80 ± 0.20) | 0.50 | **0.333** | **0.500** |
| Skipped origins | 2 | 0 | 0 |
| Verdict | fail (min_forecasts, coverage) | **fail (coverage)** | **fail (coverage)** |

Reports `b17aca9776c3…` / `96923961224d…`, artifacts `6dbe25f775da…` / `9be38e2a8920…`.

**Verdict: fail, both, on `interval_coverage` alone.** `minimum_test_forecasts`,
`beats_persistence_dm`, `parameters_within_declared_bounds`, `no_timing_leakage` and
`no_revision_leakage` all pass. Three things belong next to that:

1. **Coverage gets *worse* with more forecasts** (0.50 → 0.333 on the census panel), so this is not
   a small-sample artifact. It is the fifth wave's diagnosis again: a Gaussian interval scaled by
   the in-sample residual sd of a drift regression, against a series whose growth shifts level
   (immigration, the pandemic, and the census rebasings). On POPTHM the mean interval width is
   1.19e6 people against an MAE of 7.82e5 — the 80% half-width is smaller than the average error.
   The declared next step is a trailing-scale predictive distribution of the kind that fixed
   `inventory_balance`; it was not run here, because choosing it after seeing these holdouts would
   be tuning.
2. **Neither beats *drift*** (DM p = 0.954 and 0.907; drift MAE is slightly lower than the model's
   in both). `beats_persistence_dm` is the declared criterion and it passes decisively, but a drift
   regression on population is close to tautologically a drift extrapolation.
3. **The panel extension alone was not enough.** Rerunning the v1 splits on the twenty-vintage build
   gives n = 6 — still a failure. The binding constraint is that the **2016 census.gov migration
   overwrote `Last-Modified` for every PEP file older than it**, so vintages 2004-2015 all appear to
   become knowable in mid-2016 and the earliest annual origin with eight observations is 2016 rather
   than 2012. A `Last-Modified` upper bound delays availability and cannot leak; substituting the
   documented December release schedule would move availability earlier than the evidence supports,
   so it was not done.

### deposit_rate_pass_through — evaluable on three series, and it fails on all three

**SNDR cannot be extended backwards**: the series begins 2021-04 because it *is* the FDIC National
Rate under the methodology adopted in 2021. So the declared series was re-attempted with the splits
recut by a mechanical rule (train = exactly the 36 months the component needs; holdout = everything
after 2024-07-31; validation = the four months between), and two longer **substitute** series were
published in a new 1.08 MiB dataset `fred_deposit_rates` and declared as substitutions:
**SAVNRNJ** (FDIC National Rate on non-jumbo savings, SNDR's discontinued predecessor, weekly
2009-2021) and **M2OWN** (the M2 own rate, monthly 1959-2019).

| | `fred_realtime` (recorded) | `fred_realtime_v2` | `savnrnj_substitute` | `m2own_substitute` |
| --- | --- | --- | --- | --- |
| Deposit rate | SNDR | SNDR | SAVNRNJ *(substitute)* | M2OWN *(substitute)* |
| Frame at the cutoff | 62 monthly | 64, 2021-04..2026-07 | 142, 2009-05..2021-02 | 724, 1959-02..2019-05 |
| n test forecasts | 16 | **24** | **38** | **29** |
| `pass_through` (SE) | 0.0782 (0.0018) | 0.0782 (0.0018) | 0.0808 (0.0407) | **0.6199 (0.0519)** |
| `impact_pass_through` (SE) | — | 0.0121 (0.0095) | 0.0111 (0.0019) | 0.2507 (0.0380) |
| `adjustment_speed_per_month` (SE) | 0.2393 (0.083) | 0.2393 (0.0831) | 0.0178 (0.0089) | 0.0320 (0.0108) |
| MAE (pp) / persistence MAE | 0.0131 / 0.0056 | 0.01437 / 0.01000 | 0.00301 / 0.00259 | 0.02866 / 0.03248 |
| CRPS | — | 0.01118 | 0.00227 | 0.03716 |
| DM p vs persistence | 0.871 | 0.287 | 0.131 | 0.725 |
| 80% coverage (0.80 ± 0.15) | 0.438 | 0.458 | **0.763 pass** | 0.966 |
| Verdict | fail (3 criteria) | **fail (DM, coverage)** | **fail (DM)** | **fail (DM, coverage)** |

Reports `3a2489b407af…` / `838457583c80…` / `0132ca127434…`, artifacts `aadc3329867c…` /
`7ecb9df59d45…` / `b226e2393a0f…`.

**Verdict: fail on all three, and the interesting failure is the one that used to be unevaluable.**

- **A substituted series is a new attempt, never the declared one passing.** Neither substitute
  passes, so the question is moot here, but the record follows the `default_hazard` precedent: the
  FDIC changed the national-rate methodology in 2021, so SAVNRNJ is **not spliced** onto SNDR, and
  M2OWN includes money-market mutual fund holdings that are not bank deposits at all.
- **`beats_persistence_dm` fails on all three**, over three windows, two series and n = 24, 29, 38.
  At n = 16 that was arguably a power problem. With 38 monthly forecasts spanning the 2019 cuts and
  the March 2020 collapse to the floor it is a finding: an error-correction model in the level of an
  administered deposit rate does not beat "the rate is what it was last month" — on SAVNRNJ the
  model's MAE (0.00301) is *worse* than persistence's (0.00259). Lengthening the panel revealed the
  failure rather than fixing it, which is the more informative result.
- **The whole of 16 → 24 is the split recut, none of it newer data.** Rerunning v1's declared splits
  on `fred_macro_panel@7dcce89c` reproduces n = 16, coverage 0.4375, DM p = 0.871 exactly. And 24 is
  *exactly* the declared minimum, so v2 clears it with no margin.
- **The two substitutes disagree about pass-through by a factor of eight, and regime is why.**
  SAVNRNJ 2009-2021 is a floor-bound decade: 0.081 ± 0.041, indistinguishable from zero. M2OWN
  1959-2019 spans many complete cycles: 0.620 ± 0.052 with a 0.251 ± 0.038 within-month impact and a
  3.2%/month adjustment speed, a textbook shape. `mechanisms.deposit_interest.pass_through` is
  regime-dependent, not a constant — the same lesson as `default_hazard`'s two variants disagreeing
  on the sign of `unemployment_sensitivity`.
- **`interval_coverage` passes for the first time on this component** (0.763 on SAVNRNJ), with no
  interval work at all: a never-revised administered rate has a stable residual scale. It fails from
  the *other* side on M2OWN (0.966, mean width 0.324pp against an MAE of 0.029pp), which is what a
  heavily revised series does to an in-sample Gaussian. The declared next step there is the revision
  component that fixed `credit_growth`; unrun.

## Blocked on data

`python3 -m worldmodel estimation-load` prints this machine-readably. After
`fred_macro_panel`, the House district returns and the completed `bls_labor`, thirteen of
fifteen components and six of eleven families are loadable; what remains blocked is:

| Component / family | Missing input | Dataset that must publish it |
| --- | --- | --- |
| field_diffusion_transport | per-cell concentrations with a topology (county PM2.5) | epa_aqs_daily |
| bilateral_flow_gravity | FAF5 OD tonnage and OD distances | freight |
| trade_model | several consecutive years of bilateral flows (un_comtrade starts 2024-01) | cepii_baci |
| influence_model | a unit-period panel with exposure and outcome | lda_lobbying + fec + voteview_rollcalls (panel not built) |
| sanctions_model | — | non-estimable by declaration (legal-rule determination) |

No FRED series is outstanding. `elections_model` is no longer blocked: `mit_election_returns`
published 33,805 U.S. House district rows, the district presidential lean is substituted by a
lagged normalized House lean (declared in the plan), and the family is scored above. `bls_labor`
is now required by `regional_model.qcew_state_sectors` and by the two `default_hazard` variants
that use a national unemployment rate.

## Declared but not run

| Attempt | Reason |
| --- | --- |
| `legislative_model.voteview` | compute budget. The data exist: `voteview_rollcalls` publishes `roll_call_member_positions` for Congresses 110-119 (Senate 117 alone has 949 roll calls). The ideal-point MAP refit at every holdout origin exceeds the declared 30-minute per-attempt budget. |
| `market_abm_model.alpaca` | compute budget. Daily closes are available; the grid SMM fit at every window origin exceeds the budget. |

## Data-quality findings

0. **[FIXED 2026-09-15] Chained-dollar ALFRED vintages carried no base-year metadata.** Each GDPC1/GDPPOT vintage is
   published in the base year current at that vintage (1992, 1996, 2009, 2017 dollars), but every
   normalized record is labelled with the single current unit `billion_chained_2017_USD` and has
   no per-vintage `source_units`. A real-time level ratio of the two series therefore mixes base
   years across the 1999, 2013 and 2023 benchmark revisions: it produced output gaps of +22.5%
   (1995Q4) and +16.1% (1999Q3) before the monetary loader was changed to measure the gap relative
   to its own trailing mean, which cancels a constant log offset. `policy_rule` reads the levels
   directly. The rebuild added per-vintage `source_units`, `unit_multiplier` and `base_period`, and
   the rerun showed the scope was narrower than I first claimed: point-in-time component frames are
   single-vintage and therefore single-base, so `policy_rule`, `credit_growth` and `labor_demand`
   reproduced identical parameters. Only constructions that combine different vintages across
   periods — the monetary family's first-release rows — were actually contaminated, and there the
   effect was large (`phi_pi` 1.01 → 0.38).
0aa. **TOTALSL was published in billions for some vintages and millions for others** (fixed in the
   same rebuild): 11,612 of 13,537 values changed by 1000×. It moved the level in which
   `credit_growth`'s forecast error is reported but not its scale-invariant parameters.
0ab. **Unit tokens are not stable across rebuilds.** `fred_oil_price` went from `USD/barrel` to
   `USD_per_barrel` and RRSFS from `million_USD_1982_1984` to `million_USD_1982_1984_cpi_adjusted`,
   both with identical values. A loader pinned to a unit string silently selects nothing, which is
   how `energy_purchasing` first failed to load; the affected sources now leave the unit open.
0a. **`fred_cpi` now carries full ALFRED vintages** (`vintage_tier: vintages`, `realtime_start`/
   `realtime_end`, `dimensions.vintage`), contrary to the older note in
   docs/data/macro-labor-io-international.md that calls it current-vintage only. CPI can now be
   read point-in-time.
0c. **`bls_labor` omits the national CPS series.** `CES0000000001` payrolls are present
   (1939-2026, current vintage), but `LNS14000000` national unemployment is not, and neither are
   the other headline CPS series the README describes; only LAUS state, county and area series
   exist. A national rate has to be constructed from the 51 seasonally adjusted state series.
1. **Published metric names and units do not match `requirements.json`.** EIA publishes
   `crude_oil_commercial_stocks_excl_spr` / `Thousand Barrels` where the requirement declares
   `crude_oil_stocks_excluding_spr` / `thousand_barrels`; SEC publishes `cash_and_equivalents`,
   `revenue`, `capital_expenditures` against `cash_and_cash_equivalents`, `revenues`,
   `capital_expenditure`; Census PEP publishes `population` against `resident_population`.
   `worldmodel/estimation/loaders.py` holds the declared translation; nothing was changed in the
   datasets.
2. **Capital expenditure is filed year-to-date only.** Selecting three-month durations alone
   gives roughly a quarter of the quarters (18 of 72 for CNX). Discrete quarters must be
   recovered by differencing cumulative durations.
3. **EIA weekly flows do not close the stock identity.** The fitted `flow_scale` is 0.56 and
   the commodities balance discrepancy averages +2,481 thousand barrels a week (max 20,556),
   which is the publisher's own adjustment term, not a pipeline error.
4. **Three sub-annual EIA series start in 2022 by default**, but the eight priority PET series
   keep their full history, which is what made the energy attempts possible.
5. **[FIXED 2026-09-16] `mit_election_returns` had no House district returns.** It now
   publishes 33,805 U.S. House district rows (1976-2024) and 94,151 county presidential rows
   (2000-2024). Three wrinkles the loader has to handle and the doc should name: `party_simplified`
   is populated only for statewide rows, so Democratic and Republican House votes must be matched
   on the raw `dimensions.party` string; the House file's own `runoff` flag is dropped by the
   dataset pipeline, so House runoffs are not derivable from the published records; and fusion
   party lines (New York, Connecticut) are published on their own party rows, so a two-party share
   built from DEMOCRAT and REPUBLICAN rows alone understates the endorsed major-party candidate
   there.
6. **No contiguity/neighbour graph is published** (CShapes), so the conflict Hawkes fit runs
   without neighbour excitation.
7. **`bls_labor` was being rebuilt on a larger artifact while this ran.** Every attempt that used
   it (`default_hazard.fdic_laus_quarterly`, `monetary_model.cpi_okun_proxy`) is now superseded by
   a fred_macro_panel attempt, and both are pinned to `bls_labor@50917b81`, so the rebuild does not
   invalidate anything recorded here. If the rebuild adds the national CPS series, the LAUS
   construction stops being necessary.
9. **[FIXED 2026-09-16] `bls_labor@50917b81` was an incomplete download** holding 18.2M of
   60.9M rows and omitting every national CPS series. The completed build `ffd7f43a2761` carries
   `LNS14000000` (1948-2026) and the QCEW annual singlefiles (1990-2025, state x NAICS sector by
   ownership). Reruns of the two attempts that used the interim version reproduce their results
   exactly, so no recorded verdict moved.
10. **QCEW labels each year with the NAICS revision then in force** (`naics2002` … `naics2022`),
   so a panel keyed on the published industry string splits every sector into five unrelated
   industries and zeroes the base-year shares. The regional loader compares two-digit sector codes
   across revisions and records the namespaces it saw. QCEW monthly rows from the quarterly
   singlefiles carry the same aggregation level as the annual averages, so `period_type` must be
   filtered or every year is counted thirteen times.
11. **`fred_macro_panel` does not publish A229RX0** (real disposable personal income per capita),
   which the elections family declares. `DSPIC96` (total real disposable personal income, with
   ALFRED vintages from 1979-12 and per-vintage base periods) is the declared substitute.
12. **FEC `weball` cycle receipts can be negative** when a candidate's refunds exceed receipts,
   and a party total in a district-cycle can inherit that (5 of 5,476). Money raised is not
   negative, so the elections loader clamps and counts them.
13. **FEC candidate receipts are cycle totals whose coverage ends after election day**, and the
   candidate master flags more than 435 House incumbents per cycle in recent years (primary losers
   and redistricting duplicates). The loader treats a district-cycle as open or ambiguous unless
   exactly one party has an incumbent.
8. **UCDP monthly counts are floats in the records** (`"value": 7.0`) while the conflict family
   requires integer counts; the loader casts them.
14. **The 2016 census.gov migration destroyed the original publication dates of the older PEP vintage
   files.** `census_population` derives `attributes.released_at` from HTTP `Last-Modified`, and every
   file older than the migration returns 2016-07-19 or later instead of its December release: V2004,
   V2005, V2006, V2007, V2011, V2013 and V2014 all report 2016-07-19, the 2000-2010 intercensal
   series 2016-08-24, V2012 2016-08-25 and V2015 2016-09-01. From V2016 on the timestamps do match
   the Census release dates. This is an upper bound, so it cannot leak, but it costs origins: it is
   the reason the earliest annual origin with eight observations is 2016 rather than 2012 and
   `population_growth_rate.census_pep_v2` scores 9 forecasts rather than about 14.
15. **PEP field names and identifier widths are not stable across vintages.** V2012 publishes
   `Sumlev,Region,Division,State,Name`; V2006 publishes lowercase `births2000`; V2011 publishes
   `SUMLEV` as `10` and `STATE` as `0` rather than `010`/`00`; V2004 and V2005 use `INTERNALMIG`
   where later vintages use `DOMESTICMIG`; and pre-2010 vintages name their April-1 base fields
   `CENSUS2000POP`/`ESTIMATESBASE2000`. Each of these silently dropped rows or whole files before
   `census_population/pipeline.py` normalized field-name case and zero-padded the geographic
   identifiers. The census base year is now read from each row's `ESTIMATESBASE<year>` field rather
   than inferred from the vintage.
16. **Vintages 2008-2010 of the PEP national/state totals do not exist in machine-readable form.**
   The Census server serves only per-year `nst-est200X-popchgYYYY.csv` and `-compchgYYYY.csv`
   presentation tables for those vintages, so the 2000s come from the V2004-V2007 ALLDATA files plus
   the 2000-2010 national intercensal series (which is what fills 2008 and 2009).
17. **[FIXED 2026-09-17] A declared conditional input was read across two vintages inside one design
   row.** `rolling_origin_backtest` built the design row from the origin's point-in-time frame and
   then substituted the conditional input at the *target* period from the evaluation frame, so any
   design reading the driver against its own lag compared two vintages. `labor_demand` reads INDPRO
   only as `log(output_t / output_{t−1})`, and the Federal Reserve rebases that index — the published
   ALFRED vintages carry thirteen index bases. At the 2005-04 origin the regressor was **−0.2177**
   against a realized **+0.000493**, the difference being exactly the rebasing factor
   95.3804 / 118.393 = 0.8056. Components now declare `conditional_rebase` per input; `ratio` carries
   the realized value onto the origin's vintage through the anchor-period overlap. All seven
   conditional inputs were measured and only `labor_demand` needed one — `crude_price` and DFF show
   an overlap factor of exactly 1.000000, `policy_rule` reads its GDP levels at the target period
   from one vintage, and `default_hazard` reads UNRATE as a level. Five earlier attempts reproduce
   their report digests exactly afterwards.
18. **[KNOWN, NOT FIXED] `policy_rule`'s inflation term still mixes vintages mildly.** Its design
   reads `price_index` at *t* from the evaluation vintage against *t−4* from the origin's, giving
   0.678%/quarter where the single-vintage figure is 0.504%/quarter (anchor overlap factor 1.0017).
   CPI revisions are small and this is not why `policy_rule` fails (`beats_persistence_dm`
   p = 0.673), so it is recorded rather than silently changed: rebasing the CPI onto the origin would
   move the attempt's declared inflation measure, which is a re-specification and needs its own
   pre-registration.
19. **[FIXED 2026-09-17] The conflict holdout scored Poisson while the family simulates negative
   binomial.** `_conflict_forecast` set `sd = sqrt(mean)`, but
   `worldmodel.models.conflict.simulate` draws counts as `negative_binomial(rng, lambda, dispersion)`
   and `dispersion` (nb2_alpha) is a declared family parameter with bounds [0, 50] — `fit_hawkes`
   never estimated it, so it stayed at 0. Measured over the fit-plus-validation window alone (4,300
   one-step forecasts) the Poisson Pearson dispersion is **35.9** against the 1.0 assumed.
   `fit_hawkes` now estimates α by method of moments on its own residuals and reports the Pearson
   dispersion beside it; α = 0 reproduces `sqrt(mean)` exactly.

## Method notes

- Vintage policy per attempt: `strict` where records carry real publication dates
  (companyfacts filing dates, PEP `released_at`, ALFRED `realtime_start`), `retrospective`
  otherwise (EIA bulk), which controls timing leakage through the declared publication lag but
  not revision leakage — and the reports say so.
- `no_timing_leakage` passed in every attempt that ran: zero selection and holdout violations
  with origins checked.
- Acceptance criteria are the component and family defaults from `requirements.json`. No
  attempt weakened a threshold to obtain a pass.
- A correction that prevented an attempt from producing any result at all (a date format, an
  integer cast, a negative receipts total the family refuses) is recorded in the plan and here.
  A change made after seeing a test result has to be a new pre-registered attempt with the failed
  one kept; the fifth wave is exactly that, and every superseded attempt is still listed above
  with its original numbers.
- The predictive distribution is part of the pre-registration. Each fifth-wave attempt states
  the diagnosis it rests on and the interval method it declares *before* the holdout is scored,
  and every method is estimated from information available at the forecast origin. Where a
  method was chosen among several, the evidence used was the training window (for
  `inventory_balance`, the in-training coverage of each candidate method) or the publication
  process (for `credit_growth`, the annual G.19 benchmark), never holdout coverage.
- Coverage is an average. Where forecasts share a common shock — 51 states in one year, 141
  months spanning a dozen revision episodes — the effective number of independent observations
  is far smaller than the count, and the record says so rather than quoting the count.
- Where several predictive distributions were admissible for one attempt, the rule declared in
  advance (WS-E, 2026-09-17) is **lowest validation-window CRPS** — a proper score, blind to
  interval coverage. It is not "coverage closest to nominal", which would be tuning to the
  criterion. On two of the six attempts where it made a non-trivial choice the rule failed to
  generalize (`labor_demand`, `regional_model.qcew_state_sectors`), both times because the
  validation window's volatility regime differs from the holdout's. Neither was re-selected on
  holdout evidence; both are recorded.
## Sixth wave (WS-A: the vintages were mostly already bought)

Eight attempts failed `no_revision_leakage`, the one criterion no modelling change can fix. WS-A
audited what FRED/ALFRED actually serves for every series those eight touch and acquired **0 new
bytes**: three are closable from vintages `fred_macro_panel` already holds, three (`regional_model`)
are WS-B's `fred_state_employment_vintages`, and two cannot be closed from any vintage archive —
FRED carries neither UCDP nor any EIA petroleum *supply* series (`WCESTUS1`, `WCRFPUS2`, `WCRIMUS2`,
`WCREXUS2`, `WCRRIUS2` all answer "the series does not exist"; EIA source 53 has only the price and
Total Energy releases). All eight were re-run first and reproduce their recorded verdicts and report
digests exactly. Full audit, probe transcripts and per-attempt table:
[docs/research-log/ws-a-alfred-vintages.md](research-log/ws-a-alfred-vintages.md).

### assets_model — **pass** (all seven criteria; the process is validated)

Adjusted equity closes are restated by later corporate actions and FRED has no per-issuer prices, so
`assets_model.alpaca_daily` cannot be repaired on its own series; it keeps its verdict. This attempt
takes the point-in-time price feed the v1 record named as the fix: five major-currency FRED daily
rates quoted as USD per unit of foreign currency, the euro as the common factor (never scored),
`DFF/252` as the daily risk-free rate, **every day read from the vintage that first published it**.
Splits are identical to the equity attempt. Measured across the published panel, each of these rates
was ever revised on exactly one or two days, against 1,658 revised days for DFF.

| Metric (1,875 forecasts, 0 skipped) | Model | `constant_volatility` | Persistence | Mean | Drift |
| --- | ---: | ---: | ---: | ---: | ---: |
| MAE (log return) | 0.0027533 | 0.0027533 | 0.0051289 | 0.0035706 | 0.0051299 |
| RMSE | 0.003813 | 0.003813 | 0.0069356 | 0.0049237 | 0.006937 |
| CRPS | **0.0020104** | 0.0020852 | 0.0038049 | 0.0026742 | 0.0038056 |
| DM p vs model | — | degenerate (identical means) | 0.0 | 0.0 | 0.0 |

80% coverage 0.8597, mean width 0.01034, bias +0.0000827. Report `d6734d86ebd8...`, artifact
`55c4e9410487...`.

**Verdict: pass — all seven criteria, with four things that belong next to it.** (1) It is a
*different estimand*, declared as such before the run: a currency cross-section, not five mega-cap
issuers, so the pass does not retrospectively validate the equity attempt. (2) `volatility_crps_skill`
passes at 0.964x where the equity attempt delivered 0.996x — currency volatility clusters more than
idiosyncratic equity volatility, which was the declared reason for expecting it. (3) The forecasts are
conditional on realized factor returns (`conditional_on_realized_inputs`), so beating a random walk
here is a far weaker claim than forecasting exchange rates. (4) `no_revision_leakage` passes because
of the first-release construction; the low revision counts are corroboration, not the basis.

### monetary_model — the revision criterion closes, and a new failure appears

`monetary_model.cpi_okun_proxy` and its v2 rerun failed `no_revision_leakage` for one reason: the
unemployment input was aggregated from 51 LAUS state series and `bls_labor` publishes one current
vintage. ALFRED has carried the published national rate UNRATE since 1960-03-15 (799 vintages), all
already in `fred_macro_panel`, so the same proxy was rebuilt from first releases only.

`monetary_model.okun_unrate_realtime` **failed to fit** — `Estimated smoothing is not below one;
reaction coefficients unidentified` — because DFF entered ALFRED on 2005-06-28, putting the whole
training window inside the zero-lower-bound era where a smoothed rule's rho is 1 by construction. No
criterion was evaluated, so v2 (FEDFUNDS, in ALFRED since 1996-12-03, 337 first-release months) is a
new attempt and not a re-specification; it restores the original `cpi_okun_proxy` splits exactly.

| Parameter | v2 estimate | LAUS version |
| --- | ---: | ---: |
| `rho` | 0.98609 (SE 0.00549) | 0.9774 |
| `phi_pi` | 3.0782 | 0.7517 |
| `phi_y` | 1.5115 | 0.9327 |
| `r_star` | **-5.1386** (outside bounds) | -1.9899 |

120 forecasts, MAE 0.14855 against 0.09283 for persistence (DM p = 0.992), 80% coverage 0.71667,
bias +0.07677. Report `65df6905e3cb...`.

**Verdict: fail — `no_revision_leakage` and `interval_coverage` pass, `beats_persistence_dm` and
`parameters_within_declared_bounds` fail.** The revision criterion is closed for this specification,
which is what the attempt was registered for. `beats_persistence_dm` fails as declared in advance. The
new failure is the finding: same proxy, same Okun coefficient, same splits, and `r_star` moves from
-1.99 to -5.14 once the *published* unemployment rate is read in real time instead of a
current-vintage state aggregate. The earlier attempt's more plausible `r_star` was partly an artefact
of the substitute input, and reading the declared series point-in-time exposed the specification's
problem instead of hiding it. `monetary_model.fred_realtime_v2`, which uses the declared
GDPC1/GDPPOT gap, remains the passing attempt for this family.

## Sixth wave (WS-E: uncertainty calibration, and no new data at all)

`interval_coverage` blocked **16** of the attempt rows this file then carried — more than any other
criterion, and the only large block that needs no acquisition. (The denominator used at the time was
35: the 33 attempts the plan had registered plus the two recorded `not_run`. The reports say 17, not
16, because `energy_purchasing.fred_realtime_v2` had a report and no row here; its row exists now and
it was superseded by `_v3` in this same wave, so nothing below changes.) Each of the sixteen was
diagnosed separately into the
two causes this record already distinguishes: a **scoring bug** (a real-time forecast graded
against a later vintage, as `credit_growth` was) or **genuine miscalibration** (the predictive
distribution is the wrong width or the wrong shape for the right reason). Full working record,
including the four attempts with no legitimate fix and the criterion this wave argues is itself
wrong: [docs/research-log/ws-e-interval-coverage.md](research-log/ws-e-interval-coverage.md).

**`interval_coverage` now passes on 10 of the 16 rows** (three were the fifth wave's, seven are
new). **One attempt became a full pass**: `interest_pass_through.fred_realtime_v2`. Four more had
their coverage failure removed and still fail on skill or on bounds, which is the useful part of
the result — it moves those attempts from "the uncertainty is wrong" to "there is no demonstrated
edge over a random walk". **No process became validated.**

Nothing was widened to reach the threshold. Where several predictive distributions were
admissible, the declared selection rule — fixed before any attempt in this wave ran — is **lowest
validation-window CRPS**, a proper score that is blind to coverage. It twice selected against the
outcome that would have passed, and both cases are recorded below.

### Two code defects, both found by asking why the intervals were wrong

**1. A declared conditional input was graded against a later vintage.** `rolling_origin_backtest`
builds each design row from the origin's point-in-time frame, then substitutes the realized value
of each declared conditional input at the target period from the *evaluation* frame. A design that
reads the driver against its own lag therefore compares two vintages. `labor_demand` reads
industrial production only as `log(output_t / output_{t−1})`, and INDPRO is an index the Federal
Reserve rebases — the published ALFRED vintages carry thirteen index bases. Measured at the
2005-04 origin, the regressor was **−0.2177**, a fabricated 22% collapse in industrial production,
where realized March-to-April growth was **+0.000493**; the gap is exactly the rebasing factor
(95.3804 / 118.393 = 0.8056). Every `labor_demand` v1 and v2 forecast carried that shock, which is
why their bias ran −2,172 thousand payrolls over 2005-2009 and decayed to zero as the origins
approached the evaluation cutoff.

Components now declare `conditional_rebase` per input; `ratio` carries the realized value onto the
origin's vintage through the two frames' overlap at the anchor period, which preserves the realized
*movement* exactly and adds no information the origin lacked. All seven conditional inputs in the
catalog were measured and **only `labor_demand` declares a rebasing**: `crude_price` and DFF show
an overlap factor of exactly 1.000000, `policy_rule` reads `real_gdp`/`potential_gdp` at the target
period from one vintage, and `default_hazard` reads UNRATE as a level. The default `none` is
unchanged behaviour.

**2. Conflict counts were scored Poisson while the family simulates negative binomial.**
`_conflict_forecast` set `sd = sqrt(mean)`. `worldmodel.models.conflict.simulate` draws counts as
`negative_binomial(rng, lambda, dispersion)` and `dispersion` (nb2_alpha) is a declared family
parameter with bounds [0, 50] — `fit_hawkes` simply never estimated it, so it stayed at 0 and the
score asserted equidispersion the family never assumed. Measured over the fit-plus-validation
window alone (4,300 one-step forecasts, holdout untouched), the Poisson Pearson dispersion is
**35.9** against the 1.0 assumed. `fit_hawkes` now estimates α by method of moments on its own
residuals; α = 0 reproduces `sqrt(mean)` exactly, so this is a declared parameter measured rather
than a widening factor.

**Neither change moved any earlier attempt.** Five were re-run and reproduce their recorded report
digests exactly, not merely their printed metrics: `default_hazard.fdic_cps_quarterly`
(`a29e4a61e35c…`), `inventory_balance.eia_weekly_v2` (`ef9e071705b1…`),
`credit_growth.fred_realtime_v3` (`8ecb67bb78f9…`), `regional_model.cbp_state_sectors_v2`
(`9c97d892752c…`) and `regional_model.qcew_state_sectors` (`91090501d2aa…`). The first has a declared
conditional input and the next two exercise the two interval paths, so the no-op claim is tested
where it could have failed.

One operational note for whoever runs the suite next: `worldmodel.provenance.capture_code` hashes
every `.py` under the package root against the import-time snapshot, so any *other* process editing
anything under `worldmodel/` during a `calibrate-all` run aborts every publish with "Implementation
changed after import". Two runs died that way. The workaround used here was to copy `worldmodel/` to
a scratch directory, symlink `data/` back to the real store, and run from the copy; published
artifacts still land in the real store.

### coupled_economy / interest_pass_through — **pass** (all six criteria)

The only failing criterion was `interval_coverage` at 0.988 on an attempt that halved
persistence's error at p = 0.0011. **Diagnosis: heteroskedasticity across monetary-policy
regimes.** At the validation cutoff the in-sample residual scale is 0.2217 while the residual root
mean square by decade runs 0.1560 / 0.1796 / 0.2539 / **0.4186** / 0.1142 / 0.0816 / **0.0416**
(1950s→2010s) — a tenfold spread set mostly by the Volcker era, which enters every forecast because
a 2005 ALFRED vintage of DPRIME publishes its history back to 1955. Visible before the holdout: in
the selection window (2013-2017, 59 forecasts) the default over-covers at **1.000** with mean
predictive sd 0.2259 against a realized RMSE of 0.0498. In-sample standardized errors have
kurtosis 6.0, which is what a rate moving in discrete 25bp steps looks like.

**Declared method:** `empirical_trailing`, window 120 months, 40 quantile nodes. No
coefficient-uncertainty term (746 residuals against 5 coefficients make `x'Vx` negligible) and no
revision term (DPRIME is not revised) — neither is part of the diagnosis.

| Metric | v1 (`gaussian_in_sample`) | v2 (`empirical_trailing` w=120) |
| --- | --- | --- |
| 80% interval coverage | 0.9880 **fail** | **0.7349 pass** |
| mean interval width | 0.5558 | 0.1356 |
| CRPS | 0.06375 | **0.04075** |
| MAE / RMSE | 0.06283 / 0.08952 | identical |
| DM p vs persistence | 0.001111 | identical |

Parameters unchanged (`pass_through` 0.9266, `impact_pass_through` 0.5291, `spread` 0.02565,
`adjustment_speed_per_month` 0.04499); only the predictive distribution moved, so v1 and v2 are
directly comparable. 83 holdout forecasts (2018-2024), 0 skipped origins. Report `0ab6414b3656…`,
artifact `60dda0c3334b…`.

**Verdict: pass — all six declared criteria**, and two things belong next to it. CRPS improves by
36%, so this is a better forecast distribution and not merely a compliant one — the criterion tests
coverage, and a proper score confirms it independently. And `coupled_economy` needs nine components;
this is one more of them passing, so the process remains unvalidated.

### coupled_economy / labor_demand — the scoring fix works; the skill test still fails

`labor_demand.fred_realtime_v3` (the conditional-input correction alone, predictive distribution
left at its default) against v2:

| Metric | v2 (bug present) | **v3 (bug fixed)** | persistence |
| --- | --- | --- | --- |
| MAE (thousand payrolls) | 1,328 | **499.5** | 593.2 |
| RMSE | — | 1,620.2 | 1,911.0 |
| CRPS | — | **405.1** | 479.5 |
| 80% coverage | 0.245 **fail** | **0.8741 pass** | — |
| DM p vs persistence | 0.704 | **0.160** | — |

Parameters are **unchanged** from v2 (`employment_output_elasticity` 0.30182, `impact_elasticity`
0.28987, `persistence` 0.03958), which is itself the proof that the defect was in the scored
forecast and not in the fit: the fit reads its own frame throughout, and only the backtest's
conditional injection mixed vintages. 143 holdout forecasts, 0 skipped. Report `894830be6783…`,
artifact `1968423d9daf…`.

**Verdict: fail on one criterion.** The model is now better than persistence on MAE, RMSE and CRPS
and still does not clear the declared `beats_persistence_dm` p ≤ 0.10 (p = 0.160). That is the right
outcome: a 16% MAE improvement over a random walk on monthly payroll levels is not significant at
n = 143 with serially correlated losses.

`labor_demand.fred_realtime_v4` adds the PAYEMS benchmark-revision variance component, justified
because the scored actual is the latest vintage while the forecast is anchored on the real-time
level, and PAYEMS log revisions have root mean square 0.00458 for 2000s periods against a model
residual scale of 0.00304. **The declared selection rule picked it and it is worse on the holdout**:
CRPS 431.0 against v3's 405.1, and `interval_coverage` **0.958, which fails** 0.80 ± 0.15 by 0.008,
where v3 passes at 0.874. The selection window 2005-2012 spans the financial crisis and its
benchmark revisions; 2013-2024 has smaller PAYEMS revisions, so the component over-covers. Report
`4f3ed0b935e8…`. **v3 is not retro-selected** — choosing a specification on holdout evidence is what
pre-registration exists to prevent — and nothing turns on it, because DM fails identically in both.

### coupled_economy / policy_rule and energy_purchasing — coverage closes, skill does not

| Attempt | Diagnosis | Declared method | coverage | Other failures |
| --- | --- | --- | --- | --- |
| `policy_rule.fred_realtime_v3` | heteroskedasticity: flat scale 0.8520 against decade rms 0.4847 / 0.4350 / 1.0824 / **1.4652** / 0.3592 / 0.5720 / **0.2227** | `gaussian_trailing` w=40 quarters | 1.000 → **0.6596 pass** | `beats_persistence_dm` p = 0.6734 |
| `energy_purchasing.fred_realtime_v3` | RRSFS log revisions rms 0.0154 (2010s), mean −0.0100, against a residual scale of 0.0087; plus mild heteroskedasticity | `empirical_trailing` w=60 ⊕ revision (w=120, maturity 24) | 0.627 → **0.8675 pass** | `beats_persistence_dm` p = 0.7558; bounds on `energy_response` (−0.083), `rate_response` (−0.525) |

Both keep their v2 parameters exactly, so only the predictive distribution moved. `policy_rule`
report `f188e4198071…`, `energy_purchasing` report `e47b4d103996…`.

**Two honest caveats on `policy_rule`.** It passes coverage by **0.0096** — the allowed band is
[0.65, 0.95] and the observed value is 0.6596, so a slightly different sample fails it; this is a
pass on the declared rule and not a robust one. And the trailing scale **overshot**: over-coverage
became under-coverage, because the 2013-2024 holdout opens with a decade of ZIRP and closes with the
2022-2023 hiking cycle, and a trailing window follows a volatility jump rather than anticipating it —
the same limitation the fifth wave recorded for `inventory_balance` in 2022. Separately, the
selection window did *not* reject the default for `policy_rule` (coverage 0.871 there, because that
window contains the financial crisis), so the case for changing anything rested on the
residual-scale spread alone, and that was declared in advance.

### conflict_model — coverage closes on the family's own declared dispersion

| Metric | v1 (Poisson `sqrt(λ)`) | v2 (NB2 `sqrt(λ + αλ²)`) | persistence |
| --- | --- | --- | --- |
| 80% interval coverage | 0.596 **fail** | **0.7192 pass** | — |
| mean interval width | 11.2 | 31.0 | — |
| CRPS | 11.185 | **9.994** | 12.269 |
| MAE / RMSE | 12.943 / 51.223 | identical | 13.071 / 50.919 |
| DM p vs persistence | 0.58 | 0.5829 | — |
| fitted `dispersion` | 0 (never estimated) | **0.0566** (bounds [0, 50]) | — |

Mechanism parameters barely move (`self_excitation` 0.98798 against 0.988, `decay` 0.21084 against
0.2108), so this is purely the predictive dispersion; CRPS improves 11% and now beats persistence's.
1,200 holdout forecasts, 0 skipped. Report `33f65fec8d70…`, artifact `76468856a59c…`.

**Verdict: fail**, on two criteria instead of three. `beats_persistence_dm` is unchanged, and
`no_revision_leakage` is structural — UCDP annual releases revise earlier months and the row dates
are event months, not publication dates, so no interval method reaches it.

### regional_model — the case where the proper score rejected the interval that would have passed

This is the clearest test that the criterion was not gamed, so it is recorded in full.
`qcew_state_sectors` (v1, `per_unit_year_mean`) fails `interval_coverage` from below at 0.597. A
third method, `per_unit_year_draw`, was registered to widen the common term to `between × (1 + 1/Y)`
on the argument that the forecast error contains next year's own year effect. Then the selection
window was measured, under the rule declared before any attempt in this wave ran:

| method (212 forecasts, 2015-2018, nominal 0.80) | coverage | mean sd | **CRPS** |
| --- | --- | --- | --- |
| `per_unit_year_mean` (what v1 already declares) | 0.6321 | 0.01371 | **0.010294** |
| `per_unit_year_draw` | 0.9340 | 0.02496 | 0.010591 |
| `pooled_year_draw` | 0.9340 | 0.02541 | 0.010857 |

**A wider interval that would pass was available and was not taken.** `per_unit_year_draw` reaches
0.9340 in validation and, when run, **0.7579 on the holdout — a pass**. It loses on validation CRPS,
so the declared rule selects `per_unit_year_mean`, which is what v1 already uses. The rejection was
written into the plan's `run_note` *before* the holdout was scored.
`regional_model.qcew_state_sectors_v2` is therefore published as a **rejected candidate on the
record, not as a fix**, and the verdict for the long panel remains v1's.

**And the rule was wrong here, by its own metric.** On the *holdout*, `per_unit_year_draw` has the
**lower** CRPS — 0.016269 against `per_unit_year_mean`'s 0.016934 — so the rejected candidate is
better on both coverage and the proper score once the answer is visible. That is knowable only after
the fact and does not license selecting it: the validation window is 2015-2018, entirely
pre-pandemic, and the holdout is 2019-2024. The same failure mode appears in `labor_demand` in the
opposite direction, where the rule selected the revision component on a crisis-era validation window
and the holdout punished it. **The generalizable finding is about the protocol, not these two
attempts: a validation window that does not span the holdout's volatility regimes cannot select a
predictive distribution reliably, whatever score it is judged on.** Recorded here rather than acted
on, because acting on it means a new pre-registration.

The a-priori argument was also simply wrong, and the evidence refuting it needed no holdout: the
shift-share shock is built from *realized* other-region industry employment at the target year, so it
already contains that year's common component, and what remains to forecast is closer to an estimated
level than a fresh draw. On the synthetic shift-share panel in `tests/test_estimation_intervals.py`
(16 years, 30 regions, a genuine common year shock of sd 0.004), `per_unit_year_mean` attains coverage
**0.8000** against a nominal 0.80 while `per_unit_year_draw` over-covers at 0.8417 and
`pooled_year_draw` at 0.8667. The fifth wave's choice was right.

`regional_model.cbp_state_sectors_v3` is the control: **the same change applied to the other panel
makes its over-coverage worse**, 0.980 → **1.0000**, and worse on CRPS too (0.014012 against v2's
0.009803), exactly as its registration said to expect. A change tuned to a threshold cannot move two
panels in opposite directions. Report `764d1ca7b1db…`.

What actually blocks the long regional panel is not a variance component. v1's coverage by year is
0.85 (2019), **0.04 (2020)**, 0.38 (2021), 0.45 (2022), 0.98 (2023), 0.89 (2024): outside the pandemic
the intervals are roughly right, and the 2020-2022 common component is not something nineteen
pre-pandemic year effects can price. Worth recording next to that: on this run the mechanism is
strongly supported — `beats_persistence_dm` p = **4.3e-10**, `beats_year_effect_only_dm` p =
**1.2e-04**, `shift_share_elasticity` 1.2586, MAE 0.021089 against 0.030656 for the mechanism-off
baseline. `no_revision_leakage` remains structural and is WS-B's `fred_state_employment_vintages`.

### The four with no legitimate fix, recorded as still failing

- **`population_growth_rate.census_pep`** — `minimum_test_forecasts` (4 < 8) fails whatever the
  interval does, and the selection window yields **zero** forecasts (the published PEP vintages start
  in 2020, so nothing is visible by 2018-12-31 under the strict policy), so any interval choice would
  have to be made on the holdout. Coverage 0.500 is 2 of 4, whose Wilson 95% interval is
  **[0.150, 0.850]** — it does not distinguish a calibrated interval from a broken one. WS-D's
  `census_pep_v2` and `fred_popthm` give the criterion enough forecasts to be diagnosable, and both
  now fail on `interval_coverage` alone; that is the natural follow-on and is not claimed here.
- **`deposit_rate_pass_through.fred_realtime`** — 16 forecasts against a declared 24. Its in-sample
  residual scale is *correct* (0.00573 against a measured 2020s residual rms of 0.00540); the
  under-coverage (0.438, Wilson 95% **[0.231, 0.668]**) comes from the point forecast being worse than
  persistence (MAE 0.0131 against 0.0056), which no predictive distribution repairs. WS-D's `_v2`
  reaches 24 forecasts and fails `interval_coverage` and DM.
- **`cash_balance.sec_companyfacts`** — the selection window yields **0 or 1** usable forecast per
  issuer, so there is no pre-holdout evidence on which to declare anything. Coverage is not the
  binding defect anyway: `beats_persistence_dm` fails on all five, `cash_conversion` is statistically
  indistinguishable from zero everywhere, and two issuers breach declared bounds.
- **`regional_model.cbp_state_sectors`** (and `_v2`, `_v3`) — with one holdout year all 51 forecasts
  share one draw of the common component, so `interval_coverage` here is one Bernoulli trial. v1's
  51/51 has a Wilson 95% interval of **[0.930, 1.000]** and v2's 50/51 **[0.897, 0.997]**; neither is
  a measurement of 0.80.

### A criterion this wave argues is wrong — and did not change

`interval_coverage` tests `|coverage − 0.80| ≤ tolerance` against the *raw count* of forecasts, with
no reference to how many of them are independent. The record already knows this bites —
`credit_growth.fred_realtime_v3` passes at 0.773 with coverage clustered by revision episode, and the
fifth-wave notes say so — but the criterion cannot see it. Where forecasts share a common shock (51
states in one year; 141 months spanning perhaps a dozen benchmark revisions; 318 state-years whose
2020 collapse is one event) the effective sample is far smaller than the count. A defensible
replacement tests coverage against a confidence interval built from the *effective* number of
independent observations, clustering on the unit the common shock acts through. **The criterion was
not changed and no attempt above was judged by anything other than the declared rule**; this is
recorded as an argument for a future pre-registered change.

## Seventh wave (WS-B: the employment vintages that were said not to exist)

### regional_model — the revision criterion **passes**, and the mechanism's skill does not survive

The fifth wave recorded `no_revision_leakage` as structural for this process because "no published
employment source in this catalog carries vintages". That was true of the catalog. ALFRED archives
the BLS **CES State and Area** state-by-supersector series with **229 real-time vintages from
2007-06-19**, and it was hidden by an id trap: FRED serves the same BLS series under a short alias
and under the structured BLS id with different ALFRED coverage — `SMU48000003000000001` answers
*"does not exist in ALFRED"* where `TXMFGN` returns 229 vintage dates. Eighteen sources with URLs and
HTTP statuses are in [`docs/research-log/ws-b-employment-vintages.md`](research-log/ws-b-employment-vintages.md).

`fred_state_employment_vintages@07d42b0a` publishes 603 series (510 panel + 93 residual-check),
949,207 records from a 0.085 GiB raw artifact. `loaders.ces_sae_regional_data` dates each reference
year by **the release that completed it** — the latest first-vintage date across its 51 × 10 × 12
cells — and reads all twelve months *at that vintage*, so the row's `date` is a publication date and
`information_time` is `'real_time'` as a property of the rows rather than a claim about the publisher.
Nothing published after a row's own date enters it, so `revisions` is `'none'`, as in
`monetary_realtime_data`. Two attempts were registered before either ran, differing only in the
declared interval method; base-year shares 2012, validation 2015-2019, holdout **2020-2025 × 51
state-equivalents = 306 forecasts**.

| Attempt | n | coverage | width | MAE | `year_effect_only` MAE | DM p vs yeo | `no_revision_leakage` | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `ces_sae_realtime` (`per_unit_year_mean`) | 306 | **0.654** | 0.0321 | 0.027797 | 0.026569 | 1.000 | **pass** | fail |
| `ces_sae_realtime_v2` (`per_unit_year_draw`) | 306 | **0.686** | 0.0352 | 0.027797 | 0.026569 | 1.000 | **pass** | fail |

**Verdict: fail, both — on skill, not on revisions.** `minimum_test_forecasts`,
`interval_coverage`, `parameters_within_declared_bounds`, `no_timing_leakage` and
`no_revision_leakage` all pass. `beats_persistence_dm` (p = 0.751) and `beats_year_effect_only_dm`
(p = 1.000) fail. On the revised QCEW panel the same mechanism cut the year-effect-only MAE from
0.0307 to 0.0211 at p = 1.2e-04; on the real-time panel it does not beat that baseline at all.
Reports `751e472f71633e76…` / `003f418a5cc94596…`, artifacts `92e00c191e97a460…` / `12d69de07e105ae7…`.

**One year does all of it, and not for the reason the earlier records would predict.**

| target year | release | coverage | model MAE | yeo MAE | bias |
| --- | --- | ---: | ---: | ---: | ---: |
| 2020 | 2021-01-26 | **0.000** | **0.11932** | 0.07359 | **+0.11932** |
| 2021 | 2022-01-25 | 0.706 | 0.01158 | 0.01769 | −0.00489 |
| 2022 | 2023-01-24 | 0.725 | 0.01091 | 0.03449 | −0.00301 |
| 2023 | 2024-01-23 | 0.863 | 0.00839 | 0.01661 | +0.00217 |
| 2024 | 2025-01-28 | 0.745 | 0.00888 | 0.00851 | −0.00423 |
| 2025 | 2026-01-27 | 0.882 | 0.00771 | 0.00853 | −0.00259 |

The bias equals the MAE in 2020, so the model over-predicted growth for all 51 states: it predicted a
2020 **boom**. Decomposing one forecast, the shift-share term contributed **+0.0447** in a year whose
realized other-state shock was strongly negative, which requires a negative elasticity. Refitting at
each origin finds one:

| fit through | years ≤ | `shift_share_elasticity` | cluster-robust SE | n |
| --- | --- | ---: | ---: | ---: |
| 2020-01-24 | 2019 | **−0.5795** | **1.6968** | 357 |
| 2021-01-26 | 2020 | +0.7617 | 0.6330 | 408 |
| 2022-01-25 | 2021 | +0.8086 | 0.5256 | 459 |
| 2023-01-24 | 2022 | +1.2171 | 0.4634 | 510 |
| 2026-01-27 | 2025 | +1.0338 | 0.4908 | 663 |

**Seven pre-pandemic growth years and ten supersectors do not identify the Bartik elasticity; 2020
does.** At the first holdout origin the point estimate has the wrong sign and an SE three times its
magnitude. Every fit containing 2020 lands between +0.76 and +1.22 with an SE near 0.5. So the
pandemic is both the year the model fails on and the year that identifies the parameter it needs —
visible only because the panel is real time. On revised QCEW data the elasticity came out 1.2586
(SE 0.3269) and the question never arose. Two readings survive this evidence and it does not separate
them: either twenty two-digit sectors genuinely identify what ten supersectors cannot, or part of the
QCEW result came from benchmarking making a published panel internally consistent in ways a
first-release panel is not. Separating them needs a vintaged 2-digit-NAICS panel; the research log
costs that reconstruction at ~15 GiB from Internet Archive snapshots of the QCEW singlefiles, with
crawl dates rather than publication dates.

**Read the coverage pass with the year table next to it.** 0.654 clears 0.80 − 0.15, but it is 0.000
in 2020 and 0.71-0.88 in the other five years: the pooled statistic passes partly because one badly
failing year is averaged with five good ones. Six holdout years is six independent draws of the
common component, so the caveat in "A criterion this wave argues is wrong" applies here in full.

**What is worse about this panel, stated for the record.** Ten CES supersectors instead of twenty
two-digit NAICS sectors; thirteen growth years instead of nineteen, because a complete panel is real
time only back to its shallowest series (state Information enters ALFRED on 2011-11-22); and a tenth
industry formed as the within-vintage residual `total_nonfarm − Σ nine`, because five
state-equivalents publish no aliased mining/logging/construction series. The residual is exact where
it can be checked (Texas 2019-06: 1,031.0 = 778.6 + 252.4).
