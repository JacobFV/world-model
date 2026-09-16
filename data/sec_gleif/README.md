# sec_gleif

GLEIF LEI Level 1 golden copy plus ISIN/BIC mappings.

**Source**: GLEIF golden copy `lei2` CSV (LEI-CDF 3.1, 3,431,064 records, publish 2026-09-15 16:00 UTC; URLs rotate three times daily, refresh from `https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest`), ISIN-LEI mapping (9.3M rows) and BIC-LEI mapping from `https://mapping.gleif.org/api/v2/{isin-lei,bic-lei}/latest`.

**Credential**: none. **Licence**: CC0 1.0.

**Evidence**: one `lei:<LEI>` entity per record (`organization`, `investment_fund`, `government_agency` or `business` by category) with jurisdiction, legal form, entity/registration status, addresses' country/region, registration authority ids and key dates; `registered_in` for US jurisdictions (`geo:US` / `geo:US:state:<FIPS>`); `successor_entity`; `issuer_security` `lei:` -> `isin:` for US and CA ISINs only (2.3M of 9.3M rows; the rest stay in raw); BIC identifier assignments. Legacy GLEIF API JSONL samples keep the original path.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire sec_gleif --dry-run
wm acquire sec_gleif --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run sec_gleif
wm verify sec_gleif
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
