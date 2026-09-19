# The trade panel

`trade_panel` is a derived, versioned dataset
([declaration](../data/trade_panel/dataset.json), [README](../data/trade_panel/README.md), builder
[`worldmodel/panels/trade.py`](../worldmodel/panels/trade.py)) covering exporter x importer x HS6 x
year merchandise trade from CEPII BACI, with the importer's MFN applied tariff on each product where
a published schedule translates exactly into the flow's HS vintage, and CEPII gravity covariates for
the pair-year. **359,101,721 cells**, every one carrying a declared publication date.

Built 2026-09-18 from commit `8942c67` (clean tree) on the second GB10 (CPU only), 42 min 35 s wall
clock, peak RSS 645 MiB.

| Stage | Version | Rows | Bytes (gzip) |
| --- | --- | --- | --- |
| `concordance` | `8047ce998125…` | 10,999 source codes + 1 construction row | 662 KB |
| `tariffs` | `bf95f5f6782e…` | 644 reporter-year schedules + 1 construction row | 8.1 MB |
| `panel` (output) | `2ffa5aef1956…` | 1,111,926 pair-year records (359,101,721 HS6 cells) + 1 construction row | 3.74 GB |

Pinned inputs (recorded in every manifest):

| Dataset | Version | What the panel takes from it |
| --- | --- | --- |
| `cepii_baci_hs92` | `742e33f3` | HS1992 flows, 1995-2024 (269,894,500 cells) |
| `cepii_baci` | `0e2f1e6d` | HS2017 flows, 2017-2024 (89,207,221 cells) |
| `wits_trains_tariffs` | `dfa67e7f` | MFN applied simple averages, 332 reporter-years 2018-2023 |
| `usitc_hts_tariffs` | `56a6ecc0` | HTSUS column 1 general rates, release 2026HTSRev19 |
| `cepii_gravity` | `30fa4dec` | pair and country covariates, V202211 (to 2021) |
| `trade_concordances` | `4dbe3795` | UNSD HS2022→HS2017 and HS2017→HS1992 correlation tables |

