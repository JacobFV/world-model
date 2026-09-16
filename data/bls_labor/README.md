# bls_labor

U.S. Bureau of Labor Statistics labor-market statistics, acquired as full flat files.

## Source and scope

One `files` acquisition (one shard per file, [dataset.json](dataset.json)):

| Program | Files | Content |
| --- | --- | --- |
| CES State and Metro (SM) | `sm.data.0.Current` + `sm.series/industry/area/state/data_type/supersector/footnote` | State and metro employment, hours, earnings (current window, 2006+) |
| LAUS | `la.data.64.County`, `la.data.1.CurrentS` + `la.area/measure/area_type/footnote` | County (NSA) and seasonally adjusted state/area labor force, employment, unemployment, rate |
| JOLTS | `jt.data.1.AllItems` + mapping files | Openings, hires, separations, quits, layoffs (levels and rates) by industry, state, size class |
| CES national | `ce.data.01a.CurrentSeasAE`, `ce.data.02b.AllRealEarningsAE` + `ce.series/industry/datatype/...` | National employment and real earnings |
| CPS | `ln.data.1.AllData` | National labor-force headline series (21 listed series only, 1948+) |
| OEWS | `oesm25all.zip` (May 2025 all data), `oesm15st..oesm24st.zip`, `oesm15ma..oesm24ma.zip` | Employment and wages by area x industry x occupation (2025); state, MSA and nonmetropolitan-area occupational wages May 2015-2024 |
| QCEW | `1990..2025_annual_singlefile.zip`, `2023..2025_qtrly_singlefile.zip` | Annual and quarterly establishments, employment (monthly within quarters), wages by area x ownership x industry |

`ce.data.0.AllCESSeries` (351 MB) and `sm.data.1.AllData` (544 MB) are deliberately excluded
for budget; SM history before 2006 is therefore not included.

## Normalized evidence (`normalized`, gzip JSONL)

* Subjects are geographies: `geo:US`, `geo:US:state:SS`, `geo:US:county:SSCCC`,
  `geo:US:msa:CBSA` (SM uses BLS CBSA/NECTA codes; SM metro values are the state portion,
  see `dimensions.state_fips`/`geography_part`), `geo:US:csa:...`, `bls:laus_area:<code>`,
  `bls:oews_area:<code>` (OEWS nonmetropolitan areas).
* Industries/occupations in `dimensions`: `bls:ces_industry:<8-digit>`, `bls:jolts_industry:<6>`,
  QCEW `naics2022:<code>` (2022+), `naics2017:` (2017-2021), `naics2012:` (2012-2016), `naics2007:` (2007-2011),
  `naics2002:` (1990-2006; BLS publishes 1990-2001 on the NAICS 2002 basis) or
  `bls:qcew_industry:<code>` for totals, domains and supersectors, OEWS `bls:oews_industry:<6-char padded code>`, occupations `soc2018:<code>` (OEWS 2019+) or
  `soc2010:<code>` (OEWS 2015-2018).
* Metrics (all with explicit units, thousands scaled to persons/USD): `employment`,
  `average_weekly_hours`, `average_hourly_earnings`, `average_weekly_earnings`,
  `real_average_hourly_earnings`, production/nonsupervisory variants, diffusion indexes;
  `unemployment_rate`, `unemployed`, `labor_force`, `employment_population_ratio`,
  `labor_force_participation_rate`, `civilian_noninstitutional_population`; `job_openings`,
  `hires`, `total_separations`, `quits`, `layoffs_discharges`, `other_separations` (+ `_rate`),
  `unemployed_per_job_opening`; `establishment_count`, `total_annual_wages`,
  `average_weekly_wage`, `average_annual_pay`, quarterly `total_quarterly_wages`; CPS
  `unemployment_rate_u6`, `long_term_unemployed_27_weeks_plus`, `unemployed_job_losers`,
  `part_time_for_economic_reasons` (plus demographic variants of the rate/ratio metrics in
  `dimensions.age/sex/race/ethnicity/education`); `nonfarm_payroll_employment` (CES0000000001 only); OEWS `mean_annual_wage`, `median_annual_wage`,
  `mean_hourly_wage`, `median_hourly_wage`.
* `valid_from`/`valid_to` are half-open periods; M13/annual averages cover the year with
  `dimensions.period_type = annual_average`. OEWS May estimates use May of the reference year
  (3-year pooled panel noted in attributes).
* Footnotes stay in `attributes.footnote_codes` (`preliminary: true` for `P`). Missing, `-`,
  suppressed QCEW (`disclosure_code = N`) and OEWS special codes (`*`, `**`, `#` top-coded,
  `~`) become `value: null` with a `missing_reason`.
* OEWS state/MSA workbooks before 2019 have no `AREA_TYPE`, `NAICS` or `OWN_CODE` columns and use
  `OCC_GROUP`; the area type is inferred from the workbook (state, MSA, BOS = nonmetropolitan) and
  the industry is `cross_industry`. The alternate-definition `aMSA_*` workbooks (2015-2017) are
  skipped. `attributes.oews_file` records `all_data`, `state`, `msa` or `bos`.
* OEWS publishes some NAICS codes at more than one industry level (e.g. `327000` as both
  `3-digit` and `4-digit`, identical values) and some SOC codes as both `broad` and `detailed`.
  Each published row is kept; filter on `dimensions.industry_group` and
  `dimensions.occupation_group` before summing.
