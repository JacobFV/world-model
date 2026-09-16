# sec_issuer_reference

SEC EDGAR submissions: issuer reference and filing index.

**Source**: `https://data.sec.gov/submissions/CIK{cik10}.json` for the 8,022 CIKs in `https://www.sec.gov/files/company_tickers_exchange.json` (snapshot 2026-09-14). Only the `recent` filing array is fetched (overflow pages are not). Measured size is ~1.7x the plan estimate (long-lived filers carry up to 1,000 recent filings), so `desired_bytes` is 900 MB.

**Credential**: `SEC_USER_AGENT`; rate 8 requests/second shared with other SEC datasets (`rate_limit.key: sec.gov`). **Licence**: public domain.

**Evidence**: issuer entity `sec:cik:<10>` (SIC, filer category, state of incorporation, fiscal year end); identifier assignments (`sec_cik`, `ein`, `lei` when published); `classified_as` `sic:<code>`; `registered_in` `geo:US:state:<FIPS>`; dated `legal_name` assertions from formerNames; `issuer_listing` to `ticker:XNAS|XNYS|CBOE|OTC|SEC_UNSPECIFIED:<symbol>` (snapshot, undated); one `sec_filing` event per filing (form, accession, filing/report date, 8-K items, primary document) at acceptance time. No security identity is inferred.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire sec_issuer_reference --dry-run
wm acquire sec_issuer_reference --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run sec_issuer_reference
wm verify sec_issuer_reference
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
