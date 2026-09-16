# usda_agriculture

USDA NASS **Quick Stats crops sector**: survey and census crop statistics (area planted
and harvested, yield, production, prices received, stocks, sales, operations) for the
nation, states and counties.

## Source

- Bulk directory: `https://www.nass.usda.gov/datasets/`. The file
  `qs.crops_YYYYMMDD.txt.gz` (tab-delimited gzip, about 1.13 GB compressed and 23.9M rows)
  is rebuilt daily and **older builds are removed**. Before a new acquisition, update
  `acquisition.files[0].url` and `name` in `dataset.json` to the current build date from
  the listing. A changed URL gives a new acquisition. Resume with `--resume` on the same
  day only.
- No credential is needed for bulk files. The Quick Stats API (`USDA_NASS_API_KEY`) is used
  only by the legacy 100-row sample block.
- Not acquired: `qs.economics_*` (557 MB, farm income, expenses and land values),
  `qs.animals_products_*`, `qs.environmental_*` and `qs.census2022`. These are stretch
  sectors; add them as extra `files` entries if the budget allows.

## Normalization (`normalized`, gzip JSONL)

Two code paths share `pipeline.py`:

- **Sample** (single JSONL payload): the original annual state corn yield adapter. It is
  unchanged and exercised by `tests/test_domain_sources.py`.
- **Full** (sharded bulk artifact). The file is streamed and filtered by `parameters`:
  `sources` SURVEY and CENSUS; `agg_levels` NATIONAL, STATE and COUNTY; `domains`
  TOTAL (census NAICS, economic-class and other breakdowns are excluded); `min_year`
  2000. Supported periods are ANNUAL (YEAR, MARKETING YEAR, YEAR - <month> FORECAST or
  ACREAGE), POINT IN TIME (FIRST, MID or END OF <month>) and single MONTHLY months. Weekly
  crop-progress rows, multi-month ranges and SEASON periods are skipped.

Evidence:

- Entities: `geo:US` (`country`), `geo:US:state:SS` (`state`) and
  `geo:US:county:SSCCC` (`county`), linked by `within` assertions. County code 998,
  "other (combined) counties", becomes
  `nass:geo:US:state:SS:asd:NN:other_counties` (`jurisdiction`, aggregate).
  Commodities are `nass:commodity:<slug>` (`commodity`, with sector and group).
- Observations: subject = geography. `metric` = slug of STATISTICCAT_DESC
  (`area_planted`, `area_harvested`, `yield`, `production`, `price_received`,
  `stocks`, `sales`, `operations` ...). `unit` = the NASS UNIT_DESC exactly (`ACRES`,
  `BU / ACRE`, `BU`, `$ / BU`, `TONS`, `CWT`, `$`, `OPERATIONS`, ...). Dimensions:
  `commodity`, `series` (`nass:series:<hash of SHORT_DESC + DOMAINCAT_DESC>`, a stable
  handle for one published series), `program` (survey or census), `frequency`
  (annual, monthly or point_in_time), `reference_period`, and `class`,
  `production_practice` and `utilization_practice` when they are not "ALL".
  Attributes: `short_desc`, `cv_percent`, `suppression_code`.
- Suppression codes `(D)`, `(Z)`, `(S)`, `(NA)`, `(X)` and similar become `value: null`,
  with a `missing_reason` such as
  `nass_suppressed_withheld_to_avoid_disclosing_individual_operations`. They are never
  zero.
- Marketing-year values use calendar-year bounds as an approximation. Forecast releases
  (YEAR - AUG FORECAST ...) are separate observations, told apart by
  `reference_period`. National and state values are marked `aggregate`.
- Exact duplicate publications of the same series, geography and period are emitted once.

## Licence

US federal government work, public domain. Please cite USDA NASS Quick Stats.

## Rebuild

```sh
wm acquire usda_agriculture --dry-run
wm acquire usda_agriculture --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run usda_agriculture
wm verify usda_agriculture
python3 -m unittest data/usda_agriculture/tests/test_pipeline.py tests/test_domain_sources.py
```

`artifacts/` and `scratch/` are ignored by Git.
