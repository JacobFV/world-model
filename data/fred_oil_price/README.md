# fred_oil_price

Crude Oil Prices: West Texas Intermediate (WTI) - Cushing, Oklahoma (`DCOILWTICO`), daily, percent unless noted, from FRED/ALFRED.

## Source and scope

- Publisher: Federal Reserve Bank of St. Louis (FRED/ALFRED); originating source: U.S. Energy Information Administration.
- Full acquisition (`wm acquire fred_oil_price --allow-network`): FRED API `series/observations` for `DCOILWTICO` over the whole
  real-time history (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`), split into 1 window(s) because
  FRED rejects JSON requests spanning more than 2,000 vintage dates. One shard per window.
- Credential: `FRED_API_KEY` (query `api_key`, injected per request and redacted). Rate limit 1.5 req/s shared with
  the other fred_* datasets (`rate_limit.key = api.stlouisfed.org`; FRED allows ~120/min).
- Sample (`wm sample fred_oil_price`): the dated 2025 Q1 fredgraph.csv excerpt, unchanged.
- The last window grows by ~250 vintage dates a year; when it approaches 2,000 split it (see
  `wm acquire fred_oil_price --dry-run` and FRED `series/vintagedates`).

## Evidence

`normalized` (gzip evidence JSONL):
- entities `geo:US` (country) and `fred:DCOILWTICO` (economic_series) plus assertion `fred:DCOILWTICO describes_location geo:US`;
- observations `subject=geo:US`, `metric=oil_price`, `unit=USD/barrel`, `valid_from`/`valid_to` = the observation day
  (half-open), `dimensions = {series_id, frequency, seasonal_adjustment, vintage}` where `vintage` is the ALFRED
  `realtime_start`; `attributes.realtime_start/realtime_end` give the real-time period during which the value was
  current, and `observed_at` is `realtime_start` (knowledge time for point-in-time backtests). Real-time periods split at
  request-window boundaries are merged back. Values `.` (not available in that vintage) are `null` with
  `missing_reason`.
- ALFRED coverage for this series starts at its first vintage date; history published before ALFRED began carries that
  first vintage date as `realtime_start`.

The sample adapter (legacy JSONL sample rows) is retained for single-payload inputs.

## Licence

FRED API Terms of Use (https://fred.stlouisfed.org/docs/api/terms_of_use.html); the series data are produced by US government agencies (public domain) but FRED terms apply to API use. Internal use; cite FRED and the originating agency.

## Rebuild

```sh
wm acquire fred_oil_price --dry-run
wm acquire fred_oil_price --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run fred_oil_price
wm verify fred_oil_price
```
Tests: `python3 -m unittest discover -s data/fred_macro_panel/tests` (shared FRED tests cover this dataset).
