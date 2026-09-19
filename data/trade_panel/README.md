# trade_panel

A derived, versioned panel of bilateral merchandise trade: exporter × importer × HS6 × year values
and quantities from CEPII BACI, the importer's MFN applied tariff where a published schedule
translates exactly into the flow's nomenclature, and CEPII gravity covariates for the pair-year. It
is built by [`worldmodel/panels/trade.py`](../../worldmodel/panels/trade.py). Measured row counts,
coverage and limits: [docs/trade-panel.md](../../docs/trade-panel.md).

## Stages

| Stage | Unit | What a row holds |
| --- | --- | --- |
| `concordance` | (crosswalk, source code) | the UNSD correlation tables HS2022→HS2017 and HS2017→HS1992 as `trade_concordances` publishes them: targets and UNSD's relationship label |
| `tariffs` | (nomenclature, reporter, year) | MFN applied rates (percent, simple average of the reporter's national lines in the HS6 subheading) in BACI's HS1992 and HS2017 codes, with the native HS revision and the translation path |
| `panel` (output) | (nomenclature, exporter, importer, year) | the HS6 table of that directed flow — `[product, value_kusd, quantity_t, importer_mfn_applied_pct]` per cell — plus the importer's tariff source block, the gravity block and totals |

All stages are gzip JSONL with per-row `evidence` (input `dataset@stage@version`, contributing
record count, SHA-256 over their ids, sample ids).

**Why records are grouped.** The logical unit is one exporter × importer × HS6 × year cell, and
there are about 360 million of them. One JSON line per cell would repeat the pair, year, tariff
source and gravity block on every line; grouping by pair-year keeps the same cells with their
coordinates and costs a fraction of the bytes. `worldmodel.panels.trade.cells(record)` yields the
flat cells.

**Why the flows are directed.** BACI reconciles the exporter's and the importer's declarations into
one value per direction, so there is no separate "reporter" figure: reporter *r*'s exports to
partner *p* are the record `r -> p`, and *r*'s imports from *p* are the record `p -> r`.
`wm trade-panel lookup r p <hs6>` shows both.

## Joins

- **Countries**: ISO 3166 alpha-3 codes, which BACI, WITS/TRAINS and CEPII gravity each publish.
  BACI areas without an ISO code (`baci:area:490`, "Other Asia, nes") join nothing.
- **Products**: HS6 codes as published. A tariff published in another HS revision is carried into a
  BACI code only when the UNSD correlation table makes it **exact**: every source code that maps to
  the target maps to nothing else, and all of those sources carry the same rate. (A simple average
  over a union of line sets whose averages are equal is that same value.) Splits get no rate, and
  HS2007/HS2012 reporter-years have no concordance here and are not translated.
- **The EU common external tariff** (WITS reporter `918`) applies to an importer only in a year
  CEPII gravity publishes `eu_member = 1` for it. After gravity's last year the last published
  membership is carried forward, which is a declared rule, and every such record says so in
  `importer_tariff.applied_via`.

## Publication dates

Each value carries `available_at` and the id of the rule that produced it (`rules` in the
construction row):

| Rule | Applies to | Date |
| --- | --- | --- |
| `baci_release_calendar` | trade values and quantities | January 31 of year+2 (CEPII releases BACI each January with data through two years earlier). The values are the pinned release's vintage, recorded in `vintage`, and CEPII re-reconciles history in every release |
| `trains_year_in_force` | WITS/TRAINS MFN rates | December 31 of the year they were in force |
| `hts_release_date` | USITC HTS rates | the release date on the USITC release list |
| `gravity_static` | distances, contiguity, language, colonial ties, legal origin | before the panel year (declared exception) |
| `gravity_annual_status` | RTA in force, WTO and EU membership | December 31 of the year |
| `gravity_annual_macro` | GDP, population | December 31 of the following year |

## What it does not establish

- **BACI values are reconciliations**, not either country's published statistic, and are FOB in
  thousand current USD.
- **The tariff column is the importer's MFN applied rate**, not the duty the flow paid: preferences,
  quotas, anti-dumping duties and the US chapter 99 surcharges are not in it. A missing rate means
  no exactly translatable published rate, not a zero tariff.
- **HS1992 and HS2017 records are separate nomenclatures.** Only tariff codes are translated between
  vintages; trade values never are.
- **Gravity covariates are one CEPII release (V202211) and stop in 2021**; nothing here is an estimate.
- **Quantities are missing for some flows** (CEPII publishes none) and are metric tons, so unit
  values across products are not comparable.

## Rebuild

```sh
WORLD_MODEL_DATA=/path/to/data WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run trade_panel \
  --input cepii_baci/normalized@<v> --input cepii_baci_hs92/normalized@<v> \
  --input wits_trains_tariffs/normalized@<v> --input usitc_hts_tariffs/normalized@<v> \
  --input cepii_gravity/normalized@<v> --input trade_concordances/normalized@<v>
```

Read it with `wm trade-panel lookup USA CHN 850440` and `wm trade-panel coverage`. Tests:
`tests/test_trade_panel.py` (builder on fixtures). `artifacts/` and `scratch/` are ignored by Git.
