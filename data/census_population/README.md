# census_population

U.S. Census Bureau **Population Estimates Program** (PEP): every national/state vintage the Census server still
serves in the machine-readable ALLDATA layout, the county vintages 2020-2024, and the 2000-2010 national
intercensal series.

## Source and licence

Files under <https://www2.census.gov/programs-surveys/popest/datasets/> (28 files, ~105 MiB).
Public domain (U.S. federal work). No credential.

| Group | Files | Coverage |
| --- | --- | --- |
| National/state totals, one per vintage | `nst_est2004_alldata.csv`, `nst_est2005_alldata.csv`, `nst_est2006_alldata.csv`, `nst-est2007-alldata.csv`, `nst_est2011_alldata.csv` … `nst-est2019-alldata.csv`, `nst-est2020-alldata.csv`, `NST-EST2021-alldata.csv` … `NST-EST2025-ALLDATA.csv` | vintages 2004-2007 (2000 base), 2011-2020 (2010 base), 2021-2025 (2020 base) |
| National intercensal | `us-est00int-alldata.csv` | 2000-2010, revised after the 2010 census |
| County totals | `co-est2020-alldata.csv` … `co-est2024-alldata.csv` | vintages 2020-2024 |
| County characteristics | `cc-est2024-alldata.csv` | age/sex/race/Hispanic origin 2020-2024 |
| Metro and sub-county | `cbsa-est2024-alldata.csv`, `sub-est2024.csv` | vintage 2024 |

Vintages 2008-2010 and the 2000s state files are **not** available in this layout: the Census server serves only
per-year `nst-est200X-popchgYYYY.csv` presentation tables for those, which is why the 2000s coverage comes from
V2004-V2007 plus the intercensal series.

`desired_bytes` (220 MB) covers two raw artifacts: the current 28-file one and the superseded 13-file one, which is
retained because the published `population_growth_rate.census_pep` calibration report pins the normalized output
(`d621c962…`) built from it.

## Vintage availability is an upper bound

`attributes.released_at` is the HTTP `Last-Modified` of the published file. For every file older than the 2016
census.gov migration this is a **migration timestamp** (2016-07-19 or later), not the original December release
date, so vintages 2004-2015 all appear to become knowable in mid-2016:

| Vintage | `released_at` | Vintage | `released_at` |
| --- | --- | --- | --- |
| 2004, 2005, 2006, 2007, 2011, 2013, 2014 | 2016-07-19 | 2019 | 2019-12-30 |
| 2000-2010 intercensal | 2016-08-24 | 2020 | 2021-05-04 |
| 2012 | 2016-08-25 | 2021 | 2021-12-21 |
| 2015 | 2016-09-01 | 2022 | 2022-12-22 |
| 2016 | 2016-12-20 | 2023 | 2023-12-19 |
| 2017 | 2017-12-20 | 2024 | 2024-12-19 |
| 2018 | 2018-12-19 | 2025 | 2026-01-27 |

An upper bound delays availability and therefore cannot leak, but it does cost real-time origins: under a strict
vintage policy the earliest annual origin with eight observations is 2016, not 2012.

## Rebuild

```sh
wm acquire census_population --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run census_population && wm verify census_population
python3 -m unittest discover -s data/census_population/tests -t data/census_population/tests
```

The pipeline branches on `raw_coverage()['sampled']`: legacy 100-row samples keep the original adapter
(tests/test_normalizer_contracts.py); full files stream through `_run_full`.

Field names and identifier widths drift across vintages — `Sumlev` in V2012, lowercase `births2000` in V2006,
`SUMLEV` `10` rather than `010` and `STATE` `0` rather than `00` in V2011, `INTERNALMIG` rather than `DOMESTICMIG`
in V2004/V2005 — so `_normalize` uppercases every field name and zero-pads `SUMLEV`, `STATE`, `COUNTY`, `PLACE` and
`COUSUB`. Both are no-ops on every file published since 2016. The census base year comes from each row's
`ESTIMATESBASE<year>` field rather than from the vintage, which is what makes the first estimate year of a
2000-based or 2010-based vintage start at its own April-1 base.

## Evidence (full mode)

- Entities: `geo:US`, `geo:US:region:<n>`, `geo:US:division:<n>`, `geo:US:state:<FIPS>`, `geo:US:county:<GEOID5>`,
  `geo:US:cbsa:<code>`, `geo:US:metdiv:<code>`, `geo:US:place:<GEOID7>`, `geo:US:cousub:<GEOID10>`; `within` hierarchy and
  county -> CBSA/metropolitan division delineation assertions.
- Observations (`dimensions.vintage` is the PEP vintage year, or `2000_2010_intercensal`): `population` (1 July;
  census and estimates base on 1 April with `dimensions.basis`), `group_quarters_population`, `births`, `deaths`,
  `natural_change`, `net_international_migration`, `net_domestic_migration`, `net_migration`, `population_change`,
  `population_change_residual` (people, 1 July Y-1 to 1 July Y; first year from the 1 April base) and the matching
  `*_rate` metrics (per 1000). County characteristics: `population` by `sex`, `race` (alone), `hispanic_origin` for
  all ages and by 5-year `age_group` x `sex` (2020-2024).
- National annual `population` for `geo:US` now runs **2000-2025 (26 years) across 20 vintages**, which is what makes
  `population_growth_rate` evaluable on this source. Only the intercensal file's all-ages totals are emitted: a
  national `population` row carrying an `age_group` or `sex` dimension would collide with the annual national series
  that the estimation loader selects on metric and subject alone.
- Overlapping vintages are all kept; a point-in-time reader takes the latest vintage available at its cutoff, which
  is how the 2010 and 2020 rebasings enter a real-time series.
