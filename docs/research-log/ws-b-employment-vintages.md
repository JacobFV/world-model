# WS-B — vintaged employment data

**Question.** `docs/calibration-status.md` records that `regional_model` fails
`no_revision_leakage` on both its panels, and says the failure is structural: *"no published
employment source in this catalog carries vintages, so `regional_model` cannot pass that
criterion on CBP or QCEW whatever the intervals do."* A previous analysis generalised that to
*no published employment source carries vintages*. Is that true?

**Verdict: reconstructible — in fact, no reconstruction is needed.** The generalisation is
false. ALFRED archives the BLS **CES State and Area (SAE)** employment series at
state × supersector granularity with **229 real-time vintages from 2007-06-19 to 2026-08-21**,
one per monthly state employment release. The earlier claim was true of the *catalog* (the
`bls_labor` and `census_business` builds are current-vintage downloads) and false of the
*world*. The claim that ALFRED only carries national series is what went wrong: `TXMFGN`
("All Employees: Manufacturing in Texas") answers `series/vintagedates` with 229 dates.

What follows is every source actually checked, then the dataset built from the one that works,
then the re-run verdict.

## Sources checked

Checked 2026-09-17. "Vintages?" answers: does the source let you recover *what the series said
at a past date*, at state × sector granularity, without reading a later revision?

| # | Source | URL | HTTP | Granularity | Vintages? |
| --- | --- | --- | ---: | --- | --- |
| 1 | ALFRED — CES SAE state × supersector, monthly NSA | `https://api.stlouisfed.org/fred/series/vintagedates?series_id=TXMFGN` | 200 | state × CES supersector, monthly | **yes — 229 vintages, 2007-06-19 .. 2026-08-21** |
| 2 | ALFRED — same, seasonally adjusted (`TXMFG`) | `.../vintagedates?series_id=TXMFG` | 200 | state × supersector, monthly SA | yes — 229 vintages, same span |
| 3 | ALFRED — CES SAE state × supersector, **annual average** (`SMU48000003000000001A`) | `.../vintagedates?series_id=SMU48000003000000001A` | 200 | state × supersector, annual | yes, but only **14** vintages (2014-01-28 .. 2026-04-08) |
| 4 | ALFRED — state total nonfarm (`TXNA`, `TXNAN`, `CANA`) | `.../vintagedates?series_id=TXNAN` | 200 | state, monthly | yes — 229 vintages |
| 5 | ALFRED — BLS-style SAE ids (`SMU48000003000000001`, `SMS48000003000000001`) | `.../vintagedates?series_id=SMU48000003000000001` | **400** | — | **no** — *"does not exist in ALFRED but may exist in FRED"*. Only FRED's short alias form of this series family carries vintage history. This is the trap that makes SAE look un-vintaged if you construct ids from the BLS series-id scheme. |
| 6 | ALFRED — PAYEMS (national, control) | `.../vintagedates?series_id=PAYEMS` | 200 | national, monthly | yes — 859 vintages from 1955-05-06 |
| 7 | FRED release 112 catalog (State Employment and Unemployment) | `.../release/series?release_id=112` | 200 | 9,014 series; 51 state-equivalents × 10 supersectors at state level | source of the alias ids used here |
| 8 | FRED release 308 catalog (State and Metro Area Employment, Hours, Earnings) | `.../release/series?release_id=308` | 200 | 62,144 series, incl. 3-digit NAICS detail by state | the `SMU…A` annual form (row 3); the monthly `SMU…` form is FRED-only |
| 9 | FRED release 362 catalog (Quarterly Census of Employment and Wages) | `.../release/series?release_id=362` | 200 | 7,714 series, **county** × all-industries (`ENU…`), quarterly | QCEW *is* in FRED, but at county-all-industry level, not state × sector |
| 10 | BLS QCEW annual singlefile (current) | `https://data.bls.gov/cew/data/files/2023/csv/2023_annual_singlefile.zip` | 200 (`Last-Modified: 2024-08-29`) | county/state × 6-digit NAICS, annual | **no** — one file per reference year, revised in place; the only date is the current `Last-Modified` |
| 11 | BLS QCEW singlefile in the Internet Archive | `http://web.archive.org/cdx/search/cdx?url=data.bls.gov/cew/data/files/2019/csv/2019_annual_singlefile.zip` | 200 | as row 10 | **partly** — 16 snapshots of the 2019 file, 2020-10-17 .. 2026-06-13, with *different lengths* (78,436,222 / 78,414,722 / 78,436,480 …), so the file provably changes. But coverage is irregular (nothing between 2021-10 and 2023-11) and ~78 MiB per snapshot per reference year. See "the path not taken". |
| 12 | BLS SAE flat files (`sm` time series) | `https://download.bls.gov/pub/time.series/sm/` | 200 | state × industry, monthly, full history | **no** — `sm.data.*` are overwritten in place (all stamped `2026-08-21 10:00`); one current vintage only |
| 13 | BLS SAE archive directory `arcsm` | `https://download.bls.gov/pub/time.series/sm/arcsm/` | 200 | state × SIC industry | **one frozen snapshot**, every file stamped `2005-03-03`. This is the discontinued SIC-basis SAE series, not a vintage series of the current NAICS-basis one. Not usable as a vintage panel. |
| 14 | BLS LAUS flat files | `https://download.bls.gov/pub/time.series/la/` | 200 | state/area labour force, monthly | **no** — current vintage only, same overwrite-in-place pattern |
| 15 | Census CBP dataset directory, by reference year | `https://www2.census.gov/programs-surveys/cbp/datasets/2022/` | 200 | state/county × NAICS, annual | **no** — one directory per *reference* year, not per release. No superseded files. |
| 16 | Census CBP dataset root | `https://www2.census.gov/programs-surveys/cbp/datasets/` | **429** (repeatedly, over ~20 min with a contact User-Agent) | — | rate-limited; the per-year directories answer 200 and show the structure, so the root listing adds nothing |
| 17 | Census CBP in the Internet Archive | `http://web.archive.org/cdx/search/cdx?url=www2.census.gov/programs-surveys/cbp/datasets/2019/*` | 200 | as row 15 | **no usable panel** — 2-4 snapshots per file (e.g. `cbp19co.zip` in 2022-02, 2025-04, 2026-08), lengths differ by ~0.3% which is consistent with zip re-compression as much as with revision. Too sparse to date a release. |
| 18 | Census CBP API | `https://api.census.gov/data/2022/cbp` | 200 | state/county × NAICS, annual | **no** — the year in the path is the reference year; there is no vintage parameter |

