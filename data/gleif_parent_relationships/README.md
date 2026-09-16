# gleif_parent_relationships

GLEIF Level 2 relationships and reporting exceptions.

**Source**: GLEIF RR-CDF (487,964 relationship records) and REPEX (6,356,454 reporting exceptions) CSV golden copies, publish 2026-09-15 16:00 UTC.

**Credential**: none. **Licence**: CC0 1.0.

**Evidence**: assertions `lei:child` -> `lei:parent` with predicates directly_consolidated_by, ultimately_consolidated_by, fund_managed_by, subfund_of, feeder_fund_of, international_branch_of; `valid_from`/`valid_to` from RELATIONSHIP_PERIOD, accounting period, relationship/registration status, qualifiers and quantifiers as attributes. No equity stake or exposure is implied. Reporting exceptions: `parent_reporting_exception` claims only for ULTIMATE-category LEIs with NATURAL_PERSONS, NO_LEI, NON_PUBLIC or legal-obstacle/consent reasons; all (category, reason) pairs as `reporting_exception_count` aggregate observations. Legacy API JSONL samples keep the original path.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire gleif_parent_relationships --dry-run
wm acquire gleif_parent_relationships --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run gleif_parent_relationships
wm verify gleif_parent_relationships
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
