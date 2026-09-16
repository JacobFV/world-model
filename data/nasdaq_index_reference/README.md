# nasdaq_index_reference

Nasdaq-100 constituents snapshot and methodology.

**Source**: `https://api.nasdaq.com/api/quote/list-type/nasdaq100` (JSON) and `https://indexes.nasdaqomx.com/docs/Methodology_NDX.pdf`. **Credential**: none. **Licence**: proprietary Nasdaq index data, reference use only; no redistribution.

**Evidence**: `index:nasdaq:NDX`; `index_constituent` from `ticker:XNAS:<symbol>` valid only on the snapshot date; `market_capitalization` (USD) and `last_sale_price` (USD/share, delayed) at the snapshot time; methodology document entity (PDF retained, not parsed). No weights are published by this endpoint.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire nasdaq_index_reference --dry-run
wm acquire nasdaq_index_reference --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run nasdaq_index_reference
wm verify nasdaq_index_reference
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