* Every observation has `dimensions.survey` (`CES-SM`, `LAUS`, `JOLTS`, `CES`, `CPS`, `QCEW`,
  `OEWS` in attributes) and `dimensions.geography` equal to its geographic subject. BLS series
  observations carry `attributes.source_series` (= `series_id`). QCEW observations keep the raw
  `area_fips`, `industry_code`, `own_code` and `source_field` (e.g. `annual_avg_emplvl`,
  `month2_emplvl`) in attributes; the year is `valid_from`.
* QCEW is restricted by `parameters.qcew_agglvl_codes` (national to 4-digit, MSA total/
  ownership/sector, state to 3-digit, county total/ownership/sector) to keep output bounded.

### Vintage and release time

BLS flat files carry only the current vintage. Every observation records
`attributes.vintage = "current_at_retrieval"`, `attributes.realtime_start` (retrieval date) and
`attributes.realtime_end = null`; BLS series observations also carry `attributes.series_id`
(kept in `dimensions.series_id`). Revision-aware payrolls and unemployment rate histories come
from `fred_macro_panel` (`PAYEMS`, `UNRATE` with ALFRED `realtime_start`/`realtime_end`).

### Units across time

Units are taken from the publisher's own definitions per series, not assumed: CES/SM data-type
codes ("in thousands", "in dollars"), CPI base periods for real-earnings metrics
(`USD_1982_1984_per_hour`), and index bases (`index_2007_100`, `index_2002_100`). These are stable
within the files used here, but three things do change over time and are recorded per record
rather than globally: the NAICS vintage in QCEW industry ids (`naics2002` ... `naics2022`), the SOC
vintage in OEWS occupation ids (`soc2010` for 2015-2018, `soc2018` for 2019+), and OEWS top-coding
thresholds (`#` becomes `value: null` with `missing_reason:
top_coded_at_or_above_published_maximum`, whose dollar threshold differs by year). CES national
series are published in thousands and scaled to persons here, except `CES0000000001`, which keeps
publisher scale (`thousand_persons`) for the estimation contract; `dimensions.survey` separates
CES, CPS and LAUS employment concepts. Because flat files carry only the current vintage, values
are as-revised at `attributes.realtime_start`; use `fred_macro_panel` for revision-aware history.

### Estimation-layer series

`worldmodel/estimation/requirements.json` selects observations by exact metric and unit, in
publisher units:

| BLS series | Metric | Unit | Subject | Frequency | Source file |
| --- | --- | --- | --- | --- | --- |
| `CES0000000001` total nonfarm payrolls, SA | `nonfarm_payroll_employment` | `thousand_persons` (not scaled) | `geo:US` | monthly | `ce.data.01a.CurrentSeasAE` (1939+) |
| `LNS14000000` CPS unemployment rate, SA | `unemployment_rate` | `percent` | `geo:US` | monthly | `ln.data.1.AllData` (1948+) |
| `LASST{SS}0000000000003` | `unemployment_rate` | `percent` | `geo:US:state:SS` (+ `dimensions.geography`) | monthly SA | `la.data.1.CurrentS` |
| `LASST{SS}0000000000005` (household employment) | `employment` | `persons` | `geo:US:state:SS` | monthly SA | `la.data.1.CurrentS` |

Other CES employment series remain `employment` in persons (thousands applied). CPS employment
level (`LNS12000000`) is also `employment` in persons; `dimensions.survey` separates it from
CES and LAUS employment.

The legacy one-series API sample (LNS14000000) is still normalized when `raw-latest` points at
a sample artifact.

## Built output (2026-09-16)

| | Value |
| --- | --- |
| raw artifact | `d47260935780…` , 90 files, 4,787,690,696 bytes, complete |
| normalized version | `ffd7f43a2761…` , 60,872,022 rows, 3,498,722,448 gzip bytes (0.73x raw) |
| verification | full re-hash of stage output and raw shards passes (`wm verify bls_labor`) |

Observations by survey: QCEW 32,739,958 (annual 1990-2025; quarterly plus monthly-within-quarter
2023-2025), OEWS 13,842,550 (2025 all-data; state/MSA/nonmetro 2015-2024), CES-SM 6,013,244,
LAUS 6,364,334, CES 960,549, JOLTS 626,683, CPS 17,317. 5,904,445 QCEW values are null with
`disclosure_code: N`.

An earlier interim version `50917b81afef…` (18,183,421 rows) was built from the superseded
33-file raw artifact `17e1b8297f88…` so that downstream calibration could start before the 4.8 GB
download finished. Both are retained: `calibration_reports` and `world_evidence` versions still
cite them, so the superseded-artifact deletion rule does not permit removing either yet.
`acquisition.desired_bytes` stays at 5.9 GB while both raw artifacts exist and should drop to
about 5.0 GB once the interim version and old artifact are retired.

## Licence and access

Public domain (U.S. federal government work). download.bls.gov, www.bls.gov and data.bls.gov
block requests without a descriptive User-Agent that includes contact information: set
`BLS_USER_AGENT` in `.env` (see `.env.example`). Requests are spaced at 0.5/s.

## Rebuild

```sh
wm acquire bls_labor --dry-run
wm acquire bls_labor --allow-network        # ~1.06 GB
WORLD_MODEL_RAW_VERIFY=size wm run bls_labor
wm verify bls_labor
python3 -m unittest data/bls_labor/tests/test_bls_labor.py
```

`artifacts/` and `scratch/` are ignored by Git.
