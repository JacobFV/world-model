# Macro, labor, input-output and international datasets

Cross-dataset notes for `fred_*`, `bea_*`, `bls_*`, `lehd_lodes`, `treasury_debt`, `worldbank_wdi`, `imf_sdmx`,
`oecd_sdmx`, `bis_bulk`, `ecb_eurostat` and `exiobase3`. Each dataset README documents its own source, scope, licence
and rebuild commands; this page records the conventions they share and how they overlap.

## Shared conventions

- **Geography.** US: `geo:US`, `geo:US:state:<2-digit FIPS>`, `geo:US:county:<5-digit FIPS>`, `geo:US:msa:<CBSA>`.
  Countries: `iso3:<ISO3>` (source codes such as IMF/BIS/Eurostat 2-letter codes stay in `attributes`). Non-country
  aggregates (euro area, World, income groups) are `agg:<source>:<code>` aggregate cohorts with
  `attributes.aggregate = true`.
- **Units.** Scale multipliers are applied to `value` (millions of dollars become `USD`); the published unit and the
  multiplier stay in `attributes.source_units` / `attributes.unit_multiplier`. Common unit tokens: `USD`,
  `USD_chained_2017`, `persons`, `percent`, `index_<base>_100`, `<CUR>_per_USD`.
- **Time.** `valid_from`/`valid_to` are the half-open observation period; `dimensions.frequency` is `D`, `W`, `M`,
  `Q`, `SA` or `A`.
- **Missing values** are `null` with `missing_reason` (for example `suppressed`, `not_available_in_vintage`).

## FRED / ALFRED (`fred_macro_panel` and the `fred_*` anchors)

- **Subjects are geographies** (`geo:US`, `geo:US:state:06`); `dimensions.series_id` names the FRED series. The
  series itself is an `economic_series` entity `fred:<SERIES>` with title, source units, multiplier and frequency,
  linked by the assertion `fred:<SERIES> describes_location <geo>`.
- **Real-time vintages.** Every API-sourced observation carries `dimensions.vintage` (= ALFRED `realtime_start`) and
  `attributes.realtime_start` / `realtime_end`. `observed_at` is `realtime_start`, so filtering on
  `observed_at <= t` and `realtime_end >= t` reproduces what was published on day `t`.
  - Tier `vintages` (all ALFRED vintages): the anchor datasets (DFF, DGS2, DGS10, T10YIE, DCOILWTICO) and, since the
    100 GiB budget update, 838 of the 849 `fred_macro_panel` series (national and state).
  - Tier `as_of`: values in effect on `config.as_of` (2026-09-01) plus later revisions; a
    `realtime_start` equal to `as_of` is clipped (`attributes.realtime_start_clipped = true`) and is **not** a
    first-publication date. It is used for the 11 licensed third-party series only (listed below).
  - History from before a series entered ALFRED carries the series' first ALFRED vintage date as `realtime_start`.
- **Published 2026-09-15** (`fred_macro_panel` normalized version `7dcce89c…`): 7,567,876 records —
  849 series, 7,566,075 observations (4,000,056 national, 3,566,019 state), 901 entities and 900 assertions;
  213,047,980 bytes gzip from 800 MB raw; every observation has real-time fields. This build supersedes
  `b395bda0…` (same raw artifact `623e4cc0…`, 209,766,796 bytes). Weekly financial-conditions indexes dominate
  row counts (ANFCI 1.03M, NFCI 0.92M, STLFSI4 0.31M) because each vintage revises their whole history.
- **Request windows.** FRED rejects JSON requests spanning more than 2,000 vintage dates, so real-time history is split
  into windows of at most 1,900 vintage dates, and a window with more than 100,000 observations (FRED's response
  cap) is split further into `offset` slices. The pipeline re-merges real-time periods cut at either boundary, and
  rejects a response that stopped short of its own `count` so truncation cannot pass silently.
  Daily anchors' last windows fill up at about 250 vintage dates a year; split them again before they reach 2,000.
