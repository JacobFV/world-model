# exiobase3

EXIOBASE 3.9.4 global multi-regional input-output tables, industry-by-industry, for
2010, 2014, 2017, 2020 and 2022 (Zenodo record 14614930), normalized to compact evidence
records for input-output dynamics.

## Source and scope

* `files` strategy, one shard per year:
  `https://zenodo.org/records/14614930/files/IOT_<year>_ixi.zip` (2010 585.6 MB, 2014
  572.9 MB, 2017 566.8 MB, 2020 572.5 MB, 2022 504.8 MB; about 2.80 GB total). Each ZIP is a pymrio
  text export: `Z.txt`, `A.txt`, `Y.txt`, `x.txt`, `unit.txt` and satellite folders
  `factor_inputs`, `employment`, `air_emissions`, `water`, `material`, `nutrients`.
* 49 regions (44 countries + 5 rest-of-world regions) × 163 industries = 7,987 regional
  industries per year. Monetary unit in the source is million EUR (current prices of each
  year); values are multiplied to EUR. Compare years in real terms only after deflating.
* The table year is read from each shard's `metadata.json` (fallback: file name) and is
  emitted as `dimensions.year`, in `valid_from`/`valid_to`, and in every record id
  (`exiobase:<year>:...`). Entities are re-emitted per year with year-scoped record ids.
* More years (the record has 1995–2022) can be added as further `files` entries; the
  emission rule below is applied per year, so normalized output grows roughly linearly
  (~54 MB gzip per year vs ~0.5–0.6 GB raw).
* `desired_bytes` (3.35 GB) also covers the superseded 2022-only raw artifact that
  remains on disk and counts toward measured usage.

## Emission rules (bounded output)

The dense matrices (64 M cells each for `Z` and `A`) are not copied. See the docstring of
[pipeline.py](pipeline.py); thresholds are `parameters`:

| Metric | Rule |
| --- | --- |
| `gross_output` | every regional industry with non-zero output (x) |
| `intermediate_supply` | Z[i, j] ≥ `flow_min_meur` (0.1 M EUR) **and** input coefficient Z[i, j]/x[j] ≥ `flow_min_coefficient` (0.0001); `dimensions.purchaser` is the buying regional industry, `attributes.input_coefficient` keeps A[i, j]. For 2022 this keeps 1,072,084 of 31,640,800 non-zero cells, covering 97.1 % of total intermediate transaction value (1 M EUR / 0.001 would keep 250,970 cells, 89.8 %) |
| `final_demand_supply` | per producing regional industry × consuming region (sum of 7 categories) when ≥ `final_demand_min_meur` (0.1 M EUR), plus per category totals over all consuming regions |
| `value_added_component` | all non-zero `factor_inputs` cells (taxes less subsidies, compensation by skill, operating surplus components) |
| `employment_persons`, `employment_hours` | all non-zero `employment` cells (skill × gender) |
| `air_emission` (kg) | non-zero cells of the stressors listed in `emission_stressors` (CO2/CH4/N2O combustion, cement, agriculture, waste) |

Water, material and nutrient extensions and `A.txt` are acquired but not normalized.

## Entities

`exiobase:region:<code>` (`jurisdiction` + `corresponds_to` → `iso3:XXX` for single-country
regions; `aggregate_cohort` for WA/WE/WF/WL/WM), `exiobase:industry:<slug>` (`industry`),
`exiobase:regional_industry:<region>:<slug>` (`business_cohort`, `classified_as` industry,
`within` region).

## Licence

CC BY-SA 4.0. **Share-alike**: tables derived from EXIOBASE must be released under the same
licence; cite Stadler et al. (2018), *Journal of Industrial Ecology* 22(3), and the Zenodo record.

## Rebuild

```sh
python3 -m worldmodel acquire exiobase3 --dry-run
python3 -m worldmodel acquire exiobase3 --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run exiobase3
python3 -m worldmodel verify exiobase3
PYTHONPATH=. python3 data/exiobase3/tests/test_exiobase3.py
```
