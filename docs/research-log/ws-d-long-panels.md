# WS-D — panel length

**Question.** Two of the 45 acceptance-criterion failures in `docs/calibration-status.md` are
`minimum_test_forecasts`: the test window simply does not contain enough forecasts to evaluate.

- `population_growth_rate.census_pep` — `census_population@d621c962`, annual 2010-2024, **14
  observations and n = 4** against a declared minimum of 8. Also fails `interval_coverage` (0.50).
- `deposit_rate_pass_through.fred_realtime` — `fred_macro_panel@b395bda0` (SNDR) + DFF, monthly
  2021-2026, **62 observations and n = 16** against a declared 24. Also fails
  `beats_persistence_dm` (p = 0.871) and `interval_coverage` (0.438).

These are the only two failures in the catalog where more data genuinely is the fix.

**Verdict: both are now evaluable, and lengthening the panels converted one unevaluable criterion
into a real failure and left another real failure standing.** Five attempts were pre-registered in
`real_data_plan.json` (key `panel_length_wave`) and run. `minimum_test_forecasts` passes in all
five. Nothing else newly passes:

| Attempt | Series | Obs at the cutoff | n test | Verdict | Failing criteria |
| --- | --- | ---: | ---: | --- | --- |
| `population_growth_rate.census_pep` (recorded) | Census PEP | 14 | 4 | fail | `minimum_test_forecasts`, `interval_coverage` |
| **`population_growth_rate.census_pep_v2`** | Census PEP, 20 vintages | **26** | **9** | fail | `interval_coverage` (0.333) |
| **`population_growth_rate.fred_popthm`** | FRED POPTHM | **67** | **16** | fail | `interval_coverage` (0.500) |
| `deposit_rate_pass_through.fred_realtime` (recorded) | SNDR | 62 | 16 | fail | `minimum_test_forecasts`, `beats_persistence_dm`, `interval_coverage` |
| **`deposit_rate_pass_through.fred_realtime_v2`** | SNDR, splits recut | 64 | **24** | fail | `beats_persistence_dm` (p = 0.287), `interval_coverage` (0.458) |
| **`deposit_rate_pass_through.savnrnj_substitute`** | SAVNRNJ *(substitute)* | **142** | **38** | fail | `beats_persistence_dm` (p = 0.131) |
| **`deposit_rate_pass_through.m2own_substitute`** | M2OWN *(substitute)* | **724** | **29** | fail | `beats_persistence_dm` (p = 0.725), `interval_coverage` (0.966) |

The two attempts marked *(substitute)* fit a **different series** from the declared SNDR and are
registered as separate attempts with the substitution recorded in their overrides. Neither can be
read as `deposit_rate_pass_through` passing, exactly as `default_hazard.fdic_laus_quarterly`'s pass
on a substituted delinquency series is not read as `default_hazard` passing. In fact neither
passes, so the question does not arise.

`population_growth` has one required component, so it is still not a validated process:
`population_growth_rate` fails `interval_coverage` on both sources.

---

## 1. Population

### What was acquired

`census_population` held **two** vintages (2020 and 2024), so the national annual series ran
2010-2024. It now holds **twenty**: every national/state vintage the Census server still serves in
the machine-readable ALLDATA layout, plus the 2000-2010 national intercensal series.

| Group | Vintages | Annual national coverage |
| --- | --- | --- |
| 2000 census base | V2004, V2005, V2006, V2007 | 2000-2004 … 2000-2007 |
| 2000-2010 intercensal (revised after the 2010 census) | one series | 2000-2010 |
| 2010 census base | V2011 … V2020 | 2010-2011 … 2010-2020 |
| 2020 census base | V2021 … V2025 | 2020-2021 … 2020-2025 |

Union: **2000-2025, 26 annual observations** (was 15 years / 14 usable). 28 raw files, 102.1 MiB,
of which **0.81 MiB is new** beyond the previous 13 files; `desired_bytes` 220 MB, which covers
both raw artifacts because the superseded one is retained — the published
`population_growth_rate.census_pep` report pins the normalized output built from it. Fair-share cap
is 25 GiB, so this is 0.85% of the dataset's allowance.

**Vintages 2008, 2009 and 2010 and the 2000s state files do not exist in this layout.** The Census
server serves only per-year `nst-est200X-popchgYYYY.csv` and `nst-est200X-compchgYYYY.csv`
presentation tables for those, which is why the 2000s come from V2004-V2007 plus the intercensal
series rather than from a V2009 ALLDATA file. Probed and confirmed absent, not assumed.

### The finding that cost the most origins

