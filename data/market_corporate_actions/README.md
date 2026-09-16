# market_corporate_actions

Massive splits, dividends and ticker directory; authorized feed imports.

**Source**: Massive `/v3/reference/splits` (full history), `/v3/reference/dividends` (complete history, newest first), `/v3/reference/tickers` (market=stocks, active and delisted), next_url pagination, 1000 rows/page, 5 requests/minute shared with market_prices.

**Credential**: `MASSIVE_API_KEY`. **Licence**: Massive terms, individual non-professional use; no redistribution.

**Evidence**: `stock_split` events (split_from/split_to); `cash_dividend` events plus `cash_dividend_per_share` observations (currency/share) on the ex-date; `ticker:<primary MIC>:<T>` listings with ticker identifier assignments (delisted rows valid_to the delisting time), `issuer_listing` from `sec:cik`, `listing_security` to `figi:<composite FIGI>`, `primary_listing` from `ticker:US:<T>` for active tickers. Alpaca corporate actions (mergers/spin-offs) are not configured.

Canonical authorized-feed JSONL imports keep the strict contract.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire market_corporate_actions --dry-run
wm acquire market_corporate_actions --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run market_corporate_actions
wm verify market_corporate_actions
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
