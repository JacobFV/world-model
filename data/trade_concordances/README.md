# trade_concordances

Product and industry concordances, each code namespaced by classification revision.

## Sources and licences

| File | Crosswalk ids | Licence |
| --- | --- | --- |
| Census `impconcord26.xlsx`, `expconcord26.xlsx` (latest year published; 2002–2026 exist) | `census_hts2026_{hs22,naics2022,sitc4,enduse}`, `census_scheduleb2026_*`, `census_hs22_naics2022_{imports,exports}2026` | public domain |
| WITS `Concordance_H5_to_S3.zip`, `Concordance_H6_to_S3.zip` | `wits_hs17_sitc3`, `wits_hs22_sitc3` | WITS terms of use (internal use, cite) |
| WITS `Concordance_H4_to_I3.zip` | `wits_hs12_isic3` | WITS terms of use |
| UNSD `HS2022toHS2017ConversionAndCorrelationTables.xlsx` | `un_hs22_hs17_{conversion,correlation}` | UN terms of use, with attribution |
| UNSD `HS2017toHS1992ConversionAndCorrelationTables.xlsx` | `un_hs17_hs92_{conversion,correlation}` | UN terms of use, with attribution |

The 2026 Census concordances use **NAICS 2022**. This was checked against `worldmodel/reference/naics_2022_codes.csv`:
the files contain codes that exist only in 2022 and none that exist only in 2017, which is recorded in
`parameters.census_naics_revision`. Trade-only NAICS codes are kept and flagged: aggregates such as `1121XX` get
`trade_aggregate_code`, and `910000`, `930000`, `980000` and `990000` get `outside_naics_code_list`. The 2022 files are
already in `classifications`.

**HS→ISIC:** WITS publishes no HS2017 or HS2022 to ISIC archive. `Concordance_H5_to_I3.zip`, `H5_to_I4` and `H6_to_I4`
return an HTML page, and the WITS concordance page lists ISIC targets only for H0–H4. HS2012→ISIC Rev.3 is the newest
one obtainable without circumvention; compose it with `un_hs17_*` or WITS H5→H4 tables if needed.

## Records

- **Crosswalk assertions**
  - Predicate: `within` (HS hierarchy), `classified_as` (Census assignments) or `maps_to`.
  - Namespaces: `hts2026:`, `scheduleb2026:`, `hs92:`, `hs12:`, `hs17:`, `hs22:`, `naics2022:`, `sitc3:`, `sitc4:`,
    `isic3:`, `enduse:`.
  - Weights: 1 when the source code has exactly one target in that table, otherwise null. Splits have no published
    weights.
    - The HS6→NAICS rows derived from 10-digit lines record `tendigit_line_share` as information, not as a weight.
  - Validity: Census rows are valid for `[2026-01-01, 2027-01-01)`. UN and WITS tables are undated.
- **Entities** (`product`, or `industry` for ISIC) for Census 10-digit codes (units, USDA flag, advanced technology
  code), and for WITS source and target codes with descriptions.

## Using with worldmodel.crosswalks

```python
xw.build_crosswalk(records, 'census_hts2026_naics2022').apportion({...}, at='2026-06-01')
walk = load_concordance_csv(path, **xw.write_concordance_csv(records, 'un_hs22_hs17_correlation', path))
walk.check_partition()          # lists HS6 splits that need weights (e.g. trade values) before apportioning
```

`xw` is this directory's `crosswalk_export.py`.

## Rebuild

```sh
python3 -m worldmodel acquire trade_concordances --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run trade_concordances
python3 -m worldmodel verify trade_concordances
python3 -m unittest data/trade_concordances/tests/test_pipeline.py
```
