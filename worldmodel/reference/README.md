# Tracked reference tables

Small reference tables used by `worldmodel.units` and `worldmodel.crosswalks`. They are
tracked because they are small (under 120 KiB each) and come from public-domain or openly
licensed sources. `manifest.json` records each source URL, its licence, the SHA-256 of
the downloaded input and the row count of each output.

To regenerate them from official downloads:

```sh
python3 -m worldmodel.reference.build_reference_files --source-dir /tmp/wm-ref --download
```

Without `--download` the builder works offline from files that are already in `--source-dir`.

| File | Contents | Source | Licence |
| --- | --- | --- | --- |
| `countries_iso3166.csv` | ISO 3166-1 alpha-2/alpha-3/numeric, UN M49, currency and World Bank code; formerly used ISO 3166-3 codes with validity dates; Kosovo `XK`/`XKX` (user-assigned) | datahub `country-codes` (ISO/UN M49 compilation) + World Bank API + hand-curated ISO 3166-3 rows | ODC-PDDL; CC BY 4.0 (World Bank) |
| `worldbank_economies.csv` | World Bank economy and aggregate codes, region and income group | api.worldbank.org/v2/country | CC BY 4.0 |
| `country_states_cow.csv` | Correlates of War state system membership periods (v2016) | correlatesofwar.org `states2016.csv` | Free use with citation |
| `country_states_gw.csv` | Gleditsch-Ward independent states and microstates (to 2017) | ksgleditsch.com `iisystem.dat`, `microstatessystem.dat` | Academic use with citation |
| `country_system_links.csv` | COW/GW code periods linked to ISO alpha-3, each with a `basis` | Name matching plus dated overrides in `build_reference_files.py` | Derived; see basis |
| `currencies_iso4217.csv` | ISO 4217 codes: current and withdrawn, numeric code, minor unit | datahub `currency-codes` | ODC-PDDL |
| `us_states.csv` | State FIPS, USPS code, name, GNIS | Census `state.txt` | Public domain |
| `us_counties.csv` | 2020 county list plus 2023 Gazetteer (Connecticut planning regions), with validity dates for deleted and recoded counties | Census `national_county2020.txt`, 2023 Gazetteer | Public domain |
| `us_county_changes.csv` | County change events from the 1990s to the 2020s, with published populations | Census "Substantial Changes to Counties" pages (transcribed) | Public domain |
| `ct_county_planning_region_cousub.csv` | Connecticut town (county subdivision) crosswalk from old counties to planning regions | Census `ct_cou_to_cousub_crosswalk.xlsx` | Public domain |
| `naics_2022_codes.csv` | NAICS 2022 codes (2 to 6 digits) and titles | Census `2-6 digit_2022_Codes.xlsx` | Public domain |
| `naics_2012_2017.csv`, `naics_2017_2022.csv` | Six-digit NAICS concordances; source-piece titles show how industries split | Census NAICS concordances | Public domain |

## Interpretation notes

- **Validity dates.** Dates on ISO rows are ISO Maintenance Agency change dates. Dates in
  the COW/GW tables are political dates. An empty `valid_to` in COW (2016-12-31) or GW
  (2017-12-31) marks the end of dataset coverage, not the end of a state.
- **Link basis.** `country_system_links.csv` records a `basis` for every link. Rows marked
  `territorial_continuity_pre_iso` map states that existed before ISO 3166 (1974) by
  convention. Users who need contemporaneous codes only can filter them out.
- **Reused codes.** ISO alpha-2 `CS` was used by both Czechoslovakia and Serbia and
  Montenegro. COW `345` covers Yugoslavia, Serbia and Montenegro, and Serbia.
  `CountryCodes.convert` requires a date for these codes.
- **NAICS splits.** The Census concordances publish relationships but no weights. When an
  industry splits, apportionment requires caller-supplied weights (employment, receipts),
  or an explicit equal-split assumption that is reported with a worst-case error bound.
- **County changes.** Split weights use the populations published on the Census change
  pages, and the population basis is recorded. Partial transfers (for example Broomfield)
  have no county-level weights, so apportionment refuses them unless weights are supplied.

Larger or licence-restricted concordances (ZCTA↔county, CBSA vintages, HS↔NAICS/SITC, BEA
IO codes, GLEIF, SEC, FEC, Voteview, OFAC, OpenSanctions) are declared in
`acquisition_declarations.json` and are not tracked here.
