# WS-B — vintaged employment data

**Question.** `docs/calibration-status.md` records `regional_model` failing `no_revision_leakage`
on both its panels and calls the failure structural: *"no published employment source in this
catalog carries vintages, so `regional_model` cannot pass that criterion on CBP or QCEW whatever
the intervals do."* A previous analysis generalised that to *no published employment source
carries vintages*, which would make the process untestable rather than unfitted. Is that true?

**Verdict: false. Vintaged state-by-sector employment is published, and no reconstruction is
needed.** ALFRED archives the BLS **CES State and Area (SAE)** employment series at
state × supersector granularity with **229 real-time vintages, 2007-06-19 to 2026-08-21** — one
per monthly state employment release. `TXMFGN` ("All Employees: Manufacturing in Texas") answers
`series/vintagedates` with 229 dates. The original statement was true of the *catalog* (the
`bls_labor` QCEW and `census_business` CBP builds are current-vintage downloads) and false of the
*world*.

There is a specific trap that makes SAE look un-vintaged, and it is probably what produced the
wrong generalisation: **FRED serves the same BLS series under two ids and they are not
interchangeable in ALFRED.** Construct the id from the BLS scheme and you get a 400:

```
series_id=SMU48000003000000001 -> 400 "does not exist in ALFRED but may exist in FRED"
series_id=TXMFGN               -> 200, 229 vintage dates from 2007-06-19
```

and the patchiness runs both ways — `TXINFO` has 229 vintages while `CAINFO` is not in ALFRED at
all, and for Delaware only the structured `SMS10000001500000001` is archived while `DECONS` and
`DEMLC` are not. So ids must be **discovered and verified one at a time**, never templated.

## Sources checked

Checked 2026-09-17. "Vintages?" means: can you recover what the series said at a past date, at
state × sector granularity, without reading a later revision?

