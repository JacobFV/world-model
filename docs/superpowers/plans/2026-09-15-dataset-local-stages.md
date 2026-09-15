# Dataset-local stages implementation

User approved the dataset-local code/artifacts/scratch/manifests design and requested greenfield implementation. Prior unrelated runtime/spatial work remains paused and must not be committed with this task.

- [x] Storage: artifacts/raw and artifacts/<stage>, scratch/runs, small tracked manifests and local ignore rules; preserve legacy local directories without implicit migration.
- [x] Catalog/runner: schema2 stage DAG, local trusted code, schema checks, declared cache/retention, exact code/input provenance, stage selection and immutable publication.
- [x] Dataset code: move source-specific logic into each data/<id>/pipeline.py, keep shared parsing/runtime helpers central; real parsed->normalized example.
- [x] Integration: route sampling/report/materialization/CLI paths through Store helpers; scaffold new local datasets; package local pipeline code in wheels.
- [x] Verification: targeted storage/stage tests, full applicable baseline suite, clean source snapshot, installed wheel and sample/demo/lineage round trips; inspect Git payload exclusions.
- [x] Docs/release: document new architecture, keep a compact verified example manifest set in Git, commit/push source and metadata only after verification.

Interfaces agreed: Store.scratch_dir, runs_dir, latest_path(dataset,stage=None), raw_latest_path, samples_dir, sample_latest_path, publish_index(ref,update_latest=True). Named stage refs contain dataset/stage/version. Runner.run accepts stage selection; Context exposes stage_records/stage_ref for explicitly declared local dependencies. Full provenance is an artifact; tracked indexes omit source bodies but retain source hashes, schemas, rights and immutable input/output refs.

Verification evidence: see `docs/dataset-layout-verification.md`. Source-only clean export:355 tests passed with6 acquired-data skips; installed wheel stage/demo/sample/provenance roundtrip passed. No external data acquired.
