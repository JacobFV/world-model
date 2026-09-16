# bis_bulk

Bank for International Settlements statistics from the public bulk flat-CSV downloads
(<https://data.bis.org/bulkdownload>), normalized to evidence observations.

## Source and scope

One shard per ZIP (`files` strategy, no credentials):

| Shard | Flow | Normalized |
| --- | --- | --- |
| `WS_CBPOL_csv_flat.zip` | Central bank policy rates (daily, monthly) | all rows → `policy_rate` (percent) |
| `WS_XRU_csv_flat.zip` | US dollar exchange rates | all rows → `exchange_rate_per_usd` (`<CUR>_per_USD`, `dimensions.collection` A=average / E=end of period) |
| `WS_EER_csv_flat.zip` | Effective exchange rates | `effective_exchange_rate_nominal` / `_real` (index, `dimensions.basket` B broad / N narrow) |
| `WS_TC_csv_flat.zip` | Total credit to the non-financial sector | `total_credit` (percent_of_gdp, USD or national_currency; borrower/lender sector, valuation, adjustment dims) |
| `WS_SPP_csv_flat.zip` | Selected residential property prices | `residential_property_price_nominal` / `_real` |
| `WS_LONG_CPI_csv_flat.zip` | Long consumer price series | `consumer_prices` (index or year-on-year percent via `dimensions.unit_measure`) |
| `WS_CBS_PUB_csv_flat.zip` | Consolidated banking statistics (4.8 GB CSV) | headline cut only (`parameters.cbs_filter`): stocks, domestic banks excl. domestic positions (4R), immediate (F) and ultimate-risk (U) basis, total claims, all instruments/maturities/currencies/sectors → `bank_consolidated_claims` (USD) with `dimensions.counterparty` = counterparty country entity (bilateral exposure network) |
| `WS_DEBT_SEC2_PUB_csv_flat.zip` | International debt securities (9 GB CSV) | headline cut only (`parameters.debt_sec_filter`): all issuer nationalities/ultimate sectors/issue types/currencies/maturities/rates → `debt_securities` (USD) by issuer residence × immediate issuer sector × market × measure |

Locational banking statistics (`WS_LBS_D_PUB`, 360 MB ZIP / 17.9 GB CSV) are **excluded**:
the BIS CDN drops the connection mid-transfer and answers ranged resumes with the full body and a
weak ETag, so the shard restarts from zero on every retry and never completes (observed
2026-09-15, ~0.26 MB/s, repeated restarts at 68-126 MB). `pipeline.py` and
`describe()` still map the flow to `bank_locational_positions` (USD, reporting country ×
`dimensions.counterparty` × position), so adding the file back plus an `lbs_filter` parameter
(amounts outstanding, all instruments/currencies/parent countries/reporting banks/sectors,
cross-border positions) is enough to normalize it. Consolidated banking (`WS_CBS_PUB`) already
provides a bilateral banking-exposure network.

## Normalization

* Coded cells (`D: Daily`) are reduced to codes; `UNIT_MULT` is applied (e.g. millions → ×1e6) and kept in `attributes.unit_multiplier`.
* Entities: ISO 3166 alpha-2 reporting areas → `iso3:XXX` (`entity_type` country, BIS code in `attributes.bis_code`); BIS aggregates (`XM` euro area, `5A`, `4T`, …) → `agg:bis:<code>` (`aggregate_cohort`, `attributes.aggregate: true`).
* Observation ids are natural keys: `bis:<flow>:<series key>:<TIME_PERIOD>`; `dimensions.series_key` is `<flow>:<series key>`; `valid_from`/`valid_to` is the half-open period.
* Empty `OBS_VALUE` with status `H` (holiday/weekend gap in daily series) is not emitted; other empty values → `value: null` with `missing_reason` `missing_cannot_exist` (M), `missing_not_collected` (L), `suppressed` (Q), `not_significant` (N), or `source_status_<code>` / `source_missing`; non-normal statuses are kept in `attributes.obs_status`.
* Units: effective exchange rates are `index_2020_100` (the unit is published on series rows only); total credit uses `UNIT_TYPE` (770 → `percent_of_gdp`, `USD`, else the national currency code).
* Series-level rows (empty `TIME_PERIOD`) are skipped.

## Licence

BIS terms of use (<https://www.bis.org/terms_statistics.htm>): free reuse with attribution ("Source: BIS"). Some series are compiled from national sources and may carry source terms.

## Rebuild

```sh
python3 -m worldmodel acquire bis_bulk --dry-run
python3 -m worldmodel acquire bis_bulk --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run bis_bulk
python3 -m worldmodel verify bis_bulk
PYTHONPATH=. python3 data/bis_bulk/tests/test_bis_bulk.py
```

Generated payloads live in `artifacts/` and `scratch/` (ignored by Git).
