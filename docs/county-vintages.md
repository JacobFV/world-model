# County vintages in ALFRED

**Question.** The places assay of the world-state encoder forecasts next-year county employment,
establishments and population, and is declared to fail `no_revision_leakage`: the county panel holds
current-vintage QCEW, LAUS, BEA and Census values, all of which revise. Which county-level series
does ALFRED archive with their real-time vintages, how deep do the vintages go, and can a
first-release county panel be built from them?

**Answer.** Every county-level family in FRED that was probed is in ALFRED, but the archive is
shallow: apart from population (2004/2007) and the monthly LAUS aliases (2005/2007), county vintages
begin between 2013 and 2021. The families the places assay needs were acquired as
[`fred_county_vintages`](../data/fred_county_vintages/README.md). **QCEW county employment is not in
FRED at all**, so the assay's `qcew:employment` target has no vintaged county source there; QCEW
county *establishment counts* are, with 40 vintages from 2017-03-07.

Checked 2026-09-18 against the FRED API (`release/series`, `series/vintagedates`, `series`,
`geofred/series/group`, `geofred/regional/data`).

## How the survey was done

1. `releases` (332 FRED releases), then `release/series?tag_names=county` per release: **32 releases
   carry 331,298 series tagged `county`**.
2. Each release's county series were grouped by (release, measure, frequency, seasonal adjustment),
   the measure being the title before " in <place>". That gives 262 families; the 137 with 200 or
   more series are county-wide rather than scattered.
3. Four series per family (first, one-third, two-thirds and last by id) were asked
   `series/vintagedates`. **None of the 548 probes answered "does not exist in ALFRED".**

This grouping reads titles, but only to decide which families to look at. The dataset itself takes
membership from the publisher's id codes (or, for population, the release's measure title) and every
county code from GeoFRED; see *County codes* in the dataset README.

## What ALFRED holds at county level

Vintage counts and first vintages are the range over the probed families of each release.

| release | name | county series | families (≥200 series) | frequency | vintages | earliest first vintage |
| ---: | --- | ---: | ---: | --- | --- | --- |
| 116 | Unemployment in States and Local Areas (LAUS) | 25,310 | 8 | A, M | 8-264 | 2005-06-08 |
| 119 | Annual Estimates of the Population for Counties | 3,157 | 1 | A | 21-22 | 2007-03-22 (2004-04-09 in some series) |
| 148 | Housing Units Authorized By Building Permits | 3,024 | 1 | A | 11 | 2017-01-27 |
| 171 | House Price Index (FHFA) | 2,402 | 1 | A | 10 | 2017-03-22 |
| 175 | Personal Income by County (BEA) | 6,230 | 2 | A | 13-14 | 2014-05-30 |
| 330 | Educational Attainment (ACS 5-year) | 9,575 | 3 | A | 10-13 | 2013-12-17 |
| 346 | Small Area Income and Poverty Estimates | 78,716 | 25 | A | 12 | 2015-03-10 |
| 362 | Quarterly Census of Employment and Wages | 3,142 | 1 | Q | 38-40 | 2017-03-07 |
| 397 | Gross Domestic Product by County (BEA) | 24,904 | 8 | A | 8 | 2018-12-12 |
| 399 | Net Migration Flows (ACS 5-year) | 3,143 | 1 | A | 8 | 2016-11-17 |
| 406-419 | ACS 5-year and other indicators (homeownership, disconnected youth, crime, single-parent households, burdened households, income inequality, commute time, poverty, preventable admissions, racial dissimilarity) | ~3,100 each | 1 each | A | 1-10 | 2017-01-04 |
| 409 | Equifax Credit Quality | 3,139 | 1 | Q | 30 | 2019-02-07 |
| 429 | County Population Estimates by Race and Ethnicity (ACS 5-year) | 65,971 | 21 | A | 8-10 | 2017-05-09 |
| 430-433 | Median age; premature death rates; patent assignments | ~3,100 each | 1 each | A, M | 1-10 | 2017-05-17 |
| 439 | Mean Household Wages Adjusted by Cost of Living | 812 | 1 | A | 7 | 2017-12-07 |
| 462 | Housing Inventory Core Metrics (Realtor.com) | 35,823 | 34 | M | 17-78 | 2020-03-05 |
| 463 | Market Hotness Index (Realtor.com) | 20,000 | 14 | M | 49-76 | 2020-04-02 |
| 485 | U.S. Granted Utility Patents by County | 3,019 | 1 | A | 1 | 2021-03-19 |

(Releases 113 and 308 are metropolitan releases that tag a handful of county-named series.)

### LAUS in detail