- **Per-vintage units.** ALFRED observations carry no units and FRED rebases chained-dollar and index series at
  benchmark revisions, so each vintage is denominated in the base period current at that vintage (and some series
  change scale: TOTALSL went from billions to millions). Every FRED observation therefore carries
  `attributes.source_units` (as published in that vintage), `attributes.unit_multiplier` (actually applied),
  `attributes.base_period` / `dimensions.base_period`, and `attributes.units_change_across_vintages`
  (189 of 849 panel series). The `unit` token is per vintage: only a vintage on the requirement's base keeps the
  requirement name (`billion_chained_2017_USD`); rebased vintages get their own (`billion_USD_chained_2012`,
  `index_1992_100`). **Compare levels only within one `base_period`**, or use growth rates and gaps.
  Affected required series: CPIAUCSL, GDPC1, GDPPOT, INDPRO, PCEPILFE, TOTALSL.
- **Metric names are shared** between anchors and panel (`policy_rate`, `treasury_yield_10y`,
  `breakeven_inflation_10y`, `oil_price` in `USD/barrel`, `consumer_price_index` in `index_1982_1984_100`, ...), so
  the same series can be read from either dataset.
- **fred_cpi** moved to the FRED API on 2026-09-15 because `fredgraph.csv` timed out on every scripted request. It
  now carries all ALFRED vintages of CPIAUCSL, CPILFESL and PCEPI, the same as `fred_macro_panel`.
- **Licensed third-party series** (Moody's AAA/BAA yields and spreads, Freddie Mac mortgage rates, CBOE VIX, NASDAQ,
  S&P/Case-Shiller, University of Michigan sentiment and expectations) are the 11 series requested in the `as_of`
  tier only (DAAA, DBAA, AAA10Y, BAA10Y, MORTGAGE30US, MORTGAGE15US, VIXCLS, NASDAQCOM, CSUSHPISA, UMCSENT, MICH).
  FRED does not serve full ALFRED history for them, and SP500 is not in ALFRED at all, so it is dropped.
  `skip_statuses: [400]` records any other refused request as skipped instead of failing the run.
- **Third-party copyright.** Panel series whose FRED notes name a copyright holder (Moody's, S&P, CBOE, University of
  Michigan, Freddie Mac, NASDAQ, ...) are flagged in `config.json` and on each observation
  (`attributes.third_party_copyright`). Use them internally only.
- **Overlaps.** The Treasury par yield curve comes from the panel (DGS1MO to DGS30), not from `treasury_debt`.
  Metro-area unemployment and payrolls come from `bls_labor` (LAUS, CES state and metro) rather than FRED.

## BEA (`bea_input_output`, `bea_national_regional`) and `treasury_debt`

- **IO identifiers** are `bea_io:<level>:<class>:<code>` with class `industry`, `commodity`, `final_use`,
  `value_added`, `total` or `supply_adjustment`. The class is part of the ID because BEA reuses codes across classes
  (for example `111CA` is both an industry and a commodity). Totals without a published code use `T:<slug>`.
  GDP-by-industry entities are `bea_gdp_by_industry:<code>` and use the same codes as the IO summary level, so
  value added and gross output join IO tables by code.
- **IO metrics:** `io_make_output`, `io_use`, `io_supply` (USD, millions applied), `io_direct_requirement` and
  `io_total_requirement` (`USD_per_USD`). `produces`/`consumes` assertions exist only for the latest year. Only a
  default subset of workbooks (`io_members`) is normalized: the ZIPs are stored whole, but sector, after-redefinition
  and purchaser-price variants are not emitted.
- **NIPA metric names are not unique by themselves.** `gross_domestic_product` appears in USD, index and percent
  tables, so filter on `dimensions.series_code`, `measure` or `unit`. Quarterly and monthly dollar levels are
  generally SAAR.
