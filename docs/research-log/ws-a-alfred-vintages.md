# WS-A — ALFRED vintage coverage

**Question.** Eight pre-registered attempts in [`docs/calibration-status.md`](../calibration-status.md)
fail `no_revision_leakage`, the one acceptance criterion no modelling change can fix: it asks for
genuine point-in-time vintages. Extend vintage coverage until every series those attempts touch has
full ALFRED history, then re-run them.

**Verdict, stated up front because it changes what the workstream was for: the binding constraint is
what the publisher archives, not the download budget.** WS-A acquired **0 new bytes** and closed
`no_revision_leakage` on **three** of the eight, one of which now passes *every* criterion and makes
`assets_model` a **validated** process. Three more are closable and belong to WS-B, which was
already building the dataset for them. Two cannot be closed from ALFRED at any price, and the probe
transcripts below say why.

| Failing attempt | Can ALFRED close `no_revision_leakage`? | Where it was closed |
| --- | --- | --- |
| `assets_model.alpaca_daily` | not for those series; yes for a point-in-time price feed | **WS-A** — `assets_model.fred_fx_realtime`, **all 7 criteria pass** |
| `monetary_model.cpi_okun_proxy` | yes, from vintages already on disk | **WS-A** — `monetary_model.okun_unrate_realtime_v2` |
| `monetary_model.cpi_okun_proxy_v2` | yes, from vintages already on disk | **WS-A** — same attempt |
| `regional_model.cbp_state_sectors` | yes, from ALFRED CES State and Area | **WS-B** (`fred_state_employment_vintages`) |
| `regional_model.cbp_state_sectors_v2` | yes, same | WS-B |
| `regional_model.qcew_state_sectors` | yes, same | WS-B |
| `commodities_model.eia_weekly_balance` | **no** — FRED does not carry the series at all | nowhere |
| `conflict_model.ucdp_monthly` | **no** — FRED does not carry UCDP | nowhere |

Everything below is measured on 2026-09-17 against the FRED API and the published catalog.

## Series selection rule

> **Acquire a FRED series iff (a) it is the ALFRED publication of a quantity that a
> `no_revision_leakage`-failing attempt measures, or a point-in-time substitute for it that the
> failing record itself names, **and** (b) it is not already acquired, **and** (c) it is not the
> subject of another workstream. Then close the set under the ratios the model forms — if a model
> divides by a total, acquire the parts that sum to that total — and stop.**

Applied to the eight attempts, **(b) and (c) empty the set**. That is the finding, not an evasion:

* `fred_macro_panel` already holds **849 series, 838 of them in the vintages tier, 293,725 vintage
  dates, 8,040,812 observation rows**, raw artifact **0.744 GiB** (799,322,576 bytes, 921 shards),
  normalized **0.198 GiB** (213,047,980 bytes, 7,567,876 records). Everything the monetary and
  assets conversions need is inside it.
* The employment series the three `regional_model` failures need are WS-B's
  `fred_state_employment_vintages`.
* Nothing else these attempts touch exists in FRED.

The rule also says what *not* to buy, and that mattered more than what to buy. FRED has ~840k
series; at the panel's measured 1.4 MiB/series that is ~1.2 TiB, almost all of it geographic slices
of things already in the panel. Two candidate expansions were costed and refused:

