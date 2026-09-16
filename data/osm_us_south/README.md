# osm_us_south

OpenStreetMap network topology for the Geofabrik **US South** extract: Alabama, Arkansas, Delaware, District of Columbia, Florida, Georgia, Kentucky, Louisiana, Maryland, Mississippi, North Carolina, Oklahoma, South Carolina, Tennessee, Texas, Virginia, West Virginia.

Part of the contiguous-US OSM network: `osm_topology` (US West), `osm_us_south`, `osm_us_midwest`, `osm_us_northeast`.
All four use identical code (`helpers.py` stdlib PBF decoder, `graph.py` edge simplification, `pipeline.py` stages),
copied into each dataset directory because dataset code must be local. Fix bugs in all four.

## Source and licence

- File: `https://download.geofabrik.de/north-america/us-south-260914.osm.pbf` (4,123,962,430 bytes, md5 `ade5f90f9b5c6c393eb02266b5c4f102`, replication snapshot 2026-09-14).
- Licence: **ODbL 1.0**. Attribution "(c) OpenStreetMap contributors" is required; a publicly distributed derived
  database must be offered under ODbL (share-alike). No credential is needed.

## Evidence

Same contract as `osm_topology` (see its README):

- `osm:node:N` entities with `lat`/`lon`, `networks` and `way_references`;
- assertions `road_connects_to`, `rail_connects_to`, `power_line_connects_to`, `pipeline_connects_to` and
  `ferry_connects_to` (`length_m`, `bidirectional`, `highway`, `maxspeed_kph`, `lanes`, selected `osm_*` tags);
- `osm:feature:{node|way|relation}:N` airports, ports, power plants, substations, refineries, rail stations/yards,
  fuel stations and military sites;
- locators `shard:0/node:N`, `shard:0/way:N`, `shard:0/relation:N`.

Geofabrik regions are cut by state boundaries but keep complete ways. Ways that cross into a neighbouring region can
therefore appear in both datasets with the same record IDs (`osm:way:W:segment:K:network`). Deduplicate by ID when
unioning regions.

## Rebuild

```sh
wm budget
wm acquire osm_us_south --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run osm_us_south
wm verify osm_us_south && wm verify osm_us_south/parsed
python3 -m unittest data/osm_us_south/tests/test_pipeline.py
```
