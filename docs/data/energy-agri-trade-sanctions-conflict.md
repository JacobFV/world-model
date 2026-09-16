# Energy, agriculture, trade, sanctions and conflict datasets

Cross-dataset notes for the full-acquisition group: `eia_energy`, `eia_grid_operations`,
`ember_owid_energy`, `usda_agriculture`, `usda_fas_psd`, `faostat`, `cepii_baci`,
`cepii_gravity`, `un_comtrade`, `census_intl_trade`, `wits_trains_tariffs`,
`usitc_hts_tariffs`, `wto_timeseries`, `ofac_sanctions`, `other_sanctions_lists`,
`opensanctions`, `ucdp_conflicts`, `gdelt_events`, `vdem`, `conflict_reference`, `acled`.

Each dataset directory has its own `README.md` with source, scope, licence, credentials and
rebuild steps. This page records the conventions shared between them.

Acquisition state (`wm catalog`, 2026-09-15): every dataset above has a published normalized artifact except
`wto_timeseries` and `acled`, which are both `awaiting_credentials` with nothing acquired — treat their conventions
below as planned, not available. `usitc_hts_tariffs` is `manual_download_required` and its published build holds the
HTS REST export only (63,075 records, current 2026 revision).

## Shared conventions

- **Pipelines** stream full sharded raw artifacts through `context.raw_rows()` / `raw_shards()`
  and pass reader options explicitly. The pre-existing sample path is kept for `eia_energy`,
  `usda_agriculture` and `ucdp_conflicts`, and full mode is selected only for sharded raw
  artifacts, so the older sample tests still pass.
- **`evidence.py`** is copied into each dataset (stdlib only). It derives deterministic record
  IDs from `digest([dataset, raw artifact, identity])`, emits each entity once through a key set
  that spills to SQLite, and writes observations with explicit `unit`, half-open
  `valid_from`/`valid_to`, `dimensions.frequency`, and `missing_reason` for null values
  (suppressions are never zero).
- **Outputs** are gzip `evidence_jsonl`. Whole source rows are not copied into attributes.

## Shared identifiers

| Namespace | Meaning | Used by |
| --- | --- | --- |
| `iso3:XXX` (`country`) | Sovereign country. Source codes (M49, COW, GW, FIPS, PSD, BACI numeric) are kept in attributes | all country-level datasets |
| `geo:US`, `geo:US:state:SS`, `geo:US:county:SSCCC` | US geography by FIPS, linked with `within` | EIA, NASS |
| `hs17:NNNNNN` / `hs17:NNNN` | HS 2017 products (6-digit and 4-digit aggregates) | BACI |
| `hs:NNNNNN`, `hts:NNNNNNNN` | HS codes whose revision varies (recorded in attributes); US HTS lines | Census, WITS, Comtrade, USITC |
| `ofac:party:<ProfileID>`, `ofac:program:<CODE>` | OFAC parties and sanctions programs | OFAC; reused by the US Consolidated Screening List |
| `un:sanctions:<ref>`, `uk:sanctions:<id>`, `us_csl:<id>`, `opensanctions:<id>` | Sanctions list entries | sanctions datasets |

Aggregates without an ISO3 code (EU, World, FAO regions, USSR, "Other", PSD groupings) keep
source-namespaced IDs with `attributes.aggregate = true`. They never collapse onto `iso3:`.

## Cross-dataset links

- `other_sanctions_lists` asserts `same_designation_as` from UK entries to UN entries (by UN
  reference number), and from Consolidated Screening List Treasury rows to `ofac:party:*`.
- OFAC `controls` assertions put the owner or controller as the subject; OFAC's
  "Owned or Controlled By" relation is reversed.
- `opensanctions_graph` (the full FtM default graph, restricted to sanctions/PEP/crime/export-control
  seeds plus one-hop relationship counterparties) and `opensanctions` (sanctions targets) share
  `opensanctions:<id>`, so they join on entity id. The graph contributes `owns` (FtM Ownership,
  owner → asset, with percentage and dates), `controls`, `director_of`, `holds_position`,
  `family_member_of`, `associate_of`, `linked_to` and similar. Assertion attributes name the
  originating OpenSanctions source datasets, whose quality varies from official lists to leaks.
