# Code and data rights

The project's code and documentation are MIT licensed; see LICENSE.
Source datasets retain their own terms. MIT does not relicense third-party data.

Each acquisition retains publisher/source metadata, a full receipt and immutable input
hash under the dataset's ignored `artifacts/raw/` directory. Named pipeline stages
retain full manifests beside their outputs under `artifacts/<stage>/`. Derived
datasets retain those input references recursively. Rights summaries propagate
source-specific license identifiers, attribution requirements, terms links and unknown
or restricted redistribution status without discarding contradictory terms. Multiple
input licenses remain a set of applicable notices, not a guessed replacement license.

Unknown terms do not prevent local computation. They are surfaced for anyone considering
redistribution. Computed or synthetic datasets keep their derivation lineage; calling an
output synthetic does not erase restrictions on copied or incorporated source content.
The rights summary is a metadata inventory, not an automated legal determination.

Dataset-local `pipeline.py`, helpers, tests and documentation are project code.
Moving an adapter beside its dataset does not change the source data's terms.
Bundled fictional fixtures carry the source declarations in their respective
`dataset.json` files; the demo country and graph sources are labeled CC0-1.0 there.

Raw data, generated source-backed views, full artifact manifests and scratch files
are excluded from this Git repository. Compact metadata under `manifests/` may be
reviewed and committed. It omits payload bytes and code source bodies, but can still
contain source URLs, descriptions, identifiers, configuration and rights notices.
Its presence in Git grants no rights to the referenced data and does not substitute
for authoritative full manifests or payloads.

The storage layout is greenfield: old top-level runtime directories are left
untouched and ignored, without automatic migration or cleanup. See
[the dataset layout guide](docs/dataset-layout.md) for current paths and contracts.
