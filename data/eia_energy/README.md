# eia_energy

U.S. Energy Information Administration energy statistics from two sources. No API key is needed.

- **Bulk series ZIPs** (`www.eia.gov/opendata/bulk/*.zip`): ELEC (electricity, including plant-level
  generation, fuel use and heat rates), PET (petroleum), NG (natural gas), SEDS (State Energy Data
  System), INTL (international), TOTAL (Monthly Energy Review), STEO (Short-Term Energy Outlook) and
  COAL. Each ZIP holds one JSON-lines member. A line is either a series with a `data` array of
  `[period, value]` pairs or a category-tree node.
- **EIA-860 2025** (`eia8602025.zip`): the plant, generator (operable, proposed,
  retired/canceled) and ownership inventory. **EIA-923 2025** (`f923_2025.zip`) and the bulk
  `manifest.txt` are acquired and kept raw only. Plant generation and fuel use come from ELEC.PLANT
  series instead.

## Normalization (`full.py`, stage `normalized`, gzip evidence JSONL)

`pipeline.py` sends sharded full artifacts to `full.py`. JSONL samples still use the original
retail-sales sample adapter.

Bulk series:

- Each series becomes an `eia:series:<series_id>` `economic_series` entity with its name, units,
  frequency, geography, `geoset_id` and `last_updated`.
- Each data point becomes one observation:
  - `metric` is `eia_<dataset>_<family>`, for example `eia_elec_plant_gen`, `eia_seds_teicd` or
    `eia_intl_53_1_tbpd`. The family is EIA's geography-free `geoset_id` when published.
    Otherwise it is the series code without the frequency, and for ELEC/COAL also without the
    dash-joined key. Look up human-readable names on the series entity.
  - `unit` is EIA's `units` string, unchanged.
  - `subject` is the most specific resolvable entity: `eia:plant:<code>`, `iso3:USA` or `iso3:XXX`,
    or `geo:US:state:<fips>`. Multi-area series (PADDs, census divisions, ...) use the series
    entity itself.
  - `dimensions` holds `frequency` and `series_id`, plus `fuel`/`prime_mover` (plant series),
    `series_key` (other ELEC/COAL series) and `estimate_type` (`history` or `forecast` for STEO,
    based on `lastHistoricalPeriod`).
  - Periods are half-open. Weekly and 4-week values are period-ending: a weekly value dated D
    covers `[D-6, D+1)`.
  - Non-numeric codes such as `W` (withheld) or `NA` become `value: null` with
    `missing_reason: source_code:<code>`.
- The `plant_frequencies` parameter defaults to `["A"]`. Plant-level ELEC series (about 50 million
  monthly and quarterly points) are emitted annually only, which keeps normalized output within
  about 2× raw. Monthly plant data stays in the raw artifact; override with
  `--parameters '{"plant_frequencies": ["A", "M"]}'`.
- The `plant_families` parameter defaults to `["GEN", "AVG_HEAT"]`: plant generation and heat rate.
  Fuel input is approximately generation × heat rate, so the `CONS_*` consumption families stay raw.
- The `subannual_since` parameter defaults to `2022`. Annual series and the priority PET series below
  keep their full history, while other monthly, weekly and daily points start in 2022. Without these
  limits the output would be about 38M records (about 2.1 GB gzip, 4.6× raw); with them it stays
  within 2× raw. Older sub-annual history stays in the raw artifact; override with
  `--parameters '{"subannual_since": 1990, "plant_families": ["GEN", "AVG_HEAT", "CONS_TOT_BTU"]}'`.
- INTL geography codes that are not current ISO countries are kept out of `iso3:`:
  - `WLD` and `WAK` → `eia:region:<code>` (`aggregate: true`);
  - the aggregates EIA publishes under a member's code ("OPEC - South America" as VEN, "South Korea
    and other OECD Asia" as KOR) → `eia:region:<slug>`;
  - `CSK`, `DDR`, `SUN`, `SCG`, `YUG` → `eia:historical_country:<code>` (`historical: true`);
  - Kosovo `XKS` → `iso3:XKX`, matching the other datasets.
- The `skip_frequencies` parameter defaults to `["Q", "4"]`. Quarterly and 4-week-average series
  can be recomputed from monthly and weekly data, so they are not emitted. Override it with
  `--parameters '{"skip_frequencies": []}'`.

Priority series for the estimation layer: full history, tidy metric names, subject `iso3:USA`.
Observation attributes carry `series_id`, `series_last_updated`, `bulk_file_last_updated` (from
`manifest.txt`) and `period`. `observed_at` is the acquisition retrieval time.

| Series | Metric | Unit | Frequency |
| --- | --- | --- | --- |
| PET.WCESTUS1.W | `crude_oil_commercial_stocks_excl_spr` | Thousand Barrels | weekly |
| PET.WCRFPUS2.W | `crude_oil_field_production` | Thousand Barrels per Day | weekly |
| PET.WCRIMUS2.W | `crude_oil_imports` | Thousand Barrels per Day | weekly |
| PET.WCREXUS2.W | `crude_oil_exports` | Thousand Barrels per Day | weekly |
| PET.WCRRIUS2.W | `refiner_net_input_crude_oil` | Thousand Barrels per Day | weekly |
| PET.WGTSTUS1.W | `motor_gasoline_total_stocks` | Thousand Barrels | weekly |
| PET.MGFUPUS2.M | `motor_gasoline_product_supplied` | Thousand Barrels per Day | monthly |
| PET.EMM_EPMR_PTE_NUS_DPG.W | `gasoline_retail_price_regular` | Dollars per Gallon | weekly |

EIA-860:

- Plants are `eia:plant:<code>` (facility). Attributes include coordinates, county, NERC region,
  balancing authority and sector. Relations: `located_in geo:US:state:<fips>`,
  `within_balancing_authority eia:ba:<code>`, and `eia:utility:<id> operates` the plant.
- Generators are `eia:generator:<plant>:<id>` (facility, `located_in` the plant). Attributes hold
  technology, prime mover, status, energy sources and operating/retirement years. Observations:
  `nameplate_capacity`, `net_summer_capacity` and `net_winter_capacity` (MW, valid for the
  inventory year).
- Owners are `eia:utility:<id> owns eia:generator:...`, with `attributes.ownership_fraction`
  between 0 and 1.

Balancing-authority codes match those in `eia_grid_operations`.

## Licence

This is U.S. federal government work in the public domain. Cite "Source: U.S. Energy Information
Administration". Some NG/PET price series carry third-party copyright notices in their `copyright`
field (for example Thomson-Reuters for Henry Hub spot prices). That field is kept on the series
entity (`source`). Check it before redistributing those particular series.

## Rebuild

```sh
python3 -m worldmodel acquire eia_energy --dry-run
python3 -m worldmodel acquire eia_energy --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run eia_energy
python3 -m worldmodel verify eia_energy
python3 -m unittest data/eia_energy/tests/test_pipeline.py
```

`artifacts/` and `scratch/` are ignored by Git.
