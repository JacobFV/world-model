# sec_13f_history

SEC Form 13F institutional holdings history.

**Source**: 50 SEC DERA 13F data set ZIPs. They start with calendar-quarter files `2013q2_form13f.zip` .. `2023q4_form13f.zip` and continue with filing-window files `01jan2024-29feb2024_form13f.zip` .. `01jun2025-31aug2025_form13f.zip`, all from https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets. Total 2,675,170,639 bytes, verified by HEAD on 2026-09-15. The four newest releases are in `sec_ownership_datasets`, so the two datasets do not overlap.

**Credential**: `SEC_USER_AGENT`. **Licence**: public domain (US government work). SEC notes the data are as filed and unverified.

**Evidence**: The normalization matches `sec_ownership_datasets`' 13F branch.
- `reported_holding` assertions run from `sec:cik:<manager>` to `cusip:<CUSIP>`. Rows are aggregated per accession, CUSIP, put/call and SH/PRN.
- Each holding is valid on the report period end date and observed at the filing date.
- Attributes carry shares (or principal amount), `value_usd`, put/call and investment discretion.
- Each filing also gets a `reported_13f_portfolio_value` observation.
- Filings made before 2023-01-03 reported VALUE in thousands of USD. Those values are converted to USD and marked `value_source_unit: thousand_USD`.
- No issuer CIK is inferred.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm budget
wm acquire sec_13f_history --dry-run
wm acquire sec_13f_history --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run sec_13f_history
wm verify sec_13f_history
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
