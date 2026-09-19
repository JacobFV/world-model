# fred_county_vintages

County-level FRED series with **every ALFRED real-time vintage**: ten families, 31,452 series,
3,100-3,233 county-equivalents each. The county counterpart of
[`fred_state_employment_vintages`](../fred_state_employment_vintages/README.md), built the same way.

## Why it exists

The places assay of the world-state encoder forecasts next-year county employment, establishments
and population, and is declared to fail `no_revision_leakage`: its county panel holds only
current-vintage values, and QCEW, LAUS, BEA and the Census estimates all revise. ALFRED archives
county series with their vintages, so a first-release county panel can be built. This dataset
publishes those vintages; it does not build the panel.

## What it publishes

One `observation` per (series, reference period, ALFRED real-time period):

| field | meaning |
| --- | --- |
| `subject` | `geo:US:county:<SSCCC>`, the code GeoFRED publishes for the series (see *County codes*) |
| `metric` / `unit` | per family, below; `unit` is the unit **published in that vintage** |
| `valid_from`, `valid_to` | the reference period, half-open (a year, or a quarter for establishments) |
| `observed_at` | the period's `realtime_start`: the day this value became public |
| `dimensions.vintage` | the period's ALFRED `realtime_start`, the publication date |
| `attributes.realtime_end` | closes the period, so interval lookup recovers the value current on any date |
| `dimensions.first_release` | `true` on the earliest period of a reference period, when that period starts after the series' first ALFRED vintage |
| `attributes.realtime_start_clipped` | `true` on rows in the series' first ALFRED vintage: the archive's opening snapshot of already-revised history, never a first release |
| `attributes.series_first_vintage` | that first vintage |
| `dimensions.family`, `dimensions.program` | which of the ten families, and the originating program |
| `attributes.fips_source` | `geofred+series_id` (GeoFRED and the publisher's id agree), `geofred`, or `series_id` |
| `attributes.code_scheme` | `fips`, or `bea_combination_area` for BEA's county codes 900-999 (below) |

Missing values (`.` in FRED) are kept as `value: null` with `missing_reason: not_available_in_vintage`.
The records also carry a `county` entity per code (`within` its state, `within` `geo:US`) and an
`economic_series` entity per series (`describes_location` its county).

| family | FRED release | metric | unit | frequency | series | first vintage | vintages (sampled) |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| `population` | 119 Annual Estimates of the Population for Counties (Census) | `population` | persons (published thousands × 1000) | annual | 3,139 | 2004-04-09 | 18-25 |
| `per_capita_personal_income` | 175 Personal Income by County (BEA) | `per_capita_personal_income` | USD | annual | 3,134 | 2014-05-30 | 1-14 |
| `personal_income` | 175 Personal Income by County (BEA) | `personal_income` | USD (published thousands × 1000) | annual | 3,134 | 2014-05-30 | 1-13 |
| `gdp` | 397 Gross Domestic Product by County (BEA) | `gdp` | USD (thousands × 1000) | annual | 3,113 | 2018-12-12 | 8 |
| `real_gdp` | 397 Gross Domestic Product by County (BEA) | `real_gdp` | USD_chained_2012 until 2023-12-06, USD_chained_2017 after | annual | 3,113 | 2018-12-12 | 8 |
| `private_establishments` | 362 Quarterly Census of Employment and Wages (BLS) | `establishment_count` | establishments | quarterly | 3,142 | 2017-03-07 | 31-40 |
| `laus_unemployment_rate` | 116 Unemployment in States and Local Areas (BLS LAUS) | `unemployment_rate` | percent | annual average | 3,233 | 2017-08-30 | 9-12 |
| `laus_unemployed` | 116 (BLS LAUS) | `unemployed` | persons | annual average | 3,148 | 2019-08-28 | 7-10 |
| `laus_employed` | 116 (BLS LAUS) | `employment` | persons (residents employed, not jobs) | annual average | 3,148 | 2019-08-28 | 7-10 |
| `laus_labor_force` | 116 (BLS LAUS) | `labor_force` | persons | annual average | 3,148 | 2019-08-28 | 7-10 |

"First vintage" and "vintages" are `build_config.py`'s probe of one series per state per family;
the measured per-series numbers are in `coverage.json` and the tables below. Metric names match
`bls_labor` and `bea_national_regional`, so a consumer can swap a revised feature for its real-time
counterpart without renaming. Note that LAUS `employment` counts **employed residents** and QCEW
employment (not available here, see below) counts **jobs by place of work**.

<!-- COVERAGE -->

## County codes

`build_config.py` never reads a code out of a title. GeoFRED's `regional/data` cross-sections,
requested for every reference period of each family's series group, return
`{series_id, code, region}`; that is the code. Where the publisher's own id also carries the area
code (BLS LAUS `LAUCN<fips>…`, BEA `PCPI<fips>`/`PI<fips>`/`GDPALL<fips>`/`REALGDPALL<fips>`, BLS QCEW
`ENU<fips>20510`), the two must agree:

* **They agree** for every series both describe, with two exceptions that GeoFRED's map layer
  explains: it still publishes the pre-2015 codes **46113** (Shannon County, SD) and **02270** (Wade
  Hampton Census Area, AK) for the renamed Oglala Lakota County (46102) and Kusilvak Census Area
  (02158). `CODE_CHANGES` in `build_config.py` resolves exactly those two, citing the Census county
  changes list, in favour of the current code; any other disagreement would have excluded the
  series. GeoFRED-only population series under the old codes (`AKWADE0POP`, `SDSHAN3POP`) are
  relabelled to the successor codes so a place has one subject across families.
* **GeoFRED is silent** for the 2022 Connecticut planning regions (09110-09190), Chugach and Copper
  River (02063, 02066), BEA's combination areas and old Alaska areas, and the 78 Puerto Rico
  municipios in the LAUS unemployment rate. Those keep the id's code (`fips_source = 'series_id'`),
  and `config.json` lists every such code GeoFRED does not confirm in any other family.
* **GeoFRED lists the annual LAUS unemployment rate and labor force under the monthly alias ids**
  (`TXHARR1URN`, `…LFN`), not the `LAUCN…A` ids, so those two families' codes come from the BLS area
  code; 3,135 of 3,227 and 3,131 of 3,142 of them are confirmed by GeoFRED for the same area through
  the unemployed/employed families.
* **Population ids carry no code** (`TXHARR1POP`), so a population series GeoFRED omits is skipped:
  31 of the release's catalogue, of which 18 are Federal Reserve district aggregates, 9 are the
  Connecticut planning regions, 2 are discontinued Virginia cities, and 2 are **Broomfield County, CO
  (08014) and Prince of Wales-Hyder Census Area, AK (02198)**, which therefore have no population
  series here.

**BEA combination areas.** BEA folds most Virginia independent cities into their surrounding county
and Kalawao into Maui, and publishes them under codes 900-999 (e.g. `51901` Albemarle +
Charlottesville, `15901` Maui + Kalawao). They are not Census county FIPS codes, and their records say
so (`code_scheme = 'bea_combination_area'`). A consumer joining BEA to LAUS or Census must map them.

## What is not here, and why

**QCEW county employment and wages do not exist in FRED.** FRED's QCEW release (362, 7,714 series)
carries county **private establishment counts** only; its employment and wage series are MSA-level
(3,762 average weekly wage and 810 total wage series, all metropolitan areas), and the county
employment id (`ENU4820110010`) answers *"The series does not exist."* So the places assay's
`qcew:employment` target has **no vintaged county source** in FRED/ALFRED. Establishments do
(`private_establishments`, 40 vintages from 2017-03-07).

**Monthly LAUS county series are archived but not acquired.** ALFRED holds them: the alias
unemployment rate and labor force (`TXHARR1URN`, `AKALEU0LFN`: 3,158 and 3,153 series, 229-264
vintages from 2005-06-08/2007-06), and the structured employed and unemployed (`LAUCN…05`,
`LAUCN…04`: 3,140 each, 72-85 vintages from 2019-08-28). Sampled full-window payloads measure
about 560 MiB, 950 MiB and 500 MiB for the unemployment rate, labor force and employed families,
about 2 GiB together, against this dataset's 1 GiB envelope. The places panel consumes LAUS
**annual averages**, which are the families acquired here. The monthly alias families are the only
LAUS source whose vintages reach back before 2017 (to 2007), so they are the first follow-up if a
real-time LAUS feature is needed earlier than 2018.

**Everything else county-level in ALFRED.** FRED lists 330,958 series tagged `county` across 30
releases, and every family probed has vintages (one series per state per family). Most are ACS
5-year estimates, SAIPE, building permits, FHFA house prices, Equifax subprime shares and
Realtor.com housing inventory; none is a places-assay target, so none is acquired. The whole survey
is in [`docs/county-vintages.md`](../../docs/county-vintages.md).

**Units as FRED publishes them.** `REALGDPALL35013` (Doña Ana County, NM) is published in FRED's
metadata as *"Thousands of U.S. Dollars"* in every vintage rather than chained dollars; it is
emitted in the unit FRED gives (`USD`), listed in `config.json` as the family's one units exception,
and should not be compared with the other counties' chained-dollar levels.

## Rebuild

```sh
FRED_API_KEY=... python3 data/fred_county_vintages/build_config.py [--cache DIR]   # discover + map codes
python3 data/fred_county_vintages/build_config.py --emit-parameters                # dataset.json series list
python3 -m worldmodel acquire fred_county_vintages --allow-network                  # 31,452 requests, ~6 h at 1.5/s
python3 -m worldmodel run fred_county_vintages
python3 -m worldmodel verify fred_county_vintages/normalized
python3 data/fred_county_vintages/measure_coverage.py --markdown                   # coverage.json
```

ALFRED membership is not probed per series in `build_config.py`: every request asks for the whole
real-time window, and FRED answers `400 "does not exist in ALFRED"` for a series it does not archive,
which the declaration records as skipped (`skip_statuses: [400]`) and `coverage.json` counts.

## Rights

FRED API Terms of Use; the underlying series are US federal statistics in the public domain. Cite
FRED/ALFRED, the Census Bureau, BEA and BLS. No third-party copyright series are included.
