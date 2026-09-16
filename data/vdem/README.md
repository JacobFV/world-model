# vdem

V-Dem Country-Year Full+Others **v16** (1789-2025, 202 polities, 4,618 columns) reduced to a curated set of
86 indicators (listed in `variables.json`) for 1946-2025 (post-1945 window keeps normalized output near 2x raw).

## Source
`https://v-dem.net/media/datasets/V-Dem-CY-FullOthers-v16_csv.zip` (27 MB ZIP, 406 MB CSV, codebook included).

## Outputs (`normalized`)
- Entities `iso3:<country_text_id>` (`country`), attributes `cow_code`, `vdem_country_id`. V-Dem ids are ISO 3166-1
  alpha-3 except historical/sub-national polities (e.g. PSG Gaza, SML Somaliland, ZZB Zanzibar, XKX Kosovo, DDR, YMD, VDR).
- Observations `vdem_<variable>` per country-year, `dimensions.frequency = annual`; V-Dem indices carry the
  68% measurement interval (`attributes.interval_68` from `_codelow`/`_codehigh`). Units: `index_0_1`,
  `latent_scale`, `category_code`, `binary`, `count`, Polity/FH/WGI native scales, `real_usd_per_capita`,
  `million_usd_2014`, `km2`, `years`, `ratio`, `percent`, and Fariss et al. latent GDP/population estimates.
- Blank cells are not emitted (structural non-coverage of a wide file), so absence means "not coded".
- Polity5 (`vdem_e_polity2`, `vdem_e_democ`, `vdem_e_autoc`) comes from here; conflict_reference does not re-ingest the Polity xls.

To add variables, append to `variables.json` (column, metric, unit, bounds) and rebuild.

## Licence
CC BY-SA 4.0 (share-alike). Cite Coppedge et al. (2026) V-Dem v16 and Pemstein et al. measurement model; `e_*`
variables retain their original sources' citation requirements.

## Rebuild
```sh
wm acquire vdem --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run vdem && wm verify vdem
python3 -m unittest data/vdem/tests/test_pipeline.py
```
