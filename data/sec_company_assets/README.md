# sec_company_assets

SEC EDGAR XBRL companyfacts (point-in-time financial facts).

**Source**: `https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip` (nightly rebuild, ~1.41 GB ZIP, 20,362 per-CIK JSON members, 19.3 GB uncompressed). One snapshot per acquisition.

**Credential**: `SEC_USER_AGENT` (declared contact User-Agent, required by SEC fair access; <=10 requests/second).

**Licence**: US federal government work, public domain.

**Scope**: ~55 us-gaap concepts (balance sheet, income, cash flow, shares, EPS, dividends), dei cover-page shares outstanding and public float, 10 ifrs-full equivalents, from 10-K/10-Q/20-F/40-F/6-K/8-K (+amendments). companyfacts has no segment (dimensional) facts. See `CONCEPTS` and `FORMS` in [pipeline.py](pipeline.py).

**Evidence**: `observation` records on `sec:cik:<10 digits>`; `observed_at` = filing date (knowledge time); instants valid `[end, end+1d)`, durations `[start, end+1d)`; dimensions `concept`, `period_type`, `form`, `fiscal_period`, `fiscal_year`; attributes `accession`, `period_start`, `period_end`, `frame`, `revision` (`first_report` or `restated`). Unchanged comparative repeats in later filings are dropped. Metrics: total_assets, current_assets, total_liabilities, current_liabilities, liabilities_and_equity, stockholders_equity, total_equity_including_nci, cash_and_equivalents, cash_restricted_cash_and_equivalents, accounts_receivable, inventory, ppe_net, goodwill, intangible_assets_net, accounts_payable, long_term_debt(_current/_noncurrent), short_term_borrowings, retained_earnings, deposits, net_loans, revenue, cost_of_revenue, gross_profit, operating_expenses, costs_and_expenses, research_and_development_expense, sga_expense, operating_income, interest_expense, income_tax_expense, pretax_income, net_income, net_income_including_nci, eps_basic, eps_diluted, weighted_average_shares_basic/diluted, depreciation_amortization, share_based_compensation, operating/investing/financing_cash_flow, capital_expenditures, dividends_paid(_common), share_repurchases, debt_issuance_proceeds, debt_repayments, shares_outstanding, shares_outstanding_cover, public_float, dividends_declared_per_share. Units: USD (or reporting currency), USD/share, shares.

Legacy JSONL samples (companyconcept rows) still normalize through the original path.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire sec_company_assets --dry-run
wm acquire sec_company_assets --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run sec_company_assets
wm verify sec_company_assets
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
