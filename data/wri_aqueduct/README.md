# wri_aqueduct

World Resources Institute **Aqueduct 4.0** water risk indicators (baseline 1979-2019 and 2030/2050/2080 projections).

## Source and licence

`aqueduct-4-0-water-risk-data.zip` (~262 MB, the only published bundle; includes a file geodatabase that the pipeline
ignores) from <https://www.wri.org/data/aqueduct-global-maps-40-data>. **CC BY 4.0** — attribute WRI and cite Kuzma et
al. (2023), *Aqueduct 4.0: Updated decision-relevant global water risk indicators*. No credential.

## Rebuild

```sh
wm acquire wri_aqueduct --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run wri_aqueduct && wm verify wri_aqueduct
python3 -m unittest discover -s data/wri_aqueduct/tests -t data/wri_aqueduct/tests
```

## Evidence

- Entities: `aqueduct:pfaf:<pfaf_id>` (`watershed`, HydroBASINS level 6), `gadm36:<GID_1>` admin-1 units, `iso3:<ISO3>`
  countries, `aqueduct:unit:<string_id>` (sub-basin x admin-1 x aquifer unit) with `within` assertions.
- Baseline observations (valid 1979-01-01 .. 2020-01-01) with a raw value and a `*_score` (0-5):
  `baseline_water_stress`, `baseline_water_depletion` (ratio), `interannual_variability`, `seasonal_variability`
  (coefficient of variation), `groundwater_table_decline` (cm/year), `riverine_flood_risk`, `coastal_flood_risk`
  (fraction of population per year), `drought_risk` per sub-basin; monthly `baseline_water_stress/depletion` and
  `interannual_variability` (`dimensions.month`); `untreated_connected_wastewater`, `coastal_eutrophication_potential`,
  `unimproved_drinking_water`, `unimproved_sanitation`, `peak_reprisk_country_esg_risk` per admin-1;
  `overall_water_risk` (default weighting) per unit.
- Future observations (`attributes.projection`, `dimensions.scenario` business_as_usual_ssp3_rcp70 / optimistic_ssp1_rcp26 /
  pessimistic_ssp5_rcp85, `projection_year`): `water_stress`, `water_depletion`, `interannual_variability`,
  `seasonal_variability` (+ `_score`), `available_blue_water`, `water_withdrawal` (cm/year).
- `-9999` (no data) is omitted; raw `9999` (arid / low-use sentinel) is a missing raw value with its score retained.
