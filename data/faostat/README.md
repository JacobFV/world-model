# faostat

FAOSTAT bulk downloads (normalized "long" CSVs) covering the **production, trade and
prices** domains plus food balance sheets. All files come from
`https://bulks-faostat.fao.org/production/`; the catalog is `datasets_E.json`. No
credential is needed.

| Code | File | Content | Raw |
| --- | --- | --- | --- |
| QCL | `Production_Crops_Livestock` | Crop area, yield, production; livestock stocks, slaughter, yields (1961-) | 33.9 MB |
| QI | `Production_Indices` | Gross and net production index numbers, total and per capita (2014-2016 = 100) | 16.2 MB |
| QV | `Value_of_Production` | Gross production value, current and constant, in USD, SLC and international dollars | 30.3 MB |
| PP | `Prices` | Annual and monthly producer prices (LCU/t, USD/t) and producer price index | 11.7 MB |
| CP | `ConsumerPriceIndices` | Monthly consumer food price indices (2015 = 100) and food inflation | 2.5 MB |
| TM | `Trade_DetailedTradeMatrix` | **Bilateral** reporter-partner-item trade: quantity and value | 420.7 MB |
| TCL | `Trade_CropsLivestock` | Country total import and export quantity and value by item | 273.5 MB |
| TCLI | `Trade_CropsLivestockIndicators` | Import dependency ratio, self-sufficiency, market concentration | 0.9 MB |
| TI | `Trade_Indices` | Import and export value, quantity and unit-value indices | 68.1 MB |
| FBS | `FoodBalanceSheets` | Food balances 2010-: supply and utilization, per-capita nutrients, population | 54.8 MB |

Total raw: **912.6 MB in 10 shards**. FAOSTAT's detailed matrix is bilateral
agricultural trade in FAO item codes; `cepii_baci` covers reconciled HS6 bilateral trade,
and the two are complementary rather than duplicates.

## Normalization (`normalized`, gzip JSONL)

Parameters:

- `min_year` (default 2000) with `min_year_by_domain` overrides. The bilateral matrix
  (TM) starts at **2010**: it holds 52.4M rows back to 1986, and the floor keeps the
  normalized output near 2x the raw bytes. Raw artifacts keep the full history, so
  lowering the floor and rebuilding needs no re-download.
- `elements`: per-domain element-code allowlist. Absolute FBS nutrient totals
  (661/671/681) and kg/capita/year (645) are derivable from per-capita values and are
  excluded; producer prices in standard local currency (5531) are excluded. Domains
  without an entry keep every element.

Evidence:

- **Areas**: the M49 code maps to `iso3:XXX` (`country`) through the local
  `m49_iso3.json` table, generated from `worldmodel/reference/countries_iso3166.csv`.
  FAO aggregates (area code >= 5000, including "excluding intra-trade" regional codes),
  `351` China (which includes Taiwan, Hong Kong and Macao) and historical areas (USSR,
  Yugoslav SFR, ...) use `fao:area:<code>` (`jurisdiction`). Their observations carry
  `attributes.aggregate=true`; for bilateral flows the flag is set when **either** side
  is an aggregate.
- **Items**: `fao:item:<code>` (production, prices, trade; CPC code in attributes),
  `fao:fbs_item:<code>` (food balances; group totals such as 29xx have `item_group=true`)
  and `fao:cpi_item:<code>` (`economic_series`, consumer price indices).
- **Observations**: subject = the reporting area. Metrics:
  - QCL: `production`, `area_harvested`, `yield`, `carcass_yield`,
    `producing_or_slaughtered_animals`, `livestock_stocks`, `milk_animals`, `laying_animals`
  - QI: `gross_production_index`, `gross_per_capita_production_index` (and net variants)
  - QV: `gross_production_value`, with a `price_basis` dimension (`current` or
    `constant_2014_2016`) and units `1000 USD`, `1000 SLC`, `1000 international_USD_2014_2016`
  - PP: `producer_price` (`LCU/t`, `USD/t`), `producer_price_index`
  - CP: `consumer_price_index` (`index_2015_100`), `consumer_price_inflation` (`percent`)
  - TM and TCL: `import_quantity`, `export_quantity` (`t`, `head`, `1000 head`, `number`),
    `import_value`, `export_value` (`1000 USD`)
  - TCLI: indicator slugs such as `import_dependency_ratio`
  - TI: `import_value_index`, `import_quantity_index`, `import_unit_value_index` and the
    export equivalents (`index_2014_2016_100`)
  - FBS: `production`, `import_quantity`, `export_quantity`, `stock_variation`,
    `domestic_supply_quantity`, `feed`, `seed`, `losses`, `processing`,
    `other_uses_nonfood`, `food`, `tourist_consumption`, `residuals`,
    `food_supply_kcal_per_capita_day`, `protein_supply_per_capita_day`,
    `fat_supply_per_capita_day`, `population`
- **Dimensions**: `item`, `domain` and `frequency` (`annual` or `monthly`). Bilateral
  flows add `partner` (the counterparty entity id) and `element` (the FAO element code,
  which distinguishes head-count from tonnage quantity reporting). `domain` is what tells
  apart the several metrics that share a name, for example `production` in QCL (tonnes of
  one crop) and in FBS (1000 t of a balance-sheet item), or `import_quantity` in TM
  (bilateral), TCL (country total) and FBS (balance sheet).
- **Attributes**: `element_code` and the FAO `flag` (A official, E estimated, I imputed,
  M missing, X external, B break). Missing values are `value: null` with a
  `missing_reason` that names the flag.
- Bilateral flows are reported by one side only, in that reporter's own statistics. A
  Mexico-reported import from the United States and a United States-reported export to
  Mexico are two separate observations and will not agree exactly.

## Licence

CC BY 4.0. Cite "FAO. FAOSTAT (<domain>). Accessed <date>". Redistribution is allowed
with attribution.

## Rebuild

```sh
wm acquire faostat --dry-run
wm acquire faostat --allow-network        # --resume after a budget stop
WORLD_MODEL_RAW_VERIFY=size wm run faostat
wm verify faostat
python3 -m unittest data/faostat/tests/test_pipeline.py
```

`desired_bytes` is a hard ceiling and per-dataset usage counts **every** raw artifact
kept on disk, so raise it when adding files while keeping earlier artifacts.
