# usitc_hts_tariffs

U.S. Harmonized Tariff Schedule (HTSUS) tariff rates from the U.S. International Trade Commission.

## Scope

- **Automatic** (`wm acquire usitc_hts_tariffs --allow-network`): the current HTS revision as one JSON
  export (`https://hts.usitc.gov/reststop/exportList?from=0100&to=9999&format=JSON&styles=false`, ~12.6 MB,
  ~35.8k lines including chapter 99 Section 301/232/IEEPA provisions) and the release list
  (`https://hts.usitc.gov/reststop/releaseList`) that names the current revision and its start date.
- **Manual** (status `manual_download_required`): the USITC annual tariff databases, which return 403 to
  scripted clients. Do not script around the bot protection.

## Manual download and import

1. In a browser open <https://www.usitc.gov/tariff_affairs/documents/tariff_data/> (linked from
   <https://www.usitc.gov/harmonized_tariff_information/tariff_database>) and download
   `tariff_database_2024.zip`, `tariff_database_2025.zip`, `tariff_database_2026.zip`.
2. Import each (one raw artifact per file; the latest import becomes `raw-latest`):
   `python3 -m worldmodel import usitc_hts_tariffs ~/Downloads/tariff_database_2025.zip --source-json '{"url":"https://www.usitc.gov/tariff_affairs/documents/tariff_data/tariff_database_2025.zip","release":"2025 annual"}'`
3. `python3 -m worldmodel run usitc_hts_tariffs` (builds from `raw-latest`; use `--raw` pins to rebuild a
   specific import). An HTS CSV export from hts.usitc.gov ("Export" → CSV) can be imported the same way.

The ZIP must contain the delimited `.txt`/`.csv` table (pipe, comma or tab delimited); the XLSX-only
variant is rejected with a clear error.

## Evidence

- Entities `hts:<digits>` (`product` for tariff lines; `regulation` for chapter 99 provisions), with
  indent, parent code, quantity units and release name in attributes.
- HTS export metrics (unit `fraction`, 0.068 = 6.8%, valid from the current release start date):
  `general_ad_valorem_rate`, `column2_ad_valorem_rate`, `special_ad_valorem_rate` (special program codes in
  `attributes.special_programs`), `*_specific_rate` (`USD/<unit>`, cents converted to dollars), and for
  chapter 99 `general_additional_ad_valorem_rate` etc. ("+ 25%" surcharges). Compound or legal-text rates
  that cannot be parsed are null with `missing_reason: rate_text_not_numeric` and keep `rate_text`.
- Annual database metrics with effective windows (`begin_effect_date` → `end_effective_date` + 1 day):
  `mfn_ad_valorem_rate`, `mfn_specific_rate`, `mfn_other_specific_rate`, `mfn_ad_valorem_equivalent`,
  `column2_*`; program indicator columns (`*_indicator`) are kept as attributes.
- Claims: `hts_release_metadata`, `additional_duties_text`.

## Licence

U.S. federal government work (public domain). Attribution: U.S. International Trade Commission.

## Local files

`artifacts/` and `scratch/` are ignored by Git.
