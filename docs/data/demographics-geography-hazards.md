# Demographics, geography and hazards datasets

Cross-dataset notes for census_business, census_population, acs_5yr_tables, acs_pums, acs_pums_2019,
census_geography, irs_soi_migration, un_wpp, classifications, noaa_storm_events, fema_nri, openfema,
usgs_earthquakes, ibtracs, ghcn_daily, ghcn_monthly, noaa_climdiv, wri_aqueduct, epa_aqs_daily and usgs_resources. Each dataset README documents its source,
licence, scope and metrics.

`epa_aqs_daily` is `full_acquisition_configured` in `wm catalog` and has no published normalized artifact yet;
the identifiers and joins attributed to it below are what its pipeline will emit. Every other dataset on this
page has a published normalized stage.

## Shared identifiers

| Namespace | Meaning | Emitted by |
| --- | --- | --- |
| `geo:US`, `geo:US:state:<FIPS2>` | nation, states (incl. DC, PR) | most datasets |
| `geo:US:county:<GEOID5>` | counties / equivalents | census_*, acs_5yr_tables, irs_soi_migration, fema_nri, openfema, noaa_storm_events, noaa_climdiv, epa_aqs_daily |
| `geo:US:tract:<GEOID11>` | census tracts (2020 tracts) | census_geography, acs_5yr_tables, fema_nri |
| `geo:US:place:<GEOID7>`, `geo:US:cousub:<GEOID10>` | places, county subdivisions | census_geography, census_population |
| `geo:US:cbsa:<code>`, `geo:US:csa:<code>`, `geo:US:metdiv:<code>` | metro/micro areas | census_geography, census_population, census_business (CBP MSA file) |
| `geo:US:zcta:<ZCTA5>` vs `geo:US:zip:<ZIP5>` | Census ZCTAs vs USPS ZIP Codes (CBP ZBP) — related, not identical | census_geography vs census_business |
| `geo:US:puma20:<FIPS2><PUMA5>`, `geo:US:puma10:<FIPS2><PUMA5>` | 2020 PUMAs (2020-2024 PUMS), 2010 PUMAs (2015-2019 PUMS) — not one-to-one | acs_pums, acs_pums_2019 |
| `ghcn:station:<ID>` | GHCN stations (shared by daily records and GSOM monthly summaries) | ghcn_daily, ghcn_monthly |
| `iso3:<ISO3>` | countries | un_wpp, wri_aqueduct |
| `naics2012:` / `naics2017:` / `naics2022:` | industries (sectors as `31-33` etc.) | classifications, census_business (`dimensions.industry`) |
| `hs:<HS6>`, `scheduleb20xx:`, `hts2022:`, `sitc4:`, `enduse:`, `soc2018:` | products, occupations | classifications |

- Geography vintages: census_geography is the 2024 Gazetteer / cartographic boundary vintage (ZCTA polygons 2020);
  ACS and NRI use 2020-based tracts; CBP 2022-2023 use NAICS 2022, CBP 2019-2021 and BDS use NAICS 2017; the 2022 trade
  concordances use NAICS 2017 while Schedule B 2025 uses NAICS 2022 (joins across revisions go through
  `classifications` `maps_to` assertions).
- Connecticut replaced its 8 counties with 9 planning regions (FIPS 09110-09190) from 2022 data onwards; older
  vintages (IRS migration, CBP 2019-2021, nClimDiv county files, NRI) may still use the legacy county codes.
- nClimDiv files use NCEI alphabetical state codes (not FIPS); the pipeline maps them. Storm Events forecast zones
  (`noaa:zone:`) are not counties; only CZ_TYPE C rows link to counties.
- County names without FIPS (OpenFEMA PA/housing summaries) remain dimensions, not geography links.

## Time conventions

- Stocks at a point in time use a one-day interval (PEP 1 July populations, GHCN daily values); flows cover their
  reporting period (PEP components 1 July-1 July, CBP annual payroll calendar year, CBP employment the pay period
  including 12 March); ACS 5-year estimates carry the whole 2020-2024 period; IRS migration spans both filing years.
- Real-time vintages: census_population keeps every Population Estimates vintage (2020-2024) as separate observations
  (`dimensions.vintage`, `attributes.released_at` = HTTP Last-Modified of the published file, which matches the Census
  release dates). WPP projections are flagged `attributes.projection`; Aqueduct future values carry
  `dimensions.scenario`.
- Hazard events use UTC `occurred_at` (Storm Events converted from local CZ_TIMEZONE; ComCat origin times; IBTrACS
  ISO_TIME is UTC). OpenFEMA amounts are cumulative as of retrieval.

## Suggested joins for simulations

- Exposure x hazard: `fema_nri` expected annual loss by county/tract x `census_population`/`acs_5yr_tables`
  population and income x `census_business` employment by industry.
- Event losses: `noaa_storm_events` damage/casualties by county-day; `openfema` declarations by county give
  program responses; `ibtracs` tracks and `usgs_earthquakes` epicenters need spatial joins against
  `census_geography` geometry (stage `geometry`, delta-encoded rings).
- Climate drivers: `noaa_climdiv` county temperature/precipitation/PDSI (monthly 1991+, annual 1895+),
  `ghcn_daily` station series (monthly for 954 stations, daily for 133 reference stations), `ghcn_monthly` GSOM monthly
  summaries for ~13k long-record U.S. stations, `epa_aqs_daily` county-day PM2.5, ozone, NO2, SO2, CO and PM10,
  `wri_aqueduct` basin water stress (link counties to basins spatially).
- Change over time: `acs_pums_2019` (2015-2019, 2010 PUMAs, 2019 dollars) vs `acs_pums` (2020-2024, 2020 PUMAs, 2024
  dollars) — compare at state level or through a PUMA crosswalk, and deflate dollars consistently.
- Migration/demography: `irs_soi_migration` county flows (returns, people, AGI) with PEP net domestic migration as a
  consistency check; `un_wpp` for international context and projections.

## Operational notes

- Long builds used a frozen snapshot of `worldmodel/` and all `dataset.json` files because other workstreams edit
  them concurrently; `Runner` otherwise fails with "Code changed during execution". Dataset code snapshots include
  `tests/`, so do not edit a dataset's tests while its build runs.
- Storm Events narratives contain unescaped quotes; the shared strict CSV reader rejects them (the dataset uses a
  tolerant local reader). ArcGIS feature-service queries have URL length limits (NRI counties use `outFields=*`).
