# eia_grid_operations

This dataset holds U.S. electric system operating data from Form EIA-930, taken from the EIA bulk
file `EBA.zip` (a single JSON-lines member `EBA.txt` with one line per series and every hourly
point; data older than 7 days). It covers balancing authorities (BAs), BA subregions and EIA
regions such as US48, CAL and TEX:

- demand
- day-ahead demand forecast
- net generation (total and by fuel)
- total interchange
- bilateral interchange
- estimated CO2 emissions for some respondents

Series run from 2015-07, and the fuel mix from 2018-07.

## Scope and normalization

The source has about 90 million UTC hourly points. Emitting every hour would be many times the raw
size, so the `normalized` stage (gzip evidence JSONL) emits two things:

- **Daily UTC aggregates** for every UTC-hourly (`.H`) series. Each is the sum of reported hours,
  with `attributes.hours_reported` and `complete_day`. Demand also gets a daily peak,
  `electricity_demand_peak_hourly` (MW).
- **Hourly observations** for the last `hourly_recent_days` days before the retrieval date.
  This is a parameter with a default of 35. Override it with
  `wm run eia_grid_operations --parameters '{"hourly_recent_days": 90}'`.

Local-time duplicate series (`.HL`) are skipped. EIA-930 timestamps are hour-ending UTC, so the
hourly record for `20260102T03` covers `[02:00, 03:00)Z`. Days are UTC calendar days of the
interval start. Blank hours are `value: null` with `missing_reason: source_blank`, and they are
excluded from the daily sums.

Entities:

- `eia:ba:<code>` is a balancing authority or respondent (organization).
- `eia:region:<code>` is an EIA-930 region (location, `aggregate: true`).
- `eia:ba:<BA>:subregion:<code>` is a subregion (location, `within` its BA).
- `eia:interchange:<from>:<to>` is a resource_flow with `flow_source`, `flow_destination` and
  `transports_resource commodity:electricity`. Positive values mean net flow from the first area
  to the second.

Metrics (`dimensions.frequency` is `daily` or `hourly`; `dimensions.series_id`; `dimensions.fuel`
uses EIA fuel codes such as `col`, `ng`, `nuc`, `oil`, `wat`, `sun`, `wnd`, `oth`):

- `electricity_demand` (MWh)
- `electricity_demand_forecast_day_ahead` (MWh)
- `electricity_net_generation` (MWh)
- `electricity_total_interchange` (MWh)
- `electricity_interchange` (MWh)
- `electricity_demand_peak_hourly` (MW)
- `power_sector_co2_emissions` (t CO2)

## Licence

This is a U.S. federal government work in the public domain. Cite "U.S. Energy Information
Administration, Form EIA-930".

## Rebuild

```sh
python3 -m worldmodel acquire eia_grid_operations --dry-run
python3 -m worldmodel acquire eia_grid_operations --allow-network   # ~690 MB, single file
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run eia_grid_operations
python3 -m worldmodel verify eia_grid_operations
python3 -m unittest data/eia_grid_operations/tests/test_pipeline.py
```

No credentials are required. EIA rebuilds `EBA.zip` daily, so a resumed download of a changed file
restarts the shard. `artifacts/` and `scratch/` are ignored by Git.
