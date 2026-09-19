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