`attributes.released_at` is the HTTP `Last-Modified` of each published file. **The 2016 census.gov
migration overwrote it for every file older than the migration**, so vintages 2004-2015 all carry a
2016 timestamp instead of their original December release date:

| `released_at` | Vintages |
| --- | --- |
| 2016-07-19 | V2004, V2005, V2006, V2007, V2011, V2013, V2014 |
| 2016-08-24 | 2000-2010 intercensal |
| 2016-08-25 | V2012 |
| 2016-09-01 | V2015 |
| 2016-12-20 … 2019-12-30 | V2016, V2017, V2018, V2019 (these do match the Census release dates) |
| 2021-05-04 | V2020 (genuinely late — the 2020 census delayed it) |
| 2021-12-21 … 2026-01-27 | V2021 … V2025 |

A `Last-Modified` upper bound *delays* availability, so it cannot leak. What it costs is real-time
origins. Under the strict policy the earliest annual origin with the eight observations the
component needs is **2016** (vintage 2015, available 2016-09-01, with the intercensal series
available from 2016-08-24) rather than 2012, which the true December release dates would have
allowed. The alternative — substituting a documented December release schedule for the recorded
timestamp — would move availability *earlier* than the evidence supports, so it was not done. This
is the binding constraint on the census attempt and the reason its n is 9 and not ~14.

### Pipeline changes the older vintages needed

Field names and identifier widths drift across the vintage files, so `_normalize` uppercases every
field name and zero-pads `SUMLEV`, `STATE`, `COUNTY`, `PLACE` and `COUSUB`. Both are no-ops on every
file published since 2016. Without this the older files are silently dropped:

| Vintage | What it does | What broke |
| --- | --- | --- |
| V2012 | header `Sumlev,Region,Division,State,Name` | `row.get('SUMLEV')` missed → every row skipped |
| V2011 | `SUMLEV` = `10`, `STATE` = `0` | summary level never matched `'010'`; state ids would have been `geo:US:state:6` |
| V2006 | lowercase `births2000` … | the case-sensitive component regex missed them |
| V2004, V2005 | `INTERNALMIG` rather than `DOMESTICMIG` | field not in the metric table |
| V2004-V2007 | `CENSUS2000POP`, `ESTIMATESBASE2000` | the April-1 base fields were a hardcoded 2010/2020 list |

Two further corrections: the census base year now comes from each row's `ESTIMATESBASE<year>` field
instead of being inferred from the vintage (`first = 2020 if vintage == 2024 else 2010`), which is
what makes a 2000-based vintage's first estimate year start at its own April-1 base; and the
intercensal file has an entirely different layout (one row per reference month, year and single year
of age) handled by a new `_intercensal` branch. Only its all-ages totals are emitted — a national
`population` row carrying an `age_group` or `sex` dimension would collide with the annual national
series that the estimation loader selects on metric and subject alone, and `select()` raises on
conflicting values with equal availability.

The dataset test was extended with fixtures for the V2011, V2006 and intercensal layouts and an
assertion that there is exactly one national `population` row per (vintage, period).

### POPTHM: the fix `calibration-status.md` named was already published

