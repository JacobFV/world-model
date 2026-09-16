# Bounded sampling

**This is the exploration path, not the main one.** 107 datasets now hold complete
published data acquired through `wm acquire`; sampling exists to look at a source's
schema, identifiers, units and quirks cheaply before committing budget to it. Nothing in
the catalog depends on a sample, and the limits on this page do not bound anything else.

Every dataset has a `sampling` section in `data/<dataset>/dataset.json`; run
`wm catalog` for the current list. New source declarations start with a
blocked sampling policy until a URL/format is selected; derived datasets sample
an already-published version.

To download complete data within the global fair-share budget (default 100 GiB, of which
87.7 GiB is currently spent), declare an `acquisition` block and use `wm acquire`; see
[full acquisition](full-acquisition.md). The two paths are independent: sampling limits
below do not apply to full acquisition, and full acquisition never changes samples.

```sh
python3 -m worldmodel sample all --allow-network
python3 -m worldmodel sample census_business --allow-network
python3 -m worldmodel explore census_business
```

`sample` reports each source as `sampled` or `blocked`. A blocker does not silently
become an empty successful sample. `explore` verifies the sample artifact and
manifest before showing fields, types, missingness, examples, and provenance.
Neither command normalizes real data into the ontology or rebuilds the world graph.

## Budgets

- **100 retained rows maximum**, or a tighter per-source limit (GLEIF: 25).
- **1 MiB retained JSONL maximum**; only complete records are written. First row
  too large means blocked, not a corrupted excerpt.
- **64 MiB shared temporary disk buffer** per data root. A nonblocking file lock
  prevents simultaneous samplers from using the same buffer. Different data roots
  have independent budgets.
- **62 MiB maximum whole-file download** for archives/workbooks, reserving two
  1 MiB sample buffers. Most API/CSV responses are limited to 1 MiB downloads.
- **1 MiB decompressed parsing budget**. CSV/ZIP members stream only enough for
  matching rows. JSON and small XLSX XML load within that configured bound.
- One response per source: no automatic pagination, retries, or broadening.
- 25-second socket timeout and 90-second elapsed check between read chunks.
  A pending read may last until its socket timeout, so this is not a strict
  wall-clock interrupt. Limits count application response bytes, excluding HTTP,
  TLS, socket buffering, and protocol overhead.

The JSON parser's Python objects can occupy more memory than the encoded byte
budget. This is a bounded input-size policy, not a 1 MiB total-process RAM limit.
Download chunks are at most 1 MiB. Limits are enforced while reading even if
Content-Length is absent. A response reaching the exact byte budget without a
known complete length is rejected rather than assumed complete.

A normal success or failure removes the temporary download. A killed process
can leave `processing/sample-*`; the next run refuses to allocate another buffer
until that leftover is inspected and removed. Published samples and metadata
are separate from the temporary allowance and accumulate across acquisitions.

## ZIP, XLSX and other formats

Whole ZIP files may be downloaded if they fit the disk budget. We then open the
selected member and read complete rows without extracting the member to disk.
If a ZIP has multiple CSV members, specify `archive_member`; `.txt` members with
CSV content can be selected explicitly. A compressed member may be much larger
than the temporary budget because only its bounded prefix is decompressed.

XLSX is a ZIP format: the small NAICS workbook is downloaded whole, and bounded
shared-string/worksheet XML is parsed. Blank formatting rows are removed with an
explicit configured six-digit code filter. Files above the download budget are
rejected. Arbitrary first-megabyte ZIP truncation is not used: a normal ZIP reader
needs the central directory at the end of the file.

Large ZIPs that do not fit require a smaller published file, an API subset,
a range-aware ZIP implementation, or a larger explicitly configured budget.
PBF, proprietary database exports, arbitrary GIS archives, and PDFs are not
supported by this sampler. We use a tiny OSM API geographic query and TIGERweb
attributes instead of downloading planet files or polygon archives.

## Provenance and isolation

```text
data/<dataset>/raw/<artifact-hash>/
  payload                         # selected source rows re-encoded as JSONL
  receipt.json                    # marks payload as a sample; includes original hash

data/<dataset>/samples/<sample-id>/manifest.json
  definition + exact selection criteria
  source URL / POST body / response metadata
  original file bytes + SHA-256; original_retained=false
  retained artifact ref, row count, reason sampling stopped
  implementation entrypoint + code/environment snapshots
  field profiles and first three examples

data/<dataset>/samples/latest.json # latest sampling attempt, including blockers
```

A sample payload is **not the original full file**. It contains parsed source rows
in JSONL. The full response/archive is deleted after its hash and acquisition
metadata are recorded. Its checksum identifies the bytes we read; it does not
make those discarded bytes available offline. The retained sample itself remains
verifiable. HTTP metadata and selection/code snapshots support reacquisition and
auditing; mutable publishers may not preserve old releases.

Samples never update `raw-latest.json` or `latest.json`, so a full pipeline cannot
accidentally consume them through convenience pointers. The offline demo still
has its normal tiny full-data versions, which can be sampled as derived datasets.
Repeated sampling creates new acquisitions and retains prior small samples. It
does not overwrite historical evidence or prune old versions automatically.

## Source selection and keys

[The exploration report](sample-exploration-2026-09-15.md) is a historical record of the
pre-acquisition sampling run; its blockers (BEA, USDA, freight) have since been resolved
by full acquisition, so read `wm catalog` for current status.

Census population and business sampling use official small CSV/ZIP files after
the API returned a Missing Key page. They are explicitly different extracts from
the originally considered ACS/CBP API calls. Key requirements are documented on
[the Census API](https://api.census.gov/data/2023/acs/acs5/examples.html).

Credentials are read from the environment or `.env` and are never written into
declarations or URL receipts. Rate limits or publisher changes can still block later
attempts.

No random or representative selection is claimed: first rows and narrow geographic/time
filters are meant for examining schemas, identifiers, units and data quality. A sample is
never evidence about a population.

### Pruned sample artifacts

Sample payloads under `artifacts/samples/` can be deleted to reclaim space while the
compact `manifests/samples/latest.json` pointer survives. `wm evidence-audit`
dereferences those pointers and aborts on the first missing one, which is its current
state in this working tree. `wm catalog` and `wm rights DATASET` do not depend on
samples.
