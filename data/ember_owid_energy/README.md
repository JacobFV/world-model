# ember_owid_energy

Country-year electricity and energy panel from two open publishers:

- **Ember Yearly Electricity Data**, full release in long format
  (`yearly_full_release_long_format.csv`, about 49 MB). It covers generation (TWh and % share), capacity (GW),
  demand, net imports, and power-sector CO2 emissions and intensity, by country or region and fuel, from 2000 on.
- **Our World in Data energy dataset** (`owid-energy-data.csv`, about 9 MB). It gives primary energy
  consumption and production, the electricity mix, population and GDP by country-year (earliest years
  from 1900).

## Scope and normalization

`pipeline.py` has one stage, `normalized` (gzip evidence JSONL).

- Countries are `iso3:XXX`, with Kosovo as `iso3:XKX` (OWID gives no code), matching the other datasets.
- Publisher aggregates (World, EU, G20, income groups, "(EI)"/"(EIA)" regions) are
  `ember:region:<slug>` or `owid:region:<slug>` locations with `attributes.aggregate = true`.
- Historical states and former ISO codes (USSR, Czechoslovakia, East/West Germany, Yugoslavia, Serbia
  and Montenegro, Netherlands Antilles, ...) are `<publisher>:historical_country:<code>` countries
  with `attributes.historical = true`. They never use the shared `iso3:` namespace.
- Metrics use tidy names, units and `dimensions.frequency = "annual"`. Half-open year validity runs
  from `valid_from = YYYY-01-01` to `valid_to = YYYY+1-01-01`. `dimensions.publisher` is `ember` or `owid`, and
  `dimensions.fuel` is a fuel slug:
  - `electricity_generation` (TWh)
  - `electricity_generation_share` (percent)
  - `electricity_capacity` (GW)
  - `electricity_demand` (TWh)
  - `electricity_demand_per_capita` (MWh/person)
  - `electricity_net_imports` (TWh)
  - `power_sector_co2_emissions` (MtCO2)
  - `power_sector_co2_intensity` (gCO2/kWh)
  - `power_sector_ghg_emissions` (MtCO2e)
  - `primary_energy_consumption` (TWh)
  - `primary_energy_share` (percent)
  - `energy_production` (TWh)
  - `electricity_share_of_primary_energy` (percent)
  - `primary_energy_intensity` (kWh/international_USD_2011)
  - `population` (people)
  - `gdp_ppp` (international_USD_2011)
- Ember `fuel_level` is `fuel` (coal, gas, ...) or `aggregate_fuel` (clean, fossil, renewables, ...).
  Aggregate fuels overlap with individual fuels, so do not sum across levels.
- OWID derived columns (year-on-year changes, per-capita ratios) are skipped because they can be
  recomputed. Blank Ember values become `value: null` with `missing_reason: source_blank`.
  Blank OWID cells are omitted.
- Ember and OWID overlap for electricity metrics. They are kept side by side and distinguished by
  `dimensions.publisher`, with no reconciliation.

## Licence

Both sources are CC BY 4.0. Attribute Ember and Our World in Data. OWID in turn credits the Energy
Institute Statistical Review, EIA and Ember. Redistribution is allowed with attribution.

## Rebuild

```sh
python3 -m worldmodel acquire ember_owid_energy --dry-run
python3 -m worldmodel acquire ember_owid_energy --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run ember_owid_energy
python3 -m worldmodel verify ember_owid_energy
python3 -m unittest data/ember_owid_energy/tests/test_pipeline.py
```

No credentials are required. `artifacts/` and `scratch/` are ignored by Git.
