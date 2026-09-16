# bea_national_regional

BEA national accounts (NIPA), state regional accounts and county income and GDP, normalized into
evidence records.

## Source and scope

- NIPA flat files from `https://apps.bea.gov/national/Release/TXT/`: `TablesRegister.txt`,
  `SeriesRegister.txt`, `NipaDataA.txt`, `NipaDataQ.txt` and `NipaDataM.txt`. These contain every
  published NIPA series at every frequency. The registers must stay ahead of the data files in the
  acquisition list.
- State ZIPs from `https://apps.bea.gov/regional/zip/`: `SAGDP`, `SAINC`, `SAPCE`, `SQGDP` and `SQINC`.
  They are acquired in full. The pipeline reads only the `*__ALL_AREAS_*.csv` members of the tables
  listed in `parameters.state_tables`.
  - The defaults cover state GDP (current dollars, compensation, real), personal income summaries and
    components, earnings and wages by NAICS industry, the economic profile, transfers, taxes and
    disposable income, gross flows of earnings, PCE by product and by function, quarterly GDP and
    quarterly personal income.
  - Historical SIC tables and per-capita duplicates are left out by default.
- County data from the BEA Regional API (`GetData`, `GeoFips=COUNTY`, `Year=ALL`, one request per
  table line). County ZIPs are not served as static files; `/regional/zip/CAINC.zip` returns an HTML
  page. Lines are enumerated with `GetParameterValuesFiltered` (`TargetParameter=LineCode`):
  - CAINC1 lines 1-3 (1969+): personal income, population, per-capita income.
  - CAINC4 lines 35, 45, 46, 47, 50, 60 and 70 (1969+): earnings by place of work, net earnings by
    place of residence, dividends/interest/rent, transfer receipts, wages and salaries, supplements
    and proprietors' income.
  - CAINC5N (2001+): farm, nonfarm and private nonfarm earnings; the 19 NAICS sectors (lines 100-1900);
    government (2000), split into federal civilian, military and state/local (2001, 2002, 2010).
  - CAINC91 lines 10, 20 and 30 (1969+): gross earnings inflows, outflows and residence adjustment
    (commuting).
  - CAGDP2 and CAGDP9 (2001+): all-industry total plus 20 NAICS sector lines, in current and chained
    2017 dollars.
  - Not acquired:
    - CAINC6N duplicates the earnings view.
    - CAGDP8 quantity indexes derive from CAGDP9.
    - CAINC30 mostly repeats CAINC1 and CAINC4.
    - Sub-sector lines would multiply rows.
    - CAINC35 (transfer detail) and CAEMP25N (employment by industry) are not served for counties by
      the Regional API; the line enumeration returns no values.
  - Requests are spaced at 0.05/s so transfer stays under BEA's 100 MB/min limit.

## Licence and credential

This is a U.S. federal government work with no redistribution restriction. The BEA API terms of use
apply. Set `BEA_API_KEY` in `.env`; it is injected as `UserID`. **BEA echoes the UserID inside API
JSON responses**, so county raw shards contain the key. Raw artifacts are git-ignored. Normalized
records never copy request parameters.

## Records

- Entities:
  - Places: `geo:US`, `geo:US:state:<2-digit FIPS>` and `geo:US:county:<5-digit FIPS>`. Counties carry
    `bea_combined_area` for BEA combined areas such as Virginia independent cities (FIPS 51901+ or
    names with `+`).
  - BEA regions: `geo:US:bea_region:<91-98>`, as aggregate_cohort.
  - NIPA series: `bea:nipa:<SeriesCode>` (economic_series). Attributes hold the metric name,
    calculation type, default scale and all table:line placements.
  - `within` assertions link counties to states, and states and regions to the US.
- NIPA observations:
  - The subject is `geo:US` and the metric is the snake_case series label, for example
    `gross_domestic_product`.
  - `dimensions` hold `series_code`, `table_id`, `line_number` (first placement), `frequency`
    (A/Q/M), `measure` (for example `current_dollars` or `fisher_quantity_index_percent_change_annual_rate`)
    and `seasonal_adjustment` for NSA tables.
  - Units are `USD` (DefaultScale applied), `USD_chained_2017`, `index_2017_100`, `percent`,
    `percentage_points`, `ratio`, `persons` or `physical_units`.
  - Quarterly and monthly dollar levels follow BEA's presentation, generally seasonally adjusted at
    annual rates unless the metric name says "Period Rate" or the table is NSA.
- State and county observations:
  - Industry tables use a fixed metric with `industry` (slug) and `naics` dimensions: `gdp`,
    `real_gdp`, `compensation_of_employees`, `earnings_by_place_of_work`, `wages_and_salaries`,
    `real_gdp_quantity_index` and `real_gdp_growth_contribution`.
  - PCE tables use `personal_consumption_expenditures` with a `category` dimension.
  - Other tables use the cleaned line description, for example `personal_income`, `population`,
    `per_capita_personal_income`, `disposable_personal_income` and `total_employment` (`jobs`).
  - County API industry lines ("Private nonfarm earnings: Manufacturing", "Real GDP: Utilities")
    follow the same industry metrics with an `industry` slug. Their summary lines use the cleaned
    description: `farm_earnings`, `nonfarm_earnings`, `private_nonfarm_earnings`,
    `inflows_of_earnings`, `outflows_of_earnings` and `adjustment_for_residence`. County API rows carry
    no NAICS code, so no `naics` dimension.
  - `dimensions` hold `table`, `line_code` and `frequency`. Units have their thousands or millions
    multiplier applied, recorded as `attributes.source_multiplier`.
  - `(D)` and `(T)` become `value: null` with `missing_reason: suppressed_to_avoid_disclosure`, and
    `(L)` becomes `below_display_threshold`. `(NA)` and `(NM)` cells are not emitted.

## Rebuild

```sh
wm acquire bea_national_regional --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run bea_national_regional
wm verify bea_national_regional
python -m pytest data/bea_national_regional/tests
```
