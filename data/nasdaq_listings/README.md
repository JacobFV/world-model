# nasdaq_listings

Nasdaq Trader symbol directories.

**Source**: `nasdaqlisted.txt` and `otherlisted.txt` (pipe-delimited, daily). **Credential**: none. **Licence**: Nasdaq Trader site terms, reference use with attribution; do not redistribute.

**Evidence**: `ticker:<MIC>:<symbol>` listing entities (XNAS for Nasdaq; XNYS, XASE, ARCX, BATS, IEXG, TXSE from the Exchange code) with security name, ETF flag, market tier and financial status; `listing_venue` to `mic:<MIC>`; ticker identifier assignments scoped by MIC. Test issues are skipped. No issuer or security identity is published or inferred. Re-acquire daily to build listing history.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire nasdaq_listings --dry-run
wm acquire nasdaq_listings --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run nasdaq_listings
wm verify nasdaq_listings
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
