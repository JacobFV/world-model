# crossref_research

Crossref works funded by DoD (2024+) and DARPA (2022+).

**Source**: Crossref REST `/works` cursor pages with `select=` (no references/abstracts): funder `10.13039/100000005` from 2024-01-01 and `10.13039/100006502` from 2022-01-01. **Credential**: `SEC_USER_AGENT` used as contact User-Agent. Public pool observed 1 request/second.

**Licence**: Crossref metadata CC0; abstracts not requested.

**Evidence**: `doi:` research papers with `publication_metadata`, `citation_count` (citations at retrieval), `authored` from `orcid:` (or work-scoped author ids), `research_affiliation` to `ror:` when published else literal `research_affiliation_name`, `funded_by` to funder DOIs with award numbers. Legacy samples keep the original path.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire crossref_research --dry-run
wm acquire crossref_research --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run crossref_research
wm verify crossref_research
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
