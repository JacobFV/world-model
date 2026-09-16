# bea_input_output

BEA input-output accounts and GDP by industry, normalized into evidence records.

## Source and scope

- `https://apps.bea.gov/industry/iTables%20Static%20Files/AllTablesIO.zip` (make/use tables before
  and after redefinitions, producer and purchaser prices, commodity-by-industry direct requirements;
  sector and summary levels 1997-2023, detail benchmarks 2007/2012/2017).
- `https://apps.bea.gov/industry/iTables%20Static%20Files/AllTablesSUP.zip` (supply and use tables in
  the supply-use framework plus industry-by-industry, industry-by-commodity and commodity-by-commodity
  total requirements).
- BEA API `GetData` for `datasetname=GDPbyIndustry`, one request per table ID (value added, gross
  output, intermediate inputs, components, quantity and price indexes, and contributions, annual and
  quarterly, all years).

Both archives are acquired in full. By default the pipeline normalizes the members listed in
`parameters.io_members`: make, use (before redefinitions, producer prices), supply, use (supply-use
framework), commodity-by-industry direct requirements at summary and detail level, and the summary
industry-by-industry total requirements. The sector tables are aggregates of the summary tables.
After-redefinition, purchaser-price and the other total-requirement variants are derivable or
alternative presentations. To normalize them, add their file globs to `io_members`.

## Licence and credential

This is a U.S. federal government work with no redistribution restriction. The BEA API terms of use
apply to API access. Set `BEA_API_KEY` in `.env`; it is injected as the `UserID` query parameter
(https://apps.bea.gov/API/signup/). The static ZIPs do not need the key. It is still injected there
and ignored. **BEA echoes the UserID inside every API JSON response**, so raw GDPbyIndustry shards
contain the key. Raw artifacts are git-ignored and must not be shared. Normalized records never copy
request parameters.

## Records

- Entities:
  - `bea_io:<level>:<class>:<BEA code>` where `level` is `summary`, `detail` or `sector` and `class`
    is `industry` (type industry), `commodity` (type product), or `final_use`, `value_added`, `total`
    or `supply_adjustment` (type aggregate_cohort).
  - Totals without a published code use `T:<slug of label>`, for example
    `bea_io:summary:total:T:total_intermediate`.
  - GDP-by-industry industries use `bea_gdp_by_industry:<code>`, the same codes as IO summary and
    sector tables.
- IO observations:
  - Metrics are `io_make_output`, `io_use` and `io_supply` (`USD`, with BEA millions applied) and
    `io_direct_requirement` and `io_total_requirement` (`USD_per_USD`).
  - The subject is the row entity. `dimensions` hold `year`, `row_code`, `column_code`, `counterpart`
    (the column entity), `level` and `framework` (`make_use` or `supply_use`), plus `redefinitions`,
    `price_basis` and `matrix` where applicable.
  - Valid time is the calendar year.
  - Zero and `...` cells are not emitted.
- Assertions: `industry produces commodity` (make table) and `industry consumes commodity` (use table)
  for the latest year in each make/use workbook. Each carries the cell value in its attributes.
- GDP by industry: one metric per table, for example `value_added`, `real_value_added`
  (`USD_chained_2017`), `gross_output`, `intermediate_inputs`, `value_added_price_index`
  (`index_2017_100`), `*_pct_change` (`percent`) and `*_growth_contribution` (`percentage_points`).
  `dimensions` hold `table_id`, `frequency` (A/Q) and `industry`, plus `component` for tables
  6, 7, 25 and 26.

## Rebuild

```sh
wm acquire bea_input_output --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run bea_input_output
wm verify bea_input_output
python -m pytest data/bea_input_output/tests
```

The workbooks are nested inside ZIP archives, so the pipeline reads them with the stdlib streaming
reader in `helpers.py`.