| # | Source | URL | HTTP | Granularity | Vintages? |
| --- | --- | --- | ---: | --- | --- |
| 1 | ALFRED — CES SAE state × supersector, monthly NSA | `api.stlouisfed.org/fred/series/vintagedates?series_id=TXMFGN` | 200 | state × CES supersector, monthly | **yes — 229 vintages, 2007-06-19 .. 2026-08-21** |
| 2 | ALFRED — same, seasonally adjusted (`TXMFG`, `TXINFO`) | `…?series_id=TXMFG` | 200 | state × supersector, monthly SA | yes — 229 vintages |
| 3 | ALFRED — SAE **published annual average** (`SMU48000003000000001A`) | `…?series_id=SMU48000003000000001A` | 200 | state × supersector, annual | yes, but only **14** vintages (2014-01-28 .. 2026-04-08) — a third of the depth of the monthly form, which is why this dataset aggregates monthly rather than taking the published annual series |
| 4 | ALFRED — state total nonfarm (`TXNAN`, `CANA`) | `…?series_id=TXNAN` | 200 | state, monthly | yes — 229 vintages |
| 5 | ALFRED — structured monthly SAE ids | `…?series_id=SMU48000003000000001` | **400** | — | **mixed, and must be probed.** `SMU48000003000000001` and `SMS48000005000000001` → 400 *"does not exist in ALFRED"*; `SMS06000005000000001` → 200 with 133 vintages from 2015-05-27; `SMS11000001500000001` (DC) → 200 with 145 from 2014-01-28. The alias and structured forms cover each other's gaps. |
| 6 | ALFRED — PAYEMS (national control) | `…?series_id=PAYEMS` | 200 | national, monthly | yes — 859 vintages from 1955-05-06 |
| 7 | FRED release 112 catalogue (State Employment and Unemployment) | `…/release/series?release_id=112` | 200 | 9,014 series; 51 state-equivalents × 10 supersectors at state level | the discovery source for the alias ids used here |
| 8 | FRED release 308 catalogue (State and Metro Area Employment, Hours, Earnings) | `…/release/series?release_id=308` | 200 | 62,144 series, incl. 3-digit NAICS detail by state and the `SMU…A` annual form | finer industry detail exists in FRED; its ALFRED depth was not surveyed |
| 9 | FRED release 362 catalogue (Quarterly Census of Employment and Wages) | `…/release/series?release_id=362` | 200 | 7,714 series, **county × all industries** (`ENU…`), quarterly | QCEW *is* in FRED, but not at state × sector, so it cannot replace the QCEW panel |
| 10 | BLS QCEW annual singlefile (current) | `data.bls.gov/cew/data/files/2023/csv/2023_annual_singlefile.zip` | 200, `Last-Modified: Thu, 29 Aug 2024` | county/state × 6-digit NAICS, annual | **no** — one file per reference year, revised in place; the only date is the current `Last-Modified` |
| 11 | BLS QCEW singlefile in the Internet Archive | `web.archive.org/cdx/search/cdx?url=data.bls.gov/cew/data/files/2019/csv/2019_annual_singlefile.zip` | 200 | as row 10 | **partly.** 16 snapshots of the *2019* file, 2020-10-17 .. 2026-06-13, with different lengths (78,436,222 / 78,414,722 / 78,436,480 …), so the file provably changes. See "the path not taken". |
| 12 | BLS SAE flat files (`sm` time series) | `download.bls.gov/pub/time.series/sm/` | 200 | state × industry, monthly, full history | **no** — every `sm.data.*` is stamped `2026-08-21 10:00`; overwritten in place, one current vintage |
| 13 | BLS SAE archive directory `arcsm` | `download.bls.gov/pub/time.series/sm/arcsm/` | 200 | state × **SIC** industry | **one frozen snapshot** — every file stamped `2005-03-03`. This is the discontinued SIC-basis SAE series, not a vintage series of the current NAICS-basis one. |
| 14 | BLS LAUS flat files | `download.bls.gov/pub/time.series/la/` | 200 | state/area labour force, monthly | **no** — current vintage only, same overwrite-in-place pattern |
| 15 | Census CBP datasets, by reference year | `www2.census.gov/programs-surveys/cbp/datasets/2022/` | 200 | state/county × NAICS, annual | **no** — one directory per *reference* year, not per release; no superseded files |
| 16 | Census CBP datasets root | `www2.census.gov/programs-surveys/cbp/datasets/` | **429** on every attempt over ~20 min, with and without a contact User-Agent | — | rate-limited. The per-year directories answer 200 and show the structure, so the root adds nothing. |
| 17 | Census CBP in the Internet Archive | `web.archive.org/cdx/search/cdx?url=www2.census.gov/programs-surveys/cbp/datasets/2019/*` | 200 | as row 15 | **no usable panel** — 2-4 snapshots per file (`cbp19co.zip`: 2022-02, 2025-04, 2026-08), lengths differing by ~0.3%, which is as consistent with zip re-compression as with revision. Too sparse to date a release. |
| 18 | Census CBP API | `api.census.gov/data/2022/cbp` | 200 | state/county × NAICS, annual | **no** — the year in the path is the reference year; there is no vintage parameter |

**On the BLS 403s.** `download.bls.gov` and `www.bls.gov` answer **403** to a generic User-Agent
and **200** to one carrying a contact address (`worldmodel-research/1.0
(jacobspam0123456789@gmail.com)`). Rows 12-14 were re-checked in the contact form: a 403 there is
policy, not absence. `www.bls.gov/bls/archived_data.htm` is a genuine **404**.

## What the criterion needs, and why this satisfies it

`regional_model`'s holdout forecaster consumes `data['employment']` rows of
`{region, industry, year, date, employment}`, and `ModelFamilyEstimator` orders and truncates them
by `date` (`holdout_forecaster['time_key'] = 'date'`). `no_revision_leakage` passes only when the
loader declares `information_time: 'real_time'`, and that is honest only when a row's `date` *is*
the day its value was published.

Both existing loaders set `date = f'{year}-12-31'` — a reference-period end on a single current
vintage — so the declaration could not be made. `ces_sae_regional_data` sets it differently:

* a reference year's **release date** is the *latest* first-vintage date across all
  51 × 10 × 12 (state, supersector, month) cells of that year: the day the release that completed
  the year landed, and the earliest day anyone could have formed its annual average;
* all twelve monthly values are then read **as they stood on that release date**, so an annual
  average is one vintage's view of one year rather than a splice of twelve vintages;
