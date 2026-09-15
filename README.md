# World model

**A graph built from versioned evidence. Everything is a dataset, including computations.**

```text
external files → raw snapshots → normalized evidence ─┐
                                                     ├→ computed datasets → world_graph
other versioned datasets ────────────────────────────┘                         ↓
                                                                  SQLite query index
```

This repository implements the data substrate. It keeps source observations,
entity descriptions, relationship assertions, and events separate. It does not
choose a single truth when sources disagree. A typed process registry and temporal materializer now consume versioned graph
snapshots. See [the kernel guide](docs/world-kernel.md) for contracts, examples and
current limits.

## Running from a fresh checkout

Use Python 3.11 or later from the repository root:

```sh
python3 -m unittest discover -s tests
python3 -m worldmodel demo
python3 -m worldmodel catalog
```

The fictional demo and fixture-based checks run offline. Acquired-data integration
checks may skip when their local samples are absent; offline fixtures do not verify
current external access or dataset completeness.

Downloaded evidence, generated dashboards and runtime artifact versions stay local
and are excluded from Git. Real-evidence examples require their documented acquisition
and build steps first. Committed verification reports record prior local runs;
they do not bundle those datasets. Standalone wheels now include the read-only catalog,
fictional fixtures and examples; writable runtime data remains separate.

See [remaining concerns](docs/remaining-concerns.md) for implementation priorities,
missing evidence, validation limits and licensing status.

## Strategic systems and field foundations

The project includes fields/topologies with lazy graph projections, multimodal
routing, bank accounting, energy/business scenarios, numerical actor kernels,
lifecycle events, policy comparison and chronological model validation. A coupled
synthetic economy connects production and per-step purchasing/credit policies to one
commercial-bank ledger. `SpatialStore` adds persistent SQLite state, bounded spatial
reads and transactional birth/death/merge/split operations with conservation checks.
See [the current guide](docs/strategic-systems.md) for runnable examples, acquired
source coverage and the limits of each model.

## Reference identities and markets

The [reference backbone](docs/reference-backbone.md) adds bounded public-person and
market sources, dated identifier resolution, independent coverage maps and financial
obligation stress with explicit evidence inputs.

## Environments and visual surfaces

[Materialization environments](docs/environments-and-surfaces.md) map declared inputs
to actions, selected outputs to observations, and explicit state criteria to rewards.
The same API handles multi-entity and single-subject views. The CLI now defaults to
incremental checkpoint execution; `--backend replay` preserves the reference evaluator.
JSON checkpoints retain scheduler state, RNG and memory, with transactional rollback
and explicit limitations for external backend effects.

Bounded tabular training/evaluation, synchronous vector environments and observation
masks/delays/noise are available in the Python API. Gymnasium is an optional adapter
for explicitly bounded scalar spaces. Standalone HTML surfaces compose values, tables,
plots, coordinate maps and graph diagrams. Set `"interactive": true` for linked entity
selection, filtering, playback, source inspection and panel layout controls.

## Typed graph and process views

```sh
python3 -m worldmodel unify
python3 -m worldmodel ontology
python3 -m worldmodel processes
python3 -m worldmodel materialize world_evidence --request examples/california-population.json
python3 -m worldmodel verify california_population_scenario
```

`unify` uses the existing bounded real samples and performs no downloads.
Forecasts are explicit scenarios using illustrative, uncalibrated processes.
[Implementation and remaining capabilities](docs/world-kernel.md).

## Run it now, offline

Python 3.11+ on macOS or Linux. No runtime dependencies, services, credentials,
or data downloads are required for the core fictional demo. Run from this checkout
or use the installed `wm` command:

