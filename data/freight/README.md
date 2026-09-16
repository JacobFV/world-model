# freight

Freight Analysis Framework (FAF) origin-destination freight flows by mode and SCTG2 commodity.

## Source and licence

- `https://faf.ornl.gov/faf5/Data/Download_Files/FAF5.7.1.zip` (FAF zone flows, 2,671,386 rows)
- `https://faf.ornl.gov/faf5/Data/Download_Files/FAF5.7.1_State.zip` (state flows)
- `https://faf.ornl.gov/faf6/data/Download_Files/ZIP/FAF6.0_State.zip` (FAF6.0 2022-benchmark state flows)
- Publisher: USDOT BTS and FHWA with Oak Ridge National Laboratory. US federal government work (public domain).
  No credential is needed. Values are **modeled estimates**, not survey counts.

Units: tons in thousand short tons; FAF5 `value` in million 2017 dollars; `current_value` in million current
dollars; ton-miles in millions; FAF6.0 value in million 2022 dollars.

## Evidence (aggregated, `attributes.aggregate = true`)

| Scope (`attributes.aggregation`) | Subject -> `dimensions.destination` | Key dimensions | Metrics and years |
| --- | --- | --- | --- |
| `state_od` (FAF5.7.1_State) | `geo:US:state:NN` -> `geo:US:state:NN` | `mode`, `commodity` | `freight_tons`, `freight_value`, `freight_ton_miles` 2017-2024 and 2030-2050; `freight_value_current` 2018-2024 |
| `zone_od` (FAF5.7.1) | `faf5:zone:NNN` -> `faf5:zone:NNN` | `mode`, `commodity` | `freight_tons`, `freight_value` 2017-2024 |
| `foreign` (FAF5.7.1_State) | `faf5:foreign_region:80N` <-> `geo:US:state:NN` | `trade_direction`, `foreign_mode`, `commodity` | tons and value, 2017-2024 and forecasts |
| `faf6_state` (FAF6.0) | state or `faf6:foreign_region:80N` | `mode`, `commodity`, `trade_type` | tons and value (million 2022 USD), 2022 |

- Domestic OD aggregates sum over trade type, distance band and foreign legs: they are the domestic legs of domestic,
  import and export shipments.
- Every record carries `attributes.projection`: `true` for FAF baseline forecast years (2030, 2035, 2040, 2045,
  2050), `false` for estimates. Filter on it before treating values as observed history. `series_type` is
  `benchmark_year` (2017 and FAF6.0 2022), `annual_estimate` (2018-2024) or `baseline_forecast` (2030+).
  Records are annual, with valid time from Jan 1 of the year to the next Jan 1.
- **Zero values are omitted.** A missing year for a published key means zero flow.
- Reference entities come from the metadata workbook: `faf5:zone:NNN` (`within` state), `faf5:foreign_region:80N`,
  `geo:US:state:NN`, `sctg2:NN` commodities. Mode slugs: truck, rail, water, air, multiple_modes_and_mail,
  pipeline, other_and_unknown, no_domestic_mode.
- Evidence locators name the ZIP member (`shard:N/member:FAF5.7.1.csv`) because each value aggregates many rows
  (`attributes.source_rows`).

Raw CSVs keep full row detail (distance band, in/out foreign modes). Add scopes in `pipeline.py` if a simulation
needs them. Rows whose domestic origin or destination state is blank have no domestic leg and are skipped in state
scopes.

### History and scenario files

| Shard | Scope | Content |
| --- | --- | --- |
| `FAF5.7.1_Reprocessed_1997-2012_State.zip` (38.9 MB) | `state_od_history` | tons, value, current value, ton-miles for 1997, 2002, 2007, 2012 (`series_type: reprocessed_benchmark`, `projection: false`) |
| `FAF5.7.1_State_HiLoForecasts.zip` (203 MB) | `state_od_scenarios` | low/high tons and value for 2030-2050 (`dimensions.scenario`, `series_type: low_forecast / high_forecast`, `projection: true`) |

Both come from `https://faf.ornl.gov/faf5/Data/Download_Files/` and are part of the acquisition. The pipeline treats
them as optional, so a three-file artifact still builds.

## Rebuild

```sh
wm acquire freight --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run freight
wm verify freight
python3 -m unittest data/freight/tests/test_pipeline.py
```

Samples are unavailable; a non-sharded input fails explicitly.