* months whose first vintage *is* the series' archive start are dropped, and the whole reference
  year with them: FRED's archive opens in 2007 with a snapshot of already-revised history, and
  keeping it would reintroduce the leakage the loader exists to remove.

No row contains a value published after the row's own date, so `revisions` is `'none'` on the same
basis as `monetary_realtime_data`. A unit test puts a later benchmark revision in the fixture and
fails if it reaches a row (`tests/test_estimation_loaders.py::FamilyLoaderTests`).

## Coverage, measured rather than assumed

`build_config.py` asked `series/vintagedates` about every candidate. All 603 resolved; none were
skipped.

| | |
| --- | ---: |
| state-equivalents | **51** (50 states + DC; Puerto Rico and the Virgin Islands have no aliased form) |
| panel series (9 supersectors + total nonfarm) | **510** |
| residual-check series (`construction`, `mining_logging`, combined) | **93** |
| vintage count per series | **138 – 253** |
| latest vintage | 2026-08-21 |
| **binding first vintage** | **2011-11-22** — "All Employees: Information in Alabama" |

The last row is the number that sets the protocol. A *complete* panel is real time only back to its
shallowest member, and the state Information NSA series enter ALFRED on 2011-11-22 while every
other supersector starts 2007-06-19 (`WVINFON` is the exception at 2007-06-19). So the attempts
declare `start_year: 2012`: fourteen years, thirteen growth years. That is worse than QCEW's
nineteen and much better than CBP's three.

### Two compromises, stated plainly

**Coarser industries.** CES SAE publishes supersectors, not 2-digit NAICS: ten industries where
`qcew_state_sectors` had twenty. The Bartik shock is therefore built from a coarser decomposition,
which should weaken the instrument rather than flatter it.

**A residual tenth industry.** Mining/logging/construction has no aliased series for five
state-equivalents (Delaware, DC, Hawaii, Maryland, Nebraska), and the structured
`SMS<fips>0000015000000001` form that does cover them only enters ALFRED in **2014** (Delaware 146
vintages from 2014-01-28; Wyoming 125 from 2016-03-14). Using it would cut the panel from fourteen
years to twelve. So the tenth industry is the within-vintage residual `total_nonfarm − Σ nine`,
which is exact, not approximate — at 2019-06 on the current vintage:

| state | total nonfarm | Σ nine | residual | published Construction + Mining&Logging |
| --- | ---: | ---: | ---: | ---: |
| Texas | 12,836.3 | 11,805.3 | **1,031.0** | 778.6 + 252.4 = **1,031.0** |
| Wyoming | 298.9 | 253.8 | **45.1** | 23.9 + 21.2 = **45.1** |
| Delaware | 470.5 | 447.0 | 23.5 | *not published* |
| District of Columbia | 797.6 | 782.5 | 15.1 | *not published* |

The 93 published `construction` / `mining_logging` series are carried with
`dimensions.panel_role = 'residual_check'` so this identity can be re-checked per vintage instead
of trusted. `total_nonfarm` is **not** emitted as a panel industry: the family sums industries to
get region totals, so emitting it would double-count every job.

## The path not taken: reconstructing QCEW vintages from the Internet Archive

Row 11 shows this is possible and shows why it was not done. To build a state × 2-digit-NAICS
vintage panel over twenty reference years you would need roughly 20 × 10 snapshots × 78 MiB ≈
**15 GiB** of downloads, and the result would still have gaps (no snapshot of the 2019 file exists
between 2021-10 and 2023-11), vintage dates that differ per reference year, and — decisively —
vintage dates that are *crawl* dates rather than *publication* dates, so every "as of" claim would
be accurate only to within the gap between crawls. ALFRED gives publisher-dated vintages at half
the industry granularity, a hundredth of the cost, and none of the dating ambiguity. If 2-digit
NAICS detail turns out to matter, this is the documented fallback.

## Dataset built

[`data/fred_state_employment_vintages/`](../../data/fred_state_employment_vintages/README.md) —
603 FRED API requests, one per series over the full real-time window, declared `desired_bytes`
320 MiB.

<!-- RESULTS -->