LAUS is the one program whose county series exist under two id forms with different archives,
the same trap the state employment series set (`docs/research-log/ws-b-employment-vintages.md`):

| id form | example | measures | series | vintages | first vintage |
| --- | --- | --- | ---: | --- | --- |
| alias, monthly | `TXHARR1URN`, `AKALEU0LFN` | unemployment rate, labor force | 3,158 / 3,153 | 229-264 | 2005-06-08 (KY), 2007-06 elsewhere |
| structured, monthly | `LAUCN482010000000005` | employed, unemployed | 3,140 each | 72-85 | 2019-08-28 |
| structured, annual | `LAUCN482010000000005A` | unemployment rate, unemployed, employed, labor force | 3,233 / 3,148 ×3 | 7-12 | 2017-08-30 (rate), 2019-08-28 |

There is **no alias form of employed persons**, and **no structured monthly form of the rate or the
labor force** in the release catalogue. So employed persons are real-time only from 2019-08-28 in
any form; the labor force and the rate reach back to 2007 only through the monthly aliases.

## What was acquired

[`fred_county_vintages`](../data/fred_county_vintages/README.md): ten families, 31,452 series, the
places panel's own features and targets where ALFRED has them. Census resident population; BEA per
capita personal income, personal income, GDP and real GDP; QCEW private establishments; and the four
LAUS annual averages. The dataset README carries the field contract, the county-code rules and the
measured coverage.

### Measured coverage

Raw artifact `2bbca474` (31,452 shards, 461,245,177 bytes downloaded, 513 MiB on disk), normalized
`8468fab9` (5,319,601 records, 136,909,829 bytes gzip). No configured series was refused: every one
is in ALFRED.

| family | series | county-equivalents | states+DC | vintages/series (min-median-max) | first vintage | refused |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| population | 3,139 | 3,139 | 51 | 8-22-25 | 2004-04-09 | 0 |
| per_capita_personal_income | 3,134 | 3,134 | 51 | 1-14-14 | 2014-05-30 | 0 |
| personal_income | 3,134 | 3,134 | 51 | 1-13-13 | 2014-05-30 | 0 |
| gdp | 3,113 | 3,113 | 51 | 7-8-8 | 2018-12-12 | 0 |
| real_gdp | 3,113 | 3,113 | 51 | 7-8-8 | 2018-12-12 | 0 |
| private_establishments | 3,142 | 3,142 | 51 | 1-39-40 | 2017-03-07 | 0 |
| laus_unemployment_rate | 3,233 | 3,233 | 51 | 2-12-12 | 2017-08-30 | 0 |
| laus_unemployed | 3,148 | 3,148 | 51 | 2-10-10 | 2019-08-28 | 0 |
| laus_employed | 3,148 | 3,148 | 51 | 2-10-10 | 2019-08-28 | 0 |
| laus_labor_force | 3,148 | 3,148 | 51 | 2-10-10 | 2019-08-28 | 0 |

County-equivalents with a **first release** (a value published after the series' archive opened;
all four quarters for establishments), by reference year:

| year | population | per_capita_personal_income | personal_income | gdp | real_gdp | private_establishments | laus_unemployment_rate | laus_unemployed | laus_employed | laus_labor_force |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2001 | 0 | 0 | 0 | 3,107 | 3,107 | 0 | 0 | 0 | 0 | 0 |
| 2002 | 0 | 0 | 0 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 |
| 2003 | 0 | 0 | 0 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 |
| 2004 | 330 | 0 | 0 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 |
| 2005 | 330 | 0 | 0 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 |
| 2006 | 330 | 0 | 0 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 |
| 2007 | 3,116 | 0 | 0 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 |
| 2008 | 3,139 | 0 | 0 | 3,110 | 3,110 | 0 | 0 | 0 | 0 | 0 |
| 2009 | 3,139 | 0 | 0 | 3,113 | 3,113 | 0 | 0 | 0 | 0 | 0 |
| 2010 | 3,139 | 0 | 0 | 3,113 | 3,113 | 0 | 0 | 0 | 0 | 0 |
| 2011 | 3,139 | 0 | 0 | 3,113 | 3,113 | 0 | 0 | 0 | 0 | 0 |
| 2012 | 3,139 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2013 | 3,139 | 3,082 | 3,082 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2014 | 3,138 | 3,081 | 3,081 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2015 | 3,137 | 3,108 | 3,108 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2016 | 3,136 | 3,108 | 3,108 | 3,113 | 3,113 | 0 | 0 | 0 | 0 | 0 |
| 2017 | 3,136 | 3,108 | 3,108 | 3,113 | 3,113 | 3,139 | 3,219 | 0 | 0 | 0 |
| 2018 | 3,136 | 3,108 | 3,108 | 3,113 | 3,113 | 3,139 | 3,219 | 0 | 0 | 0 |
| 2019 | 3,136 | 3,108 | 3,108 | 3,113 | 3,113 | 3,139 | 3,219 | 3,128 | 3,128 | 3,128 |
| 2020 | 3,135 | 3,107 | 3,107 | 3,112 | 3,112 | 3,138 | 3,141 | 3,137 | 3,137 | 3,137 |
| 2021 | 3,135 | 3,107 | 3,107 | 3,112 | 3,112 | 3,138 | 3,218 | 3,136 | 3,136 | 3,136 |
| 2022 | 3,127 | 3,107 | 3,107 | 3,112 | 3,112 | 3,138 | 3,218 | 3,136 | 3,136 | 3,136 |
| 2023 | 3,127 | 3,107 | 3,107 | 3,112 | 3,112 | 3,138 | 3,218 | 3,136 | 3,136 | 3,136 |
| 2024 | 3,127 | 3,098 | 3,099 | 3,104 | 3,103 | 3,130 | 3,210 | 3,128 | 3,128 | 3,128 |

