# fdic_bank_financials

FDIC BankFind financials, institutions and Summary of Deposits.

**Source**: `https://api.fdic.gov/banks/{financials,institutions,sod}` CSV: financials for every report date 2010-03-31..2026-06-30 (56 fields per bank-quarter), institution directory (27,833 active and inactive charters), 2025 Summary of Deposits (76,097 branches).

**Credential**: none. **Licence**: public domain.

**Evidence** (thousand-USD fields converted to USD): observations on `fdic:cert:<CERT>` valid `[REPDTE, REPDTE+1)` (YTD flows from Jan 1): total_assets, bank_deposits, bank_uninsured_deposits, bank_brokered_deposits, bank_net_loans, bank_loans_nonfarm_nonresidential_re, bank_loans_multifamily_re, bank_loans_construction_land_development, bank_loans_residential_1_4_family, bank_loans_commercial_industrial, bank_loans_consumer, bank_securities, bank_cash_and_due, bank_equity, bank_noncurrent_loans, bank_net_income (YTD), bank_net_charge_offs (YTD), bank_tier1_leverage_ratio (percent), bank_total_risk_based_capital_ratio (percent), bank_employees (people). `regulatory_high_holder` intervals to `rssd:<RSSDHCR>`; bank entities with `rssd` identifier, charter period and county (`geo:US:county:<FIPS>`); branch entities `fdic:branch:<UNINUMBR>` with `branch_of`, `located_in` and `branch_deposits` (USD, as of 2025-06-30). Other fetched fields stay in raw.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire fdic_bank_financials --dry-run
wm acquire fdic_bank_financials --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run fdic_bank_financials
wm verify fdic_bank_financials
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
