# ecb_eurostat

ECB euro foreign exchange reference rates and selected Eurostat datasets, normalized to
evidence observations.

## Source and scope

`files` strategy, no credentials, one shard per download:

* ECB `eurofxref-hist.zip` — daily euro reference rates since 1999 → `exchange_rate_per_eur`
  (unit `<CUR>_per_EUR`, subject `agg:ecb:euro_area`, `dimensions.quote_currency`).
* Eurostat SDMX 2.1 dissemination API, full-dataset gzip TSV
  (`https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/<code>?format=TSV&compressed=true`):

| Dataset | Metric(s) | Filter (`parameters.filters`) |
| --- | --- | --- |
| `nama_10_gdp` annual GDP and components | `gdp`, `final_consumption_expenditure`, `gross_fixed_capital_formation`, `exports_goods_services`, … (`esa2010_<code>` fallback) | units CP_MEUR, CP_MNAC, CLV10_MEUR, CLV_PCH_PRE, PD10_EUR, CP_EUR_HAB, CLV10_EUR_HAB |
| `namq_10_gdp` quarterly GDP and components | same | units CP_MEUR, CLV10_MEUR, CLV_PCH_PRE, CLV_PCH_SM, PD10_EUR; SCA/NSA |
| `nama_10r_2gdp`, `nama_10r_3gdp` regional GDP (NUTS 2/3) | `regional_gdp` | none |
| `prc_hicp_midx` HICP monthly index | `hicp_index` (index_2015_100) | unit I15 |
| `une_rt_m` monthly unemployment | `unemployment_rate` / `unemployed_persons` | none |
| `ext_st_eu27_2020sitc` EU trade by SITC | `exports_value` / `imports_value` / `trade_balance` (`dimensions.partner`) | indic_et TRD_VAL, TRD_VAL_SCA |
| `nrg_bal_c` energy balances | `energy_balance` (tonne_oil_equivalent) | unit KTOE |
| `sts_inpr_m` industrial production | `industrial_production_index` (index_2021_100) | unit I21, SCA |
| `irt_lt_mcby_m` long-term interest rates (convergence) | `long_term_interest_rate` | none |

The plan estimated 80.6 MB; the recommended dataset list measures about 107 MB (HEAD
Content-Length, 2026-09-15), hence `desired_bytes` 115 MB.

## Normalization

* Geography: 2-letter codes → `iso3:` (Eurostat `EL`→GRC, `UK`→GBR); NUTS codes →
  `nuts2021:<code>` (`jurisdiction`) with a `within` assertion to the country (the NUTS
  version is the one Eurostat publishes for the dataset; the namespace name follows the
  project convention); EU/euro-area/other groupings and trade partner groups →
  `agg:eurostat:<code>` (`aggregate_cohort`).
* Unit scale prefixes are applied (`*_MEUR`, `MIO_*` ×1e6, `THS_*`, `KTOE` ×1e3); the source
  unit code stays in `dimensions.unit_code`; all other TSV dimensions are kept as codes.
  Datasets without a unit dimension get their documented unit: EU trade values
  (`indic_et` TRD_VAL*) are million EUR → `EUR` ×1e6 (`stk_flow` EXP/IMP/BAL_RT →
  `exports_value`/`imports_value`/`trade_balance`); `irt_lt_mcby_m` is `percent_per_annum`.
  Regional "percent of EU27 average" codes (`PPS_HAB_EU27_2020`, `EUR_HAB_EU27_2020`) map to
  `percent_of_eu27_average`.
* Size: `nrg_bal_c` (all fuels × balance items, KTOE) is 43 % of rows; normalized output is
  about 1.9× the raw gzip bytes because Eurostat's gzip TSV is extremely compact.
* Flags (`p` provisional, `e` estimated, `b` break, …) go to `attributes.flags`. Bare `:` cells
  are not emitted; `: c` (confidential), `: n`, `: z`, `: u` become `value: null` with a
  `missing_reason`.
* Observation ids: `eurostat:<dataset>:<row key>:<period>` and `ecb:EXR.D.<CUR>.EUR.SP00.A:<date>`.

## Licence

Eurostat: free re-use with attribution (Eurostat copyright notice; CC BY 4.0-equivalent),
some third-party data excepted. ECB statistics: re-use with source citation.

## Rebuild

```sh
python3 -m worldmodel acquire ecb_eurostat --dry-run
python3 -m worldmodel acquire ecb_eurostat --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run ecb_eurostat
python3 -m worldmodel verify ecb_eurostat
PYTHONPATH=. python3 data/ecb_eurostat/tests/test_ecb_eurostat.py
```
