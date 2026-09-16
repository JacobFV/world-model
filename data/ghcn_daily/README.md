# ghcn_daily

NOAA NCEI **GHCN-Daily** station observations for a documented set of U.S. stations: 133 reference stations plus
all U.S. century-long records.

## Station set

1. Reference stations (133): stations in `ghcnd-stations.txt` whose ID starts with `US` and carry the GCOS Surface
   Network flag (`GSN`, 70), plus U.S. Historical Climatology Network first-order stations (`HCN` flag and ID prefix
   `USW`, 78).
2. Long-record stations (821): U.S. stations whose `ghcnd-inventory.txt` entries for TMAX, TMIN and PRCP all start in
   1900 or earlier and extend to 2026 (inventory of 2026-09-15; 730,254,903 bytes of by-station files).

The explicit list is the `files` array in `dataset.json` (one `by_station/<ID>.csv.gz` per station, full period of
record) plus `ghcnd-stations.txt`. Daily observations are emitted for reference stations only
(`parameters.daily_stations = reference`); monthly aggregates are emitted for every station. The earlier 133-station
artifact (195 MB) is retained, so `desired_bytes` covers both artifacts.

## Source and licence

<https://www.ncei.noaa.gov/pub/data/ghcn/daily/>. U.S. station data are public domain (some non-U.S. GHCN sources
restrict redistribution; none are included). Cite Menne et al. (2012). `SEC_USER_AGENT` is sent as contact User-Agent.

## Rebuild

```sh
wm acquire ghcn_daily --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run ghcn_daily && wm verify ghcn_daily
python3 -m unittest discover -s data/ghcn_daily/tests -t data/ghcn_daily/tests
```

## Evidence

- Entities `ghcn:station:<ID>` (`location`; GSN/HCN flags, WMO id) with `within` state assertions and `latitude`,
  `longitude`, `elevation` observations.
- Daily observations from `parameters.daily_from` (1991): `maximum_temperature`, `minimum_temperature`,
  `average_temperature` (degC), `precipitation`, `snowfall`, `snow_depth` (mm); values with a QC flag are excluded, MFLAG
  and SFLAG kept in attributes.
- Monthly aggregates for the full record (`dimensions.frequency = monthly`): temperature means with >= 25 valid days,
  precipitation/snowfall totals with at most 2 missing days (`attributes.valid_days`).
- `helpers.py` parses the fixed-width station file.
