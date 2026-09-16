# conflict_reference

International-system reference data from the Correlates of War (COW) project and ATOP.

## Source files (one shard each)
| File | Content | Output |
| --- | --- | --- |
| `States2024.zip` (statelist2024.csv) | COW state system membership spells | `iso3:` / `cow:state:<ccode>` countries; `member_of cow:system:interstate` with valid time |
| `NMCv7.zip` (nested `NMC-v7-abridged.zip`) | National Material Capabilities v7, 1816-2022 | observations `military_expenditure` (thousand_usd_current), `military_personnel`, `total_population`, `urban_population` (thousand_people), `iron_steel_production` (thousand_tonnes), `primary_energy_consumption` (thousand_coal_ton_equivalent), `composite_index_national_capability` (share_of_system); -9 -> null `source_missing_-9` |
| `DirectContiguity320.zip` (contdir.csv) | Direct contiguity 1816-2016 | `contiguous_with` assertions (undirected, conttype 1-5, valid time; 201612 end = right-censored) |
| `version4.1_csv.zip` (alliance_v4.1_by_member.csv) | COW Formal Alliances v4.1 | `cow:alliance:<id>` (`military_alliance`) + `member_of` with membership dates |
| `MID-5-Data-and-Supporting-Materials.zip` (MIDA/MIDB 5.0) | Militarized interstate disputes 1816-2014 | events `cow_mid:dispute` with participant states and side/hostility details |
| `Inter-StateWarData_v4.0.csv` | Inter-state wars | events `cow_war:interstate` with sides and battle deaths |
| `atop_5.1__.csv_.zip` (atop5_1a, atop5_1m) | ATOP 5.1 alliances and member phases | `atop:alliance:<id>` + `member_of` |

COW codes map to ISO3 via `country_codes.json` (derived from V-Dem v16 COWcode plus manual microstate/GW overrides);
historical states without ISO codes keep `cow:state:<ccode>`. Unknown day/month (-9) dates are coarsened to the period
start with a `date_precision` attribute.

## Not included
- **Polity5** (`https://www.systemicpeace.org/inscr/p5v2018.xls`): BIFF xls is unsupported; the same Polity scores ship
  in `vdem` (`vdem_e_polity2`, `vdem_e_democ`, `vdem_e_autoc`).
- **SIPRI Arms Transfers** (https://armstrade.sipri.org/armstrade/page/trade_register.php): web-form export only
  (`manual_download_required`). Export the trade register CSV 1950-2025 in a browser; a separate dataset/import path is
  needed because this dataset's raw artifact is multi-shard.

## Licence
COW and ATOP data are free for academic use with citation (no explicit open licence; redistribution restricted/unspecified).
Cite each dataset version (see `dataset.json` `source.attribution`).

## Rebuild
```sh
wm acquire conflict_reference --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run conflict_reference && wm verify conflict_reference
python3 -m unittest data/conflict_reference/tests/test_pipeline.py
```
