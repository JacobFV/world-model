# World model

**A graph built from versioned evidence. Everything is a dataset, including computations.**

```text
external files → raw snapshots → normalized evidence ─┐
                                                     ├→ computed datasets → unified graph
other versioned datasets ────────────────────────────┘                         ↓
                                                                  SQLite query index
```

This repository implements the data substrate, plus an estimation/validation layer and
a set of simulation kernels over it. It keeps source observations, entity descriptions,
relationship assertions and events separate, and it does not choose a single truth when
sources disagree.

**State as of 2026-09-18**, measured that day on the machine that holds the data (a fresh
checkout has the declarations and pipeline code but none of the payloads). Every row was
re-derived rather than carried over: the data rows from `wm catalog`, `wm budget` and the latest
normalized manifests, the estimation rows from the published validation reports with
`wm calibration-status --all`, and the test row from a full run. Read this before anything else
in the repository:

| | |
| --- | --- |
| Dataset declarations | 130 (120 source, 10 derived) |
| Published normalized datasets | 113 |
| Normalized records | ~1.56 billion (48.5 GiB gzipped) |
| Raw acquired data | 90.0 GiB of a 100 GiB fair-share budget |
| Pre-registered estimation attempts | 63 registered; 35 current, 28 superseded and kept |
| Current attempts that met every declared acceptance criterion | 9 of 35 |
| **Registry processes that are validated** | **5 of 22 — `monetary_model`, `resource_inventory`, `elections_model`, `assets_model`, `legislative_model`** |
| Model families declaring themselves validated | 0 of 11 (a family's descriptor never claims it; validation comes only from a passing report) |
| World-state embedding attempts | 8 registered, 2 not run on compute budget; 5 scored, none validated (see [world-state embeddings](docs/world-state-embeddings.md)) |
| Natural-experiment designs run | 4; none identified an effect (see [natural experiments](docs/natural-experiments.md)) |
| Tests | 1,447 discovered; the 1,175 that run here pass, and 272 skip without the optional `agents` extra (`tensorcode`) or local data payloads |

The validation rows are the ones that matter. This system can acquire, version, join and
query a great deal of real data, and it can score a model honestly against a frozen
holdout. When it does, almost everything fails. Those failures are recorded, not hidden
— see [calibration status](docs/calibration-status.md). A passing test suite says the
software honours its contracts; it says nothing about the world. Nothing here is a
calibrated model of anything.

## Running from a fresh checkout

Python 3.11 or later, no runtime dependencies, from the repository root:

```sh
python3 -m worldmodel catalog          # every declaration and its status
python3 -m worldmodel budget           # download budget and per-dataset allocation
python3 -m worldmodel models list      # the 11 model families and their validation state
python3 -m worldmodel estimation-load  # which estimation components can load real data
python3 -m unittest discover -s tests  # 1,268 tests; skips depend on extras and data; ~7 minutes
```

None of those touch the network. Acquired payloads, generated dashboards and runtime
artifact versions stay local and are excluded from Git, so a fresh checkout has
declarations, pipeline code and compact manifests but no data. Committed verification
reports record prior local runs; they do not bundle those datasets. Built wheels include
the read-only catalog, fictional fixtures and examples; writable runtime data is separate.

See [remaining concerns](docs/remaining-concerns.md) for what is still wrong, and the
[strategic affordances audit](docs/strategic-affordances-audit.md) for what the system
can and cannot be used for.

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

The [reference backbone](docs/reference-backbone.md) adds public-person and market
sources, dated identifier resolution, independent coverage maps and financial obligation
stress with explicit evidence inputs. Its sampling-era limits (100 rows per source) apply
to the `sample` path only; those sources are now fully acquired — GLEIF level 1 alone
publishes 6.16 million records. See [identity, units and
crosswalks](docs/identity-units-crosswalks.md) for the joins built on top of them.

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
python3 -m worldmodel ontology
python3 -m worldmodel processes
python3 -m worldmodel unify
```

`unify` performs no downloads: it streams the already-published normalized outputs of every
catalog dataset in the selected scope into one disk-backed SQLite graph index, pinning every
input version in the index metadata and in a published summary artifact. Scope is selectable
(`--profile`, `--datasets`, `--domain`, `--exclude`, `--all`); `unify-scope` reports what a
scope selects and skips, and `unify-resolve` attaches asserted identity clusters.
[The unified graph guide](docs/unified-graph.md) describes the scopes, their measured cost and
their limits, and is the document to trust over any edge or record count quoted elsewhere. Materialized forecasts are explicit scenarios using
illustrative, uncalibrated processes — see [the kernel guide](docs/world-kernel.md).

## The fictional demo

The demo exists to exercise the storage and provenance contract offline. It is not
evidence about anything:

```sh
python3 -m worldmodel --data-root /tmp/worldmodel-demo demo
python3 -m worldmodel --data-root /tmp/worldmodel-demo neighbors org:acme
python3 -m worldmodel --data-root /tmp/worldmodel-demo observations rando_joes_happiness_index
python3 -m worldmodel --data-root /tmp/worldmodel-demo lineage world_graph
python3 -m worldmodel --data-root /tmp/worldmodel-demo verify world_graph
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

This matters more now that the data is real and large. 81 of the 111 published datasets
carry `redistribution_review_required` in their manifest rights block. At least 25 carry
a source term that restricts redistribution or commercial use outright, including
OpenSanctions (non-commercial only), UN Comtrade (no bulk redistribution), WITS/UNCTAD
TRAINS (attribution required, no resale), Alpaca and Massive market data
(personal/internal use), Nasdaq reference lists (reference use only), SSGA fund
disclosures (informational use) and FRED third-party series, which are flagged per
observation with `attributes.third_party_copyright`. **Do not republish the acquired
data.** The rights inventory is metadata, not a legal determination.

## Acquiring data

Full raw data is acquired with `wm acquire DATASET|all --allow-network`. Datasets declare
`files`, `url_list` or `paged_api` strategies in `dataset.json`. A global download budget
(default 100 GiB, `--budget` or `WORLD_MODEL_DOWNLOAD_BUDGET`) is split by weighted max-min
fairness with a 5% per-dataset ceiling (`wm budget`). Downloads resume, respect rate limits
and `Retry-After`, and are published as sharded raw artifacts marked `complete` or
`complete: false` with a stop reason. API keys come from the environment or `.env` and are
never written. See [full acquisition](docs/full-acquisition.md).

```sh
python3 -m worldmodel budget                            # allocation table, no network
python3 -m worldmodel acquire fred_cpi --dry-run        # plan and allocation, no network
python3 -m worldmodel acquire fred_cpi --allow-network  # download and publish
python3 -m worldmodel run fred_cpi                      # build normalized records from it
python3 -m worldmodel verify fred_cpi/normalized        # re-read payload bytes and lineage
```

87.7 GiB of the 100 GiB pool is already spent, so the next large source displaces an
existing one. There is no tiering or eviction policy.

A separate bounded `sample` path still exists for schema exploration only — at most 100
retained rows and 1 MiB per source, sharing a 64 MiB temporary disk buffer. Samples never
move `raw-latest.json`, and full acquisition never touches samples. See
[sampling](docs/sampling.md). The historical
[sample exploration report](docs/sample-exploration-2026-09-15.md) describes the state
before full acquisition and should not be read as current.

### What is not acquired

| Declaration | Why |
| --- | --- |
| `acled`, `global_fishing_watch` | approval-gated accounts |
| `wto_timeseries` | optional API key not held |
| `bts_airline_t100`, `usitc_hts_tariffs` | interactive download (HTS is published; T-100 is not) |
| `mit_election_returns` | statewide president/senate only; House and county returns are behind a guestbook-gated Dataverse download |
| `epa_aqs_daily`, `market_corporate_actions` | downloads in flight |
| `contract_candidates`, `reviewed_obligations`, `market_obligations` | require a supplied, authorized file; nothing is inferred from aggregates |
| `lda_lobbying` | partial acquisition in progress |

## Estimating and validating

`worldmodel.estimation` fits parameters from the normalized datasets and scores them on a
frozen holdout. Every attempt is pre-registered in
`worldmodel/estimation/real_data_plan.json` — splits, loader options and acceptance
criteria are fixed before any holdout is scored.

```sh
python3 -m worldmodel estimation-requirements   # what each component needs
python3 -m worldmodel estimation-load           # what can actually be loaded today
python3 -m worldmodel calibrate-all --help      # rerun attempts (slow; not offline-free)
python3 -m worldmodel calibration-status calibration_reports@VERSION
python3 -m worldmodel calibration-status --all   # every published report, joined to the plan
```

`calibration-status` re-reads a published report, recomputes its digest and re-evaluates
the declared criteria; it does not trust the stored `validated` flag.

`calibration-status --all` does that for every published report and joins them to the plan,
which is where the headline numbers above come from.

Of 33 current attempts, nine pass, and five processes are validated: `monetary_model`,
`resource_inventory`, `elections_model`, `assets_model` and `legislative_model`. Each is
validated on the specification and series it declared, and several sit beside failing attempts
of the same process that stay on the record. `coupled_economy` needs nine components and has
three passing, one of them only on a substituted series. The most common failing criterion is
now skill: 15 current attempts do not beat a persistence baseline. Interval coverage fails on 9;
fat-tailed predictive distributions (Student-t by maximum likelihood, empirical quantiles,
rolling split-conformal) were tried on every attempt failing it on 2026-09-18 and resolved none.
`default_hazard` passes on a substituted FDIC series and fails on its declared credit-card
series with the opposite sign on unemployment sensitivity; the difference is the definition of
the delinquency series, not units, lags or the sample window, so it must be read as not
validated. [The full record, attempt by attempt](docs/calibration-status.md).

Eleven political, market and geopolitical model families
([guide](docs/political-market-geopolitical-models.md)) plus a multi-actor game layer are
implemented. All eleven declare `validated: false`, and `wm models list` will say so.

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
| Acquisition | Local import; bounded fetch/samples; full sharded acquisition with fair-share budget, resume, pagination and rate limits |
| Ontology | Validated entities, observations, assertions, events; units, dimensions, valid and observed times |
| Graph | Evidence-preserving union; indexed bounded neighborhoods; observation and temporal queries |
| Temporal execution | Incremental checkpoint evaluator, replay reference, seeded processes, explicit inputs and rewards |
| Economy | Closed synthetic firms/households/commercial-bank ledger with dynamic policy and accounting checks |
| Spatial state | SQLite selections, persistent supports/topology, atomic lifecycle changes and conservation |
| RL/perception | Bounded tabular learning/evaluation, local vector adapter, masks/delays/noise, optional Gymnasium |
| Inspection | Static or interactive standalone HTML, linked selection/playback, provenance and explicit limits |
| Identity/units | Dated country/county/NAICS codes, total-conserving crosswalk apportionment, unit and currency conversion, deterministic links, Fellegi-Sunter resolution, belief materialization |
| Estimation | OLS/WLS/2SLS, AR/ARIMA-lite/VAR, error-correction pass-through, hazard/logit, PPML gravity, Kalman local level, SMM/ABC, block bootstrap |
| Validation | Pre-registered splits, knowledge cutoffs with vintage policy and leakage audits, rolling-origin backtests, proper scoring rules, Diebold-Mariano tests, immutable reports |
| Models | 11 political/market/geopolitical families and a multi-actor game layer, all `validated: false` |
| Scale | Named configurable limits (`worldmodel/limits.py`), optional numpy backend, measured benchmarks |
| Distribution | Bundled catalog/fictional fixtures/examples, separate writable data root, inherited rights metadata |
| Examples | Offline source pipelines, a computed country index, a derived graph |

The catalog mixes fully acquired sources, configured-but-unacquired declarations and
fictional examples; `wm catalog` reports each declaration's status and is the only
authority on which is which. A declaration is not a dataset, and a sampling policy does
not by itself finish a publisher-specific connector. Running a source without a
configured adapter fails before acquisition. Release selection, missing bilateral
relationships, dated identity crosswalks and licensed feeds remain data integration work.
No complete global evidence base is claimed.

`wm evidence-audit` inventories catalog access, readiness and rights, but in this working
tree it aborts: it dereferences every dataset's retained sample manifest, and some sample
payloads have been pruned since acquisition. Use `wm catalog` and `wm rights DATASET`
until that is fixed.

## Provenance: “this function, this code, these inputs”

Every version manifest includes:

- `definition`: the exact dataset declaration, including its source metadata.
- `code.entrypoint`: the local stage function, for example `pipeline.py:run`.
- `code.files` and `code.sources`: hashes and source snapshots for the core implementation
  and dependency/config files. A dataset build captures its own declaration through
  `code.dataset_code` and the manifest's `definition`, so it deliberately does **not**
  snapshot every other `data/*/dataset.json`; including them made an unrelated dataset's
  edit abort a long build at publish time. A non-dataset build still snapshots the whole
  catalog.
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

## Relocating the data root

This repository now runs on the GB10 (20-core aarch64, 121 GB RAM), which is where the
87.7 GiB of acquired data and the benchmarks in [scale-benchmarks.md](docs/scale-benchmarks.md)
live. Copy this checkout or install a built wheel. Set the data root to a local filesystem
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
size I/O budgets accordingly. This is a single-machine, single-process execution engine,
not a cluster scheduler: there is no distributed or out-of-core execution, no GPU backend
and no partitioned SQLite. The acquired data exists in one place and is not in Git.

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

`strategic_scenarios` is a fictional dataset that is not built in a fresh checkout. Build
it first, or every command below fails with a missing `manifests/latest.json`:

```sh
wm import strategic_scenarios examples/scenario-entities.jsonl
wm run strategic_scenarios
```

The coupled example then makes daily policy changes through the environment action port:

```sh
wm environment strategic_scenarios --request examples/environment-coupled-economy.json --dataset coupled_environment_episode
wm sensitivity --request examples/sensitivity-coupled-economy.json
python3 examples/train-materialization.py
wm assess-model series_calibration
```

`wm sensitivity` needs no prebuilt dataset. The training script uses a small fictional
checkpoint environment, disjoint seeds, and an explicit inventory objective. The
sensitivity sweep changes opening credit limits. Neither establishes real-world policy
validity. `assess-model` requires an existing calibration report in the same data root
and records failure when its holdout misses the baseline.

## Simulation kernels

Journaled resume, banking/production mechanisms, signed/vector spatial timelines,
structured RL spaces, isolated rollout workers, dated financial imports and reviewed
contract extraction:

```sh
python3 -m worldmodel coupled-economy --request examples/economy-policy-feedback.json
python3 -m worldmodel benchmark-scenarios --request examples/scenario-benchmark.json
python3 -m worldmodel spatial-timeline --request examples/spatial-timeline.json --dataset spatial_timeline
python3 -m worldmodel surface spatial_timeline --spec examples/spatial-surface.json --output /tmp/spatial.html
```

Every size cap in these kernels is a named field of `worldmodel.limits.Limits`, raisable
per call, per process (`WORLD_MODEL_LIMITS`) or per command (`--limits`); measured wall
time and peak RSS are in [scale-benchmarks.md](docs/scale-benchmarks.md). The parameters
in these examples are assumptions, not estimated responses.

## Grounded cognitive agents

Entities that act: persons, firms and institutions bound to real ids from the unified
index, perceiving published records, holding beliefs with provenance, deciding under
constraints, and speaking to one another over channels drawn from published edges.

```sh
pip install "worldmodel-substrate[agents]"         # optional `agents` extra (tensorcode)
python3 -m worldmodel agent-inspect bioguide:K000367
python3 -m worldmodel agent-explain bioguide:K000367 decided
python3 -m worldmodel society-run --config examples/society-congress.json --ticks 6 --seed 7
```

A person carries episodic memory and affect read as structural measures over its own
processing. A firm has no single self: roles hold claims that can contradict, decisions are
procedures across roles, and its readings are named for solvency and exposure. An
institution is governed by authority, and an act outside declared power is refused and
recorded rather than performed. See [the agent layer](docs/agents.md).

**Nothing in this layer is validated or fitted to outcomes**, and its parameters are
authored assumptions. It is inspectable cognition over real evidence, not a predictor.

## Where to read next

| Document | What it is |
| --- | --- |
| [agents.md](docs/agents.md) | the agent layer: three kinds of mind, grounding, what it does not claim |
| [strategic-affordances-audit.md](docs/strategic-affordances-audit.md) | current audit: what works, where the boundary is, what is missing |
| [session-2026-09-15-summary.md](docs/session-2026-09-15-summary.md) | what changed in this session, including the bugs found |
| [calibration-status.md](docs/calibration-status.md) | every estimation attempt and its verdict |
| [remaining-concerns.md](docs/remaining-concerns.md) | known limits, by area |
| [full-acquisition.md](docs/full-acquisition.md) | the acquisition contract and budget |
| [use-policy.md](docs/use-policy.md) | declared purpose, and when identified natural persons are retained |
| [dataset-layout.md](docs/dataset-layout.md) | storage, stages, caching and recovery |
| [identity-units-crosswalks.md](docs/identity-units-crosswalks.md) | how cross-source joins are made defensible |
| [unified-graph.md](docs/unified-graph.md) | the unified graph's current contents and limits |

[Chronological benchmarks](docs/benchmarks.md) keep final-test outcomes separate from
model selection. [Bounded source outcomes](docs/source-access-2026-09-15.json) and
[the handoff completion ledger](docs/handoff-completion.md) are historical records of the
pre-acquisition state, kept for audit; they do not describe the catalog as it is now.
