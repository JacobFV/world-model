# ghcn_monthly

NOAA NCEI **Global Summary of the Month** (GSOM, derived from GHCN-Daily): monthly summaries for every U.S. station
with data in at least 30 distinct years.

## Why a separate dataset

`ghcn_daily` downloads full daily records for a documented station set (954 stations). Monthly summaries for all
long-record U.S. stations (~13,000 by the GHCN inventory) come from a different NCEI product and archive, and adding
them to `ghcn_daily` would have changed its download identity and discarded an in-progress acquisition. Station
entities share the `ghcn:station:<ID>` namespace, so both datasets join directly.

## Source and licence

`https://www.ncei.noaa.gov/data/global-summary-of-the-month/archive/gsom-latest.tar.gz` (1,506,691,701 bytes on
2026-09-15; one CSV per station worldwide). U.S. station data are public domain; non-U.S. members are skipped (some
foreign sources restrict redistribution). `SEC_USER_AGENT` is sent as contact User-Agent. No credential.

## Rebuild

```sh
wm budget && wm acquire ghcn_monthly --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run ghcn_monthly && wm verify ghcn_monthly
python3 -m unittest discover -s data/ghcn_monthly/tests -t data/ghcn_monthly/tests
```

## Evidence

- Station selection: member ID starts with `US` and TAVG, TMAX, TMIN or PRCP has values in at least
  `parameters.min_years` (30) distinct years of the station's GSOM file. That yields **12,014 stations** (30 to 176
  years of record, median 62); the GHCN-Daily inventory suggests ~13,000 on a first-to-last-year span, which is a
  looser test than counting years actually present.
- Entities `ghcn:station:<ID>` (`location`; latitude, longitude, elevation, years_with_data, first/last year) with
  `within` `geo:US:state:<FIPS>` parsed from the station name.
- Monthly observations (valid calendar month, `dimensions.frequency = monthly`, `source = gsom`):
  `average_temperature`, `maximum_temperature`, `minimum_temperature` (degC), `precipitation`, `snowfall` (mm),
  `heating_degree_days`, `cooling_degree_days` (degC_days). The raw GSOM attribute string (measurement/quality flags and
  missing-day counts) is kept in `attributes.flags`; blank values are omitted.
- The archive is streamed member by member (stdlib `tarfile`), so memory is bounded by one station file.
