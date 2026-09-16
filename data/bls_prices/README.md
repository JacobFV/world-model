# bls_prices

U.S. price statistics from the Bureau of Labor Statistics flat files, plus the World Bank
Commodity Price Data ("Pink Sheet") monthly prices.

## Source and scope

One `files` acquisition, one shard per file ([dataset.json](dataset.json)):

| Family | Data files | Mapping files |
| --- | --- | --- |
| CPI-U (`cu`) | `cu.data.0.Current` (1997+ all items/areas), `cu.data.1.AllItems` (all-items history from 1913/1947) | `cu.series`, `cu.item`, `cu.area`, `cu.periodicity`, `cu.footnote` |
| PPI commodity (`wp`) | `wp.data.0.Current` (recent window), `wp.data.22.FD-ID` (final/intermediate demand, 2009+), `wp.data.1.AllCommodities` | `wp.series`, `wp.item`, `wp.group`, `wp.footnote` |
| PPI industry (`pc`) | `pc.data.0.Current` | `pc.series`, `pc.industry`, `pc.product`, `pc.footnote` |
| Average prices (`ap`) | `ap.data.0.Current` | `ap.series`, `ap.item`, `ap.area`, `ap.footnote` |
| Import/export prices (`ei`) | `ei.data.0.Current` | `ei.series`, `ei.index`, `ei.footnote` |
| World Bank Pink Sheet | `CMO-Historical-Data-Monthly.xlsx` ("Monthly Prices" sheet, 1960+) | |

History supplement files only contribute periods before the first year that the family's
`Current` file publishes for the same series, so each (series, year, period) appears once.
Detailed PPI commodity history before the `Current` window is not included (only final demand
and all-commodities history).

## Normalized evidence (`normalized`, gzip JSONL)

| Metric | Subject | Unit | Key dimensions |
| --- | --- | --- | --- |
| `consumer_price_index` (`consumer_dollar_purchasing_power` for purchasing-power items) | `bls:cpi_item:<item>` | `index_1982_1984_100` etc. | `area` (`geo:US` or `bls:cpi_area:<code>`), `seasonal_adjustment`, `series_id` |
| `producer_price_index` | `bls:ppi:wp:<group+item>` / `bls:ppi:pc:<industry+product>` | `index_<base>_100` | `ppi_type`, `naics_industry_code` (pc) |
| `average_price` | `bls:ap_item:<item>` | `USD_per_lb`, `USD_per_gallon`, `USD_per_dozen`, ... | `area` |
| `import_price_index`, `export_price_index`, `terms_of_trade_index` | `bls:mxp:<series root>` | `index_<base>_100` | `index_code` |
| `commodity_price`, `commodity_price_index` | `worldbank:cmo:<commodity>` | `USD_per_barrel`, `USD_per_metric_ton`, `USD_per_kg`, `index_2010_100`, ... | nominal |

**Pink Sheet coverage caveat.** The workbook at the planned URL
(`.../5d903e848db1d1b83e0ec8f744e55570-0350012021/related/CMO-Historical-Data-Monthly.xlsx`) is a
stale snapshot: "Updated on January 03, 2025", monthly data 1960M01-2024M12. On 2026-09-15 the
World Bank commodity-markets page linked a newer file at
`https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx`.
Switching the URL changes the acquisition config and refetches every shard, so it was not done in
the 2026-09-15 acquisition. To refresh: update the URL in `dataset.json`, remove or supersede the
old raw artifact (it counts against the budget), `wm budget reconcile`, then re-acquire and rebuild.

Units encode the index base period from the series file (e.g. `198200` -> `index_1982_100`,
`December 2020=100` -> `index_2020_12_100`). Periods: monthly `M01..M12`, annual averages
`M13` (`period_type: annual_average`), semiannual CPI `S01/S02`. Footnote codes stay in
attributes (`preliminary: true` for `P`); dashes and Pink Sheet `…` become `value: null` with a
`missing_reason`. BLS PPI values are revised for four months after first publication; this is a
current-vintage snapshot. Every observation records `attributes.vintage = "current_at_retrieval"`,
`attributes.realtime_start` (retrieval date) and `attributes.realtime_end = null`; BLS observations
also carry `attributes.series_id`.

## Licence and access

BLS data are public domain (U.S. federal government work). The Pink Sheet is CC BY 4.0
(World Bank) and must be attributed. download.bls.gov rejects requests without a descriptive
User-Agent containing contact information: set `BLS_USER_AGENT` in `.env`.

## Rebuild

```sh
wm acquire bls_prices --dry-run
wm acquire bls_prices --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run bls_prices
wm verify bls_prices
python3 -m unittest data/bls_prices/tests/test_bls_prices.py
```
