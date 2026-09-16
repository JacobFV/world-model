# ssga_dia_premium

DIA premium/discount history.

**Source**: `pdhist-us-en-dia.xlsx`. **Credential**: none. **Licence**: SSGA terms, informational use; no redistribution.

**Evidence**: daily `fund_premium_discount` (percent of NAV) on `ssga:fund:DIA`.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire ssga_dia_premium --dry-run
wm acquire ssga_dia_premium --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run ssga_dia_premium
wm verify ssga_dia_premium
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
