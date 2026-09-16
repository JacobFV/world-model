# marine_ais

MarineCadastre US coastal AIS vessel tracks for seasonal months of 2025 (January, July, December).

## Source and licence

Monthly GeoParquet files from `https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/aistrack/`
(index: `index-aistrack.html`, 24 monthly files for 2024-01..2025-12), one shard per month, listed in priority order:

| Priority | Month | File | Bytes | Season |
| --- | --- | --- | --- | --- |
| 1 | 2025-01 | `ais-track-2025-01.parquet` | 1,143,946,313 | winter |
| 2 | 2025-07 | `ais-track-2025-07.parquet` | 1,781,213,231 | peak summer |
| 3 | 2025-12 | `ais-track-2025-12.parquet` | 1,395,724,810 | winter / holiday peak |

Total 4,320,884,354 bytes. No further month fits under the 5 GiB per-dataset cap (10% of the 50 GiB shared budget);
the smallest remaining 2025 month (February) is 1,081,091,902 bytes, so spring and fall are not represented.

All three months were acquired (artifact `9077691c8351…`, 4,320,884,354 bytes, complete). The engine reserves one
file at a time, so a tight allocation stops between months: the first run published January only
(`complete: false`, `stop_reason: budget`) and `wm acquire marine_ais --allow-network --resume` added July and
December once the budget allowed. The pipeline processes whatever monthly shards the artifact contains, so a partial
artifact still builds; check the raw receipt for the months held.

Build (three months): 7,399,666 normalized records (389 MB gzip) in 11m03s at 3.6 GB peak RSS - 4,983,666 track
segments, 1,788,210 stationary periods, 70,514 vessels, 16,213 IMO identifiers. Records per period: 1,626,502
(2025-01), 2,376,261 (2025-07), 1,397,611 (2025-12). The superseded January-only artifact `16068df6df07…` and its
build `5536ae77e574…` were retired once this build verified.

- Publisher: NOAA Office for Coastal Management, BOEM and USCG. CC0 / US government work. No credential is needed.
- Coverage is limited to US coastal AIS receivers; vessels outside coverage or not transmitting are absent.

## Format and dependency

Each GeoParquet row is a cleaned track segment (at most one UTC day) with MMSI, name, IMO, call sign, AIS vessel
type, navigation status, length, width, draft, cargo, transceiver class, start/end time and a WKB LineString.
Reading requires the optional **pyarrow** package, imported lazily (`pip install --user pyarrow`). WKB parsing
and distance use only the stdlib.

## Evidence

- `vessel_track_segment` events `marinecadastre:track:YYYYMM:ROW` (participants `mmsi:N`): start, end, duration,
  vertices, haversine `distance_km`, bounding box, start/end position, nav status, vessel type, draft.
- `vessel_stationary_period` events (`...:stationary`) for moored/anchored segments (AIS status 5/1) or segments
  that stay within `stationary_max_km` (1 km) for at least `stationary_min_minutes` (60). These are port-call and
  anchorage candidates; matching them to ports (e.g. `transport` `wpi:*` / `usace:port:*`) is left to consumers.
- One `mmsi:N` `vessel` entity per MMSI across all months: names, static fields, first/last seen,
  `source_periods`; `identified_by imo:NNNNNNN`; `vessel_length` / `vessel_width` (m, first reported value).
- Vessel-month observations (`valid_from`/`valid_to` = calendar month, `dimensions.source_period`):
  `vessel_track_segments` (segments), `vessel_tracked_hours` (hours), `vessel_track_distance` (km),
  `vessel_max_draft` (m).

Full geometry remains only in the raw artifact (`shard:N/row:R`). Memory holds one Arrow batch plus per-vessel and
per-vessel-month summaries.

## Rebuild

```sh
pip install --user pyarrow
wm budget
wm acquire marine_ais --allow-network          # stops resumably on budget between months
wm budget reconcile && wm acquire marine_ais --allow-network --resume
WORLD_MODEL_RAW_VERIFY=size wm run marine_ais
wm verify marine_ais
python3 -m unittest data/marine_ais/tests/test_pipeline.py   # skipped without pyarrow
```