```sh
python3 -m worldmodel catalog
python3 -m worldmodel plan world_graph
python3 -m worldmodel --data-root /tmp/worldmodel-demo demo
python3 -m worldmodel --data-root /tmp/worldmodel-demo neighbors org:acme
python3 -m worldmodel --data-root /tmp/worldmodel-demo observations rando_joes_happiness_index
python3 -m worldmodel --data-root /tmp/worldmodel-demo lineage world_graph
python3 -m worldmodel --data-root /tmp/worldmodel-demo verify world_graph
python3 -m unittest discover -s tests -v
```

`demo` imports two tiny **fictional** fixtures. It produces two country index
values (65 and 50), two conflicting ownership claims, and an aggregate observation
of 217 establishments. That observation does not create 217 businesses.

All commands emit JSON. Errors go to stderr with a nonzero exit code. You can
optionally install the CLI with `python3 -m pip install -e .` and use `wm`.
A built wheel also supplies the catalog, fictional demo fixtures, examples and
implementation source for provenance. Inspect installation paths with:

```sh
wm resources
wm --data-root /tmp/worldmodel-installed-demo demo
```

Outside a checkout, the default writable root is
`$XDG_DATA_HOME/worldmodel` (or `~/.local/share/worldmodel`). `WORLD_MODEL_DATA` and
`--data-root` override it; `--catalog-root` selects another declaration tree. Installed
examples are located under the path returned by `wm resources`, so relative
`examples/...` paths below refer to the checkout. Optional Gymnasium support is
available through the `rl` installation extra; the core package has no runtime dependencies.

## Code and dataset rights

Code and documentation are [MIT licensed](LICENSE). Third-party source data retains
its own terms. Derived artifacts preserve input lineage and source license/attribution
metadata, including unspecified or restricted terms; MIT does not relicense those
inputs. `wm rights DATASET` reports the inherited inventory. Unknown terms do not block
local computation, but the metadata does not grant redistribution rights or decide
license compatibility. See [code and data rights](DATA_RIGHTS.md).

## Laptop-sized real samples

All datasets now have individual sampling policies: at most 100 retained rows /
1 MiB, with a shared 64 MiB temporary disk buffer for whole-file downloads.

```sh
python3 -m worldmodel sample all --allow-network
python3 -m worldmodel explore census_business
```

Small real samples have been acquired and explored. See
[results and blockers](docs/sample-exploration-2026-09-15.md) and
[sampling budgets, archives and provenance](docs/sampling.md).
Samples remain separate from full pipeline pointers. API subsets, CSV, ZIP/CSV,
and small XLSX are supported; source-specific limits and credentials still apply.

## Dataset-local directory contract

Each dataset keeps its declaration, processing code, tests and reviewable metadata
in one directory. Source-specific adapters and formulas live in `pipeline.py`;
shared ontology and generic readers remain in `worldmodel/`.

```text
data/demo_countries/
  dataset.json                   # tracked schema 2 declaration and stage DAG
  pipeline.py                    # tracked parse/normalize implementation
  README.md, .gitignore
  tests/                         # local acceptance tests when present
  manifests/                     # compact metadata and convenience pointers
    raw/<artifact>.json
    parsed/<version>.json
    normalized/<version>.json
    stages/<stage>/latest.json
    raw-latest.json, latest.json
  artifacts/                     # ignored immutable payloads and full manifests
    raw/<artifact>/{payload,receipt.json}
    parsed/<version>/{records.jsonl,manifest.json}
    normalized/<version>/{records.jsonl,manifest.json}
  scratch/                       # ignored staging, locks and run receipts
```

The same contract applies to computed datasets and graph materializations. Full
artifact manifests are authoritative; compact `manifests/` indexes omit code
source bodies and are suitable for metadata review. They do not contain the
payload bytes needed to rebuild or verify a dataset.

This is a greenfield layout. Old top-level `raw/`, `final/`, `processing/`, `runs/`
and `samples/` paths are ignored and never read, moved or deleted by the new store.
New imports and builds use `artifacts/`, `scratch/` and `manifests/`.

Schema 2 declares named stages, their dependencies and an `output_stage`:

