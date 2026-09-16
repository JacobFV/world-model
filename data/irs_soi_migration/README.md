# irs_soi_migration

IRS Statistics of Income **U.S. population migration data**: county-to-county and state-to-state flows of
returns (households), individuals (exemptions) and adjusted gross income, filing-year pairs 2017-18 .. 2022-23.

## Source and licence

`countyinflow{YYyy}.csv`, `countyoutflow{YYyy}.csv`, `stateinflow{YYyy}.csv`, `stateoutflow{YYyy}.csv` from
<https://www.irs.gov/statistics/soi-tax-stats-migration-data> (~55 MB). Public domain. No credential. Some files
are Latin-1 and later years drop leading zeros in FIPS codes (handled).

## Rebuild

```sh
wm acquire irs_soi_migration --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run irs_soi_migration && wm verify irs_soi_migration
python3 -m unittest discover -s data/irs_soi_migration/tests -t data/irs_soi_migration/tests
```

## Evidence

- `flow` entities `irs:migration_flow:<level>:<origin>-<destination>` (one per pair across years) with `flow_source` /
  `flow_destination` assertions to `geo:US:county:`, `geo:US:state:` or IRS pseudo-geographies
  `irs:migration_aggregate:<code>` (`aggregate_cohort`: 96 total, 97 US / same state / different state, 98 foreign,
  57-59 "other flows").
- Observations on the flow: `migration_returns` (returns), `migration_individuals` (people), `migration_agi` (USD, from
  thousands), valid from 1 Jan of the first filing year to the end of the second, with `dimensions.origin/destination/
  perspective/flow_type` (`migration`, `non_migrant`, `aggregate`).
- County-to-county cells are taken from inflow files; outflow files contribute only their aggregate rows (their pair cells
  duplicate inflow). Cells with fewer than 20 returns are published as -1 and become `missing_reason =
  suppressed_fewer_than_20_returns`.
