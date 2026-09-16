# world_evidence

The catalog-scale unified graph. `python3 -m worldmodel unify` streams the **published
normalized outputs** of every catalog dataset in the selected scope straight into
`index.sqlite`, and publishes an immutable summary artifact here that pins every input as
`dataset@stage@version`.

[docs/unified-graph.md](../../docs/unified-graph.md) is the authority on scopes, measured
record counts, build times, index sizes and the cross-dataset queries the index answers.

## What is published here

| Path | Content |
| --- | --- |
| `index.sqlite` | the queryable graph: `records`, `edges`, `resolved`, `metadata` (schema 3). Rebuildable, gitignored. |
| `artifacts/final/<version>/report.json` | the build summary: scope, per-dataset records read / indexed / entities / assertions / observations / events, skipped datasets with reasons, timings, peak RSS |
| `artifacts/final/<version>/manifest.json` | `inputs` pins every source dataset version; `code` pins the unify code snapshot; `rights` inventories every source's terms |
| `manifests/final/<version>.json` | the compact tracked index of the above |

`records.jsonl` in the artifact is intentionally **empty**. A record union at this scale would
duplicate hundreds of gigabytes of published evidence; the union is materialized in
`index.sqlite`, whose `metadata.inputs` pins exactly the same versions as the manifest.

## Commands

```sh
python3 -m worldmodel unify-scope --inventory        # every dataset, its published output, its row count
python3 -m worldmodel unify-scope                    # what the default profile selects and skips, and why
python3 -m worldmodel unify                          # build the default scope into index.sqlite and publish
python3 -m worldmodel unify --domain politics --index data/world_evidence/politics.sqlite
python3 -m worldmodel unify --all                    # exhaustive; see the cost table in the guide first
python3 -m worldmodel unify-resolve --workdir /tmp/resolve   # attach asserted identity clusters
python3 -m worldmodel graph-neighborhood lei:5493001KJTIIGC8Y1R12 --hops 2 --resolved
```

## Scope and evidence

Records are copied verbatim from each dataset's published output stage. Nothing is merged,
deduplicated or reconciled by the build: contradictions remain in the index as separate
evidence. A scope narrower than `--all` indexes a documented subset, so an absent edge may mean
*out of scope* rather than *absent from the evidence* - the summary's `skipped` list names every
dataset that was left out and why.

Unify verifies each input's **output** checksums before reading it. It does not re-hash raw
acquisition payloads; `python3 -m worldmodel verify <dataset>` still performs the full recursive
lineage check.

The `graph` stage declared in `dataset.json` is the superseded sample-era union of eleven bounded
samples. `unify` no longer runs it.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work.
Both are ignored by Git, as is `index.sqlite`. Source terms and access requirements remain in each
source dataset's `dataset.json`; repository code licensing does not grant dataset redistribution
rights.