The v1 record says *"A longer real-time series (POPTHM ALFRED vintages from `fred_macro_panel`, or
older PEP vintage files) would make this testable."* The first half needed **no acquisition at all**:
`fred_macro_panel@7dcce89c` already carries POPTHM with **811 monthly periods 1959-01..2026-07 and
325 ALFRED vintages, the earliest 1999-07-30**. `requirements.json` names POPTHM as a source for the
same `population` series alongside Census PEP, so this is a source change within the declared series,
not a substitution — but it is not the same object either, and that is recorded in the attempt's
override: POPTHM is a total-resident-population measure aggregated to annual with the declared
`last` rule (so an annual point is that year's December level), where PEP publishes a July-1
resident estimate. Both are rebased at each decennial census.

Aggregated to annual it gives **67 annual observations 1959-2025** and, because the vintages start in
1999, annual origins from 2000 onward. Holdout 2010-2025 = **16 forecasts**.

### Results

Both attempts, pre-registered splits, published reports:

| | `census_pep` (recorded) | `census_pep_v2` | `fred_popthm` |
| --- | --- | --- | --- |
| Input | `census_population@d621c962` | `census_population@063a1413` | `fred_macro_panel@7dcce89c` |
| Frame at the cutoff | 14, annual 2010-2024 | **26, annual 2000-2025** | **67, annual 1959-2025** |
| Published vintages of the national annual series | 2 | **20** | 325 ALFRED vintages of POPTHM |
| Splits (train / validation / holdout) | ≤2016 / 2017-2018 / 2019-2024 | ≤2015 / 2016 / **2017-2025** | ≤2005 / 2006-2009 / **2010-2025** |
| n test forecasts | 4 | **9** | **16** |
| `growth_rate_per_year` (SE) | 0.006777 (0.000472) | 0.006652 (0.000401) | 0.006427 (0.000645) |
| MAE (people) | 1.28e6 | 1.10e6 | 7.82e5 |
| persistence MAE | 3.36e6 | 2.51e6 | 2.98e6 |
| drift MAE | 1.36e6 | 1.05e6 | 7.42e5 |
| CRPS | 1.07e6 | 8.79e5 | 6.13e5 |
| DM p vs persistence | 0.020 | **0.0094** | **3.6e-07** |
| 80% interval coverage (nominal 0.80 ± 0.20) | 0.50 | **0.333** | **0.500** |
| Skipped origins | 2 | **0** | **0** |
| `minimum_test_forecasts` | **fail** (4 < 8) | **pass** (9 ≥ 8) | **pass** (16 ≥ 8) |
| `beats_persistence_dm` | pass | pass | pass |
| `interval_coverage` | **fail** | **fail** | **fail** |
| `parameters_within_declared_bounds` | pass | pass | pass |
| `no_timing_leakage` | pass | pass | pass |
| `no_revision_leakage` | pass | pass | pass |
| Verdict | fail (2 criteria) | **fail (1 criterion)** | **fail (1 criterion)** |

Reports: `b17aca9776c3…` / artifact `6dbe25f775da…` (census_pep_v2); `96923961224d…` / artifact
`9be38e2a8920…` (fred_popthm). Both read back and re-verify under
`wm calibration-status calibration_reports@<version>`.

**How much came from the data and how much from the splits.** Rerunning v1's *declared splits* on
the extended panel (unpublished diagnostic) gives **n = 6** — still a `minimum_test_forecasts`
failure. The panel extension alone was not sufficient; the split recut alone would not have helped
either, because v1's earliest usable origin is set by the 2016 availability wall. Both were needed.

**What the remaining failure is.** `interval_coverage` gets *worse* on the census panel (0.50 → 0.333)
and stays at 0.50 on POPTHM, so this is not a small-sample artifact that more forecasts fix — it is
the same defect the fifth wave diagnosed elsewhere: a Gaussian interval whose scale is the in-sample
residual sd of a drift regression, against a series whose year-to-year growth shifts in level
(immigration policy, the pandemic, and on POPTHM the census rebasings). On POPTHM the mean interval
width is 1.19e6 people against an MAE of 7.82e5, i.e. the 80% half-width is *smaller* than the
average error. Nine and sixteen forecasts are also few enough that coverage has a wide sampling
error in its own right — with n = 9 the observed 0.333 is 3 of 9.

Worth recording alongside: neither attempt beats **drift** (DM p = 0.954 and 0.907; drift MAE is
slightly *lower* than the model's in both). `beats_persistence_dm` is the declared criterion and it
passes decisively, but a drift regression on population is close to tautologically a drift
extrapolation, so that pass says less than its p-value suggests.

---

## 2. Deposit rates

### SNDR cannot be extended backwards

Checked first, because the brief required it. **FRED's SNDR begins 2021-04-01 and no earlier data
exists**: the series is the FDIC National Rate under the methodology adopted in 2021. Its published
history in `fred_macro_panel@7dcce89c` is 65 monthly periods 2021-04..2026-08 with 64 vintages, and
64 align with DFF. The component needs 36 observations to fit and declares 24 holdout forecasts.

So two things were done, and they are kept separate.

### (a) The declared series, splits recut — not a substitution

65 months is *just* enough for 36 + 24 if the validation window is small. The v1 declaration spent
twelve months on validation and was left with 17 possible origins. `fred_realtime_v2` recuts the
splits by a mechanical, outcome-free rule: **train is exactly the first 36 months** (2021-04..2024-03,
the minimum the component needs), the **holdout is everything after 2024-07-31**, and validation is
the four months in between. Boundaries were fixed before the run; the rationale predicted 25 holdout
forecasts and the realised count is **24**, because the first target after `validation_end` has no
informative origin. 24 is exactly the declared minimum, so `minimum_test_forecasts` passes with **no
margin** — recorded as a `run_note` on the attempt.

**Attribution.** Rerunning v1's declared splits on the current panel version (unpublished
diagnostic) reproduces **n = 16, coverage 0.4375, DM p = 0.871** — the recorded v1 verdict exactly.
So the whole of 16 → 24 is the split recut; none of it is data recency.

### (b) Longer substitutes: a new dataset, and they are different estimands

Searched FRED for a longer-history deposit rate. What exists:

| Candidate | Coverage | Why / why not |
| --- | --- | --- |
| **`SAVNRNJ`** | weekly 2009-05-18..2021-03-29, 365 ALFRED vintages from 2014-03-10 | **used.** FDIC National Rate on Non-Jumbo Deposits: Savings — SNDR's direct predecessor, same programme and same deposit product, discontinued in 2021 when FDIC replaced a simple average over a sampled branch panel with a deposit-weighted average over all institutions (credit unions included) |
| `MMNRNJ`, `CD12NRNJ` | same window | acquired (`MMNRNJ`) for the money-market counterpart; no attempt registered |
| **`M2OWN`** | monthly 1959-02..2019-06, 203 vintages from 2014-03-07 | **used.** The weighted average of rates received on the interest-bearing assets in M2. Sixty years, many complete rate cycles |
| `IR3TCD01USM156N`, `CD1M`, `CD3M`, `DCD6M` | 1964-2023 | wholesale secondary-market CD rates, not retail deposit rates; pass-through from fed funds is near-mechanical |
| `BRMCDS0101` (Bankrate 1-year CD) | weekly 1984- | commercial (Bankrate) copyright, and a CD rate rather than a savings rate |
| FDIC call reports (`data/fdic_bank_financials/`) | quarterly 1984- | an implied deposit cost (interest expense / deposits) is constructible, but the component declares **monthly** frequency, and changing that is a re-specification rather than a substitution |

**Nothing here is SNDR.** The 2021 FDIC methodology change means SAVNRNJ and SNDR are not comparable
in level or in pass-through, so **nothing is spliced**; and M2OWN includes money-market mutual fund
holdings, which are not bank deposits at all. Each attempt declares its substitution in its
`overrides.deposit_rate.description`, which is what the estimate's data audit carries.

New dataset **`fred_deposit_rates`** (SAVNRNJ, MMNRNJ, M2OWN, every ALFRED vintage): 3 requests,
**1.08 MiB** downloaded, 11,763 records — 624 / 624 / 10,515 observations, matching the FRED row
counts exactly. `desired_bytes` 4 MiB declared, 1.13 MiB used. Two shapes worth noting: the FDIC
national rates are **never revised** (one real-time period per weekly value, `realtime_start` equal
to the publication week from 2014-03 on), while M2OWN is revised heavily (up to 118 vintages for a
single month). Both series' pre-2014 history carries the series' first ALFRED vintage date, so under
the strict policy the earliest usable origin for either is 2014-04.

### Results

| | `fred_realtime` (recorded) | `fred_realtime_v2` | `savnrnj_substitute` | `m2own_substitute` |
| --- | --- | --- | --- | --- |
| Deposit rate | SNDR | SNDR | SAVNRNJ *(substitute)* | M2OWN *(substitute)* |
| Input | panel@b395bda0 | panel@7dcce89c | `fred_deposit_rates@ec9e94d6` | `fred_deposit_rates@ec9e94d6` |
| Frame at the cutoff | 62 monthly | 64, 2021-04..2026-07 | **142, 2009-05..2021-02** | **724, 1959-02..2019-05** |
| Splits | ≤2024-03 / …2025-03 / 2025-04.. | ≤2024-03 / …2024-07 / **2024-08..2026-08** | ≤2015-12 / …2017-12 / **2018-01..2021-03** | ≤2015-12 / …2016-12 / **2017-01..2019-06** |
| n test forecasts | 16 | **24** | **38** | **29** |
| `pass_through` (SE) | 0.0782 (0.0018) | 0.0782 (0.0018) | 0.0808 (0.0407) | **0.6199 (0.0519)** |
| `impact_pass_through` (SE) | — | 0.0121 (0.0095) | 0.0111 (0.0019) | 0.2507 (0.0380) |
| `adjustment_speed_per_month` (SE) | 0.2393 (0.083) | 0.2393 (0.0831) | 0.0178 (0.0089) | 0.0320 (0.0108) |
| `spread` (SE) | — | 0.00059 (0.00006) | −0.00021 (0.00049) | −0.00088 (0.00218) |
| MAE (percentage points) | 0.0131 | 0.01437 | 0.00301 | 0.02866 |
| persistence MAE | 0.0056 | 0.01000 | 0.00259 | 0.03248 |
| drift MAE | — | 0.01465 | 0.00319 | 0.03214 |
| CRPS | — | 0.01118 | 0.00227 | 0.03716 |
| DM p vs persistence | 0.871 | **0.287** | **0.131** | 0.725 |
| 80% coverage (nominal 0.80 ± 0.15) | 0.438 | 0.458 | **0.763 pass** | 0.966 |
| Skipped origins | 0 | 0 | 0 | 0 |
| `minimum_test_forecasts` | **fail** (16 < 24) | **pass** (24 = 24) | **pass** (38) | **pass** (29) |
| `beats_persistence_dm` | **fail** | **fail** | **fail** | **fail** |
| `interval_coverage` | **fail** | **fail** | **pass** | **fail** |
| `parameters_within_declared_bounds` | pass | pass | pass | pass |
| `no_timing_leakage` | pass | pass | pass | pass |
| `no_revision_leakage` | pass | pass | pass | pass |
| Verdict | fail (3 criteria) | **fail (2)** | **fail (1)** | **fail (2)** |

Reports: `3a2489b407af…` / artifact `aadc3329867c…`; `838457583c80…` / artifact `7ecb9df59d45…`;
`0132ca127434…` / artifact `b226e2393a0f…`.

### Reading these

- **`minimum_test_forecasts` is no longer the blocker anywhere.** It was the criterion that made the
  other two unevaluable, and it now passes on all three.
- **`beats_persistence_dm` fails on every one of the three, over three different windows, two
  different series and sample sizes 24, 29 and 38.** With n = 16 that was arguably a power problem;
  with 38 monthly forecasts spanning the 2019 cuts and the March 2020 collapse to the floor it is a
  finding: an error-correction model in the level of an administered deposit rate does not beat "the
  rate is what it was last month". On SAVNRNJ the model's MAE (0.00301) is *worse* than
  persistence's (0.00259) and the p-value runs the other way. This is the more informative result and
  it was not visible at n = 16.
- **The two substitutes disagree about pass-through by a factor of eight**, and the reason is which
  regime each covers. SAVNRNJ 2009-2021 is a floor-bound decade in which the FDIC national savings
  rate barely moves, so the fitted long-run pass-through is 0.081 ± 0.041 — statistically
  indistinguishable from zero at any conventional level. M2OWN 1959-2019 spans many complete cycles
  and gives 0.620 ± 0.052 with a within-month impact of 0.251 ± 0.038 and a 3.2%/month adjustment
  speed, which is a textbook shape for a deposit rate. The mechanism parameter is regime-dependent,
  not a constant, and anyone binding `mechanisms.deposit_interest.pass_through` should say which
  regime they mean. The same pattern as `default_hazard`'s two variants disagreeing on the sign of
  `unemployment_sensitivity`.
- **`interval_coverage` passes for the first time on this component** (0.763 on SAVNRNJ) without any
  interval work, purely because a never-revised administered rate has a residual scale that is
  stable over the holdout. It fails on M2OWN from the *other* side (0.966, intervals far too wide:
  mean width 0.324pp against an MAE of 0.029pp), which is what a heavily revised series does to an
  in-sample Gaussian.

---

## 3. What was not done, and why

- **Original PEP December release dates.** Recoverable from Census release notes, not from the
  files. Using them would move availability earlier than the recorded evidence supports, so the
  2016 migration timestamps stand and the census attempt keeps 9 origins instead of ~14.
- **Splicing SAVNRNJ onto SNDR** to get 2009-2026 in one series. The 2021 FDIC methodology change is
  a level break at exactly the point where the holdout would begin; a spliced series would put a
  measurement discontinuity inside the test window and call the result pass-through.
- **A quarterly FDIC implied deposit cost** from `fdic_bank_financials` (interest expense on deposits
  / total deposits, 1984-). The component declares monthly frequency; changing it is a
  re-specification of the component, not a substitution of a series, and would have to be filed as
  such.
- **Interval work.** Three of the five attempts now fail only or partly on `interval_coverage`, and
  the diagnoses differ (too narrow on both population attempts and on SNDR, far too wide on M2OWN).
  That is WS-E's subject, and changing an interval method after seeing these holdouts would be
  tuning. The declared next steps, unrun: for population, a trailing-scale predictive distribution
  of the kind that fixed `inventory_balance`; for M2OWN, the revision component that fixed
  `credit_growth`, since its dispersion is exactly what an in-sample residual scale over-states here.
- **`population_growth_rate.census_pep` was not re-recorded.** It is unpinned, so a rerun today
  reads `census_population@063a1413` rather than the `d621c962` its published report names. Its
  recorded verdict belongs to that earlier version and is kept unchanged; `census_pep_v2` supersedes
  it.
