# census_population

U.S. Census Bureau **Population Estimates Program** (PEP): vintage 2024 (2020-2024) and vintage 2020
(2010-2020) population and components of change.

## Source and licence

Files under <https://www2.census.gov/programs-surveys/popest/datasets/> (~103 MB): `NST-EST2024-ALLDATA.csv`,
`co-est2024-alldata.csv`, `cc-est2024-alldata.csv` (county characteristics), `cbsa-est2024-alldata.csv`,
`sub-est2024.csv` (places and county subdivisions), `nst-est2020-alldata.csv`, `co-est2020-alldata.csv`.
Public domain (U.S. federal work). No credential.

## Rebuild

```sh
wm acquire census_population --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run census_population && wm verify census_population
python3 -m unittest discover -s data/census_population/tests -t data/census_population/tests
```

The pipeline branches on `raw_coverage()['sampled']`: legacy 100-row samples keep the original adapter
(tests/test_normalizer_contracts.py); full files stream through `_run_full`.

## Evidence (full mode)

- Entities: `geo:US`, `geo:US:region:<n>`, `geo:US:division:<n>`, `geo:US:state:<FIPS>`, `geo:US:county:<GEOID5>`,
  `geo:US:cbsa:<code>`, `geo:US:metdiv:<code>`, `geo:US:place:<GEOID7>`, `geo:US:cousub:<GEOID10>`; `within` hierarchy and
  county -> CBSA/metropolitan division delineation assertions.
- Observations (`dimensions.vintage` 2020 or 2024): `population` (1 July; census and estimates base on 1 April with
  `dimensions.basis`), `group_quarters_population`, `births`, `deaths`, `natural_change`, `net_international_migration`,
  `net_domestic_migration`, `net_migration`, `population_change`, `population_change_residual` (people, 1 July Y-1 to
  1 July Y; first year from 1 April) and the matching `*_rate` metrics (per 1000). County characteristics: `population` by
  `sex`, `race` (alone), `hispanic_origin` for all ages and by 5-year `age_group` x `sex` (2020-2024).
- Overlapping vintages are both kept; prefer vintage 2024 for 2020 onwards.
