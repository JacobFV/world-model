> **Historical record.** A dated verification of the storage layout when the catalog held
> 43 datasets. The layout contract it checks is still current
> ([dataset-layout.md](dataset-layout.md)); its counts are not — the catalog now has 121
> declarations. See [strategic-affordances-audit.md](strategic-affordances-audit.md).

# Dataset-local layout verification — 2026-09-15

## Implemented

All 43 bundled datasets have schema 2 declarations, local pipeline code, README
and ignore rules. Named stage DAGs support schema validation, content caching,
explicit retention policy, exact local code/input snapshots and stage selection.
The fictional country pipeline exercises parsed → normalized; the happiness
formula lives in its derived dataset directory.

Storage uses ignored `artifacts/` and `scratch/`, with compact indexes under
`manifests/`. CLI, sampling, standalone artifact publication, materialization and
graph query provenance support this layout. Graph indexes use schema 2 and
request an explicit rebuild for older indexes.

## Checks performed

- Clean export of staged source: `python3 -m unittest discover -s tests -q`:
  **355 tests passed, six skipped**. The skips require acquired real-world samples,
  which are absent from a clean source export. A non-failing SQLite connection
  ResourceWarning appeared during the suite.
- Independent storage, stage, adapter and packaging reviews. Regression checks
  cover corrupt caches, local helper changes, evidence validity, stage provenance,
  source-distribution JSON resources, old pointer exclusion and payload symlinks.
- Built a source distribution, then a wheel from its extracted contents.
  Inspected the wheel: **43 local pipelines**, no generated artifacts, scratch,
  compact runtime manifests, old latest pointers or paused implementation files.
- Installed the wheel into an isolated environment and exercised CLI resources,
  demo, verify, lineage, observations, sample, named-stage run and inspect.
  Result: seven graph records, five lineage versions, two sampled fictional rows,
  and preserved named-stage query provenance.
- Built the fictional demo using a clean source export into the checkout's new
  storage paths; verified its graph recursively. Added **26 compact metadata files
  totaling 83,657 bytes**. No payloads or full source-bearing manifests are staged.
- Inspected staged paths and Git exclusions. Existing local legacy payloads were
  preserved. Paused simulation/journal source and tests remain outside this change.

## Practical limits

No external datasets were downloaded and no full-scale GB10 run was attempted.
A clone includes definitions, code and compact example indexes; it must rebuild
or obtain artifact bytes before verifying those example indexes. There is no
legacy-data migration, automatic garbage collection or remote artifact store.
Pipeline Python is trusted code; undeclared external reads are not automatically
tracked. Code remains MIT; dataset terms remain separately recorded and inherited.