```sh
python3 -m worldmodel run demo_countries --stage parsed
python3 -m worldmodel inspect demo_countries/parsed
python3 -m worldmodel run demo_countries
python3 -m worldmodel verify demo_countries/normalized
```

`cache: "content"` verifies and reuses matching stage artifacts without rerunning
code. `cache: "off"` executes again. `retention: "rebuildable"` permits rebuilding
an absent cached artifact directory; it does not schedule deletion. There is no
automatic garbage collection. See the [dataset layout guide](docs/dataset-layout.md)
for stage schemas, Context APIs, cache keys, exact references and recovery.

## What is implemented

| Area | Implementation |
| --- | --- |
| Storage | Dataset-local immutable artifacts, compact metadata indexes, scratch staging and atomic rename |
| Execution | External dataset and internal stage DAGs, verified stage caches, exact pins and per-dataset writer locks |
| Provenance | Row/record references, recursive lineage and verification, source receipts, code snapshots |
| Adapters | Dataset-local source mappings and formulas; shared CSV/JSONL readers and bounded JSON inputs |
| Acquisition | Local import; explicit HTTP(S) fetch with limits, timeouts, retries, optional expected checksum |
| Ontology | Validated entities, observations, assertions, events; units, dimensions, valid and observed times |
| Graph | Evidence-preserving union; indexed bounded neighborhoods; observation and temporal queries |
| Temporal execution | Incremental checkpoint evaluator, replay reference, seeded processes, explicit inputs and rewards |
| Economy | Closed synthetic firms/households/commercial-bank ledger with dynamic policy and accounting checks |
| Spatial state | SQLite selections, persistent supports/topology, atomic lifecycle changes and conservation |
| RL/perception | Bounded tabular learning/evaluation, local vector adapter, masks/delays/noise, optional Gymnasium |
| Inspection | Static or interactive standalone HTML, linked selection/playback, provenance and explicit limits |
| Distribution | Bundled catalog/fictional fixtures/examples, separate writable data root, inherited rights metadata |
| Examples | Offline source pipelines, a computed country index, a derived graph |

The catalog includes source-family declarations and runnable example/derived datasets.
Declarations marked `requires_configuration` describe intended mappings; a sampling
policy does not by itself finish a publisher-specific connector. Running a source
without a configured adapter fails before acquisition. `wm evidence-audit` inventories
catalog access/readiness and explicit sample limits. Release selection, missing
bilateral relationships, dated identity crosswalks and licensed feeds remain data
integration work. No complete global evidence base is claimed.

## Provenance: “this function, this code, these inputs”

Every version manifest includes:

- `definition`: the exact dataset declaration, including its source metadata.
- `code.entrypoint`: the local stage function, for example `pipeline.py:run`.
- `code.files` and `code.sources`: hashes and source snapshots for the core implementation,
  catalog declarations and dependency/config files.
- `code.dataset_code`: local `dataset.json`, `pipeline.py`, Python helpers and JSON configuration with
  portable relative names, hashes and source snapshots.
- `code.git_commit`, `code.git_dirty`: Git identity when the checkout is a repository;
  actual file hashes work even without Git or with uncommitted edits.
- Python, platform, and installed package versions.
- `inputs`: exact `{dataset, stage, version}` references for named stages, or
  `{dataset, version}` for standalone products; never mutable `latest` paths.
- `raw_inputs`: exact `{dataset, artifact}` references.
- `parameters` and output checksums, byte sizes, and record counts.

Each record supplies evidence like:

```json
{
  "input": {"dataset": "demo_countries", "stage": "normalized", "version": "<64-character-sha256>"},
  "record_id": "fixture:AA:income"
}
```

That input record points to an artifact and a locator (`row:1`, `line:3`, or JSON
pointer `/1`). CSV row numbers count data records after the header; JSONL numbers
count physical lines. Raw locators are emitted by adapters. The runner verifies
that every referenced artifact/version is declared and every referenced input
record exists. It cannot prove the scientific meaning of an adapter's mapping.

