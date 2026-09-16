# oecd_sdmx

OECD Data Explorer extracts via the OECD SDMX REST API
(`https://sdmx.oecd.org/public/rest/data/<agency>,<DSD>@<DF>,<version>/<key>?format=csvfile`).

## Source and scope

`files` strategy (anonymous). The API allows **60 data downloads per hour** and blocks VPN
traffic, so requests are spaced about 70 s apart (`rate_limit.requests_per_second` 0.014).
Keys were sized with probe downloads on 2026-09-15 to keep the extract near 125 MB:

| File | Dataflow | Key restriction | Metrics |
| --- | --- | --- | --- |
| CLI | `DSD_STES@DF_CLI` 4.1 | monthly; LI, BCICP, CCICP | `composite_leading_indicator`, `business_confidence_index`, `consumer_confidence_index` |
| BTS | `DSD_STES@DF_BTS` 4.0 | monthly; BCICP, PR, OB, EM | `business_tendency_<measure>` |
| CS | `DSD_STES@DF_CS` 4.0 | monthly | `consumer_opinion_<measure>` |
| FINMARK | `DSD_STES@DF_FINMARK` 4.0 | monthly | `share_price_index`, `short_term_interest_rate_call_money`, `interbank_rate_3m`, `long_term_interest_rate`, `exchange_rate_ncu_per_usd`, … |
| EO | `DSD_EO@DF_EO` 1.5 (Economic Outlook, latest edition) | annual; 33 key measures incl. projections | `eo_<measure>` (e.g. `eo_gdpv`, `eo_unr`, `eo_cbgdpr`, `eo_ggflq`) |
| HOUSE_PRICES | `DSD_AN_HOUSE_PRICES@DF_HOUSE_PRICES` 1.0 | quarterly | `house_price_<measure>` |
| QNA | `DSD_NAMAIN1@DF_QNA` 1.1 | quarterly SA; S1/S13/S1M; B1GQ, P3, P5, P51G, P52, P6, P7, B11, D1, EMP, POP; XDC/USD_PPP/persons; current prices & chain-linked volumes; levels | `gdp`, `final_consumption_expenditure_s13`, `employment`, … |
| PRICES | `DSD_PRICES@DF_PRICES_ALL` 1.0 | monthly national CPI; all items, food, energy, core; index and y/y | `cpi_index`, `cpi_inflation_yoy` (`dimensions.expenditure`) |
| UNE_M | `DSD_LFS@DF_IALFS_UNE_M` 1.0 | total sex; 15+ and 15-24 | `unemployment_rate` |
| PDB_ULC_Q | `DSD_PDB@DF_PDB_ULC_Q` 1.0 | all | `productivity_<measure>` (unit labour costs, labour productivity) |
| MONAGG | `DSD_STES@DF_MONAGG` 4.0 | monthly | `monetary_aggregate_<measure>` |

TiVA / ICIO flows were probed but not included (budget; EXIOBASE covers MRIO structure).

## Normalization

* `REF_AREA` ISO3 → `iso3:XXX`; OECD groupings (OECD, EA20, G20, EU27_2020, …) → `agg:oecd:<code>`.
* `UNIT_MULT` (power of ten) is applied; unit from `UNIT_MEASURE` (`IX` + `BASE_PER` → `index_<base>_100`).
* Observation id `oecd:<DF>:<dimension key>:<TIME_PERIOD>`; all non-area dimensions are kept lower-cased in `dimensions`.
* Empty values → `value: null`, `missing_reason` `source_status_<OBS_STATUS>`.

## Licence

CC BY 4.0 (OECD terms since July 2024); some indicators include third-party data.

## Rebuild

```sh
python3 -m worldmodel acquire oecd_sdmx --dry-run
python3 -m worldmodel acquire oecd_sdmx --allow-network     # ~13 minutes at the API's hourly limit
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run oecd_sdmx
python3 -m worldmodel verify oecd_sdmx
PYTHONPATH=. python3 data/oecd_sdmx/tests/test_oecd_sdmx.py
```
