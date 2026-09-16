# market_prices

US equity daily bars (Massive grouped aggregates) and authorized feed imports.

**Source**: Massive (formerly Polygon.io) `GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true`, one request per weekday 2024-09-16..2026-09-14 (free-plan 2-year horizon, 5 requests/minute, ~105 minutes). Massive S3 flat files would be smaller but return 403 on the free plan.

**Credential**: `MASSIVE_API_KEY` (query `apiKey`, redacted in receipts). **Licence**: Massive Stocks Basic terms, individual non-professional use; no redistribution of raw or derived prices.

**Evidence**: per ticker per trading day on `ticker:US:<T>` (consolidated tape): `close_price_split_adjusted` (USD/share; open/high/low/VWAP/transactions attributes) and `volume_split_adjusted` (shares), valid `[date, date+1)`. Join to venue listings, CIK and FIGI via market_corporate_actions' ticker directory.

Canonical authorized-feed JSONL imports (`wm import`) still use the strict contract in [financial evidence imports](../../docs/financial-evidence-imports.md).

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire market_prices --dry-run
wm acquire market_prices --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run market_prices
wm verify market_prices
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
