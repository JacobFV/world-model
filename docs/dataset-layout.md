# Dataset-local pipelines and storage

Every source, computation and graph has a dataset directory. Its declaration,
processing code, tests and compact manifests can be reviewed together. Large
payloads and temporary work remain local.

All bundled declarations use schema version 2. This is a greenfield storage
contract: the runtime does not read, move, convert or delete old top-level
`raw/`, `final/`, `processing/`, `runs/` or `samples/` directories. Those paths
remain ignored. Acquire or import inputs into the new layout before building;
an old `latest.json` is not a current runtime pointer.

## Directory contract

In a checkout, the default catalog and writable data root are both `data/`.
They can be separate: `--catalog-root` supplies declarations and code, while
`--data-root` or `WORLD_MODEL_DATA` selects writable storage. Installed wheels
provide read-only catalog resources and use a separate writable root.

```text
data/example/
  dataset.json                     # tracked source/dependency/stage declaration
  pipeline.py                      # tracked dataset-specific Python implementation
  helpers.py                       # optional tracked local helper
  config.json                      # optional tracked local JSON configuration
  tests/                           # optional tracked acceptance tests
  README.md
  .gitignore
  manifests/                       # compact metadata; eligible for Git review
    raw/<artifact>.json
    normalized/<version>.json
    graph/<version>.json
    final/<version>.json           # products without a named pipeline stage
    raw-latest.json
    latest.json                    # default output-stage pointer
    stages/normalized/latest.json
    operations/normalized/<key>.json
    samples/<sample-id>.json
    samples/latest.json
  artifacts/                       # ignored; authoritative immutable products
    raw/<artifact>/
      payload
      receipt.json
    normalized/<version>/
      records.jsonl
      manifest.json
    graph/<version>/...
    final/<version>/...
    samples/<sample-id>/manifest.json
  scratch/                         # ignored; disposable work and local run audit
    .lock
    runs/<run-id>.json
    <run-id>/...
  index.sqlite                     # ignored, disposable graph/query projection
```

Only paths needed for a dataset or operation are populated. A derived dataset
need not contain raw payloads. Stage names determine the corresponding
`artifacts/<stage>/` and `manifests/<stage>/` directories.

Raw references have the form `{dataset, artifact}`. Named stage references have
`{dataset, stage, version}`. Standalone reports and other products may use
`{dataset, version}`, which resolves under `artifacts/final/`. Always preserve
the `stage` field when copying a named-stage reference.

## Declare a pipeline

`dependencies` names other datasets. A stage's `depends_on` names stages in the
same dataset. `output_stage` selects the dataset's default published result.
Both dependency graphs must be acyclic. External dependencies normally resolve
to their own output stages; explicit immutable input pins stop rebuilding those
dependencies.

The runnable [country fixture](../data/demo_countries/dataset.json) demonstrates
two real stages:

```json
{
  "id": "demo_countries",
  "schema_version": 2,
  "kind": "source",
  "dependencies": [],
  "parameters": {"format": "csv"},
  "entrypoint": "pipeline.py:run",
  "output_stage": "normalized",
  "stages": [
    {
      "id": "parsed",
      "entrypoint": "pipeline.py:parse",
      "depends_on": [],
      "schema": {"format": "jsonl"},
      "validation": {"allow_empty": false, "max_rows": 100000},
      "cache": "content",
      "retention": "retain"
    },
    {
      "id": "normalized",
      "entrypoint": "pipeline.py:run",
      "depends_on": ["parsed"],
      "schema": {"format": "evidence_jsonl"},
      "validation": {"allow_empty": false, "max_rows": 100000},
      "cache": "content",
      "retention": "retain"
    }
  ]
}
```

This excerpt omits the fixture's source and sampling metadata. The stage
entrypoints are authoritative; the optional top-level entrypoint is descriptive
compatibility metadata. Entry functions must be named `pipeline.py:function`.
Relative local imports such as `from .helpers import transform` are supported.

The catalog permits 1–100 stages. Each stage yields JSON objects, with finite
JSON values. `schema.format` is one of:

- `jsonl`: generic intermediate objects, without the evidence-record contract.
- `evidence_jsonl`: validated entities, observations, assertions or events, with
  unique record IDs and valid declared-input evidence.

Either format can declare top-level required fields, for example
`"required": {"raw_index": "integer", "locator": "string", "row": "object"}`.
Supported field types are `number`, `integer`, `string`, `boolean`, `object`,
`array` and `null`. This is a small field/type check, not full JSON Schema.
`validation.max_rows` defaults to 100,000 and may be 1–1,000,000. Empty output is
rejected unless `allow_empty` is explicitly true.

## Write dataset-local code

The runner calls the stage function with a `worldmodel.pipeline.Context` and
streams its yielded objects. Dataset-specific mappings, units, identities and
formulas belong in `pipeline.py` or local helpers. Shared format readers and
generic evidence-preserving operations live in `worldmodel/source_helpers.py`.
The former central adapter modules retain compatibility imports; new stage
execution loads local code directly.

| Context API | Meaning |
| --- | --- |
| `definition`, `parameters` | Copies of the dataset declaration and merged parameters |
| `records(dataset)` | Records from a declared external dataset dependency |
| `input_ref(dataset)` | Exact external dependency reference |
| `stage_records(stage)` | Records from a stage listed in this stage's `depends_on` |
| `stage_ref(stage)` | Exact reference for that declared stage input |
| `raw_inputs` | Exact raw acquisition references, available to every source stage |
| `raw_path(index=0)` | Verified raw payload path |
| `raw_evidence(locator, index=0)` | Raw-file evidence with the exact input reference |