`lineage` walks the full dataset ancestry; `inspect` displays one manifest;
`verify` checks manifests, output bytes, raw receipts and payloads recursively.
Audit timestamps live in run records rather than changing a computation's identity.
A long-running Python SDK process must restart after implementation files change;
the runner rejects stale imported code instead of recording misleading provenance.

## Ontology and time

| Kind | Meaning | Required domain fields |
| --- | --- | --- |
| `entity` | A source's description of a named thing | `entity_type`, `label`; optional stable `entity_id` |
| `observation` | A measurement or aggregate constraint | `metric`, `value`, `unit`, `dimensions` |
| `assertion` | A relationship or literal claim | `subject`, `predicate`, exactly one of `object` / `value` |
| `event` | Something reported to have happened | `event_type`, `occurred_at`, `participants` |

Every record requires a namespaced `id`, `observed_at`, and nonempty `evidence`.
Entities can cover organizations, people, facilities, infrastructure, commodities,
locations, industries, contracts, institutions and other types in
`worldmodel/model.py`. Extend this small vocabulary explicitly; predicates and
metrics are open strings. Classification identifiers should include revisions,
e.g. `naics:2022:31-33`.

A record ID identifies a piece of evidence. `entity_id` identifies the thing it
describes; for a source entity it defaults to the record ID. Graph union gives
records distinct IDs while preserving entity IDs, so two sources may describe
the same organization without either description being overwritten. Identity
resolution should itself publish evidenced `same_as` assertions or an explicit
reconciled view; the engine never fuzzy-merges names automatically.

`valid_from` / `valid_to` are optional, half-open world-time intervals `[from, to)`.
`observed_at` is the source/evidence observation time. `retrieved_at` is when this
pipeline acquired the bytes. Date-only values mean midnight UTC; timestamps
require a timezone. Missing valid bounds are unbounded. Confidence is optional
and never defaults to certainty. Null observations require `missing_reason`.

```sh
python3 -m worldmodel --data-root /tmp/worldmodel-demo neighbors org:acme \
  --valid-at 2024-06-01 --known-at 2024-06-01 --hops 2 --limit 100
```

`--known-at` filters evidence by `observed_at`, **not** when this installation first
acquired it. It is not a historical reconstruction of the installation's database.
No averaging, ranking or automatic winner selection occurs. Aggregate observations
are queried with `observations METRIC`, separately from relationship traversal.
Endpoints with no entity description remain unresolved references. Query results
include `_provenance`, and bounded neighborhoods report `truncated` when capped.

## Add a source dataset

```sh
python3 -m worldmodel new country_income --kind source \
  --description 'Country income observations from a selected release'
```

The command creates `data/country_income/dataset.json`, `pipeline.py`, a README,
ignore rules and a test placeholder. Configure its source metadata and sampling
policy, then implement the local stage. The scaffold fails explicitly until its
transformation is provided.

For a simple CSV mapping, the local `pipeline.py` can use a shared reader:

```python
from worldmodel.source_helpers import normalize


def run(context):
    yield from normalize(context)
```

Bind that reader through the dataset's `parameters`, for example:

```json
{
  "format": "csv",
  "constants": {
    "kind": "observation", "metric": "income",
    "unit": "fictional_currency_per_person",
    "observed_at": "2025-01-01T00:00:00Z",
    "valid_from": "2024-01-01", "valid_to": "2025-01-01"
  },
  "columns": {"value": "income"},
  "dimensions": {"country": "country"},
  "numeric_fields": ["value"],
  "id_columns": ["country"]
}
```

These values describe a fictional example. Replace units, periods and publisher
metadata with the selected source's declarations. Keep the scaffold's schema 2
stage definition and `output_stage`; the [layout guide](docs/dataset-layout.md)
shows complete stage declarations.

