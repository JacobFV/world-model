# usgs_resources

USGS **Mineral Resources Data System** (MRDS) and **Mineral Commodity Summaries 2026** data release.

## Source and licence

- `https://mrdata.usgs.gov/mrds/mrds-csv.zip` (~26 MB; legacy global inventory, largely not updated since 2011).
- ScienceBase items 696a75d5d4be0228872d3bf8 / 69837e43b66b01367d7ec7c7 (doi:10.5066/P13XCP3R): `MCS2026_Commodities_Data.csv`,
  `MCS2026_T3_State_Value_Rank.csv`, `MCS2026_T7_Critical_Minerals_Salient.csv` (~3.2 MB).
- Public domain (USGS). No credential. Plan desired 50 MB included USMIN mine features, which were not added; desired is
  set to the actual 32 MB.

## Rebuild

```sh
wm acquire usgs_resources --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run usgs_resources && wm verify usgs_resources
python3 -m unittest discover -s data/usgs_resources/tests -t data/usgs_resources/tests
```

`pipeline.py` keeps the legacy 100-row ArcGIS sample adapter (tests/test_normalizer_contracts.py); full files are handled by
`pipeline_full.py`.

## Evidence (full mode)

- MRDS: `mrds:<dep_id>` (`resource_deposit`) with `contains_resource` -> `commodity:usgs:<slug>` (`mineral`), `located_in`
  -> `geo:US:state:<FIPS>` or `mrds:country:<slug>`, `latitude`/`longitude` and `development_status` observations
  (`attributes.valid_time_unknown`).
- MCS: observations on `geo:US`, `mcs:country:<slug>` or `mcs:world` with `dimensions.commodity` and `statistic`:
  `mineral_production`, `mineral_reserves`, `mineral_import`, `mineral_export`, `mineral_consumption`,
  `mineral_net_import_reliance`, `mineral_price`, `mineral_stock`, ... in the published unit (`unit`), yearly valid time,
  `attributes.estimated` for 2025e values, withheld (W) / not available (NA) as missing; state
  `nonfuel_mineral_production_value` (million USD), `_rank`, `_share`; critical-mineral salient statistics.
- Country names are MCS/MRDS labels, not ISO-resolved.