- **Regional:** state and county observations use `geo:US:state:NN` / `geo:US:county:NNNNN`; BEA combined areas
  (for example Virginia independent cities) are flagged and BEA regions are `geo:US:bea_region:NN` aggregates. County
  coverage is CAINC1 lines 1–3, CAINC4 lines 35/45/46/47/50/60/70, CAINC5N, CAINC91 lines 10/20/30, and CAGDP2 /
  CAGDP9 (all-industry total plus 20 NAICS sector lines). `(D)`/`(T)` are null with a
  suppression reason; `(L)` is `below_display_threshold`.
- **County detail** (`bea_national_regional`, 7.2M county observations): `earnings_by_place_of_work` with an
  industry dimension (CAINC5N), `farm_earnings` / `nonfarm_earnings` / `private_nonfarm_earnings`,
  `inflows_of_earnings` / `outflows_of_earnings` / `adjustment_for_residence` (CAINC91), and `gdp` / `real_gdp`
  by NAICS sector (CAGDP2 / CAGDP9). County API rows carry no NAICS code, so these records have an industry slug
  but no `naics` dimension; use the slug, not a code join. BEA does not serve CAINC35 or CAEMP25N for counties.
- **National income overlaps FRED:** BEA NIPA levels (`bea_national_regional`) and the FRED panel's national accounts
  series are the same data. FRED adds real-time vintages; BEA adds full table and line detail.
- **treasury_debt** emits `public_debt`, `debt_held_by_public`, `intragovernmental_debt`, `average_interest_rate`,
  DTS cash and debt flows and MTS receipts and outlays on `us:agency:treasury`. Each observation is tagged day, month,
  fiscal-year-to-date or fiscal year, and Total/Sub-Total rows are flagged as aggregates.
- **Raw BEA API shards contain the API key** (BEA echoes `UserID` in every response). Raw artifacts are git-ignored
  and normalized records never contain it; do not share raw shards.

## International (`worldbank_wdi`, `imf_sdmx`, `oecd_sdmx`, `bis_bulk`, `ecb_eurostat`, `exiobase3`)

- **Identifiers:** `iso3:XXX` for countries everywhere (publisher codes kept in attributes; IMF `KOS`/`UVK` and Eurostat
  `EL`/`UK` are mapped to `XKX`, `GRC`, `GBR`). Aggregates are `agg:<source>:<code>` (`aggregate_cohort`,
  `attributes.aggregate`), EU regions are `nuts2021:<code>` with `within` to their country, and EXIOBASE uses
  `exiobase:region:<code>` (with `corresponds_to` to `iso3:` for the 44 single-country regions),
  `exiobase:industry:<slug>` and `exiobase:regional_industry:<region>:<slug>`.
- **Overlapping metrics — pick one source per metric and use the rest for validation:** CPI appears in `imf_sdmx`,
  `oecd_sdmx`, Eurostat HICP and BIS long CPI; FX in BIS XRU, ECB reference rates and IMF ER; policy and market rates in
  BIS CBPOL, OECD FINMARK and IMF MFS. US series in these datasets also duplicate `fred_macro_panel`, which is the only
  source with real-time vintages.
- **EXIOBASE** covers 2010, 2014, 2017, 2020 and 2022 with `dimensions.year`. `intermediate_supply` keeps the cells
  carrying 97.1% of value (1.07M of 31.6M non-zero cells for 2022) plus `input_coefficient`; values are current prices
  per year, so deflate before comparing years. **Licence CC BY-SA 4.0: share-alike binds derived tables**, unlike every
  other dataset here.
- **Data quality:** 7.6% of WDI observations fall back to `as_published` units; IMF values are full units (no SCALE);
  BIS keeps 415,830 nulls for suppressed or impossible cells and skips holiday gaps, and its effective-exchange-rate
  units (2020=100) come from BIS documentation because the rows carry none. BIS locational banking statistics are
  excluded: the server restarts the 360 MB shard indefinitely; consolidated banking already provides a bilateral network.

## Estimation-layer required series (`worldmodel/estimation/requirements.json`)

