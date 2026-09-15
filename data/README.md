# Versioned datasets

Each child directory is a dataset, whether imported, computed, or materialized as
a graph. Git retains the available data and its provenance together:

| Path | Git policy | Purpose |
| --- | --- | --- |
| `dataset.json` | Track | Dataset definition and sampling limits |
| `raw/<artifact>/payload` | Track | Retained source sample or imported input |
| `raw/<artifact>/receipt.json` | Track | Source metadata, acquisition context and checksum |
| `samples/<sample-id>/manifest.json` | Track | Selection criteria, source hash and sample profile |
| `samples/latest.json` | Track | Current exploratory sample, including blocked status |
| `final/<version>/` | Track | Immutable normalized/computed records, reports and manifests |
| `latest.json`, `raw-latest.json` | Track | Current published version/input |
| `processing/` | Ignore | Temporary downloads and in-progress writes |
| `runs/` | Ignore | Local execution logs; durable provenance remains in manifests |
| `.lock`, SQLite indexes and sidecars | Ignore | Writer coordination and rebuildable indexes |

All retained versions are tracked. Do not remove an older raw or final artifact
merely because it is no longer latest: another artifact may reference it. Verification
follows immutable references recursively, so payloads and their receipts/manifests
must travel together.

A clone can inspect the committed samples and computed results without fetching
sources again. They remain bounded, nonrepresentative snapshots; source access and
completeness are separate questions. Rebuild SQLite query indexes locally as needed.

```sh
python3 -m worldmodel explore sec_company_assets
python3 -m worldmodel verify reference_evidence
```

Python wheels continue to bundle only catalog definitions, fictional fixtures and
examples, not this artifact history. For bulk acquisitions outside the shared sample
collection, use an external `WORLD_MODEL_DATA` directory or `--data-root`.

Source datasets retain their own terms; see [data rights](../DATA_RIGHTS.md).
Sampling limits and commands are described in [laptop sampling](../docs/sampling.md).
