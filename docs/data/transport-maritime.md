# Transport, logistics and maritime datasets

Cross-dataset notes for `osm_topology`, `osm_us_south`, `osm_us_midwest`, `osm_us_northeast`, `transport`,
`airport_nodes`, `freight`, `marine_ais`, `bts_airline_t100` and `global_fishing_watch`. Each dataset README documents its own source, licence and rebuild
steps.

Acquisition state (`wm catalog`, 2026-09-15): the four OSM datasets, `transport`, `airport_nodes`, `freight` and
`marine_ais` have published normalized artifacts. `bts_airline_t100` is `manual_download_required` and
`global_fishing_watch` is `awaiting_credentials`; neither has been acquired, so the identifiers, metrics and event
streams attributed to them below describe what their pipelines will emit, not data you can query today.

## Identity conventions and joins

| Concept | ID | Emitted by | Join notes |
| --- | --- | --- | --- |
| US state | `geo:US:state:NN` (FIPS) | freight (entities), transport (`within`, `state` attributes), airport_nodes (`same_as` from `iso3166-2:US-XX`) | Same namespace as the Census datasets |
| Country | `iso3:XXX` | airport_nodes | OurAirports alpha-2 codes are mapped locally; Kosovo is `iso3:XKX` |
| Subnational region | `iso3166-2:XX-YY` | airport_nodes | OurAirports uses `XX-U-A` for unassigned regions |
| Airport (reference) | `ourairports:ID` | airport_nodes | Carries `identified_by` literals `iata:XXX` / `icao:XXXX` |
| Airport (code-keyed) | `iata:XXX`, `icao:XXXX` | transport (OpenFlights routes), bts_airline_t100 (subjects) | Resolve to `ourairports:ID` through airport_nodes `identified_by` values; code entities are not merged automatically |
| Vessel | `mmsi:NNNNNNNNN` | marine_ais, global_fishing_watch | `identified_by imo:NNNNNNN` in marine_ais; GFW falls back to `gfw:vessel:ID` for non-MMSI ssvids |
| Port | `usace:port:CODE`, `wpi:N`, `marad:strategic_seaport:*`, `osm:feature:*:N` (category port) | transport, osm_topology | WPI has `identified_by unlocode:XXXXX`; USACE codes and WPI numbers are not cross-walked |
| FAF geography | `faf5:zone:NNN` (`within` state), `faf5:foreign_region:80N`, `faf6:foreign_region:80N` | freight | FAF zones are CFS areas or state remainders; no county crosswalk is included |
| Commodity | `sctg2:NN` | freight | SCTG 2-digit, not HS or NAICS; a crosswalk is needed for trade datasets |
| Road/rail graph nodes | `osm:node:N` (contiguous US across four Geofabrik regions; Alaska, Hawaii and the territories are not covered — the US West extract's 8.78M nodes contain none in Hawaii and 4 west of 130°W), `ntad:rail_node:N` (national), `usace:waterway_node:N` | osm_topology (US West), osm_us_south, osm_us_midwest, osm_us_northeast, transport | OSM IDs are global, so the regional graphs join at shared border nodes. NTAD and USACE are separate graphs; match spatially if a multimodal network is needed |

## Graph semantics

- The OSM network is split by Geofabrik region: `osm_topology` = US West, `osm_us_south`, `osm_us_midwest` and
  `osm_us_northeast`. Each region extract keeps complete ways, so ways crossing a region boundary appear in both
  neighbouring datasets with identical record IDs (`osm:way:W:segment:K:network`, `osm:node:N`). Union the regions
  and deduplicate by record ID. Their code is identical dataset-local copies of the stdlib PBF decoder and graph
  builder.

- `*_connects_to` assertions are directed from subject to object in a permitted travel direction.
  `attributes.bidirectional` (or `bidirectional: true` for NTAD rail and USACE waterway links) marks reverse
  travel. OSM `oneway=-1` edges are already reversed.
- Edge lengths are in attributes (`length_m` for OSM, `length_km` / `length_miles` for NTAD and USACE). Speeds
  (`maxspeed_kph`) are posted limits where tagged; there is no traffic or travel-time model.
- OSM edges are simplified chains between intersections and way endpoints, so interior shape points are dropped
  from normalized evidence. The `parsed` stage keeps an encoded polyline (precision 1e-5) for each edge.

## Flows and activity series (for estimation)

| Dataset | Metric | Unit | Frequency | Notes |
| --- | --- | --- | --- | --- |
| freight | `freight_tons`, `freight_value`, `freight_value_current`, `freight_ton_miles` | thousand_short_tons, million_USD_2017, million_USD_current, million_ton_miles | annual 2017-2024 plus forecasts 2030-2050 | Origin subject, `dimensions.destination`/`mode`/`commodity` (`sctg2:NN`); one record per year with valid time set to that year; `attributes.projection` is true for FAF forecast years; modeled estimates; zero values omitted; aggregated over trade type and distance band |
| transport | `port_tonnage_{total,domestic,foreign,imports,exports}` | short_ton | CY2023 | USACE principal ports |
| marine_ais | `vessel_track_segments`, `vessel_tracked_hours`, `vessel_track_distance`, `vessel_max_draft` | segments, hours, km, m | monthly: 2025-01, 2025-07, 2025-12 in acquisition priority order (seasonal sample, not a continuous series; see the dataset receipt for months held) | US coastal receivers only; per vessel-month |
| bts_airline_t100 | `air_passengers`, `air_freight`, `air_mail`, `seats_offered`, `departures_performed`, ... | passengers, lb, seats, departures | monthly | Manual imports only |

Event streams: `vessel_track_segment` and `vessel_stationary_period` (marine_ais); `vessel_port_visit`,
`vessel_encounter` and `vessel_loitering` (global_fishing_watch, `awaiting_credentials`, nothing acquired).
Stationary periods are port-call
candidates: join their `center` to `wpi:*` / `usace:port:*` coordinates with a distance threshold.

## Licences to respect when combining

- ODbL 1.0 (share-alike for public derived databases): `osm_topology`, `osm_us_south`, `osm_us_midwest`,
  `osm_us_northeast`, and the OpenFlights routes in `transport`.
- CC BY-NC 4.0 (non-commercial): `global_fishing_watch` (not acquired).
- Public domain or CC0: TIGER, NTAD, USACE, MARAD, NGA, FAF, MarineCadastre, OurAirports, BTS T-100.

## Optional dependency

`marine_ais` needs `pyarrow` to read GeoParquet (imported lazily; install with `pip install --user pyarrow`).
Every other reader is stdlib-only: the OSM PBF decoder, shapefile/DBF, GeoJSON pages, FAF ZIP CSV and the metadata
XLSX (through `worldmodel.workbooks`).
