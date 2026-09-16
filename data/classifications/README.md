# classifications

Industry, product and occupation classifications and official concordances for joins across CBP, trade, IO and
labour data.

## Source and licence

- NAICS 2022 and 2017 2-6 digit code lists; NAICS 2012->2017 and 2017->2022 concordances (census.gov/naics).
- Census foreign trade `expconcord22.xlsx` (Schedule B) and `impconcord22.xlsx` (HTS) 10-digit concordances to NAICS,
  SITC Rev. 4 and end-use; Schedule B 2025 code list `exp-code.txt` (fixed width).
- BLS SOC 2018 structure `soc_structure_2018.xlsx` (bls.gov requires a contact User-Agent: `BLS_USER_AGENT`).
- Public domain. ~4.9 MB. The 2012 NAICS code list is only published as legacy `.xls` (not read); 2012 codes appear via the
  concordance.

## Rebuild

```sh
wm acquire classifications --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run classifications && wm verify classifications
python3 -m unittest discover -s data/classifications/tests -t data/classifications/tests
```

## Evidence (full mode)

- `industry` entities `naics2012:`, `naics2017:`, `naics2022:` (sectors as `31-33`, `44-45`, `48-49`) with `within` hierarchy
  (6 -> 5 -> 4 -> 3 -> sector); `maps_to` assertions for concordance pieces (`attributes.identical_code`,
  `source_piece_title`).
- `product` entities `scheduleb2022:`, `scheduleb2025:`, `hts2022:` (10-digit) `within` `hs:<HS6>`; `classified_as` to
  `sitc4:`, `enduse:` and NAICS. The 2022 trade concordances use **NAICS 2017** codes (verified by code overlap); the 2025
  Schedule B uses **NAICS 2022**. Trade-only NAICS codes (e.g. `1121XX`, `910000`) are kept as flagged industry entities.
- `occupation` entities `soc2018:` with `within` hierarchy (detailed -> broad -> minor -> major).
- The legacy sample adapter keeps its `naics:2022:` IDs.
