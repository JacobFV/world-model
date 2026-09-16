# Companies, markets and research datasets

Cross-dataset notes for `sec_company_assets`, `sec_issuer_reference`, `sec_ownership_datasets`,
`sec_gleif`, `gleif_parent_relationships`, `companies_house_uk`, `fdic_bank_financials`,
`nasdaq_listings`, `iso_mic_venues`, `nasdaq_index_reference`, `ssga_dia_*`, `market_prices`,
`alpaca_daily_bars`, `market_corporate_actions`, `crossref_research`, `openalex_people` and
`nasa_publications`. Per-dataset scope, licence and metrics are in each `data/<id>/README.md`.

## Identity namespaces

Issuer, security and listing identities stay distinct (see
[financial evidence imports](../financial-evidence-imports.md)). Nothing is merged by name.

`market_corporate_actions` is `full_acquisition_configured` in `wm catalog` and has no published normalized
artifact yet, so the namespaces attributed to it below describe what its pipeline will emit, not data you can
join today. Every other dataset on this page has a published normalized stage.

| Kind | Namespace | Emitted by | Join evidence |
| --- | --- | --- | --- |
| Issuer | `sec:cik:<10 digits>` | sec_company_assets, sec_financial_statements, sec_issuer_reference, sec_ownership_datasets and sec_13f_history (13F managers, insiders, issuers), market_corporate_actions (`issuer_listing` subject) | CIK published by SEC or Massive |
| Legal entity | `lei:<20 chars>` | sec_gleif, gleif_parent_relationships, iso_mic_venues | `identifier_assignment` `lei` on `sec:cik` when SEC submissions publish one; no CIK-LEI inference otherwise |
| Security | `isin:<12>` | sec_gleif (`issuer_security` from LEI; US/CA ISINs) | GLEIF/ANNA mapping |
| Security | `cusip:<9>` | sec_ownership_datasets and sec_13f_history (13F), ssga_dia_holdings (check-digit-valid identifiers) | CUSIP is the 9 characters inside a US ISIN (`US` + CUSIP + check digit); this derivation is not emitted |
| Security | `figi:<composite FIGI>` | market_corporate_actions (`listing_security`) | Massive ticker directory |
| Venue listing | `ticker:<MIC>:<symbol>` | nasdaq_listings, sec_issuer_reference (XNAS/XNYS; other SEC venues source-scoped), nasdaq_index_reference, market_corporate_actions (primary exchange MIC) | venue MIC + symbol; symbols are reused, use dated records |
| Consolidated listing | `ticker:US:<symbol>` | market_prices, alpaca_daily_bars, market_corporate_actions (splits/dividends) | `primary_listing` (active tickers) links to `ticker:<MIC>:<symbol>` |
| Venue | `mic:<MIC>` | iso_mic_venues, nasdaq_listings | ISO 10383 |
| Bank | `fdic:cert:<CERT>`, holding company `rssd:<id>`, branch `fdic:branch:<UNINUMBR>` | fdic_bank_financials | FED_RSSD identifier assignment on the bank |
| Funds | `ssga:fund:<TICKER>` | ssga_dia_* | publisher ticker |
| UK company | `gb:companies_house:<number>` | companies_house_uk | company number; corporate PSCs link via `registered_as` only from a published UK registration number |
| UK PSC register entry | `gb:psc:<psc id>` | companies_house_uk (`significant_control_over` with natures of control and share bands) | per-company register entry; the same person across companies is not merged |
| Research | `doi:`, `orcid:`, `ror:`, `openalex:I…`/`openalex:A…` | crossref_research, openalex_people | published identifiers only |
| Geography | `geo:US`, `geo:US:state:<FIPS>`, `geo:US:county:<5-digit FIPS>` | sec_gleif, sec_issuer_reference, fdic_bank_financials | |
| Classification | `sic:<code>`, `uksic2007:<code>` | sec_issuer_reference, companies_house_uk | |

Typical join path from a price series to fundamentals: `ticker:US:AAPL` -`primary_listing`->
`ticker:XNAS:AAPL` <-`issuer_listing`- `sec:cik:0000320193` (market_corporate_actions or
sec_issuer_reference) -> companyfacts observations on the same CIK. Delisted tickers carry
`valid_to`; historical bars for a reused symbol must be matched by date.

## Time semantics

- **Knowledge time**: SEC facts, 13F holdings and Forms 3/4/5 use the filing date as
  `observed_at`; later restatements are separate observations (`revision: restated`).
  Other snapshots use the retrieval time.
