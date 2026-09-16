# imf_sdmx

IMF statistics from the IMF SDMX API (<https://api.imf.org/external/sdmx/>), all economies,
normalized to evidence observations.

## Source and scope

`files` strategy, anonymous (no key needed as of 2026-09-15), `Accept:
application/vnd.sdmx.data+csv;version=1.0.0`, gzip transfer, ~0.5 requests/s (IMF reports
≈10 calls per 5 s). SDMX 3.0 queries use `attributes=none` because full attribute columns
inflate responses ~40× (US CPI alone is 110 MB with attributes).

| File | Dataflow / key | Metric(s) | Unit |
| --- | --- | --- | --- |
| CPI | `IMF.STA/CPI` `*.CPI._T.IX+YOY_PCH_PA_PT.M+Q+A` | `consumer_price_index`, `cpi_inflation_yoy` | index / percent_change_year_on_year |
| ER | `IMF.STA/ER` `*.XDC_USD.PA_RT+EOP_RT.M` | `exchange_rate_ncu_per_usd` (`dimensions.type_of_transformation` PA_RT average, EOP_RT end of period) | national_currency_per_USD |
| IMTS_world_M | `IMF.STA/IMTS` exports FOB, imports CIF, balance vs world (G001), monthly | `goods_exports_fob`, `goods_imports_cif`, `goods_trade_balance` | USD |
| IMTS_bilateral_A | SDMX 2.1 `IMF.STA,IMTS` exports/imports by counterpart, annual, 2015+ | same, `dimensions.counterpart` = partner entity | USD |
| BOP_AGG | `IMF.STA/BOP_AGG` all, annual | `bop_<indicator>` (current account, goods, services, income, FDI, portfolio, reserves, IIP) | USD or percent_of_gdp |
| IL | `IMF.STA/IL` total reserves incl. gold (market value), FX reserves, USD, monthly | `total_reserves_incl_gold_market_value`, `foreign_exchange_reserves` | USD |
| WEO | `IMF.RES/WEO` all indicators, annual (latest vintage incl. projections) | `weo_<indicator>` (e.g. `weo_ngdp_rpch`, `weo_pcpipch`, `weo_lur`, `weo_ggxwdg_ngdp`) | per indicator |
| ANEA / QNEA | national accounts, annual / quarterly SA | `gdp`, `final_consumption_expenditure`, `gross_fixed_capital_formation`, `exports_goods_services`, … | national_currency / USD / index |
| MFS_IR | interest rates, monthly | `interest_rate_<indicator>` | percent |
| MFS_MA | broad money, currency in circulation, monthly | `monetary_aggregate_<indicator>` | national_currency / USD |
| PCPS | primary commodity prices, monthly | `commodity_price_<commodity>` | USD / index |

The plan's 200 MB estimate is exceeded (258 MB actual, `desired_bytes` 265 MB): the bilateral
IMTS extract alone is 69 MB even restricted to annual data since 2015; all other keys are
restricted to headline series. IIP detail, GFS, IRFCL detail and DOTS monthly bilateral are
not included.

## Normalization

* Countries: ISO3 codes → `iso3:XXX` (IMF `KOS`/`UVK` → `XKX`; retired ISO3 codes such as
  `ANT` kept); IMF groups (`G001` world, `G110` advanced economies, …) → `agg:imf:<code>`.
* Values are taken as published in full units (the IMF portal's SCALE attribute is
  presentational); SDMX 2.1 `NaN` cells → `value: null`, `missing_reason`.
* Observation id `imf:<flow>:<series key>:<TIME_PERIOD>`; `dimensions` hold frequency, series
  key and every non-country dimension code (lower-cased names).
* Rows without TIME_PERIOD/OBS_VALUE (key-only series) are skipped.

## Licence

IMF Copyright and Usage (<https://www.imf.org/external/terms.htm>): free use with
attribution; some content sourced from third parties may be restricted.

## Rebuild

```sh
python3 -m worldmodel acquire imf_sdmx --dry-run
python3 -m worldmodel acquire imf_sdmx --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run imf_sdmx
python3 -m worldmodel verify imf_sdmx
PYTHONPATH=. python3 data/imf_sdmx/tests/test_imf_sdmx.py
```
