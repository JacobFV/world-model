# ibtracs

NOAA NCEI **IBTrACS v04r01** global tropical cyclone best tracks, all storms since 1980 (merged "list" CSV).

## Source and licence

`ibtracs.since1980.list.v04r01.csv` (~144 MB) from
<https://www.ncei.noaa.gov/products/international-best-track-archive>. Public domain (NOAA); cite Knapp et al. (2010)
and the v04r01 data release. Recent seasons are provisional (`TRACK_TYPE` PROVISIONAL / US-PROVISIONAL).
Uses `SEC_USER_AGENT` as a contact User-Agent.

## Rebuild

```sh
wm acquire ibtracs --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run ibtracs && wm verify ibtracs
python3 -m unittest discover -s data/ibtracs/tests -t data/ibtracs/tests
```

## Evidence

- Entities: storms `ibtracs:storm:<SID>` (`entity`, `attributes.hazard_type = tropical_cyclone`, name, season, basin, ATCF id),
  basins `ibtracs:basin:<code>` (`ocean`); one `tropical_cyclone_track_start` event per storm.
- Observations per track point (valid 3 h from ISO_TIME, `dimensions.track_type/nature/interpolated/basin`): `latitude`,
  `longitude` (degrees), `distance_to_land` (km), `max_sustained_wind` (kt; `dimensions.source` wmo or usa, WMO agency
  averaging periods differ), `min_central_pressure` (hPa), `saffir_simpson_category` (USA_SSHS, -5..5 codes),
  `storm_translation_speed` (kt), `storm_heading` (degrees).
