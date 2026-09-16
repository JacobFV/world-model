# treasury_debt

U.S. Treasury Fiscal Service debt, cash and budget statistics from the Fiscal Data API
(`https://api.fiscaldata.treasury.gov`), normalized into evidence records.

## Source and scope

The acquisition is a `paged_api` over 20 endpoints. Each shard is one JSON page of 10,000 rows with
selected fields only. The request parameters `version`, `group`, `table` and `fields` are recorded in
the shard metadata.

- `v2/accounting/od/debt_to_penny`: daily total public debt, debt held by the public and
  intragovernmental holdings, 1993+.
- `v2/accounting/od/debt_outstanding`: historical public debt outstanding at fiscal year end.
- `v2/accounting/od/avg_interest_rates`: monthly average interest rates by security, 2001+.
- MTS tables 1, 2, 3, 4, 5, 6, 8 and 9: monthly receipts, outlays, deficit, budget estimates, outlays
  by agency and account, means of financing, trust funds, and receipts and outlays by source and
  function. Table 7 repeats monthly values across fiscal-year columns and is omitted.
- DTS tables, daily since October 2005: operating cash balance, deposits and withdrawals of operating
  cash, public debt transactions, cash-basis adjustments, debt subject to limit, federal tax deposits,
  short-term cash investments, income tax refunds and inter-agency tax transfers.

The home.treasury.gov daily par yield curve is **not** acquired here. `fred_macro_panel` carries the
H.15 constant-maturity Treasury curve (DGS1MO..DGS30), which is based on the same Treasury par yield
curve.

## Licence and credential

This is a U.S. federal government work with no redistribution restriction. No credential is needed.
The API has no documented hard limit; the acquisition makes 1 request/second.

## Records

- Entities: `us:agency:treasury` (government_agency) and `treasury:security_type:<slug>` (security),
  for example `bills`, `notes` or `inflation_protected_securities_tips`.
- Observations: `unit` is `USD`, or `percent` for average rates. DTS amounts are published in millions
  and are scaled using the page `meta.dataFormats`; `attributes.source_multiplier` records it.
  `dimensions.table` and `dimensions.period` are always set, together with the source category
  fields.
  - Debt: `public_debt`, `debt_held_by_public` and `intragovernmental_debt` (daily). `public_debt`
    from `debt_outstanding` has `dimensions.table=debt_outstanding`.
  - Rates: `average_interest_rate`, with the security entity as subject and a monthly valid period.
  - DTS, one-day valid periods: `treasury_operating_cash_balance_close` and `_open`,
    `treasury_cash_deposits`, `treasury_cash_withdrawals`, `public_debt_issues` and
    `public_debt_redemptions` (security subject), `public_debt_cash_basis_adjustment`,
    `debt_subject_to_limit_close_balance`, `federal_tax_deposits`, `short_term_cash_investments`,
    `income_tax_refunds_issued` and `inter_agency_tax_transfers`. Month-to-date and fiscal-year-to-date
    columns are derivable and are not acquired.
  - MTS: `federal_receipts`, `federal_outlays`, `federal_deficit_surplus`, `federal_budget_result`
    (and `_estimate`), `federal_receipts_outlays`, `federal_receipts_gross`, `_refunds`, `_net`,
    `federal_outlays_gross`, `_applicable_receipts`, `_net`, `federal_financing_*`, `trust_fund_*` and
    `federal_receipts_outlays_by_source_function`.
    - `dimensions.period` is `month` (calendar month), `fytd` (from October 1 through the record date),
      `fiscal_year`, `next_fiscal_year` or `day` (closing balances).
    - `classification_desc`, `line_code_nbr` and `sequence_number_cd` identify the line.
- Rows whose labels start with Total, Sub-Total, Net change or Equals are flagged
  `attributes.aggregate`. Null amounts are not emitted.

A single-payload artifact, such as the original 100-row sample or a local import, still goes through
the legacy Debt to the Penny sample adapter.

## Rebuild

```sh
wm acquire treasury_debt --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run treasury_debt
wm verify treasury_debt
python -m pytest data/treasury_debt/tests
```
