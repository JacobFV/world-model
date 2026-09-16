# census_intl_trade

U.S. monthly merchandise trade at HS6 from the U.S. Census Bureau international trade API
(`api.census.gov/data/timeseries/intltrade/{imports,exports}/hs`).

## Scope

- **All-country totals** (`CTY_CODE=-`) by HS6: imports and exports, 2022-01 through the latest released
  month (2026-07 as of 2026-09-15).
- **Partner detail** by HS6 for major U.S. partners, 2024-01 through the latest month. The partner list is in
  `acquisition.combinations` and is chosen for trade weight and tariff-policy relevance.
- Imports: `GEN_VAL_MO` (general imports), `CON_VAL_MO` (imports for consumption), `CAL_DUT_MO` (calculated
  duty), `DUT_VAL_MO` (dutiable value). Exports: `ALL_VAL_MO` (total exports, domestic + foreign).
- Requests cover 6-month `time=from X to Y` windows. Whole-month HS6 x all-partner queries (and HS4 x
  all partners) return HTTP 500 after ~150 s (verified 2026-09-15), so the grid is split by partner.
- Budget ~150-185 MB. All partners x HS6 x monthly since 2022 would be roughly 1 GB, so it is out of scope.
  Add partners or earlier windows to `combinations` if budget allows.

Credential: `CENSUS_API_KEY` (`.env`), injected as the `key` query parameter and redacted from receipts.
Get one at <https://api.census.gov/data/key_signup.html>.

## Rebuild

```sh
python3 -m worldmodel acquire census_intl_trade --dry-run
python3 -m worldmodel acquire census_intl_trade --allow-network   # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run census_intl_trade
python3 -m worldmodel verify census_intl_trade
```

## Evidence

- Entities:
  - `iso3:USA` is the observation subject.
  - Partners are `iso3:XXX`, mapped via Census Schedule C ISO alpha-2 codes (local `census_countries.json`,
    from <https://www.census.gov/foreign-trade/schedules/c/country.txt>, ISO3 from
    `worldmodel/reference/countries_iso3166.csv`).
  - `census:partner:all` is the all-country total. `census:country:<code>` covers groups or unmapped codes
    (Kosovo, Gaza, West Bank); both are marked `aggregate`.
  - Products are `hs:NNNNNN`.
- Observations, all monthly `USD` with dimensions `frequency`, `flow`, `partner`, `product`, `hs_level`,
  `hs_revision` (HS2022 from 2022):
  - `import_general_value`
  - `import_calculated_duty`: attributes carry `import_consumption_value_usd`, `import_dutiable_value_usd`
    and the derived `effective_duty_rate_fraction` = calculated duty / consumption value.
  - `export_value`
- Rows whose monthly values are all zero (no trade that month) are skipped rather than emitted as zeros.
  Values are nominal and not seasonally adjusted. Census suppresses nothing at HS6 x country, but small
  shipments under the low-value threshold are excluded by the source.
- Locators are `shard:<n>/record:<k>` (1 = first data row after the header).

## Licence

U.S. federal government work (public domain). Attribution: U.S. Census Bureau, USA Trade / international
trade API. This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau.

## Local files

`artifacts/` and `scratch/` are ignored by Git.
