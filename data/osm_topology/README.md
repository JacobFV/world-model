# osm_topology

OpenStreetMap network topology for the western United States from the Geofabrik US West extract (a superset of
California): simplified road, rail, power-line, pipeline and ferry edges between intersection/endpoint nodes, plus
transport and energy facilities. The
`sampling` block still describes the bounded San Francisco Overpass excerpt.

## Source and licence

- Publisher: OpenStreetMap contributors, extract by Geofabrik GmbH.
- File: `https://download.geofabrik.de/north-america/us-west-260914.osm.pbf` (dated snapshot, 3,400,591,554 bytes,
  md5 `09b41da4f8d8b3eb4a46074d33384e60`, replication timestamp 2026-09-14). `us-west-latest.osm.pbf` redirects to
  the newest dated file; update the `files` entry to move to a newer snapshot.
- History: the first full build used `california-260914.osm.pbf` (1.33 GB; 4.32M edges, 3.41M graph nodes,
  7.75M normalized records). After the shared budget grew, the scope moved to US West. Once the US West build was
  published and verified, the California raw artifact `8b550bf7…` (1,328,228,325 bytes) and the stage versions built
  from it (`parsed` `4b00eacc…`, 221,962,728 bytes; `normalized` `5c293a35…`, 228,384,682 bytes) were retired with
  their compact manifests and operation-cache entries, and `desired_bytes` was lowered to the US West size.
- Scale (US West, 3.40 GB PBF): 62,939 blobs, 460,002,431 nodes decoded, 5,720,289 selected ways, 10,998,618 edges
  between 8,733,511 graph nodes, 1,095 facility relations, no missing coordinates. `parsed` 19,779,493 rows
  (590 MB gzip), `normalized` 19,779,989 rows (582 MB gzip); 14m45s wall, 3.4 GB peak RSS with 12 workers.
- Licence: **ODbL 1.0**. Attribution "(c) OpenStreetMap contributors" is required; a derived *database* that is
  distributed publicly must be offered under ODbL (share-alike). No credential is needed.

## Pipeline

| Stage | Entry | Output |
| --- | --- | --- |
| `parsed` | `pipeline.py:parse` | gzip JSONL rows `summary`, `feature`, `edge` (with encoded polyline), `node` |
| `normalized` | `pipeline.py:run` | gzip evidence records |

`helpers.py` is a stdlib-only PBF decoder (BlobHeader/Blob framing, zlib blocks, protobuf varints, string tables,
dense nodes, ways, relations, granularity and offsets). `graph.py` runs three passes over independent blocks in
forked workers (`parameters.workers`, default 12):

1. select ways (roads, rail, power lines, pipelines, ferries, facility areas) and facility multipolygons;
2. read coordinates only for referenced nodes (sorted `array` indexes, no per-node dictionaries);
3. split ways at shared nodes and endpoints into edges with haversine length.

California took about 4 minutes for `parsed` and 11 minutes for `normalized` at 1.2 GB peak RSS; US West (2.6x
larger) took 14m45s for both stages at 3.4 GB, so cost scales roughly linearly with extract size.

Samples and imported single JSONL payloads skip `parsed` (empty) and use the original Overpass normalizer.

Road classes: motorway..tertiary (and links), unclassified, residential, living_street, service (excluding parking
aisles, driveways, drive-throughs, emergency access), road, busway. Rail: rail, light_rail, subway, tram,
narrow_gauge, monorail, funicular. Override with `road_highways`, `excluded_service`, `railways`, `power_lines`.

## Evidence

- `osm:node:N` entities (`road_junction` when a road meets the node, else `location`) with `lat`/`lon`
  attributes, `networks` and `way_references`.
- Assertions `road_connects_to`, `rail_connects_to`, `power_line_connects_to`, `pipeline_connects_to`,
  `ferry_connects_to`, one per simplified edge, IDs `osm:way:W:segment:K:network`. The subject-to-object direction is
  always travel-permitted (`oneway=-1` is reversed); `bidirectional` says whether the reverse is also allowed.
  Attributes: `length_m`, `vertices`, `highway`, `maxspeed_kph` (mph converted), `lanes`, and selected `osm_*` tags.
- `osm:feature:{node|way|relation}:N` facilities (`airport`, `port`, `facility`, `infrastructure`, `refinery`) with
  centroid (`mean_of_vertices` for areas), bounding box and selected tags; `identified_by` `iata:XXX` literals.
- Locators: `shard:0/node:N`, `shard:0/way:N`, `shard:0/relation:N`.

Access restrictions, turn restrictions and time-dependent rules are kept as tags only and are not interpreted.

## Rebuild

```sh
wm acquire osm_topology --dry-run
wm acquire osm_topology --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run osm_topology
wm verify osm_topology
python3 -m unittest data/osm_topology/tests/test_pipeline.py
```

## Local files

`artifacts/` holds immutable stage products and `scratch/` temporary work (including the per-run `osm-graph-*`
directory, removed after the parsed stage). Both are ignored by Git.