For example, the country parser yields `{raw_index, locator, row}` objects. Its
normalizer reads `context.stage_records('parsed')` and preserves the original
raw locators when emitting measurements. It does not reread the original CSV.
The stage manifest also records the parsed artifact as an input.

A computed stage can attach record-level lineage like this:

```python
source = context.input_ref('demo_countries')
evidence = [{'input': source, 'record_id': 'fixture:AA:income'}]
```

See [the local happiness formula](../data/rando_joes_happiness_index/pipeline.py)
for a complete computation. Generic intermediate JSONL need not have record IDs;
to cite an intermediate row as record-level evidence, the input must provide a
string `id`. The runner checks that referenced input records exist.

Stage code is trusted Python, not a sandbox. Network access, random seeds, model
weights, auxiliary tables and other dependencies must be explicit. The runner
does not discover hidden reads made by arbitrary Python code.

## Build, inspect and pin stages

```sh
wm import demo_countries tests/fixtures/countries.csv
wm run demo_countries --stage parsed
wm inspect demo_countries/parsed
wm run demo_countries
wm verify demo_countries/normalized
wm lineage demo_countries/normalized
```

`--stage` builds the chosen stage and its ancestors, not unrelated branches or
descendants. It does not replace the dataset default pointer unless that stage
is `output_stage`. The CLI accepts `dataset/stage@VERSION_HASH` for exact stage
references, including input pins:

```sh
wm run rando_joes_happiness_index \
  --input demo_countries/normalized@VERSION_HASH \
  --parameters '{"income_weight":0.25}'
```

The Python equivalents are `Catalog.stage_plan(dataset, stage=None)` and
`Runner.run(dataset, stage=None, parameters=None, raw_refs=None, input_refs=None)`.
Parameter overrides apply only to the requested dataset. A source resolves
`manifests/raw-latest.json` once unless explicit raw references are supplied.
`run` never downloads inputs.

Use `wm new NAME --kind source|derived --description TEXT` to scaffold a local
declaration, pipeline, README, ignore rules and test placeholder. The generated
function deliberately raises until its transformation is implemented.

## Cache and retention semantics

`cache: "content"` uses an operation key derived from the complete declaration,
stage, parameters, input references, captured implementation/local code hashes,
Python version and installed packages. An existing cache entry is verified
recursively before reuse. A successful hit skips executing the transformation,
records a new attempt with `cached: true`, and refreshes convenience pointers.
The key is conservative: a code or declaration change can invalidate more than
one stage even if a particular output would remain identical.

`cache: "off"` executes and validates the stage every time. Content-addressed
publication can still reuse an existing identical version after execution.
Neither setting excuses undeclared external state or nondeterministic code.

`retention: "retain"` is the default. Missing or corrupted cached products fail
verification. `retention: "rebuildable"` additionally permits rerunning a cached
stage when its entire artifact directory is absent. Existing but corrupted
directories still fail verification. There is **no automatic eviction, garbage
collector or retention schedule**. The setting is recorded policy and missing-
artifact handling, not an instruction that deletes data.

Rebuilding requires the original verified inputs and matching executable code.
Deleting an ancestor breaks recursive verification of descendants until that
ancestor is restored. Compact manifests alone cannot reconstruct payload bytes.

## Full manifests, compact metadata and rights

The full receipt or version manifest lives beside its payload in `artifacts/`.
It is authoritative for checksums and lineage. Stage manifests retain the exact
stage/declaration, input references, parameters, output hashes and row counts,
code/environment snapshot and inherited rights metadata.

Dataset code snapshots include `dataset.json`, local Python files and local JSON
configuration resources, excluding generated directories and runtime pointers.
Symlinks are not accepted as dataset source files. Local code is executed from the captured source snapshot,
including relative imports. Publication checks that sources did not change.
Core implementation, catalog and environment provenance are also recorded.

`manifests/` contains compact derived indexes and convenience pointers. Code
source bodies are omitted; computed parameter values are represented by a hash
and a relative path to the full manifest. Sampling indexes omit example rows
and retain a reference to the local full sample manifest. These indexes help
review metadata without committing payloads, but do not replace full integrity
verification or grant access to the underlying data.

Compact metadata can still contain source URLs, descriptions, identifiers,
configuration and rights notices. Review it before publication. Raw payloads,
full code-bearing artifact manifests and scratch files are ignored by Git.
Source data terms remain separate from the project's MIT code license; see
[DATA_RIGHTS.md](../DATA_RIGHTS.md).

## Failure and publication boundaries

A per-dataset lock serializes writers. Each stage writes under
`scratch/<run-id>/`, validates its records and inputs, then atomically renames
the complete product into `artifacts/<stage>/<version>/`. That rename is the
publication commit point. Updating compact indexes, pointers and run receipts
is subsequent bookkeeping.

Ordinary successful or failed executions remove their staging directory in
cleanup. Attempt receipts, including errors, remain under `scratch/runs/`.
A killed process can leave an orphan staging directory or a `running` receipt;
neither is published output. Kernel locks release when the process exits.

If bookkeeping fails after a new artifact was published, the runner reports the
published reference with a warning. Verify that exact reference, restore writable
storage and rerun to repair pointers. Do not delete a valid immutable artifact
to conceal an audit or pointer error.