`SeriesRequirement.matches` (`worldmodel/estimation/data.py`) selects observations by **exact `metric` and `unit`**
(then optional `subject`, `attributes.source_series` and `dimensions.geography`), and the requirements ask for
publisher-scale units. The series it names therefore deviate from the base-unit convention above: their metric and
unit are taken from the requirement and values are rescaled to that unit
(`fred_macro_panel/config.json#estimation_overrides`, e.g. TOTALSL is published in millions and emitted as
`billion_USD`). Every FRED and BLS observation carries `attributes.source_series` (the publisher series id).

Every FRED observation carries `attributes.series_id` (also `dimensions.series_id`), `attributes.realtime_start` /
`realtime_end` and `dimensions.frequency`; filter `realtime_start <= t < realtime_end` (or `observed_at <= t`) to
reproduce what was published on day `t`. Units are as emitted (estimation overrides applied).

| Series | Dataset (metric) | Unit | Freq | Real-time coverage | Estimation component |
| --- | --- | --- | --- | --- | --- |
| CORCCACBS | `fred_macro_panel` (`credit_card_chargeoff_rate`) | `percent` | Q | all ALFRED vintages |  |
| CPIAUCSL (vintages required) | `fred_macro_panel` (`consumer_price_index`) | `index_1982_1984_100` | M | all ALFRED vintages | policy_rule |
| DCOILWTICO | `fred_macro_panel` (`oil_price`); `fred_oil_price` (`oil_price`, `USD/barrel`) | `USD/barrel` | D | all ALFRED vintages (also in `fred_oil_price`) | demand_price_elasticity, price_adjustment, energy_purchasing |
| DFEDTARU | `fred_macro_panel` (`fed_funds_target_upper`) | `percent` | D | all ALFRED vintages |  |
| DFF | `fred_macro_panel` (`policy_rate`); `fred_policy_rate` (`policy_rate`, `percent`) | `percent` | D | all ALFRED vintages (also in `fred_policy_rate`) | interest_pass_through, deposit_growth, default_hazard, energy_purchasing |
| DPRIME | `fred_macro_panel` (`prime_rate_daily`) | `percent` | D | all ALFRED vintages |  |
| DPSACBW027SBOG (vintages required) | `fred_macro_panel` (`commercial_bank_deposits`) | `billion_USD` | W | all ALFRED vintages | deposit_growth |
| DRBLACBS | `fred_macro_panel` (`business_loan_delinquency_rate`) | `percent` | Q | all ALFRED vintages |  |
| DRCCLACBS | `fred_macro_panel` (`credit_card_delinquency_rate`) | `percent` | Q | all ALFRED vintages | default_hazard |
| FEDFUNDS | `fred_macro_panel` (`federal_funds_rate`) | `percent` | M | all ALFRED vintages | policy_rule |
| GASREGW | `fred_macro_panel` (`retail_gasoline_price`) | `USD/gallon` | W | all ALFRED vintages | demand_price_elasticity, price_adjustment |
| GDPC1 (vintages required) | `fred_macro_panel` (`real_gdp`) | `billion_chained_2017_USD` | Q | all ALFRED vintages | policy_rule |
| GDPPOT (vintages required) | `fred_macro_panel` (`potential_gdp`) | `billion_chained_2017_USD` | Q | all ALFRED vintages | policy_rule |
| INDPRO (vintages required) | `fred_macro_panel` (`industrial_production_index`) | `index_2017_100` | M | all ALFRED vintages | labor_demand |
| MPRIME | `fred_macro_panel` (`prime_loan_rate`) | `percent` | M | all ALFRED vintages | interest_pass_through (see note) |
| NROU | `fred_macro_panel` (`natural_rate_of_unemployment`) | `percent` | Q | all ALFRED vintages |  |
| PAYEMS (vintages required) | `fred_macro_panel` (`nonfarm_payroll_employment`) | `thousand_persons` | M | all ALFRED vintages | labor_demand |
| PCEPILFE | `fred_macro_panel` (`core_pce_price_index`) | `index_2017_100` | M | all ALFRED vintages |  |
| POPTHM (vintages required) | `fred_macro_panel` (`resident_population`) | `people` | M | all ALFRED vintages | population_growth_rate |
| RRSFS (vintages required) | `fred_macro_panel` (`real_retail_sales`) | `million_USD_1982_1984` | M | all ALFRED vintages | energy_purchasing |
| RSAFS (vintages required) | `fred_macro_panel` (`retail_sales`) | `million_USD` | M | all ALFRED vintages | energy_purchasing (alternate) |
| SNDR | `fred_macro_panel` (`national_savings_deposit_rate`) | `percent` | M | all ALFRED vintages | deposit_rate_pass_through |
| TOTALSL (vintages required) | `fred_macro_panel` (`consumer_credit_outstanding`) | `billion_USD` | M | all ALFRED vintages | credit_growth |
| UNRATE | `fred_macro_panel` (`unemployment_rate`) | `percent` | M | all ALFRED vintages | default_hazard |