The full per-family, per-year numbers, including how many counties have any value at all for a year,
are in `data/fred_county_vintages/coverage.json`.

## What does not exist, and what was left out

* **QCEW county employment and wages: not in FRED.** Release 362 has 7,714 series: 3,142 county
  private establishment counts, and 3,762 average weekly wage and 810 total wage series that are all
  metropolitan. `ENU4820110010` (Harris County total covered employment in the BLS id scheme) answers
  *"The series does not exist."* A vintaged county employment-by-place-of-work panel would have to be
  reconstructed from archived QCEW files (the Internet Archive route costed in WS-B, with crawl dates
  rather than publication dates).
* **Monthly LAUS: in ALFRED, not acquired.** About 2 GiB for the three monthly families, measured
  on sampled full-window payloads, against a 1 GiB envelope for this dataset. The annual averages the
  panel consumes were acquired instead. The monthly alias rate and labor force are the only LAUS
  source reaching back to 2007 and are the first follow-up.
* **Population for four places in recent years.** FRED's population ids carry no code, and GeoFRED
  does not list Broomfield County, CO, Prince of Wales-Hyder Census Area, AK, or the nine Connecticut
  planning regions, so those have no population series in the dataset.
* **Everything outside the places panel** (ACS 5-year indicators, SAIPE, permits, house prices,
  Equifax, Realtor.com) is archived but was not acquired; the table above is the map for whoever
  needs it. Realtor.com series also carry third-party terms.

## What this does and does not fix

With these vintages a places attempt can read population, personal income, GDP, establishments and
LAUS annual averages **as first published**. It cannot do so for QCEW employment, and not before each
family's first vintage: a first-release panel spanning every family starts no earlier than the
2019-08-28 LAUS archive (reference year 2019 at the earliest), and one limited to the three BEA and
Census families plus establishments starts at reference year 2017 (establishments) or 2013 (personal
income). Whether that is enough test years for the assay's criteria is for the attempt to measure.

---

# The monthly LAUS aliases, acquired

*Appended 2026-09-19. The section above stands as written; this one closes the follow-up it named —
"**Monthly LAUS: in ALFRED, not acquired**" — and corrects two of its measurements.*

The monthly LAUS county aliases are now
[`fred_county_laus_monthly_vintages`](../data/fred_county_laus_monthly_vintages/README.md): **6,282
series, 3,140 county-equivalents in each of two families, a median of 233 vintages per series, first
vintage 2005-06-08, 16,138,199 vintaged observations of which 1,459,534 are first releases**.
`wm verify` passes on stage `4b58e747` (raw artifact `6b5c8ae3`).

**A sibling dataset, not new families in `fred_county_vintages`.** The annual dataset is one raw
artifact whose declared `parameters.series_id` is the whole 31,452-series list, so adding families
would re-acquire it and rebuild its 5.3 M records for data its only consumer does not read; the byte
envelopes differ by an order of magnitude and the fair-share ledger books them per dataset; and
nothing joins the two but the county code. `alfred.py` is copied across unchanged apart from monthly
reference periods, so the record contract is identical and
`worldmodel/embedding/realtime_panel.py` reads either stage.

## Corrections to the survey above

