# noaa_storm_events

NOAA NCEI **Storm Events Database**, all available years 1950-2026: details, fatalities and locations. Event-type
coverage is uneven before 1996 (1950-1954 tornadoes only; 1955-1995 tornado, thunderstorm wind and hail); all 48
standardized event types are recorded from 1996.

## Source and licence

231 yearly `StormEvents_{details,fatalities,locations}-ftp_v1.0_dYYYY_cYYYYMMDD.csv.gz` files (353,256,536 bytes listed on
2026-09-15) from <https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/>. Public domain. NCEI republishes files
with new creation dates (`_c` suffix); refresh the `files` list from the directory listing before re-acquiring.
Uses `SEC_USER_AGENT` as a contact User-Agent. The earlier 1996-2026 artifact (343.7 MB) is retained, so
`desired_bytes` covers both artifacts.

## Rebuild

```sh
wm acquire noaa_storm_events --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run noaa_storm_events && wm verify noaa_storm_events
python3 -m unittest discover -s data/noaa_storm_events/tests -t data/noaa_storm_events/tests
```

## Evidence

- Events: one per EVENT_ID (`event_type` = slug of EVENT_TYPE, e.g. `tornado`, `flash_flood`), `occurred_at` in UTC from
  local time + CZ_TIMEZONE, participants `noaa:storm_event:<EVENT_ID>` and the county `geo:US:county:<SSCCC>` (CZ_TYPE C) or
  NWS zone `noaa:zone:<SS>:<Z|M>:<ZZZ>`; attributes hold episode, magnitude, EF scale, tornado path, begin/end lat/lon and all
  casualty/damage values (including zeros). Fatality records are `storm_fatality` events (age, sex, location, direct/indirect).
- Observations (non-zero only): `storm_deaths`, `storm_injuries` (people, `dimensions.attribution` direct/indirect),
  `storm_damage_property`, `storm_damage_crops` (nominal USD parsed from K/M/B strings); locations file -> `latitude` /
  `longitude` per `location_index` (valid time = event month).
- Narratives contain unescaped quotes, so the pipeline uses a tolerant local CSV reader (same locator convention).