Note on the prime rate: `requirements.json` names DPRIME (daily) as the `interest_pass_through` loan rate and MPRIME
as its "monthly alternative". Only MPRIME carries the `prime_loan_rate` estimation override in
`fred_macro_panel/config.json`, so exact metric/unit matching resolves the requirement through MPRIME; DPRIME is
published as `prime_rate_daily` and is not selected.

BLS series (flat files are current vintage only: `attributes.vintage = current_at_retrieval`,
`attributes.realtime_start` = retrieval date, `attributes.realtime_end = null`; BLS id in `attributes.series_id`):

All carry `attributes.source_series` (= BLS series id) and `dimensions.geography` (= subject).

| Series | Dataset (metric) | Subject | Unit | Freq | Notes |
| --- | --- | --- | --- | --- | --- |
| CES0000000001 | `bls_labor` (`nonfarm_payroll_employment`) | `geo:US` | `thousand_persons` (publisher scale) | M, SA | 1939+; other CES series stay `employment`/`persons`; vintages of the same series: PAYEMS in `fred_macro_panel` |
| LNS14000000 | `bls_labor` (`unemployment_rate`) | `geo:US` | `percent` | M, SA | from `ln.data.1.AllData` (21 headline CPS series normalized); FRED `UNRATE` is the same series with ALFRED vintages |
| LASST{fips}0000000000003 | `bls_labor` (`unemployment_rate`) | `geo:US:state:<fips>` | `percent` | M, SA | 50 states + DC + PR |
| LASST{fips}0000000000005 | `bls_labor` (`employment`) | `geo:US:state:<fips>` | `persons` | M, SA | household (LAUS) employment, a different concept from payrolls |

Interim `bls_labor` build (normalized version `50917b81…`, 18,183,421 rows, 1,035,722,110 bytes) was built from the
superseded 1.06 GB raw artifact `17e1b829…` and covers only SM, LAUS, JOLTS, CES national, OEWS May 2025 and QCEW
annual 2023–2025: **LNS14000000, QCEW 1990–2022, QCEW quarterly and OEWS state/MSA are absent from it**.
The replacement 4.79 GB raw artifact `d4726093…` (90 shards) finished downloading on
2026-09-15 but has not been normalized, so `bls_labor` is still `full_acquisition_configured` in `wm catalog`. Use
`fred_macro_panel` `unemployment_rate` (UNRATE, with vintages) until the full rebuild lands.

Regional model inputs from `bls_labor` (full build: QCEW annual 2014–2025 and quarterly 2023–2025; the published
interim build `50917b81…` has QCEW annual 2023–2025 only and no quarterly rows) keep `area_fips`, `industry_code`,
`own_code` and `source_field` (e.g. `annual_avg_emplvl`) in attributes; NAICS ids follow the file year's vintage
(`naics2022:` 2022+, `naics2017:` 2017–2021, `naics2012:` 2014–2016), so cross-vintage industry panels need a
concordance. LAUS county `labor_force` / `employment` / `unemployed` are included. `lehd_lodes` tract-to-tract commuting
flows keep pairs with at least 5 jobs plus a per-workplace-tract residual; county pairs keep every job.
