# sec_financial_statements

SEC Financial Statement Data Sets: statement values as filed.

**Source**: 55 quarterly ZIPs, `2012q4.zip` .. `2026q2.zip`, from https://www.sec.gov/files/dera/data/financial-statement-data-sets/. Total 5,186,899,071 bytes, verified by HEAD on 2026-09-15. Each ZIP holds `sub.txt`, `num.txt`, `tag.txt` and `pre.txt`; the format is described at https://www.sec.gov/files/aqfs.pdf. The 2009q1-2012q3 files (452 MB) are left out to stay under the 5 GiB per-dataset cap. `sec_company_assets` (companyfacts) covers those years.

**Credential**: `SEC_USER_AGENT`. **Licence**: public domain (US government work). SEC notes the data are as filed and unverified.

**Evidence**:
- Scope: periodic reports only (10-K, 10-Q, 20-F, 40-F, plus transition and amended forms) and only the standard us-gaap/ifrs/dei tags that companyfacts uses. Metric names are identical to `sec_company_assets`. Custom extension tags are skipped.
- Each observation is on `sec:cik:<10 digits>`. `observed_at` is the filing date.
- The end date comes from `ddate`. For durations, `valid_from` is derived as (end plus one day) minus `qtrs`×3 months, and is flagged `period_start_derived`.
- Dimensions: `concept`, `form`, `fiscal_period`, `fiscal_year`, `qtrs`, `period_type`. When published, they also include `statement` (from `pre.txt`), `segments` and `coregistrant`.
- Record ids end with the physical line in `num.txt`, because that file occasionally repeats a key.
- Attributes: `accession`, `taxonomy_version`, `as_filed_label`, statement `placements` (stmt:report:line), `negating_label`, `amended_by_later_filing`.
- Every value is kept as filed, including prior-period comparatives. That differs from companyfacts, which drops repeated values.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm budget
wm acquire sec_financial_statements --dry-run
wm acquire sec_financial_statements --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run sec_financial_statements
wm verify sec_financial_statements
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
