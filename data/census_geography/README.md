# census_geography

U.S. Census Bureau 2024 geography: Gazetteer files and 1:500,000 cartographic boundary shapefiles.

## Source and licence

~174 MB from <https://www2.census.gov/geo/>: 2024 Gazetteer (counties, places, CBSAs, tracts, ZCTAs) and
`cb_2024_us_{state,county,cbsa,csa,place,tract}_500k.zip` plus `cb_2020_us_zcta520_500k.zip` (no 2024 ZCTA
cartographic file is published). Public domain. Generalized boundaries; use TIGER/Line for legal precision.

## Rebuild

```sh
wm acquire census_geography --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run census_geography                    # normalized (output stage)
WORLD_MODEL_RAW_VERIFY=size wm run census_geography --stage geometry    # compact polygons
python3 -m unittest discover -s data/census_geography/tests -t data/census_geography/tests
```

## Stages

- `normalized` (evidence): entities `geo:US:state:`, `geo:US:county:`, `geo:US:tract:<GEOID11>`, `geo:US:place:<GEOID7>`
  (`jurisdiction` for functioning governments, `location` for CDPs), `geo:US:cbsa:`, `geo:US:csa:`, `geo:US:zcta:<ZCTA5>`;
  `within` assertions (tract -> county -> state -> US, place -> state, CBSA -> CSA); observations `land_area`,
  `water_area` (m2), `latitude`/`longitude` (degrees, `dimensions.point = internal_point`), valid for the geography vintage
  year. State and CSA attributes come from the shapefile DBF (no Gazetteer file).
- `geometry` (generic JSONL, `retention: rebuildable`): one record per feature with `entity_id`, `bbox`, `crs` EPSG:4269 and
  `rings` quantized to 1e-5 degree and delta-encoded (`helpers.decode_ring`). The stdlib shapefile/DBF reader lives in
  `helpers.py`.