- **Validity**: point observations are `[date, date+1)`; flows use their reporting period
  (`[start, end+1)`, FDIC YTD from Jan 1). Snapshot-only facts (ticker directories, SEC ticker
  lists, OpenAlex last known institution) carry no validity interval.
- **Adjustment bases** differ and use different metric names: `close_price_split_adjusted`
  (Massive, 2024-09-16+, all US tickers) vs `close_price_total_return_adjusted` (Alpaca,
  2016-01-01..2026-09-14, 11,611 symbols — every non-test listed US equity and ETF in the
  2026-09-15 Nasdaq Trader directories — split and dividend adjusted). Provider adjustments are
  as of retrieval.

## Fields requested by the estimation layer

| Requirement | Dataset | Metric | Selector and time fields |
| --- | --- | --- | --- |
| us-gaap:CashAndCashEquivalentsAtCarryingValue | sec_company_assets | `cash_and_equivalents` | `dimensions.concept`; `observed_at` = filed; `valid_from`/`valid_to` and `attributes.period_start`/`period_end`; `dimensions.form`, `fiscal_period`, `fiscal_year`; `attributes.accession`; `unit` |
| us-gaap:Revenues | sec_company_assets | `revenue` | `dimensions.concept = us-gaap:Revenues` |
| us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | sec_company_assets | `revenue` | `dimensions.concept` distinguishes the alternative tag; choose one tag per filer and period to avoid double counting |
| us-gaap:CostsAndExpenses | sec_company_assets | `costs_and_expenses` | as above |
| us-gaap:PaymentsToAcquirePropertyPlantAndEquipment | sec_company_assets | `capital_expenditures` | as above |
| FDIC DEP | fdic_bank_financials | `bank_deposits` (USD) | subject `fdic:cert:<CERT>`; `valid_from` = REPDTE, `valid_to` = REPDTE+1; `dimensions.report_date`; `attributes.source_field` |
| FDIC NCLNLS | fdic_bank_financials | `bank_noncurrent_loans` (USD) | as above |
| FDIC LNLSNET | fdic_bank_financials | `bank_net_loans` (USD) | as above |

In companyfacts, a later filing that repeats an unchanged value is dropped. A changed value is kept as a separate observation with `attributes.revision = restated`. To get as-of-date values, select the latest `observed_at` that is on or before the knowledge cutoff.

## Overlapping sources

- **Fundamentals.** `sec_company_assets` (companyfacts) holds one deduplicated point-in-time series per concept for 2009 onward. `sec_financial_statements` holds every value as filed for 2012Q4 onward, with statement placement, labels and segment/coregistrant dimensions. Metric names are shared. Filter on `dimensions.segments` being absent before comparing the two.
- **13F holdings.** `sec_13f_history` covers filings 2013-04..2025-08. `sec_ownership_datasets` covers filings 2025-09..2026-08. The two do not overlap and use the same predicates.
- **Companies House.** The monthly accounts bulk product (iXBRL/HTML, tens of GB per year) and the officers bulk product are not configured. Accounts would need a separate iXBRL extraction pipeline, and the officers file is distributed on request rather than as a public download.

## Coverage caveats for estimation

- companyfacts has no segment facts; revenue appears under several concepts (dimension
  `concept`) that can overlap for the same period.
- 13F positions are long equity/option/principal positions of managers above the filing
  threshold for one quarter; no shorts and no issuer CIK.
- The Alpaca universe is all currently listed equities and ETFs (no delisted tickers: survivorship bias).
- 13F covers four quarterly data sets (filings 2025-09..2026-08); Forms 3/4/5 cover 2023Q1-2026Q2.
- PSC control is a band (e.g. 25-50% of shares), not an exact stake; UK PSC and US 13F/insider evidence are not linked to each other.
- GLEIF Level 2 is accounting consolidation, not ownership percentages; REPEX claims are
  restricted (see gleif_parent_relationships README); ISIN links cover US/CA ISINs only.
- FDIC normalizes 20 of 56 fetched Call Report fields; the rest remain in raw.

## Build notes

Long builds were run through a wrapper that snapshots `worldmodel/` and the target
declaration, because `worldmodel.provenance.capture_code` hashes every `data/*/dataset.json`
and all shared code, so any concurrent edit elsewhere fails `verify_code_snapshot` at the end
of a long run. Capturing only the target dataset's declaration and its dependencies would
remove that coupling.
