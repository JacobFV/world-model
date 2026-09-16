# usgs_earthquakes

USGS Earthquake Hazards Program **ComCat** catalog via the FDSN event web service: all events with magnitude >= 2.5,
worldwide, 1990-01 through 2026-09.

## Source and licence

`https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&starttime=...&endtime=...&minmagnitude=2.5` — 441
calendar-month requests (the service caps a query at 20,000 events), ~155 MB. Public domain. No credential; polite
0.5 requests/second with `SEC_USER_AGENT` contact User-Agent. The catalog is revised continuously (automatic ->
reviewed); re-acquire to refresh.

## Rebuild

```sh
wm acquire usgs_earthquakes --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run usgs_earthquakes && wm verify usgs_earthquakes
python3 -m unittest discover -s data/usgs_earthquakes/tests -t data/usgs_earthquakes/tests
```

## Evidence

- Events: `usgseq:event:<id>` with `event_type` from the catalog `type` (`earthquake`, `quarry_blast`, `explosion`, ...),
  `occurred_at` origin time, participant `usgs:eq:<id>`; attributes: place, status, network, updated, magnitude type,
  depth, location quality (nst, gap, rms).
- Observations on `usgs:eq:<id>` at origin time: `earthquake_magnitude` (magnitude, `dimensions.magnitude_type`),
  `hypocenter_depth` (km), `latitude`/`longitude` (degrees, epicenter); uncertainties in `attributes.uncertainty`.
- Rows at or after a window's `endtime` are skipped so boundary events are not duplicated.