- FAOSTAT `production` appears in two domains (production and food balance sheets) with
  different item codes; select with `dimensions.domain`.
- **Three bilateral trade sources, three conventions.** `cepii_baci` reconciles exporter and
  importer reports into one flow per exporter-importer-product-year. FAOSTAT's detailed trade
  matrix (`dimensions.domain = TM`) is single-side reported, so a reported import and its mirror
  export are separate observations that will not match. `un_comtrade` and `census_intl_trade`
  are as-reported monthly flows. Never sum across them; pick one per question.
- FAOSTAT flows set the aggregate flag when either side is an FAO aggregate, including the
  "excluding intra-trade" regional codes.
- NASS keeps NASS unit strings, so one metric can appear with several units (for example
  `area_harvested` in ACRES and OPERATIONS). Select series by metric plus unit.

## Licences requiring care

| Dataset | Terms |
| --- | --- |
| `opensanctions`, `opensanctions_graph` | CC BY-NC 4.0: non-commercial only (`redistribution: restricted`, `non_commercial: true`) |
| `wto_timeseries` | WTO terms: non-commercial use with attribution |
| `acled` | ACLED EULA: raw events may not be redistributed; commercial use requires a licence |
| `un_comtrade`, `wits_trains_tariffs` | Free use with attribution; no bulk redistribution or resale |
| `faostat`, `ember_owid_energy`, `ucdp_conflicts` | CC BY 4.0 (attribution) |
| `vdem` | CC BY-SA 4.0 |
| `other_sanctions_lists` | UN terms; UK Open Government Licence v3; US Consolidated Screening List public domain |
| `cepii_baci` | Etalab Open Licence 2.0 (attribution) |
| US federal sources (EIA, NASS, FAS, Census, OFAC, USITC) | Public domain |

## Refresh notes

- **Snapshot-only sources.** Sanctions lists, PSD and FAOSTAT publish only the current state or
  latest estimate. Listing history, delistings and revisions require periodic re-acquisition
  (monthly is recommended). Raw artifacts are immutable, so snapshots accumulate side by side.
- **Changing file names.** The NASS Quick Stats bulk file name contains the build date and old
  builds are deleted. Update `acquisition.files[0].url` from https://www.nass.usda.gov/datasets/
  before re-acquiring.
- **Dates in snapshot sources.** OpenSanctions dates record when OpenSanctions first saw an
  entry, not official designation dates. About 80% of US Consolidated Screening List rows
  have no `start_date`.

## Energy

- **Shared balancing-authority IDs.** `eia:ba:<code>` is used by both `eia_energy` (EIA-860
  plant and generator links) and `eia_grid_operations` (EIA-930 demand, generation and
  interchange). Interchange flows are `eia:interchange:<from>:<to>` resource flows.
- **Grid-operations resolution.** `eia_grid_operations` publishes daily UTC sums for every
  EBA hourly series, plus hourly records for only the most recent `hourly_recent_days` (35).
  Hourly timestamps are treated as hour-ending. The full hourly history remains in the raw ZIP.
- **Series metric names.** Most `eia_energy` bulk series use metric
  `eia_<file>_<family>`, with the series id in `dimensions.series_id` and one
  `eia:series:<series_id>` entity per series. By default, quarterly and 4-week-average series
  are skipped, sub-annual points start in 2022, and plant series are annual only
  (`GEN`, `AVG_HEAT`). Parameters override these defaults; the raw artifact keeps full history.
- **Non-ISO country codes.** EIA INTL and OWID codes that are not current ISO countries use
  `eia:region:<code>` / `owid:region:<code>` (aggregates, `aggregate: true`) or
  `eia:historical_country:<code>` / `owid:historical_country:<code>` (`historical: true`).
  EIA publishes some regions under real country codes, for example "OPEC - South America"
  under VEN; these are mapped to regions too. Kosovo is `iso3:XKX` in every dataset.
- **Overlapping sources are not reconciled.** Ember and OWID electricity overlap EIA INTL
  country data; all three are kept side by side.
- **Copyright notices.** Henry Hub price series carry Thomson Reuters copyright notices.

