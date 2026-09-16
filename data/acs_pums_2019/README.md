# acs_pums_2019

U.S. Census Bureau **ACS 2015-2019 5-year Public Use Microdata Sample**, aggregated to weighted 2010 PUMA-level
statistics for change-over-time comparisons with `acs_pums` (2020-2024). No microdata rows are retained.

## Why a separate dataset

The 2015-2019 files (3.17 GB) plus the 2020-2024 files (3.23 GB) exceed the 5 GiB per-dataset download cap, and the
two releases use different PUMA geographies (2010 vs 2020) and dollar years. The pipeline is the same code as
`acs_pums/pipeline.py` (keep the copies identical), selected by `parameters.acs_end_year = 2019` and
`parameters.puma_vintage = 2010`; column aliases handle the older layout (`ST`, `TYPE`).

## Source and licence

`https://www2.census.gov/programs-surveys/acs/data/pums/2019/5-Year/csv_pus.zip` (2,238,752,642 bytes) and `csv_hus.zip`
(931,800,925 bytes). Public domain; PUMS are disclosure-protected samples (top-coding, data swapping). No credential.

## Rebuild

```sh
wm budget && wm acquire acs_pums_2019 --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run acs_pums_2019 && wm verify acs_pums_2019
python3 -m unittest discover -s data/acs_pums_2019/tests -t data/acs_pums_2019/tests
```

## Evidence

Same metrics and dimensions as `acs_pums` (see its README), with:

- entities `geo:US:puma10:<state FIPS><PUMA>` `within` `geo:US:state:<FIPS>` (2010 PUMAs; not comparable one-to-one with
  2020 PUMAs — use a PUMA crosswalk or aggregate to states/counties for comparisons);
- valid time 2015-01-01 .. 2020-01-01, `dimensions.period = 2015-2019`;
- dollar units in 2019 dollars (`USD_2019`, `USD_2019_per_month`, `attributes.dollar_year = 2019`).