```sh
python3 -m worldmodel import country_income tests/fixtures/countries.csv
python3 -m worldmodel run country_income
```

`normalize` supports explicit missing-value codes, `missing_reason`, `delimiter`,
`encoding` and `max_json_bytes`. Identifiers and dimensions stay strings, preserving
leading zeroes. Put source-specific revision handling, geometry, unit conversions
and entity extraction in the dataset's local pipeline. Never infer named entities
from aggregate measurements.

For acquisition on the workstation, use `fetch DATASET URL --allow-network`,
optionally with `--sha256`, `--max-bytes`, `--source-json`, and `--user-agent`.
`run` never calls `fetch`. Fetch does not discover release URLs, paginate APIs,
or unpack archives. The separate `sample` command supports bounded ZIP/CSV and XLSX extraction.
CLI `fetch` uses the dataset sampling download ceiling by default; `--max-bytes` can lower it. API pagination should capture each page as a raw artifact and
pass all pages with repeated `--raw` arguments. Import preserves archives intact.

## Add a computed dataset

```sh
python3 -m worldmodel new my_metric --kind derived \
  --entrypoint pipeline.py:compute \
  --depends-on country_income --description 'A derived observation dataset'
```

Implement `data/my_metric/pipeline.py:compute` before running it. Read declared
external inputs with `context.records(dataset)` and attach the exact
`context.input_ref(dataset)` plus contributing record IDs as evidence. Read local
stage dependencies with `context.stage_records(stage)` and `context.stage_ref(stage)`.
See [the local happiness pipeline](data/rando_joes_happiness_index/pipeline.py) for
a complete working computation.

Transforms are trusted Python code, not a sandbox. Keep random seeds, model weights,
units, assumptions and other dependencies explicit. Register auxiliary tables and
model weights as input datasets. The runner cannot discover hidden network or file
reads inside arbitrary code. Generic JSONL intermediates and normalized evidence
stages have separate validation contracts; each stage writes a bounded output stream.

Pin inputs for repeatable computations and comparative experiments:

```sh
python3 -m worldmodel run rando_joes_happiness_index \
  --input demo_countries/normalized@VERSION_HASH --parameters '{"income_weight":0.25}'
python3 -m worldmodel run census_population \
  --raw census_population@ARTIFACT_HASH_1 --raw census_population@ARTIFACT_HASH_2
```

Use the full hashes emitted by import/run. Parameter overrides apply only to the
target dataset. Pins prevent rebuilding the pinned dependency; unpinned sources
resolve `manifests/raw-latest.json` once and record that exact reference.

## Move to the GB10

Copy this checkout or install a built wheel. Set the data root to a local filesystem
with adequate space:

```sh
export WORLD_MODEL_DATA=/mnt/world-model/data
python3 -m worldmodel init
python3 -m worldmodel catalog
```

To transfer data created under this layout, copy the **entire dataset tree**,
including `artifacts/` payloads/full manifests and `manifests/` metadata. Old-layout
directories are not imported automatically. References contain dataset IDs and hashes, not
absolute paths. Declarations come from the checkout or installed package resources;
`--catalog-root` can select another declaration tree. Verify important pinned versions after copying.
The SQLite index can be omitted and rebuilt:

```sh
python3 -m worldmodel graph-build world_graph
```

The implementation uses standard-library Python and SQLite, with no GPU or
architecture-specific dependency. CSV/JSONL processing and validation use bounded
streams and disk-backed uniqueness checks. JSON arrays are capped at 64 MiB by
default. Configure regional/release partitions for large data. Hash verification
reads bytes in full and may reread ancestors at several pipeline boundaries;
size I/O budgets accordingly. This is a single-machine execution engine, not a
cluster scheduler. ARM64/GB10 execution has not been tested on this machine.

## Publication and recovery