Rebuild command with every dependency pinned:
[data/trade_panel/README.md](../data/trade_panel/README.md#rebuild).

## The unit, and why records are grouped

The panel's unit is one exporter x importer x HS6 x year **cell**. A record holds one directed
pair-year and its whole HS6 table:

```
id        trade_panel:hs17:2019:iso3:USA:iso3:CHN
columns   ["product", "value_kusd", "quantity_t", "importer_mfn_applied_pct"]
cells     [["850440", 253603.038, 21738.9, 2.94], …]
totals    value_kusd, cells, cells_with_quantity, cells_with_tariff, value_kusd_with_tariff
importer_tariff   which schedule supplied the rates, how it was translated and applied, its date
gravity           the pair's and both countries' covariates for that year, with dates by type
available_at      the flow publication date; vintage records the BACI release
evidence          BACI record count, SHA-256 over their ids, samples; the tariff row; gravity ids
```

One JSON line per cell would repeat the pair, year, tariff block and gravity block 359 million
times; grouping keeps every cell with its coordinates at a fraction of the bytes.
`worldmodel.panels.trade.cells(record)` yields the flat cells.

BACI reconciles the exporter's and the importer's declarations into one value per direction, so
there is no separate "reporter" figure: reporter *r*'s exports to partner *p* are the record
`r -> p`, and *r*'s imports from *p* are the record `p -> r`.
`wm trade-panel lookup USA CHN 850440 --nomenclature hs17 --tariffs` reads both (≈75 s, it streams
the panel) and prints, for each year, the exported and imported value and quantity, the tariff the
importer applied, which schedule it came from and the gravity block:

| year | US exports to CN (k$) | CN MFN % | US imports from CN (k$) | US MFN % |
| --- | --- | --- | --- | --- |
| 2018 | 348,656 | 3.94 | 4,928,023 | 0.28 |
| 2019 | 253,603 | 2.94 | 3,828,675 | 0.12 |
| 2020 | 268,139 | 1.88 | 3,416,851 | 0.00 |
| 2021 | 287,254 | 0.97 | 3,834,832 | 0.00 |
| 2022-2023 | 296,992 / 292,421 | not translatable | 4,128,778 / 3,288,784 | not translatable |

(2022 and 2023 are blank because both reporters switched to HS2022 schedules and this HS6 code is
not exactly translatable back to HS2017 — see below.)

## Coverage

Records, cells, trade value and the share joined, by nomenclature and year (`wm trade-panel coverage`
recomputes this from the panel):

| Nomenclature | Years | Records | Cells | Cells with a tariff | Import value with a tariff | Records with gravity |
| --- | --- | --- | --- | --- | --- | --- |
| `hs92` | 1995-2024 | 870,755 | 269,894,500 | 30,065,975 (11.1%) | — | 776,047 (89.1%) |
| `hs17` | 2017-2024 | 241,171 | 89,207,221 | 42,508,780 (47.7%) | — | 148,298 (61.5%) |

Those overall shares are diluted by the years no tariff source covers. Within the WITS years:

| Year | hs17 cells with a tariff | hs17 value with a tariff | hs92 cells | hs92 value |
| --- | --- | --- | --- | --- |
| 2018 | 67.5% | 87.3% | 46.8% | 51.1% |
| 2019 | 69.0% | 91.7% | 48.9% | 52.9% |
| 2020 | 69.2% | 91.9% | 49.5% | 51.2% |
| 2021 | 69.1% | 90.9% | 49.6% | 52.4% |
| 2022 | 51.0% | 58.9% | 39.4% | 43.4% |
| 2023 | 50.1% | 56.0% | 39.0% | 41.0% |

Three things drive the rest of the gap:

- **Reporters.** TRAINS here covers 59 reporters (the G20, the EU as one customs union, and other
  large economies), so a flow into any other importer has no schedule. 58 of them are actually used;
  the EU schedule is applied to 65,023 records, more than any single country.
- **HS vintage.** 2022-2023 schedules are HS2022 and must be translated twice for HS1992 flows;
  10 reporter-years are HS2012 and 1 is HS2007, for which this catalog has no concordance at all, so
  they are not translated (counted in the `tariffs` construction row).
- **Exactness.** A rate is carried to a code only when the UNSD correlation table makes it exact:
  every source code that maps to the target maps to nothing else, and all such sources carry the
  same rate. An HS2017 schedule assigns all 5,388 of its own codes but only ~3,470 HS1992 codes;
  an HS2022 schedule assigns ~4,140 HS2017 codes and ~2,740 HS1992 codes.

Other measured coverage: 92-99% of cells carry a quantity (BACI publishes none for the rest;
the 1995-1999 HS1992 years are the weakest at 91.9-94.0%). 232 exporters or importers appear in the
HS1992 records and 226 in the HS2017 records, including BACI statistical areas without an ISO3 code
(`baci:area:490`), which join no tariff and no gravity. Gravity covers every record through 2021 and
none after, because CEPII V202211 ends there.

The USITC HTS schedule is in the `tariffs` stage (10,535 rate lines averaged into 5,325 HS6 codes,
translated to 3,873 HS2017 and 2,536 HS1992 codes) but joins **no flow**: the current release is the
2026 one and BACI ends in 2024. It is kept because it is the only tariff source here that is not an
annual TRAINS average, and because the next BACI release will reach it.

## Publication dates

Every value carries `available_at` and the id of the rule behind it; the rules are in the `panel`
construction row and in `worldmodel.panels.trade.RULES`.

| Rule | Applies to | Date | Basis |
| --- | --- | --- | --- |
| `baci_release_calendar` | trade values and quantities | January 31 of year+2 | CEPII releases BACI each January (V202601 = January 2026) with data through two years earlier |
| `trains_year_in_force` | WITS/TRAINS MFN rates | December 31 of the year | the rate describes the schedule in force that year; a mid-year change is only complete at year end, and WITS publishes no ingestion date |
| `hts_release_date` | USITC HTS rates | 2026-09-09 | the date on the USITC release list for release 2026HTSRev19 (in force from 2026-09-15) |
| `gravity_static` | distances, contiguity, language, colonial ties, legal origin | before the panel year | declared exception: these are time-invariant, though published in V202211 |
| `gravity_annual_status` | RTA in force, WTO and EU membership | December 31 of the year | CEPII codes the legal status during the year |
| `gravity_annual_macro` | GDP, population | December 31 of the following year | after the WDI update that first publishes the year |

Two honest caveats about these dates. The BACI rule is a release **calendar**, not a measured
release date, and it is wrong in one direction for old years: BACI did not exist in 1997, so
`available_at: 1997-01-31` on a 1995 flow means "this is when the year would first appear under the
current calendar". And every value in the panel is the *current vintage*: CEPII re-reconciles all
history in every BACI release, so the record also carries `vintage: {release: V202601, published:
2026-01-31}`, which is the date a strict as-of reader should use.

## Identity

- **Countries** join on ISO 3166 alpha-3 codes, published by BACI (from its own country table), by
  WITS (reporter codes) and by CEPII gravity. Nothing is matched by name. BACI areas without an ISO3
  code join nothing.
- **Products** join on HS6 codes as published, within one nomenclature. Between nomenclatures only
  the UNSD correlation tables are used, and only where they are exact (above). Trade values are
  never translated between vintages.
- **The EU common external tariff** (WITS reporter `918`) is applied to an importer only in a year
  CEPII gravity publishes `eu_member = 1` for it. Gravity ends in 2021, so 2022-2024 membership is
  the 2021 membership carried forward — a declared rule, and every record says which case applies in
  `importer_tariff.applied_via`. Records whose exporter and importer are both EU members in that
  year carry `intra_customs_union: true`, because the MFN rate is not what such a flow pays.

## What the panel does not establish

- **BACI values are reconciliations**, not either country's published statistic. They are FOB, in
  thousand current USD; quantities are metric tons.
- **The tariff column is the importer's MFN applied rate** — a simple average of its national tariff
  lines in that HS6 subheading — **not the duty this flow paid.** Preferences and FTAs, tariff-rate
  quotas, anti-dumping and countervailing duties and the US chapter 99 surcharges (Section 301/232,
  IEEPA) are all outside it. The US-China columns above therefore do **not** show the 2018-2019
  Section 301 tariffs.
- **A missing rate is not a zero rate.** It means no reporter schedule, no published concordance, or
  no exact translation for that code.
- **A simple average over tariff lines understates protection** where lines are specific or compound
  rather than ad valorem: WITS excludes those lines from its average and publishes the count, and
  the HTS aggregation here does the same.
- **HS1992 and HS2017 records are separate nomenclatures** and both cover 2017-2024; CEPII
  reconciles each separately, so their totals differ slightly for the same year. Do not add them.
- **Gravity covariates are one release and stop in 2021.** Nothing here estimates a gravity model,
  and no covariate is extrapolated (only EU membership is carried forward, and only to decide which
  tariff schedule applies).
- **Records are not a sample**: they are every flow CEPII publishes in the two releases.

## Follow-ups

- TRAINS preferential (partner-specific) rates use the same WITS endpoint with a partner code; they
  would turn the tariff column into the rate a flow actually faced for pairs with an agreement, and
  the `gravity.pair.rta_in_force` flag already says where that matters.
- Pre-2018 TRAINS years are acquirable from the same endpoint and would extend the tariff join back
  over the long HS1992 history, which is currently tariff-free before 2018.
- The USITC annual tariff databases (`manual_download_required`, 2024 and 2025 editions) would give
  the US a dated schedule inside BACI's years, including the chapter 99 surcharges as a separate
  column.
- A split-aware translation (apportioning a parent code's rate to its children by trade weight)
  would raise HS1992 tariff coverage above 50%, but it would be an assumption about within-code
  composition, which is why this panel refuses it.
- CEPII gravity V202211 stops in 2021; a newer release would extend the covariates and remove the
  EU carry-forward rule.
