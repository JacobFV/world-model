# acs_5yr_tables

U.S. Census Bureau **American Community Survey 2020-2024 5-year** detailed tables via the Census Data API.

## Scope

- County (all 3,222 counties incl. Puerto Rico), full `group()` tables: B01003 total population, B01001 sex by age,
  B01002 median age, B19013 median household income, B19001 household income brackets, B19301 per capita income,
  B17001 poverty status, B15003 educational attainment, B23025 employment status, B08301 means of transportation to
  work, B08303 travel time to work, B25001 housing units, B25003 tenure, B25064 median gross rent, B25077 median home
  value, C24030 industry by sex, C24010 occupation by sex.
- Census tract (all ~85k tracts), 38 key estimates in two variable sets per state (population, median age, income,
  home value, rent, housing units and tenure, labor force/unemployment, poverty, bachelor's+ attainment, sex, commute
  mode, household income brackets, long commutes).
- 121 API calls, ~73 MB (the plan's 300 MB assumed summary-file downloads; desired set to 80 MB).

## Source, credential and licence

`https://api.census.gov/data/2024/acs/acs5?get=...&for=...&in=...` with `CENSUS_API_KEY` (query `key`, redacted in
receipts). Public domain; Census API terms apply (no implied endorsement). Survey estimates with 90% margins of error.

## Rebuild

```sh
wm acquire acs_5yr_tables --allow-network     # needs CENSUS_API_KEY in .env
WORLD_MODEL_RAW_VERIFY=size wm run acs_5yr_tables && wm verify acs_5yr_tables
python3 -m unittest discover -s data/acs_5yr_tables/tests -t data/acs_5yr_tables/tests
```

## Evidence

- Entities `geo:US:county:<GEOID5>` and `geo:US:tract:<GEOID11>` (`within` county/state).
- One observation per estimate variable, valid 2020-01-01 .. 2025-01-01, `dimensions.table`, `variable`, `survey = acs5`,
  `attributes.moe90`. Named metrics for headline variables (`population`, `median_age`, `median_household_income`,
  `per_capita_income`, `median_home_value`, `median_gross_rent`, `housing_units`, `owner_occupied_housing_units`,
  `renter_occupied_housing_units`, `labor_force`, `employed_civilian_labor_force`, `unemployed_civilian_labor_force`,
  `population_below_poverty`, ...); other cells use `acs_<table>_<line>` (see the Census variable list for labels).
  Units: people, households, workers, housing_units, USD_2024, USD_2024_per_month, years.
- Jam values (-666666666 etc.) become missing values with explicit reasons; annotations are kept.
