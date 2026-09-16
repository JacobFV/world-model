# cbsa_delineations

OMB metropolitan and micropolitan delineations (List 1: county to CBSA, metropolitan division and CSA) for three
vintages. Each vintage's memberships are dated so they can be queried at a point in time.

## Source and licence

The files come from <https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/> and are public
domain (OMB and U.S. Census Bureau).

| File | Bulletin | Validity |
| --- | --- | --- |
| `2018/delineation-files/list1_Sep_2018.xls` | OMB 18-04 | 2018-09-14 to 2020-03-06 |
| `2020/delineation-files/list1_2020.xls` | OMB 20-01 | 2020-03-06 to 2023-07-21 |
| `2023/delineation-files/list1_2023.xlsx` | OMB 23-01 | 2023-07-21 onward |

Census publishes the 2018 and 2020 lists only as legacy `.xls`; `.xlsx` requests return 404. `helpers.py` therefore
includes a stdlib BIFF8/OLE2 reader, which matched LibreOffice's CSV export cell-for-cell on both files. The 2023
`.xlsx` is read with `worldmodel.workbooks`. The 2023 vintage uses Connecticut planning regions (`09110`–`09190`) as
county-equivalents.

## Records

Each vintage `v` gets `valid_from` and `valid_to` from the table above.

- **Membership assertions** (`predicate: within`, weight 1):
  - `omb_county_cbsa_<v>`: county to CBSA, with attributes `central_outlying`, `cbsa_type`, and county and state names
  - `omb_county_metdiv_<v>`: county to metropolitan division
  - `omb_county_csa_<v>`: county to CSA
  - `omb_cbsa_csa_<v>`: CBSA to CSA
  - `omb_metdiv_cbsa_<v>`: metropolitan division to CBSA
- **Literal assertions:**
  - `cbsa_type`: `metropolitan_statistical_area` or `micropolitan_statistical_area`
  - `cbsa_county_status`: `central` or `outlying`
- **Entities** (`location`, labelled with that vintage's title): `geo:US:cbsa:NNNNN`, `geo:US:metdiv:NNNNN` and
  `geo:US:csa:NNN`.

## Using with worldmodel.crosswalks

```python
walk = xw.build_crosswalk(records, 'omb_county_cbsa_2020')      # dated: pass at=
walk.apportion({'36005': 1.0}, at='2021-06-01')
load_concordance_csv(path, **xw.write_concordance_csv(records, 'omb_county_cbsa_2023', path))  # single vintage, undated
```

`xw` is `data/cbsa_delineations/crosswalk_export.py`; see `census_relationship_files/README.md` for how to load it.

## Rebuild

```sh
python3 -m worldmodel acquire cbsa_delineations --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run cbsa_delineations
python3 -m worldmodel verify cbsa_delineations
python3 -m unittest data/cbsa_delineations/tests/test_pipeline.py
```