A dataset lock excludes concurrent writers. Each stage executes in
`scratch/<run-id>/`; only complete validated output is renamed into
`artifacts/<stage>/<version>/`. Normal cleanup removes staging on success or
failure, while attempt receipts remain under `scratch/runs/`. A killed process
may leave an orphan staging directory or a `running` receipt. Neither is published
output. Kernel locks release when a process exits.

Rename is the publication commit point. If compact-index, pointer or run-receipt
bookkeeping fails after publication, the runner reports the exact published
reference with a warning. Verify that reference, restore writable storage and rerun
to repair convenience pointers. Never delete valid output to conceal an audit error.
This contract assumes ordinary local filesystem rename/locking semantics.

See [the layout guide](docs/dataset-layout.md) for cache verification, retention
limits, code snapshots and the distinction between full and compact manifests.

## Project map

- `worldmodel/store.py`: raw snapshots, version verification, lineage.
- `worldmodel/pipeline.py`: DAG builds, validation, publication and run audit.
- `worldmodel/provenance.py`: code/environment snapshots.
- `worldmodel/model.py`: shared record contracts.
- `data/*/pipeline.py`: source-specific adapters, computations and named stages.
- `worldmodel/source_helpers.py`: reusable readers and evidence-preserving operations.
- `worldmodel/transforms.py`: compatibility imports for older Python callers.
- `worldmodel/graph.py`: disposable temporal query projection.
- `worldmodel/checkpoints.py`, `environments.py`: incremental temporal episodes and replay adapter.
- `worldmodel/coupled_economy.py`: closed commercial-bank economy with step policies.
- `worldmodel/spatial_store.py`: persistent spatial state and lifecycle transactions.
- `worldmodel/rl.py`, `perception.py`: bounded learning/evaluation and observation wrappers.
- `worldmodel/surfaces.py`, `interactive_surfaces.py`: standalone source-preserving views.
- `worldmodel/resources.py`, `rights.py`: installed resources and inherited data terms.
- `worldmodel/fetch.py`, `cli.py`: explicit acquisition and CLI.
- `data/*/dataset.json`: stage declarations, dependencies and source integration requirements.
- `data/*/manifests/`: compact reviewable metadata; payloads remain in ignored `artifacts/`.
- `tests/`: offline behavioral tests and fictional fixtures.

### Integrated policy and sensitivity examples

After building `strategic_scenarios`, the coupled example makes daily policy changes
through the environment action port:

```sh
wm environment strategic_scenarios --request examples/environment-coupled-economy.json --dataset coupled_environment_episode
wm sensitivity --request examples/sensitivity-coupled-economy.json
python3 examples/train-materialization.py
wm assess-model series_calibration
```

The training script uses a small fictional checkpoint environment, disjoint seeds,
and an explicit inventory objective. The sensitivity sweep changes opening credit
limits. Neither establishes real-world policy validity. `assess-model` requires an
existing calibration report and records failure when its holdout misses the baseline.

## Handoff expansion

The latest laptop implementation adds journaled resume, richer banking/production
mechanisms, signed/vector spatial timelines, structured RL spaces, isolated rollout
workers, dated financial imports and reviewed contract extraction. Process composition
and verification status are tracked in [the completion ledger](docs/handoff-completion.md).

```sh
python3 -m worldmodel coupled-economy --request examples/economy-policy-feedback.json
python3 -m worldmodel benchmark-scenarios --request examples/scenario-benchmark.json
python3 -m worldmodel spatial-timeline --request examples/spatial-timeline.json --dataset spatial_timeline
python3 -m worldmodel surface spatial_timeline --spec examples/spatial-surface.json --output /tmp/spatial.html
```

[Bounded source outcomes](docs/source-access-2026-09-15.json) distinguish acquired
samples from credentials/export gaps. [Chronological benchmarks](docs/benchmarks.md)
keep final-test outcomes separate from model selection. Full-scale GB10 execution
remains deferred.
