# osm_us_midwest

OpenStreetMap network topology for the Geofabrik **US Midwest** extract: Illinois, Indiana, Iowa, Kansas, Michigan, Minnesota, Missouri, Nebraska, North Dakota, Ohio, South Dakota, Wisconsin.

Part of the contiguous-US OSM network: `osm_topology` (US West), `osm_us_south`, `osm_us_midwest`, `osm_us_northeast`.
All four use identical code (`helpers.py` stdlib PBF decoder, `graph.py` edge simplification, `pipeline.py` stages),
copied into each dataset directory because dataset code must be local. Fix bugs in all four.

## Source and licence

- File: `https://download.geofabrik.de/north-america/us-midwest-260914.osm.pbf` (2,500,351,197 bytes, md5 `0a0c331cf3c9e8d92b9911a07d911533`, replication snapshot 2026-09-14).
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
wm acquire osm_us_midwest --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run osm_us_midwest
wm verify osm_us_midwest && wm verify osm_us_midwest/parsed
python3 -m unittest data/osm_us_midwest/tests/test_pipeline.py
```