### EIA series for the estimation layer

All eight series come from `eia_energy` (PET bulk file), with subject `iso3:USA`. Each record
carries `series_id`, `series_last_updated` and `bulk_file_last_updated` attributes, and
`observed_at` is the retrieval time. A weekly period is the week-ending date, and the valid
window is `[period - 6 days, period + 1 day)`.

| EIA series | Metric | Unit | Frequency |
| --- | --- | --- | --- |
| PET.WCESTUS1.W | `crude_oil_commercial_stocks_excl_spr` | Thousand Barrels | weekly |
| PET.WCRFPUS2.W | `crude_oil_field_production` | Thousand Barrels per Day | weekly |
| PET.WCRIMUS2.W | `crude_oil_imports` | Thousand Barrels per Day | weekly |
| PET.WCREXUS2.W | `crude_oil_exports` | Thousand Barrels per Day | weekly |
| PET.WCRRIUS2.W | `refiner_net_input_crude_oil` | Thousand Barrels per Day | weekly |
| PET.WGTSTUS1.W | `motor_gasoline_total_stocks` | Thousand Barrels | weekly |
| PET.MGFUPUS2.M | `motor_gasoline_product_supplied` | Thousand Barrels per Day | monthly |
| PET.EMM_EPMR_PTE_NUS_DPG.W | `gasoline_retail_price_regular` | Dollars per Gallon | weekly |

## Trade and tariffs

- **Effective tariff incidence.** `census_intl_trade` emits `import_calculated_duty` beside
  `import_general_value`, with `effective_duty_rate_fraction` in attributes: observed US duty
  incidence per HS6 and partner per month, which reflects the 2025-2026 tariff actions.
  `wits_trains_tariffs` (`mfn_applied_tariff_simple_avg`) and `usitc_hts_tariffs` (statutory HTS
  rates) are schedule rates, not incidence. Compare, do not mix.
- **Publisher quirks worth knowing before re-acquiring.** WITS returns HTTP 500 without an
  `Accept: application/xml` header and rejects the default urllib User-Agent. Census HS6
  queries covering all partners in one month time out with HTTP 500 after ~150 s, so the
  request grid is split per partner over 6-month windows.
- **USITC is partly manual.** The HTS REST export is acquired automatically; the annual tariff
  database ZIPs return 403 to scripts and must be downloaded in a browser and imported with
  `wm import` (see that dataset's README). They have not been imported, so the published build holds the
  current (2026) revision only.
- **WITS coverage ends in different years per reporter** (2023 for 54 of 59, down to 2019 for
  Egypt). Compare reporters within a year, never at each one's "latest". The HS revision also
  changes across years, so HS6 codes need a concordance for time series, and non-ad-valorem
  lines are excluded from the simple averages, which understates protection.
- **USITC null rates are explicit.** 312 HTS rates stated only as legal text are null with
  `missing_reason: rate_text_not_numeric` rather than guessed.

## Conflict and political events

- **Country IDs.** `ucdp_conflicts` maps Gleditsch-Ward codes and `conflict_reference` maps COW
  codes to `iso3:` through dataset-local tables; unmapped codes keep `gw:<code>` / `cow:<code>`.
  `vdem` uses `country_text_id` and keeps `cow_code` in attributes.
- **Three tiers of event evidence.** `ucdp_conflicts` (curated, CC BY 4.0) is the reference;
  `gdelt_events` is machine-coded news and a weak signal; `acled` would sit between them but
  needs an approved API tier. Do not sum events across datasets.
- **GDELT keys on DATEADDED.** GDELT publishes old events in today's files, so the
  `daily_dyads` stage keys days on the date the record was added, not the event date.
  The event-level `normalized` stage keeps both.
- **Polity5 substitute.** Polity5 ships only as BIFF `.xls`, which the readers do not support.
  `vdem` carries the Polity scores as `vdem_e_polity2`, `vdem_e_democ` and `vdem_e_autoc`, so
  `conflict_reference` omits Polity5 and is correspondingly smaller than the plan estimated.
- **Missing means not coded.** `vdem` skips blank cells rather than emitting nulls, and COW
  `-9` values become null with `source_missing_-9`.

