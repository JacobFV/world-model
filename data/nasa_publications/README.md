# nasa_publications

NASA.gov RSS feed metadata.

**Source**: `https://www.nasa.gov/feed/?paged=1..20` (~200 most recent posts). **Credential**: none. **Licence**: NASA content generally public domain; metadata only.

**Evidence**: `nasa:publication:<digest>` posts with `published_by` `us:agency:nasa` and `publication_metadata` (time, URL, author) as publisher claims, not verified world state. Parsed with a DTD/entity-rejecting stdlib reader.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire nasa_publications --dry-run
wm acquire nasa_publications --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run nasa_publications
wm verify nasa_publications
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