1. **There *is* a structured monthly form of the rate and the labor force** — `LAUCN…0003` and
   `LAUCN…0006` — but only for **17 county-equivalents** (the nine Connecticut planning regions, six
   Alaska areas and Oglala Lakota County), plus 37 CSAs that are not counties. Their archives hold
   **12 vintages from 2025-08-27**, so they add geography but no history, and they are not acquired.
   The claim that no structured monthly form exists was read off a catalogue in which these ids were
   scarce; the counts here are from release 116 on 2026-09-19.
2. **County labor force changes units mid-archive.** FRED published it as *Thousands of Persons*
   through the 2016-03-17 vintage and as *Persons* from 2016-03-18, and the observations changed with
   the label: `CTFAIR1LFN` for 2015-06 is `489.537` in the 2016-03-01 vintage and `489537` in the
   2016-03-18 one. Anything reading these series by their current units understates every pre-2016
   level by 1000. Seven series deviate from even that history and carry their own in `config.json`.

## Measured coverage

| family | series | county-equivalents | states+DC | vintages/series (min-median-max) | first vintage | latest vintage | refused |
| --- | ---: | ---: | ---: | --- | --- | --- | ---: |
| laus_monthly_unemployment_rate | 3,141 | 3,140 | 51 | 42-233-264 | 2005-06-08 | 2026-09-02 | 0 |
| laus_monthly_labor_force | 3,141 | 3,140 | 51 | 43-233-264 | 2005-06-08 | 2026-09-02 | 0 |

No configured series was refused: every alias is in ALFRED. There are no Puerto Rico aliases, so
coverage is the 50 states and DC; the 78 municipios remain annual-only. 3,141 series carry 3,140
codes because FRED publishes two aliases for Hancock County, KY, both kept rather than merged. The
18 series dropped per family are the release's Federal Reserve district aggregates, which are not
counties.

### County-equivalents with a first release, by reference month

This is the number that decides how far back a real-time monthly panel can start. The archive opens
in three steps.

| reference months | rate | labor force | published on | what it is |
| --- | ---: | ---: | --- | --- |
| 1976-01 .. 1989-12 | 1 | 0 | **2016-03-18** | `DCDIST5URN` (District of Columbia). Not early real-time data: FRED extended that one series back to 1976 *in 2016*, so these are first releases of forty-year-old months |
| 2005-04 .. 2006-06 | 339 | 339 | from **2005-07-06** | the **Federal Reserve Eighth District** — every county of AR (75), IL (44), IN (24), KY (64), MS (39), MO (72), TN (21) — under FRED's short aliases. FRED is the St. Louis Fed and archived its own district two years before the rest of the country |
| 2006-07 .. 2007-04 | 346 | 346 | from 2006-09-07 | seven more join |
| **2007-05** | **3,137** | **3,138** | **2007-07-05** | the national cross-section arrives |
| 2007-06 .. 2008-02 | 3,138 | 3,138 | 2007-08-08 on | |
| 2008-03 onwards | 3,140 | 3,140 | 2008-05-07 on | every county-equivalent in the dataset |

Then it holds: 3,134-3,140 county-equivalents in every month of every year to 2024, 3,133 from 2022,
3,125 in 2025-2026, losing only counties FRED discontinues. All twelve months of every year from 2006
to 2024 have a first release. The opening snapshot reaches back to 1990-01 in both families; those
rows carry `realtime_start_clipped = true` and are never releases. A reference month is published on
the **fifth to eighth day of the second following month**, which is a measured publication lag rather
than a declared one.

Per-month numbers for all 423 months, with counties-holding-any-value beside them, are in
`data/fred_county_laus_monthly_vintages/coverage.json`.

## What this unblocks

* **A real-time county panel can take its first origin on 2005-07-06** for the 339 Eighth District
  counties and **on 2007-07-05** for the whole country (3,131 of 3,140 that day, all 3,140 by
  2008-05-07). The annual county panel's earliest all-family origin is the 2019-08-28 LAUS archive.
* **A monthly county outcome can be scored from reference month 2005-04** for those 339 counties and
  from **2007-05** nationally — 148 monthly observations per county before reference year 2019, the
  first year `county_realtime_panel` can score LAUS at all, and enough history to cover the 2008-09
  recession as it was published.
* It does **not** reach QCEW county employment or wages, which FRED does not carry at county level in
  any form, or monthly employed and unemployed persons, which exist at county level only in the
  structured ids from 2019-08-28. Those walls stand exactly where the survey above put them.

## What a consumer still needs

`worldmodel/embedding/realtime_panel.py:first_releases` indexes a value by the **calendar year** of
its `valid_from`, so pointing it at this dataset unchanged would collapse twelve months onto one year
and keep whichever was published first. A monthly panel needs a month key there. That file is not
this track's to change, and the change is reported rather than made.
