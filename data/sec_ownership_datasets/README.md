# sec_ownership_datasets

SEC Form 13F holdings and Forms 3/4/5 insider data sets.

**Source**: SEC DERA structured data ZIPs: four Form 13F data sets `01sep2025-30nov2025` .. `01jun2026-31aug2026_form13f.zip` (one year of filings; the latest under `/files/datastandardsinnovation/`, earlier under `/files/structureddata/`) and fourteen Form 345 data sets `2023q1`..`2026q2_form345.zip` (2026q2 under `/files/datastandardsinnovation/`). Expanded after the shared budget rose to 50 GiB.

**Credential**: `SEC_USER_AGENT`. **Licence**: public domain; SEC notes data are as filed and unverified.

**Evidence**:
- 13F-HR(/A): `reported_holding` assertions `sec:cik:<manager>` -> `cusip:<CUSIP>` aggregated per accession/CUSIP/put-call/SH-PRN, valid on the report period end, observed at the filing date; attributes shares (or principal_amount), value_usd, put_call, investment_discretion. `reported_13f_portfolio_value` (USD) per filing. Security entities carry issuer name/title/FIGI; 13F has no issuer CIK, so none is inferred.
- Forms 3/4/5: `insider_of` (owner CIK -> issuer CIK) per quarter with relationship and officer title; `insider_transaction` and `insider_derivative_transaction` events (code, shares, price, acquired/disposed, shares owned following); `insider_shares_owned` observations (shares) from non-derivative holdings. Derivative holdings and footnotes remain raw only.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire sec_ownership_datasets --dry-run
wm acquire sec_ownership_datasets --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run sec_ownership_datasets
wm verify sec_ownership_datasets
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
