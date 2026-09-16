# epa_aqs_daily

EPA **Air Quality System** pre-generated daily summary files, 2010-2024, aggregated to county-day means for field
dynamics: PM2.5 (88101, FRM/FEM), ozone (44201, daily maximum 8-hour average), NO2 (42602), SO2 (42401), CO (42101)
and PM10 (81102).

## Source and licence

`https://aqs.epa.gov/aqsweb/airdata/daily_<parameter>_<year>.zip` for the six parameters (90 files, 289,988,152 bytes
on 2026-09-15). Public domain (U.S. EPA). No credential; 0.5 requests/second with `SEC_USER_AGENT` contact User-Agent.
EPA regenerates the files periodically and recent years change until data are certified.

## Rebuild

```sh
wm acquire epa_aqs_daily --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run epa_aqs_daily && wm verify epa_aqs_daily
python3 -m unittest discover -s data/epa_aqs_daily/tests -t data/epa_aqs_daily/tests
```

## Evidence

- Entities: monitored counties `geo:US:county:<GEOID5>`.
- Observations per county and local day (valid [day, day+1)): `pm25_daily_mean` (ug/m3, local conditions),
  `ozone_daily_max_8hr` (ppm), `no2_daily_mean` (ppb), `so2_daily_mean` (ppb), `co_daily_mean` (ppm),
  `pm10_daily_mean` (ug/m3, standard conditions); `dimensions.pollutant`, `parameter_code`,
  `aggregation = county_mean_of_monitors`; attributes `monitors`, `county_max` and, for daily means,
  `mean_daily_max` (mean of monitors' 1st Max Value). Evidence cites the first contributing monitor row.
- Monitor rows repeat once per pollutant standard; the pipeline keeps one value per (site, POC, day, sample duration),
  preferring rows not flagged `Excluded` for exceptional events (wildfire-influenced `Included` values are kept).
  Ozone uses only 8-hour running-average rows; NO2, SO2 and CO use 1-hour sample rows (daily mean of hourly values).
