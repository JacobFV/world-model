# census_relationship_files

U.S. Census Bureau geographic relationship files, normalized into weighted crosswalk assertions for
`worldmodel.crosswalks`.

## Source and licence

About 60 MB of text from <https://www2.census.gov/geo/docs/maps-data/data/> ([2020 relationship files](https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.2020.html)).
The files are US federal government works in the public domain.

| File | Crosswalk id | Weights |
| --- | --- | --- |
| `rel2020/zcta520/tab20_zcta520_county20_natl.txt` | `census_zcta520_county20` | land and total-area part shares, both directions |
| `rel2020/zcta520/tab20_zcta510_zcta520_natl.txt` | `census_zcta510_zcta520` | land and total-area part shares |
| `rel2020/cousub/tab20_cousub20_cousub10_natl.txt` | `census_cousub10_cousub20` | land and total-area part shares |
| `rel2020/tract/tab20_tract20_tract10_natl.txt` | `census_county10_county20_via_tract` | tract parts summed to county pairs |
| `rel/zcta_county_rel_10.txt` | `census_zcta510_county10` | population (primary), housing-unit, land and total-area shares |

The 2020 files publish only area parts, so population weights exist only in the 2010 ZCTA–county file.

**Census has no 2020↔2010 county relationship file.** `rel2020/county/tab20_county20_county10_natl.txt` returns 404,
and the comparability layout page lists no county file. The rel2020, rel2022 and rel2024 directories were also checked.
The county crosswalk is therefore aggregated from the tract file, because tracts nest within counties. Very small shares
there usually come from boundary re-digitization, not real transfers.

## Records

- **Crosswalk assertions** (`predicate: maps_to`): subject and object are `geo:US:zcta:NNNNN`, `geo:US:county:SSCCC` or
  `geo:US:cousub:SSCCCNNNNN`, with `valid_from` set to the target vintage (`2020-01-01`). The 2010 ZCTA–county rows
  cover `[2010-01-01, 2020-01-01)`.
  - `attributes`: `crosswalk`, `source_system`/`target_system` (e.g. `zcta5_2020`, `county_2010`),
    `source_code`/`target_code`, `weight`, `weight_key`, `weight_basis`, `weights`/`reverse_weights` (`{basis: share}`)
    and part areas in m2.
- **`land_area_without_counterpart` observations** (m2): the part of a unit not covered by any unit of the other file,
  e.g. county land outside every ZCTA.

## Using with worldmodel.crosswalks

`crosswalk_export.py` is an adapter; `worldmodel/` needs no changes.

```python
from worldmodel.store import Store
from worldmodel.crosswalks import load_concordance_csv, zcta_county_crosswalk
import importlib.util
spec = importlib.util.spec_from_file_location('xw', 'data/census_relationship_files/crosswalk_export.py')
xw = importlib.util.module_from_spec(spec); spec.loader.exec_module(xw)
store = Store('data'); records = list(store.records(store.latest('census_relationship_files')))
xw.build_crosswalk(records, 'census_zcta520_county20').apportion({'00601': 100})        # ZCTA -> county, land share
xw.build_crosswalk(records, 'census_zcta520_county20', reverse=True)                     # county -> ZCTA
xw.build_crosswalk(records, 'census_zcta510_county10', basis='housing_units')            # dated; pass at='2015-01-01'
load_concordance_csv(path, **xw.write_concordance_csv(records, 'census_county10_county20_via_tract', path))
zcta_county_crosswalk(raw_shard_0_path)   # the shared loader also reads the raw 2020 ZCTA-county shard directly
```

## Rebuild

```sh
python3 -m worldmodel acquire census_relationship_files --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run census_relationship_files
python3 -m worldmodel verify census_relationship_files
python3 -m unittest data/census_relationship_files/tests/test_pipeline.py
```
