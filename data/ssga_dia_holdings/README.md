# ssga_dia_holdings

SPDR ETF daily holdings (DIA, SPY, MDY, 11 sector SPDRs).

**Source**: `holdings-daily-us-en-{ticker}.xlsx` for DIA, SPY, MDY, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY. **Credential**: none. **Licence**: SSGA website terms, informational/personal use; no redistribution.

**Evidence**: dated `investment_position` entities with `position_holder` (`ssga:fund:<TICKER>`) and `position_instrument` (`cusip:<id>` when the published identifier is a check-digit-valid CUSIP, else a publisher-scoped instrument); `position_shares` (shares) or `position_cash` (USD) and `portfolio_weight` (percent; futures overlays can be slightly negative). Not index membership or issuer ownership.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire ssga_dia_holdings --dry-run
wm acquire ssga_dia_holdings --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run ssga_dia_holdings
wm verify ssga_dia_holdings
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
