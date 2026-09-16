# fred_cpi

Headline CPI-U (`CPIAUCSL`), core CPI (`CPILFESL`) and the PCE chain-type price index (`PCEPI`), monthly, seasonally
adjusted, with every ALFRED real-time vintage.

## Source and scope

- Full acquisition: `url_list` over the FRED API `series/observations` for the three series with
  `realtime_start=1776-07-04&realtime_end=9999-12-31` (each has fewer than 1,900 vintage dates, so one request each).
  Credential `FRED_API_KEY` (query `api_key`, injected per request, redacted). Rate limit 1.5 req/s, shared with the
  other `fred_*` datasets (`rate_limit.key = api.stlouisfed.org`). `skip_statuses: [400]`.
- History: this dataset used the keyless `fred.stlouisfed.org/graph/fredgraph.csv` until 2026-09-15. Every scripted
  request to that endpoint then timed out (HEAD answered `200` with `content-length: 0`), while the keyed API worked.
  The acquisition was moved to the API. The pipeline still reads fredgraph CSV artifacts (current vintage only) if one
  is supplied.
- Sample (`wm sample fred_cpi`): the dated 2018–2025 CPIAUCSL fredgraph excerpt, unchanged.
- The same three series also appear in `fred_macro_panel`, with identical metric names and units.

## Evidence

`normalized` (gzip evidence JSONL):
- entities `geo:US` and `fred:<series>`;
- observations with `subject=geo:US` and these metrics:
  - `consumer_price_index` and `core_consumer_price_index`, unit `index_1982_1984_100`;
  - `pce_price_index`, unit `index_2017_100`.
- Each observation has monthly `valid_from`/`valid_to` and `dimensions = {series_id, frequency, seasonal_adjustment, vintage}`,
  where `vintage` is the ALFRED `realtime_start`.
- `attributes.source_series`, `series_id`, `realtime_start` and `realtime_end` are set on every observation, and
  `observed_at` equals `realtime_start` (knowledge time). Values not published in a vintage (`.`) are `null` with
  `missing_reason = not_available_in_vintage`.
- Built 2026-09-15: 14,681 observations (CPIAUCSL 3,362, CPILFESL 2,260, PCEPI 9,059 vintage rows), 0.29 MB gzip.

## Licence

FRED API Terms of Use (https://fred.stlouisfed.org/docs/api/terms_of_use.html). The CPI comes from BLS and the PCE price
index from BEA, both public domain US government data. Cite FRED and the originating agency.

## Rebuild

```sh
wm acquire fred_cpi --dry-run
wm acquire fred_cpi --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run fred_cpi && wm verify fred_cpi
python3 -m unittest discover -s data/fred_macro_panel/tests
```

`tests/test_acquisition.py::test_fred_cpi_worked_example_end_to_end`, which is not owned by this dataset, still expects
the old fredgraph `url_list` block and needs updating to the API form (see the group report).
