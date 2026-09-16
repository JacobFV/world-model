# ssga_dia_nav

DIA NAV history.

**Source**: `navhist-us-en-dia.xlsx` (daily since 1998). **Credential**: none. **Licence**: SSGA terms, informational use; no redistribution.

**Evidence**: daily observations on `ssga:fund:DIA`: `fund_nav` (USD/share), `fund_shares_outstanding` (shares), `fund_net_assets` (USD); blanks carry `missing_reason`. Not market prices.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire ssga_dia_nav --dry-run
wm acquire ssga_dia_nav --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run ssga_dia_nav
wm verify ssga_dia_nav
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
