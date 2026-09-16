# Dataset catalog

121 declarations: 116 sources and 5 derived datasets. 107 publish a normalized stage,
totalling about 1.31 billion records built from 87.7 GiB of acquired raw data. Run
`wm catalog` for the authoritative per-declaration status; the counts here are a
2026-09-15 snapshot.

Every child directory owns one source, computation or graph dataset. Its tracked
`dataset.json` declares external dependencies, source metadata, parameters and
schema 2 stages. Source-specific processing lives in the adjacent `pipeline.py`,
with optional local helpers, JSON configuration resources and tests.

`demo_countries`, `demo_graph`, `rando_joes_happiness_index`, `world_graph` and
`strategic_scenarios` are deliberately fictional fixtures; `wm catalog` labels them
`offline_example` or `synthetic_example`. The rest are real public sources. A pipeline
file does not imply that credentials, acquired data or redistribution rights are
available: fourteen declarations have no published normalized stage, and 75 of the 107
that do carry `redistribution_review_required` in their rights metadata.

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

Full data is acquired with `wm acquire DATASET --allow-network` within the 100 GiB
fair-share budget (`wm budget`); see [full acquisition](../docs/full-acquisition.md).

The separate bounded `sample` path retains at most 100 rows and 1 MiB per source, with a
shared 64 MiB temporary download budget, and exists for schema exploration only.
`sample all --allow-network` explicitly requests network acquisition; `explore DATASET`
verifies the retained sample profile. Sampling does not update the full-data raw pointer
or establish complete coverage. Sample payloads may have been pruned to reclaim space
while their manifests remain, which makes `wm evidence-audit` abort.

See [the dataset layout guide](../docs/dataset-layout.md) for schemas, Context APIs,
caching, storage and recovery, and [sampling](../docs/sampling.md) for the sampling
limits and source-specific access requirements.
