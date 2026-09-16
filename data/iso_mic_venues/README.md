# iso_mic_venues

ISO 10383 Market Identifier Codes.

**Source**: `ISO10383_MIC.csv` (2,883 MICs). **Credential**: none. **Licence**: free public reference list.

**Evidence**: `mic:<MIC>` trading venues, `published_mic_status` with creation/update/expiry dates, `mic_operating_venue` from segment to operating MIC, `venue_operator` to `lei:<LEI>` when published. Registration does not prove operation.

Implementation: [pipeline.py](pipeline.py); declaration: [dataset.json](dataset.json).

## Rebuild

```sh
wm acquire iso_mic_venues --dry-run
wm acquire iso_mic_venues --allow-network        # --resume after interruptions
WORLD_MODEL_RAW_VERIFY=size wm run iso_mic_venues
wm verify iso_mic_venues
```

Tests: `python3 -m unittest tests.test_companies_markets_research_datasets`.
`artifacts/` and `scratch/` are ignored by Git. Cross-dataset identity notes: [docs/data/companies-markets-research.md](../../docs/data/companies-markets-research.md).