**BLS note.** `download.bls.gov` and `www.bls.gov` return **403** to a generic User-Agent
(`worldmodel-research/1.0`) and **200** to one carrying a contact address
(`worldmodel-research/1.0 (jacobspam0123456789@gmail.com)`). Rows 12-14 were re-checked with the
contact form; a 403 here means "policy", not "absent".

## What the model actually needs, and why ALFRED SAE satisfies it

`regional_model`'s holdout forecaster (`worldmodel/models/regional.py::_holdout_forecast`)
consumes `data['employment']` rows of `{region, industry, year, date, employment}`, and
`ModelFamilyEstimator` orders and truncates them by `date`
(`holdout_forecaster['time_key'] = 'date'`). `no_revision_leakage` passes only when the loader
declares `information_time: 'real_time'`, and that declaration is honest only when each row's
`date` *is* the day its value was published.

The CBP and QCEW loaders set `date = f'{year}-12-31'` — a reference-period end, not a
publication date — so the declaration could not be made. With ALFRED vintages it can:

* a reference year's **first release** is the earliest vintage that contains all twelve of its
  months, and that vintage's `realtime_start` is the row's publication date;
* the annual average is formed from twelve monthly values **drawn from that one vintage**, so it
  is an annual average *as of* that release and never mixes vintages;
* nothing later than the row's own vintage is read, so there is no revised level anywhere in
  the panel. It is a first-release panel, the same construction that
  `monetary_model.fred_realtime_v2` used to pass.

Vintage depth sets the number of real-time origins. The monthly NSA series (229 vintages from
2007-06) give first releases for reference years 2007-2025 — nineteen — against the fourteen
vintages of the published annual-average form, which is why this dataset takes the monthly form
and aggregates, rather than taking the published annual series.

### The one compromise, stated plainly

CES SAE publishes **supersectors**, not 2-digit NAICS. The QCEW attempt used 20 two-digit
sectors; this panel has 10. The shift-share shock is therefore built from a coarser industry
decomposition, which should *weaken* the instrument, not flatter it. Recorded as a declared
difference from `regional_model.qcew_state_sectors`, not as an improvement.

Second: mining/logging/construction has no aliased FRED series for five state-equivalents
(Delaware, DC, Hawaii, Maryland, Nebraska), so the tenth industry is the residual
`total_nonfarm − sum(nine published supersectors)`, formed inside a single vintage. The
residual is exact, not approximate — checked against published levels at 2019-06 (current
vintage):

| State | total nonfarm | Σ nine supersectors | residual | published Construction + Mining&Logging |
| --- | ---: | ---: | ---: | ---: |
| Texas | 12,836.3 | 11,805.3 | **1,031.0** | 778.6 + 252.4 = **1,031.0** |
| Wyoming | 298.9 | 253.8 | **45.1** | 23.9 + 21.2 = **45.1** |
| Delaware | 470.5 | 447.0 | 23.5 | *not published* |
| District of Columbia | 797.6 | 782.5 | 15.1 | *not published* |

The dataset carries the published `construction` and `mining_logging` series where they exist
(`dimensions.panel_role = 'residual_check'`) so this identity can be re-checked per vintage
rather than taken on trust.

## The path not taken: reconstructing QCEW vintages from the Internet Archive

Row 11 shows this is *possible* and not worth doing here. The 2019 annual singlefile has 16
Wayback snapshots with provably different contents. To build a state × 2-digit-NAICS vintage
panel over 20 reference years you would need roughly 20 × 10 snapshots × 78 MiB ≈ **15 GiB** of
downloads, and the result would still have gaps (no 2019-file snapshot exists between 2021-10
and 2023-11), irregular vintage dates that differ per reference year, and vintage dates that are
*crawl* dates rather than *publication* dates — so an "as of" claim would be accurate only to
within the gap between releases. ALFRED gives publisher-dated vintages, at a fifth of the
granularity but a hundredth of the cost and none of the dating ambiguity. If 2-digit NAICS
detail turns out to matter, this is the documented fallback.

## Dataset built

`data/fred_state_employment_vintages/` — see that README. Status, bytes, vintage count and the
`regional_model` re-run verdict are recorded below as the work lands.

<!-- RESULTS -->
