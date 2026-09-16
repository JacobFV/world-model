# openalex_people

OpenAlex US institutions and strategic-institution authors.

**Source**: OpenAlex `/institutions` (country US, type company|facility|government; 13,075) and `/authors` whose last known institution is one of 43 strategic US defense, national-laboratory and technology institutions (IDs resolved by OpenAlex search 2026-09-15; see dataset.json) with works_count > 99 (12,998). Anonymous API budget ~1000 requests/day.

**Licence**: CC0.

**Evidence**: `openalex:I...` institutions (business / government_agency / institution) with ROR/GRID/Wikidata identifiers, `institution_lineage_ancestor`, `works_count` and `citation_count`; `openalex:A...` persons with ORCID/Scopus identifiers, undated `last_known_affiliation`, works and citation counts. No employment is asserted. Legacy samples keep publication-affiliation years.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire openalex_people --dry-run
wm acquire openalex_people --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run openalex_people
wm verify openalex_people
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
