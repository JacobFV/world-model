# worldbank_wdi

World Bank World Development Indicators (WDI) bulk CSV, normalized to country-year evidence
observations.

## Source and scope

* `files` strategy, one shard, no credentials:
  <https://databankfiles.worldbank.org/public/ddpext_download/WDI_CSV.zip> (the
  `databank.worldbank.org/data/download/WDI_CSV.zip` link redirects here; ~283 MB).
* Members used: `WDICSV.csv` (wide: economy × indicator rows, one column per year 1960–latest),
  `WDICountry.csv` (economies, regions, income groups), `WDISeries.csv` (indicator metadata
  and licence type). `WDIfootnote.csv` and `WDIcountry-series.csv` are kept raw only.

## Records

* Entities: economies → `iso3:XXX` (`country`; WDI codes are ISO3 except a few such as `XKX`
  Kosovo and `CHI` Channel Islands, kept as published) with attributes `region`,
  `income_group`, `lending_category`, `currency_unit`, `iso2_or_wb2`; regional/income/other
  aggregates (blank Region in WDICountry.csv) and codes missing from that table (e.g. `INX`
  "Not classified") → `agg:wdi:<code>` (`aggregate_cohort`, `attributes.aggregate: true`).
* Assertions: `member_of` from each economy to its region and income-group aggregate;
  indicator metadata as literal assertions on `wdi:indicator:<code>` (`label`, `topic`,
  `unit_of_measure`, `normalized_unit`, `periodicity`, `aggregation_method`,
  `license_type`, `source`).
* Observations: one per non-empty year cell. `metric` = `wdi_<indicator code lower-cased,
  dots → underscores>` (e.g. `wdi_ny_gdp_mktp_cd`), `dimensions` = `{frequency: "A",
  indicator: "<WDI code>"}`, `valid_from`/`valid_to` = calendar year, id
  `wdi:<economy>:<indicator>:<year>`. `unit` is derived from the unit-like parenthetical of
  the indicator name: `USD`, `USD_constant_2015`, `national_currency`,
  `percent_of_gdp`, `percent_annual_growth`, `percent`, `persons`,
  `international_dollar_ppp_current`, … (fallback: slug of the parenthetical, or
  `as_published`). WDI values are already in the stated units (no multipliers).

## Licence

CC BY 4.0 (World Bank Dataset Terms of Use), attribution "World Bank, World Development
Indicators". A few indicators come from third parties with their own terms; see the
`license_type` assertions per indicator before redistribution.

## Rebuild

```sh
python3 -m worldmodel acquire worldbank_wdi --dry-run
python3 -m worldmodel acquire worldbank_wdi --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run worldbank_wdi
python3 -m worldmodel verify worldbank_wdi
PYTHONPATH=. python3 data/worldbank_wdi/tests/test_worldbank_wdi.py
```
