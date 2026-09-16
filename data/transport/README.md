# transport

National US transport networks: Census TIGER 2024 primary roads and rail lines, BTS NTAD North American Rail
Network links and nodes, National Highway Freight Network, intermodal freight facilities, USACE principal ports
(CY2023 tonnage) and waterway network, MARAD strategic seaports, NGA World Port Index and OpenFlights airline
routes. The `sampling` block still describes the bounded San Francisco Overpass excerpt.

## Sources and licences

| Shard name prefix | Source | Licence |
| --- | --- | --- |
| `tiger2024_primaryroads.zip`, `tiger2024_rails.zip` | Census TIGER/Line 2024 shapefiles | Public domain |
| `ntad_rail_lines`, `ntad_rail_nodes`, `ntad_nhfn`, `ntad_intermodal_*`, `marad_strategic_seaports` | BTS NTAD ArcGIS feature services | Public domain |
| `usace_principal_ports`, `usace_waterway_nodes`, `usace_waterway_links` | USACE (ArcGIS, services7 org `n1YM8pTrFmm7L4hs`) | Public domain |
| `nga_wpi_pub150.csv` | NGA World Port Index (Pub. 150) | Public domain |
| `openflights_routes.dat` | OpenFlights routes (frozen around June 2014) | **ODbL 1.0** (attribution, share-alike for public derived databases) |

No credentials. ArcGIS layers are fetched as GeoJSON pages of 2,000 features ordered by `OBJECTID` with 5-decimal
coordinates; lines and polygons use `maxAllowableOffset=0.0001` (about 10 m) generalization. The `files` list was
generated from live layer counts on 2026-09-15 (counts are in `acquisition.description`) with one trailing page per
layer. Regenerate it if a layer grows by more than 2,000 features. The old `NTAD_Principal_Ports` and
`NTAD_Navigable_Waterway_Lines` services no longer exist; the USACE `Principal_Ports` and `Waterway_Networks`
services replace them.

### ArcGIS error pages and repair (2026-09-15)

ArcGIS sometimes answers a query with HTTP 200 and an error body
(`{"error":{"code":400,...,"Unable to perform query"}}`). The acquisition engine only retries on status codes, so
10 of the 304 pages were stored as error bodies: `ntad_rail_lines` p069, p103, p132, p134, p136, p137, p140 and
`ntad_rail_nodes` p101, p121, p122. The errors were transient. The pages were refetched after checking each had a
full FeatureCollection with the expected feature count, and a new raw artifact
`67b132f692f70a432f1775250d3d1529905fca747e84d74c265a3c03d4112df0` was published. It hardlinks the 294 unchanged
shards, and its receipt `source.repair` lists each repaired shard with the replaced sha256. The superseded artifact
`83a4103e357987e6082d7da2af4970f55302457662d8d32101aa1bc4e27950cd` was retired. The pipeline rejects error pages
(`ArcGIS query page returned an error`), so a bad page cannot silently drop features. After a fresh
`wm acquire transport`, check for this error and refetch before building.

## Evidence

- `tiger:linearid:N` `road` / rail `infrastructure` entities with length, bounding box and endpoints, and
  `road_segment_length` / `rail_line_length` observations (km).
- `ntad:rail_node:N` entities and `rail_connects_to` assertions (`ntad:rail_link:FRAARCID`) with owners, trackage
  rights, track count, network class, length (km and miles) and state.
- `ntad:nhfn:*` `road` entities with `road_segment_length` (mile) and `within geo:US:state:NN`.
- `usace:port:CODE` `port` entities with `port_tonnage_{total,domestic,foreign,imports,exports}` (short_ton, CY2023).
- `usace:waterway_node:N` entities and `waterway_connects_to` assertions with river, class and length.
- `ntad:intermodal:{kind}:OBJECTID` and `marad:strategic_seaport:*` facilities; terminal storage capacity (barrel).
- `wpi:N` `port` entities with harbour attributes, `identified_by unlocode:XXXXX`, and depth/vessel-limit
  observations in metres. WPI encodes unknown numbers as 0, so zeros are not emitted.
- `air_route_to` assertions between `iata:XXX` / `icao:XXXX` airport entities with airline, codeshare, stops and
  equipment. `airport_nodes` links these codes to OurAirports entities through `identified_by`.

## Rebuild

```sh
wm acquire transport --dry-run
wm acquire transport --allow-network        # 304 requests at 1 request/second
WORLD_MODEL_RAW_VERIFY=size wm run transport
wm verify transport
python3 -m unittest data/transport/tests/test_pipeline.py
```

`helpers.py` holds a stdlib shapefile/DBF reader that streams from the ZIP, a GeoJSON page reader and polyline
summaries. See also `docs/data/transport-maritime.md`.
