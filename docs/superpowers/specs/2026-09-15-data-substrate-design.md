# World model data substrate

## Contract
Everything is a named dataset. External and computed datasets share the same
storage and publication contract. The graph is a derived dataset. Simulation
processes are outside this build. No external datasets are downloaded here.

## Layout and ownership
`data/<dataset>/dataset.json` declares purpose, schema version, source metadata,
transformation entrypoint, dependencies and parameters. `raw/<sha256>/` contains
immutable imported bytes and acquisition receipts. `processing/<run-id>/` contains
staged results and failure records. `final/<version>/` contains immutable JSONL
records and a content-addressed manifest. `runs/<run-id>.json` records attempts.
`WORLD_MODEL_DATA` relocates all data directories to workstation storage; tracked
catalog definitions remain in this project. Outputs and caches are git-ignored.

## Provenance
A final version hashes dataset definition, input version refs, raw artifact refs,
parameters, output checksums, and implementation identity. Implementation identity
includes the entrypoint, all project Python/config file hashes, optional Git commit
and dirty status, Python version and installed package versions. Runs capture times
and errors independently of deterministic version identity. Each evidence record
references an input artifact and row/JSON pointer, or input version and record ID.
Manifests preserve full definitions and code snapshots for audit after edits.
Lineage inspection recursively verifies and traverses input versions and artifacts.

## Execution
Python 3.11+ standard library; no service dependency. A dependency DAG is validated
before work; topological execution captures exact inputs. File locks prevent two
writers to one dataset; staging then rename prevents partial publication. Transformation failures
leave an auditable run with no visible final version. Rename commits publication;
later pointer/audit I/O failures return the published ref with a bookkeeping warning. Identical builds reuse the
same immutable version. Source downloads require an explicit CLI flag and URL;
local imports do not access the network. Transport is bounded streaming HTTP with
retry, timeout, checksums, and acquisition metadata; source-specific normalization
is a plugin, not a guessed universal mapping. JSONL and CSV readers stream. JSON
array responses are explicitly limited in size; large archives remain raw and
need a partitioned extraction plugin on the workstation.

## Evidence and graph
Minimal versioned contracts: entity, observation, assertion and event. Entities
have namespaced IDs and types. Record identity is separate from entity identity;
multiple sources may describe one entity without overwriting evidence. Observations have a metric, value, unit, dimensions,
valid period, observation timestamp and evidence. Assertions have subject,
predicate, entity object or literal value, valid time, evidence, and optional
confidence/methodology. Events have event time and participants. Statistical
aggregates remain observations; they never fabricate named entities. The graph
preserves conflicting assertions. An indexed SQLite projection supports bounded
neighborhoods, valid-time and knowledge-time filters; no automatic truth ranking.
Rebuilding the projection cannot change the evidence datasets.

## Scope and validation
Implement the storage/lineage engine, plugin runner, generic import/download,
CSV/JSONL/Census response normalization, extensible ontology, source catalog for
the proposed bootstrap families, offline fixture pipelines and computed happiness
example. Source-family catalog entries explicitly distinguish configured adapters
from planned specialized mappings; an unconfigured production dataset fails closed.
Test immutable publication, tampering, failure isolation, lineage, cycles, record
validation, conflicting claims, temporal filtering, and the CLI end to end.
