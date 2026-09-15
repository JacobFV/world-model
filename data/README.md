# Dataset catalog

Every child directory owns one source, computation or graph dataset. Its tracked
`dataset.json` declares external dependencies, source metadata, parameters and
schema 2 stages. Source-specific processing lives in the adjacent `pipeline.py`,
with optional local helpers, JSON configuration resources and tests.

The `demo_countries` pipeline has a real `parsed` → `normalized` dependency.
`rando_joes_happiness_index` computes from that normalized dataset; `world_graph`
is another computed dataset. Other directories contain bounded public-source
mappings or explicit unavailable feeds. A pipeline file does not imply that
credentials, a usable sample or redistribution rights are available.

## Files

- `dataset.json`, `pipeline.py`, helpers, configuration, tests and `README.md`: reviewable code and declarations.
- `manifests/`: compact acquisition/stage/sample indexes and convenience pointers, eligible for Git review.
- `artifacts/`: ignored raw bytes, stage records and authoritative full receipts/manifests.
- `scratch/`: ignored staging, locks and local run receipts.
- `index.sqlite` and other SQLite indexes: ignored, disposable query projections.

Compact manifests omit code source bodies and payload data. They retain references,
hashes and metadata; they cannot replace artifacts for verification. Review source
and configuration metadata before sharing it.

This is a greenfield layout. Older top-level `raw/`, `final/`, `processing/`, `runs/`
and `samples/` directories remain ignored. The runtime does not read, migrate or
delete their contents. New imports and builds use the paths above.

## Use

```sh
wm import demo_countries tests/fixtures/countries.csv
wm run demo_countries --stage parsed
wm inspect demo_countries/parsed
wm run demo_countries
wm verify demo_countries/normalized
```

`output_stage` determines the default result. `depends_on` declares local stage
inputs; `dependencies` declares other datasets. Verified content caches can skip
stage execution. Retention declarations do not automatically evict artifacts.

Sampling policies retain at most 100 rows and 1 MiB on the laptop, with a shared
64 MiB temporary download budget. `sample all --allow-network` explicitly requests
network acquisition; `explore DATASET` verifies the retained sample profile.
Sampling does not update the full-data raw pointer or establish complete coverage.

See [the dataset layout guide](../docs/dataset-layout.md) for schemas, Context APIs,
caching, storage and recovery, and [sampling](../docs/sampling.md) for acquisition
limits and source-specific access requirements.