| Considered | Size | Refused because |
| --- | ---: | --- |
| CES **metro-area** employment by supersector, all ALFRED vintages (~390 MSAs × 12 industries) | ~2.5 GiB est. | No pre-registered attempt is at metro level, and `regional_model`'s *other* failing criterion (`interval_coverage`) fails on the common year component, which more cross-sectional units cannot fix. More units would not close a criterion. |
| Reconstructing QCEW vintages from Internet Archive snapshots | ~15 GiB (WS-B's costing) | Crawl dates are not publication dates. |

**Budget consequence.** WS-A declared no dataset and used no allocation. `python3 -m worldmodel budget`
after the work: **89.73 GiB used of the 500 GiB pool**, fair-share cap 25 GiB per dataset, and
`fred_macro_panel` at **0.745 GiB used** of its 0.820 GiB declared — the same figure it had before.
An honest under-spend with a stated rule is reported here rather than padded: the 10–20 GiB the
workstream was scoped for does not exist to buy for this purpose.

## The audit: what ALFRED actually serves, per failing attempt

### `commodities_model.eia_weekly_balance` — not in FRED at all

The attempt reads the weekly U.S. crude balance from `eia_energy`. Every one of its series was
probed against FRED with a full real-time window:

```
{"id": "WCESTUS1", "error": "Bad Request.  The series does not exist."}
{"id": "WCRSTUS1", "error": "Bad Request.  The series does not exist."}
{"id": "WCRFPUS2", "error": "Bad Request.  The series does not exist."}
{"id": "WCRIMUS2", "error": "Bad Request.  The series does not exist."}
{"id": "WCREXUS2", "error": "Bad Request.  The series does not exist."}
{"id": "WCRRIUS2", "error": "Bad Request.  The series does not exist."}
{"id": "WGTSTUS1", "error": "Bad Request.  The series does not exist."}
{"id": "MGFUPUS2", "error": "Bad Request.  The series does not exist."}
```

This is not an id-format problem. FRED source 53 (U.S. Energy Information Administration) publishes
exactly five releases, and none is the Weekly Petroleum Status Report:

```
183 Gasoline and Diesel Fuel Update
212 Spot Prices
342 Natural Gas Spot and Futures Prices (NYMEX)
461 Energy-Related CO2 Emissions by State
480 Total Energy
```

Release 480 was enumerated in full: 24 series, all CO2 emissions by fuel and sector. So FRED carries
EIA *prices* (which is why `DCOILWTICO` and `GASREGW` are in the panel with vintages) and no EIA
*supply* quantities. `inventory_balance`, `demand_price_elasticity` and `price_adjustment` read the
same series, so the same conclusion covers them: their `retrospective` policy is not a shortcut, it
is the only policy those series admit. **No acquisition closes this. Recorded as structural.**

### `conflict_model.ucdp_monthly` — not in FRED, and the revision is in the event record

UCDP GED is an academic release; FRED has no conflict data. The deeper point is that the leakage is
not a units-of-publication problem: UCDP revises *which events happened* in earlier months at each
annual release, and the row date is the event month. A vintage archive of UCDP would fix it; ALFRED
is not that archive and neither is anything else in this catalog. **Recorded as structural.**

### `regional_model` × 3 — closable, and WS-B's

ALFRED archives the BLS CES State and Area series with 229 vintages from 2007-06-19, so a
first-release state × supersector panel is buildable. WS-B reached this independently and further;
see [ws-b-employment-vintages.md](ws-b-employment-vintages.md). **WS-A stood down** (commit
`f2dd9a9`): a `fred_state_industry_vintages` dataset, a `regional_realtime_data` loader and a
pre-registered attempt were all removed before anything was acquired or run, because two datasets
downloading the same series and two loaders building the same panel would spend the budget twice and
leave the catalog with two answers to one question.

One finding from the removed work is worth keeping, because it is the trap in this series family:
**FRED serves the same BLS series under a short alias and under a structured id, and the two are not
interchangeable in ALFRED.** `SMS06000003000000001` (California manufacturing) exists in FRED and
answers a real-time request with *"does not exist in ALFRED but may exist in FRED"*, while `CAMFG`
has the full 229-vintage archive. The District of Columbia is the mirror case: `DCMFG` does not
exist and `SMS11000003000000001` does. A build that constructs ids from the BLS scheme alone
concludes, wrongly, that most of the family has no vintages — 34 of 51 states dropped on exactly
that before the resolver was made to try both forms.

### `monetary_model.cpi_okun_proxy` and `_v2` — closable from data already on disk

The recorded reason for the failure is precise: *"the LAUS unemployment input carries no vintages"*.
The gap proxy is `−2 × (u − u*)` with u aggregated from 51 LAUS state series in `bls_labor`, which is
a current-vintage download. But ALFRED has carried the published national rate **UNRATE since
1960-03-15 (799 vintages)** and `fred_macro_panel` already holds every one. So the same proxy is
rebuildable with nothing published after each row's own release date.

### `assets_model.alpaca_daily` — the series cannot be fixed; the estimand can be changed

Total-return adjusted closes are restated by later corporate actions, and FRED has no per-issuer
equity prices, so no vintage archive repairs `AAPL`. The failing record names the fix itself:
*"an unadjusted or point-in-time price feed would make the second criterion evaluable."* FRED's daily
exchange rates are one, and they are already acquired. Measured across the published panel, the
number of days each rate was **ever** revised in any vintage:

| Series | Days kept after the archive-start rule | Days ever revised |
| --- | ---: | ---: |
| `DEXUSEU` (EUR) | 5,314 | **1** |
| `DEXUSUK` (GBP) | 5,314 | **1** |
| `DEXUSAL` (AUD) | 5,314 | **1** |
| `DEXSZUS` (CHF) | 5,314 | **1** |
| `DEXJPUS` (JPY) | 3,128 | **2** |
| `DEXCAUS` (CAD) | 3,128 | **1** |
| `DFF` (risk-free) | 7,750 | 1,658 |

The attempt does not rest on that measurement — every day is still read from the vintage that first
published it — but it is the reason the declaration is credible, and the `DFF` column is why reading
first releases is not a formality.

## Two rules every first-release panel here enforces

`worldmodel/estimation/loaders.py::first_releases` implements both, and both are enforced rather than
assumed:

1. **Drop the archive start.** A series' first ALFRED vintage republishes its whole back history, so
   for every period before that date the "earliest vintage" is a snapshot of already-revised numbers,
   not a first release. Those periods are dropped and counted. The counts are large and belong in the
   record: `DFF` 18,624 pre-archive days dropped, `CPIAUCSL` 19 months, `UNRATE` 146 months,
   `DEXJPUS` 10,832 days.
2. **Never form a ratio across a rebasing.** Two vintages of a rebased index are levels in different
   units, so the CPI inflation endpoints are taken inside one `base_period`.

Both rules bite. Without (1) the "first release" of 1999 euro data would be a 2005 snapshot; without
(2) a twelve-month CPI change straddling a rebasing is nonsense.

## Units across vintages: the audit and the evidence the fix works

FRED restates the **scale** a series is published in, not only its index base. The catalog already
carries the per-vintage fix (`build_config.py --units-history` → `config.json#series[*].units_history`,
resolved per observation by `fred_alfred.emitted_units`); what was missing was a measurement of it
against the publisher. Measured over the published `fred_macro_panel` config:

| | Series |
| --- | ---: |
| Total series | 849 |
| Units change across vintages | **189** |
| …of which the **multiplier** changes (a 1000× error if one multiplier is used) | **59** |
| …of which only the index base changes | 124 |
| …other (unit token changes, same scale and base) | 6 |

The 59 scale changes are the dangerous set, and 51 of them are the state personal-income series
(`ALOTOT` … `WYOTOT`), which go **Millions → Thousands → Millions**. A round trip means "use the
latest units" is wrong for the middle era *in both directions*, which no single-multiplier heuristic
survives. The rest: `TOTALSL`, `REVOLSL`, `BOGMBASE`, `BCNSDODNS`, `CMDEBT`, `HHMSDODNS`,
`TNWBSHNO`, `WRESBAL`, `WTREGEN`.

### Hand-checked against the FRED API, 2026-09-17

```
TOTALSL 1998-03-01 as of 2019-05-07 -> [('1332.90344',  '2019-05-07')] | units: Billions of Dollars
TOTALSL 1998-03-01 as of 2025-02-07 -> [('1332903.44',  '2025-02-07')] | units: Millions of Dollars
CAOTOT  2000-01-01 as of 2018-09-25 -> [('1102918418.0','2018-09-25')] | units: Thousands of Dollars
CAOTOT  2000-01-01 as of 2018-12-20 -> [('1102918.4',   '2018-12-20')] | units: Millions of Dollars
```

The same four rows as the published panel serves them (`fred_macro_panel@7dcce89c`):

| Series | Period | Vintage | `source_units` | `unit_multiplier` | stored value | unit |
| --- | --- | --- | --- | ---: | ---: | --- |
| TOTALSL | 1998-03 | 2019-05-07 | Billions of Dollars | 1.0 | 1332.90344 | `billion_USD` |
| TOTALSL | 1998-03 | 2025-02-07 | Millions of Dollars | 0.001 | 1332.90344 | `billion_USD` |
| CAOTOT | 2000-01 | 2018-09-25 | Thousands of Dollars | 1,000 | 1,102,918,418,000 | `USD` |
| CAOTOT | 2000-01 | 2018-12-20 | Millions of Dollars | 1,000,000 | 1,102,918,400,000 | `USD` |

The raw pairs differ by exactly 1000×; the stored pairs agree (CAOTOT to five significant digits —
the 18,000 USD gap is the publisher's own rounding at the coarser scale). Distribution of multipliers
actually applied in the published records, which is what proves it is per-vintage and not per-series:

```
TOTALSL: ('Billions of Dollars', 1.0) x 11,732   ('Millions of Dollars', 0.001) x 1,330
         ('Millions of U.S. Dollars', 0.001) x 475        [13,537 observations]
CAOTOT:  ('Millions of Dollars', 1e6) x 1,191    ('Thousands of Dollars', 1e3) x 1,114
                                                          [2,305 observations]
```

### The test

`data/fred_macro_panel/tests/test_fred_macro_panel.py::UnitsAcrossVintagesTests` (commit `622fb40`),
three assertions:

* `test_the_units_change_flag_is_derived_from_the_recorded_history` — the flag must be *derived* from
  `units_history`, never asserted by hand, and the measured scale-change count must not fall below 59.
* `test_hand_checked_values_normalize_to_the_same_quantity_in_both_units` — the four rows above,
  asserting both that the raw values differ by 1000× and that the normalized values agree. A
  single-multiplier pipeline fails here by exactly that factor, at the point of the defect, instead
  of surfacing later as an implausible output gap.
* `test_published_records_carry_their_own_vintage_multiplier` — reads the shipped
  `records.jsonl.gz` and requires two *different* multipliers across the two vintages of one period.
  Skips when nothing is published.

The pre-existing suite had a synthetic chained-dollar fixture and no measurement against the
publisher; these are the addition.

```
$ python3 -m unittest discover -s data/fred_macro_panel/tests
...............
Ran 15 tests in 9.291s
OK
```

## The eight attempts: before, after, and what still fails

All eight were re-run on 2026-09-17 with `--no-publish` before anything changed, to establish that the
recorded verdicts are reproducible rather than trusted. **All eight reproduce exactly**, including
report digests (`9c97d892752c…`, `91090501d2aa…`, `72d996827f4f…`):

```
assets_model.alpaca_daily             n=1890 fails=volatility_crps_skill,no_revision_leakage
commodities_model.eia_weekly_balance  n=20   fails=beats_persistence_dm,parameters_within_declared_bounds,no_revision_leakage
conflict_model.ucdp_monthly           n=1200 fails=beats_persistence_dm,interval_coverage,no_revision_leakage
monetary_model.cpi_okun_proxy         n=120  fails=beats_persistence_dm,no_revision_leakage
monetary_model.cpi_okun_proxy_v2      n=120  fails=beats_persistence_dm,no_revision_leakage
regional_model.cbp_state_sectors      n=51   fails=interval_coverage,no_revision_leakage
regional_model.cbp_state_sectors_v2   n=51   fails=interval_coverage,no_revision_leakage
regional_model.qcew_state_sectors     n=318  fails=interval_coverage,no_revision_leakage
```

A pre-registered attempt is never re-specified, so none of these verdicts changes. What changes is
whether a *superseding* attempt exists that closes the criterion on data that can carry it:

| # | Attempt | Before | Superseding attempt | After | Still failing there |
| --- | --- | --- | --- | --- | --- |
| 1 | `assets_model.alpaca_daily` | fail: `volatility_crps_skill`, `no_revision_leakage` | `assets_model.fred_fx_realtime` | **pass — all 7 criteria; `assets_model` validated** | none |
| 2 | `monetary_model.cpi_okun_proxy` | fail: `beats_persistence_dm`, `no_revision_leakage` | `monetary_model.okun_unrate_realtime_v2` | fail | `beats_persistence_dm`, `parameters_within_declared_bounds` — **`no_revision_leakage` passes** |
| 3 | `monetary_model.cpi_okun_proxy_v2` | fail: `beats_persistence_dm`, `no_revision_leakage` | same | fail | same |
| 4 | `regional_model.cbp_state_sectors` | fail: `interval_coverage`, `no_revision_leakage` | WS-B | see WS-B | — |
| 5 | `regional_model.cbp_state_sectors_v2` | fail: `interval_coverage`, `no_revision_leakage` | WS-B | see WS-B | — |
| 6 | `regional_model.qcew_state_sectors` | fail: `interval_coverage`, `no_revision_leakage` | WS-B | see WS-B | — |
| 7 | `commodities_model.eia_weekly_balance` | fail: `beats_persistence_dm`, bounds, `no_revision_leakage` | none possible | unchanged | `no_revision_leakage` is **not closable from any vintage archive**: FRED has no EIA supply series |
| 8 | `conflict_model.ucdp_monthly` | fail: `beats_persistence_dm`, `interval_coverage`, `no_revision_leakage` | none possible | unchanged | same — UCDP is not in FRED |

## `assets_model.fred_fx_realtime` — all seven criteria pass

Pre-registered before the run (commit `bf18e03`): five major-currency FRED daily rates quoted as USD
per unit of foreign currency (`DEXJPUS`, `DEXUSUK`, `DEXCAUS`, `DEXSZUS`, `DEXUSAL`, the
foreign-per-USD quotes inverted), the euro (`DEXUSEU`) as the common factor and never scored, `DFF/252`
as the daily risk-free rate, **every day read from the vintage that first published it**. Splits are
identical to the equity attempt so the two are comparable: train ≤2021-12-31, validate to 2023-06-30,
holdout 2023-07..2024-12, refit every 21 origins. 13,480 bars and 2,695 factor days over
2014-03-19..2024-12-31; 18,870 source records from `fred_macro_panel@7dcce89c`.

| Metric (1,875 holdout forecasts, 0 skipped) | Model | `constant_volatility` | Persistence | Historical mean | Drift |
| --- | ---: | ---: | ---: | ---: | ---: |
| MAE (log return) | 0.0027533 | 0.0027533 | 0.0051289 | 0.0035706 | 0.0051299 |
| RMSE | 0.003813 | 0.003813 | 0.0069356 | 0.0049237 | 0.006937 |
| CRPS | **0.0020104** | 0.0020852 | 0.0038049 | 0.0026742 | 0.0038056 |
| DM p vs model (squared) | — | degenerate (identical means) | 0.0 | 0.0 | 0.0 |

80% interval coverage 0.8597, mean width 0.01034, bias +0.0000827. Report `d6734d86ebd8…`,
artifact `55c4e9410487…`.

```
    PASS minimum_test_forecasts        PASS beats_persistence_dm
    PASS interval_coverage             PASS beats_historical_mean_dm
    PASS volatility_crps_skill         PASS no_timing_leakage
    PASS no_revision_leakage
 processes {"assets_model": {"failing_components": [], "missing_components": [],
            "required_components": ["assets_model_parameters"], "validated": true}}
```

**Verdict: pass — all seven declared criteria, and four things belong next to it.**

1. **This is a different estimand, not a repaired one.** A currency cross-section with the euro as
   the factor is not five mega-cap issuers with SPY. The pass does not retrospectively validate
   `assets_model.alpaca_daily`, and that attempt keeps its verdict on its own data. What the pass
   licenses is narrower and was declared in advance: the GARCH mechanism has skill *on this asset
   class*.
2. **`volatility_crps_skill` passes by 0.964, where the equity attempt delivered 0.996.** The
   criterion wants CRPS ≤ 0.99× the same conditional means with constant volatility. The margin is
   real but it is one number on one sample; currency volatility clusters more than idiosyncratic
   equity volatility, which was the declared reason for expecting it, and the declaration said the
   outcome was genuinely open.
3. **The DM tests against persistence and the historical mean are conditional forecasts.** The family
   scores next-day returns *given* realized factor returns, which is labelled
   `conditional_on_realized_inputs` in the audit. Beating a random walk on a conditional forecast is
   a much weaker claim than forecasting exchange rates.
4. **`no_revision_leakage` passes because of the construction, not because FX is clean.** The rows are
   first releases; the measurement that only one day per rate was ever revised is corroboration.

## `monetary_model.okun_unrate_realtime_v2` — the criterion closes, the attempt still fails

Two attempts were registered, and the first one's failure is part of the record.

**`monetary_model.okun_unrate_realtime` (v1): `status: failed`, no holdout scored.**
`ValueError: Estimated smoothing is not below one; reaction coefficients unidentified`. The cause is
a data boundary: `DFF` entered ALFRED on 2005-06-28, so first-release monthly means begin 2005-06,
and the training window 2005-06..2013 lies almost entirely inside the zero-lower-bound era where the
policy rate is flat and a smoothed rule's ρ is 1 by construction. No acceptance criterion was
evaluated, so registering v2 with a different declared policy-rate series is a new attempt and not a
re-specification after a result.

**v2** changes one thing: `FEDFUNDS` (monthly, in ALFRED since 1996-12-03) instead of `DFF`. That
gives 337 first-release months, 1996-12..2024-12, which restores the *original*
`cpi_okun_proxy` splits exactly — train ≤2007, validate 2008-2014, holdout 2015-2024, 120 forecasts —
so the leaking attempt and its replacement differ only in where unemployment and the policy rate come
from.

| Parameter | Estimate | SE |
| --- | ---: | ---: |
| `rho` | 0.98609 | 0.00549 |
| `phi_pi` | 3.0782 | — |
| `phi_y` | 1.5115 | — |
| `r_star` | **−5.1386** | — |
| `policy_shock_sd` | 0.19427 | — |

| Metric (120 forecasts, 0 skipped) | Model | Persistence | Drift | Historical mean |
| --- | ---: | ---: | ---: | ---: |
| MAE (percent) | 0.14855 | 0.09283 | 0.10281 | 1.73846 |
| RMSE | 0.21785 | 0.18579 | 0.18882 | 1.97191 |
| CRPS | 0.11627 | 0.08953 | 0.09185 | 1.15476 |
| DM p vs model (squared) | — | 0.99225 | 0.98579 | 0.0 |

80% coverage 0.71667, mean width 0.51201, bias +0.07677. Report `65df6905e3cb…`.

```
    PASS minimum_test_forecasts
    FAIL beats_persistence_dm   {"pvalue": 0.9922, "mean_loss_difference": 0.01294}
    PASS interval_coverage
    FAIL parameters_within_declared_bounds  ["r_star"]
    PASS no_timing_leakage
    PASS no_revision_leakage
```

**Verdict: fail, with the WS-A objective met and one new failure that is a finding.**
`no_revision_leakage` passes: the row set is first releases throughout, so the criterion the whole
workstream exists for is closed for this family's proxy specification. `beats_persistence_dm` fails
as predicted in the declaration — a smoothed quarterly rule scored monthly is worse than a random
walk on the policy rate, and it was worse in the LAUS version too.

The new failure is the interesting one. `r_star` came out at **−5.14** against −1.99 on the LAUS
construction, far outside its declared bounds. Same proxy, same Okun coefficient, same splits; the
only change is a **published** national unemployment rate read in real time instead of one aggregated
from state labour-force levels at current vintage. So the earlier attempt's more plausible `r_star`
was partly an artefact of the substitute input, and reading the declared series point-in-time made
the specification's problem visible rather than hiding it. The Okun proxy is still a proxy:
`monetary_model.fred_realtime_v2`, which uses the declared GDPC1/GDPPOT gap, remains the passing
attempt for this family.

## Counts, measured

| | |
| --- | ---: |
| New bytes acquired by WS-A | **0** |
| ALFRED bytes the conversions read (already acquired, `fred_macro_panel`) | **0.744 GiB** raw / **0.198 GiB** normalized |
| Series in that panel | 849 (838 in the vintages tier) |
| Vintage dates across the panel | 293,725 |
| Observation rows across the panel | 8,040,812 requested / 7,567,876 published records |
| Source records read by `assets_model.fred_fx_realtime` | 18,870 (13,480 bars + 2,695 factor + 2,695 risk-free) |
| Source records read by `monetary_model.okun_unrate_realtime_v2` | 1,348 (1,304 distinct; 337 months × 4 inputs) |
| Series whose units change across vintages | 189 of 849 |
| …whose **scale** changes | 59 |
| `no_revision_leakage` failures closed by WS-A | **3 of 8** |
| …closable by WS-B | 3 of 8 |
| …not closable from any vintage archive | **2 of 8** |
| Processes newly validated | **1** (`assets_model`) |

## What WS-A did not do, and why

* **Did not acquire 10–20 GiB.** There is nothing to buy for this purpose. The two expansions that
  would have spent it are costed above and neither closes a criterion.
* **Did not build a second employment panel.** WS-B owns it; WS-A's copy was removed unacquired.
* **Did not touch the `interval_coverage` failures** that sit alongside `no_revision_leakage` on the
  regional and conflict attempts. That is WS-E.
* **Did not re-specify any existing attempt.** Every verdict above is either a reproduced rerun or a
  newly registered attempt with its splits frozen before the run.
* **Did not change `fred_macro_panel`.** Its series list and windows determine its acquisition digest,
  so adding series would force a re-download of 0.744 GiB to gain nothing the failing attempts use.

## Commits

| Commit | What |
| --- | --- |
| `202b867` | (reverted) declared the duplicate state-industry panel |
| `ae18b12`, `fcf3729` | (reverted) the duplicate regional loader, attempt and tests |
| `f2dd9a9` | stood down from the duplication, with the reason |
| `bf18e03` | `first_releases`, `assets_fred_realtime_data`, `monetary_okun_realtime_data`, and both attempts pre-registered |
| `622fb40` | the units-across-vintages measurement against the publisher, plus `FEDFUNDS` as a declared policy-rate source and the v2 attempt |
