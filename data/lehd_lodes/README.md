# lehd_lodes

LEHD Origin-Destination Employment Statistics (LODES version 8, 2020 census blocks) from the
U.S. Census Bureau.

## Source and scope

One `files` acquisition ([dataset.json](dataset.json)), one shard per csv.gz:

* **WAC** (workplace area characteristics), segment `S000` (all workers), job type `JT00`
  (all jobs): 2023 for 48 states + DC. LODES8 publishes Michigan only through 2021 and Alaska
  only through 2016, so those two states use their latest year (`dimensions.lodes_year`).
* **OD main** (home and workplace in the same state), `JT00`, 2023, for California and Texas
  (focus slice from the acquisition plan).

## Normalized evidence (`normalized`, gzip JSONL)

Block-level rows are aggregated while streaming; block records are not re-emitted.

* `jobs_count` (unit `jobs`) with subject `geo:US:tract:<11-digit GEOID>` or
  `geo:US:county:<5-digit FIPS>`, `dimensions.segment_type` in `total`, `worker_age`,
  `monthly_earnings`, `naics_sector` (NAICS 2-digit sector codes such as `31-33`).
  Zero-valued segments are omitted; the total is always present.
  `attributes.blocks_aggregated` records how many blocks were summed.
* `commuting_jobs` (primary jobs, unit `jobs`) with the **workplace** geography as subject and
  `dimensions.home_geography` for the residence: tract-to-tract totals
  (`aggregation: census_tract_pair`; pairs below `parameters.od_min_tract_pair_jobs` are
  collapsed into one `other_tracts_below_threshold` residual per workplace tract; set to 5,
  which keeps 1.23M of 9.84M CA+TX tract pairs and 57% (CA) / 63% (TX) of commuting jobs at
  tract level, bounding output near raw size; every job is still in the county pairs) and county-to-county flows with all age, earnings and industry-group
  segments (`aggregation: county_pair`).
* Entities: states, counties and tracts appearing in each shard, with `within` assertions
  (tract -> county -> state -> `geo:US`).
* Validity: the LODES reference year as `[YYYY-01-01, YYYY+1-01-01)`.

The OD pipeline requires the source file to be grouped by workplace block (true for the
published files) and fails loudly otherwise.

## Caveats and licence

Public domain (U.S. federal government work); cite LEHD. LODES worker counts are
noise-infused for confidentiality (block-level counts, especially small ones, are not exact),
so use tract or county aggregates. Federal jobs coverage and the Michigan/Alaska year gaps are
documented in the LODES 8 technical documentation
(https://lehd.ces.census.gov/data/lodes/LODES8/LODESTechDoc8.3.pdf).

## Rebuild

```sh
wm acquire lehd_lodes --dry-run
wm acquire lehd_lodes --allow-network   # ~230 MB (desired_bytes 250 MB)
WORLD_MODEL_RAW_VERIFY=size wm run lehd_lodes
wm verify lehd_lodes
python3 -m unittest data/lehd_lodes/tests/test_lehd_lodes.py
```
