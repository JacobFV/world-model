# Full-scale acquisition

Samples (see [sampling](sampling.md)) are 100-row excerpts for schema exploration.
Full acquisition downloads complete raw data into immutable, sharded raw artifacts
that pipelines consume through `wm run`. A global budget (default **100 GiB** of raw
bytes across all datasets) is split fairly, so one dataset cannot take all the space.

```sh
wm budget                                   # allocation table
wm acquire fred_cpi --dry-run               # plan + allocation, no network
wm acquire fred_cpi --allow-network         # download, publish, point raw-latest at it
wm acquire all --allow-network --workers 4  # every dataset with an acquisition block
wm acquire usaspending --allow-network --resume   # continue interrupted/budget-stopped work
wm budget reconcile                         # rescan real on-disk usage
wm run fred_cpi                             # builds from the full artifact
```

Sampling is unchanged: samples never move `raw-latest.json`, and full acquisition
never touches samples.

## The `acquisition` block

Add an optional `acquisition` object to `data/<id>/dataset.json` (source datasets only).
Unknown keys are rejected, so typos fail fast. `wm acquire ID --dry-run` validates a
block without any network access.

### Common fields

| Field | Default | Meaning |
| --- | --- | --- |
| `strategy` | required | `files`, `url_list` or `paged_api` |
| `desired_bytes` | required | Estimated full raw size in bytes (demand used for fair sharing) |
| `min_bytes` | `0` | Guaranteed allocation, capped at `desired_bytes` |
| `priority` | `1` | Positive weight for water-filling |
| `description`, `documentation` | `null` | Human notes, recorded in receipts |
| `reader` | `null` | Default raw reader options for pipelines (see below) |
| `user_agent` | `"worldmodel-substrate/0.3 full-acquisition"` | Request User-Agent |
| `user_agent_env` | `null` | Environment variable holding a required contact User-Agent (e.g. `SEC_USER_AGENT`); missing means the acquisition fails before any request |
| `credentials` | `[]` | Each entry: `env` plus exactly one of `query` (URL parameter), `header`, or `body` (JSON body key, dotted path; requires an object `body_template`); optional `prefix` (e.g. `"Bearer "`) and `"optional": true` to skip when unset |
| `headers` | `{}` | Static, non-secret request headers (recorded) |
| `rate_limit` | `{"requests_per_second": 1.0, "requests_per_day": null, "key": null}` | Per-host limits; `key` shares a quota across datasets/hosts (e.g. `"api.data.gov"`) |
| `timeout_seconds` | `60` | Socket timeout per request |
| `retries` | `5` | Retries on 408/425/429/5xx, connection errors and truncated bodies |
| `backoff_seconds`, `max_backoff_seconds` | `2.0`, `300` | Exponential backoff `backoff * 2^attempt`, capped |
| `max_retry_after_seconds` | `3600` | A longer `Retry-After` stops the run as `rate_limited` (resumable) |
| `reserve_chunk_bytes` | `8388608` | Incremental budget reservation when size is unknown |
| `accept_encoding` | `identity` for `files`, `gzip` otherwise | gzip transfer encoding is decoded while streaming; **decoded** bytes count against the budget |
| `publish_partial` | `true` | Point `raw-latest.json` at a `complete: false` artifact after a budget/page-limit stop |

### `files`

```json
"acquisition": {
  "strategy": "files",
  "desired_bytes": 2147483648,
  "files": [
    {"url": "https://www2.census.gov/programs-surveys/cbp/datasets/2023/cbp23co.zip", "name": "cbp23co.zip"},
    {"url": "https://example.org/big.csv.gz", "sha256": "…64 hex…"}
  ],
  "skip_statuses": [],
  "reader": {"format": "csv", "members": ["*.txt"]}
}
```

Each URL becomes one shard. Entries may be plain strings. `sha256` is checked after
download. Downloads resume with HTTP `Range` + `If-Range` after interruption; a server
that ignores the range restarts the shard. Archives are stored as downloaded; pipelines
open them with the raw readers.

### `url_list`

```json
"acquisition": {
  "strategy": "url_list",
  "desired_bytes": 1048576,
  "url_template": "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}",
  "parameters": {"series": ["CPIAUCSL", "CPILFESL", "PCEPI"]},
  "rate_limit": {"requests_per_second": 0.5, "requests_per_day": 1000},
  "reader": {"format": "csv"}
}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `url_template` | required | `{name}` placeholders, values URL-encoded |
| `parameters` | `{}` | Grid: `{"year": {"range": [2000, 2024], "step": 1}, "state": ["06", "48"]}`; cartesian product in declared key order |
| `combinations` | `null` | Explicit list of parameter objects instead of a grid |
| `method` | `GET` | `GET` or `POST` |
| `body_template` | `null` | JSON body; a string that is exactly `"{name}"` keeps the value's JSON type; use `{{`/`}}` for literal braces |
| `skip_statuses` | `[]` | e.g. `[404]`: record as skipped instead of failing |
| `max_requests` | `1000000` | Refuse larger expansions |

One request = one shard. Shard metadata records the redacted URL, body and parameters.
[data/fred_cpi/dataset.json](../data/fred_cpi/dataset.json) is the worked example.

### `paged_api`

```json
"acquisition": {
  "strategy": "paged_api",
  "desired_bytes": 5368709120,
  "url_template": "https://api.usaspending.gov/api/v2/search/spending_by_award/",
  "method": "POST",
  "parameters": {"fy": {"range": [2018, 2024]}},
  "body_template": {"filters": {"time_period": [{"start_date": "{fy}-01-01", "end_date": "{fy}-12-31"}],
                                "award_type_codes": ["A", "B", "C", "D"]},
                    "fields": ["Award ID", "Recipient Name", "Award Amount"], "limit": 100},
  "pagination": {"mode": "page"},
  "records_path": ["results"],
  "stop": {"has_more_path": ["page_metadata", "hasNext"], "short_page": false},
  "store": "jsonl",
  "rollover_bytes": 268435456,
  "rate_limit": {"requests_per_second": 2},
  "reader": {"format": "jsonl"}
}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `url_template`, `parameters`, `combinations`, `method`, `body_template` | as `url_list` | Pagination runs separately for every combination |
| `pagination.mode` | required | `offset`, `page`, `cursor` or `next_url` |
| `pagination.param` | the mode name | Query/body key for offset/page/cursor (dotted path for nested body keys) |
| `pagination.size_param`, `page_size` | `null` | Page-size key and value; `page_size` is also the offset step |
| `pagination.start` | `0` offset, `1` page, `null` cursor | First value (cursor `null` = first request without cursor; e.g. `"*"` for OpenAlex) |
| `pagination.location` | `body` if `body_template` else `query` | Where pagination keys go |
| `pagination.cursor_path` | required for `cursor` | JSON path to the next cursor; stops when missing, empty or repeated |
| `pagination.next_url_path` / `link_header` | `link_header` when no path | Next URL from the JSON body or `Link: <…>; rel="next"`; must stay on the same host |
| `records_path` | `null` | JSON path to the record array |
| `stop.empty_page` | `true` | Stop when the page has zero records |
| `stop.short_page` | `true` | Stop when records < `page_size` |
| `stop.total_path` | `null` | Total record count path (offset/page) |
| `stop.total_pages_path` | `null` | Total page count path |
| `stop.has_more_path` | `null` | Stop when this value is falsy |
| `stop.max_pages` | `null` | Per-combination cap; hitting it marks the artifact `complete: false`, `stop_reason: "page_limit"` |
| `store` | `jsonl` | `jsonl`: records (or whole pages without `records_path`) appended as lines into shards rolled over at `rollover_bytes`, plus a `page_log` shard mapping pages to line ranges and requests; `pages`: each raw response body is its own shard |
| `rollover_bytes` | `268435456` | JSONL shard size target |
| `max_page_bytes` | `67108864` | Decoded in-memory page cap |
| `error_paths` | `[]` | JSON paths whose truthy value means a source error |

## Budget model

- **Total**: `--budget` flag, else `WORLD_MODEL_DOWNLOAD_BUDGET`, else 100 GiB. Sizes accept
  bytes or units (`500MB`, `100GiB`).
- **Pool**: total minus raw usage of datasets without an acquisition block (imports, samples).
- **Allocation** is weighted max-min fairness (water-filling):
  1. each dataset gets `min(min_bytes, desired_bytes)` (if guarantees alone exceed the pool,
     they are water-filled by priority);
  2. the rest is water-filled with per-dataset caps `min(desired_bytes, max_share × total)`
     (`--max-share`, default 0.05, i.e. 5 GiB of the default total), weighted by `priority`; datasets that want less than their
     share get exactly what they want and the remainder is redistributed;
  3. leftover that would otherwise go unused is water-filled up to `desired_bytes`, ignoring
     `max_share`.
- A dataset whose last acquisition completed (same download-defining config) demands only what
  it holds, releasing the rest to others.
- Bytes already held (used + reserved) are always allocated first, as both the guarantee and a
  floor on demand. When shares shrink because more datasets declare demand later, a dataset
  that already holds more than its new share keeps those bytes but gets no new ones. Only the
  remaining pool is shared among the others, so actual usage can never exceed the total.
- **Usage is measured**: the SQLite ledger at `<data_root>/.acquisition/ledger.sqlite`
  (ignored) stores per-dataset scans of `artifacts/raw/**` plus `scratch/acquire-*/**`
  (hardlinked files count once), staged bytes written since the scan, and outstanding
  reservations. `wm budget reconcile` rescans, so deleting old artifacts frees budget.
- **Enforcement**: bytes are reserved before writing (exact Content-Length when known,
  otherwise `reserve_chunk_bytes` at a time, extended as data arrives). If the allocation
  cannot cover the next bytes, the shard is aborted cleanly, the reservation released, and the
  acquisition published as `complete: false`, `stop_reason: "budget"`. Reservations of dead
  processes are purged. Ledger updates use a file lock plus `BEGIN IMMEDIATE`.
- `desired_bytes` is also a hard ceiling: an underestimate stops with `budget`. Raise it and
  rerun with `--resume` (budget and politeness settings do not invalidate staging).
- Metadata (receipts, state, page logs) in those directories counts toward usage.

`wm budget` output: per dataset `desired`, `demand`, `min`, `priority`, `allocated`, `used`,
`reserved`, `remaining`, `settled`, plus `total`, `pool`, `unmanaged_bytes` and totals.

## Runs, stops and resume

| Status | Meaning | Artifact | Staging kept |
| --- | --- | --- | --- |
| `complete` | Source exhausted | `complete: true`, `raw-latest.json` updated | no |
| `partial` | `budget` or `page_limit` stop | `complete: false` + `stop_reason`, pointer updated if `publish_partial` | yes for `budget` |
| `blocked` | Budget exhausted before any shard | none | yes |
| `incomplete` | `max_shards`, `daily_request_limit`, `rate_limited` | none | yes |
| `failed` | Error after retries, missing credential, bad config | none | yes |

Staging lives in `<dataset>/scratch/acquire-<content digest>/` (`state.json`, `shards/`,
`page_log.jsonl`). Rerunning while staging exists requires `--resume` (continue) or
`--restart` (discard). Completed shards are never refetched; partial files resume via
`Range`; JSONL shards are truncated to the last saved page. Publishing hardlinks sealed shards
into `artifacts/raw/<artifact>/shards/<n>`, so a later complete artifact shares bytes with an
earlier partial one. A summary goes to `manifests/acquisitions/latest.json`, and each attempt
to `scratch/runs/acquire-*.json`.

`acquire all` runs up to `--workers` datasets at once (default 4), smallest allocation first,
with one connection per host in the process and cross-process request spacing from the ledger.

## Credentials and environment

The CLI (`wm` / `python -m worldmodel`) loads `<project>/.env` and `$WORLD_MODEL_ENV_FILE` at
startup without overriding variables that are already set. It supports `export`, comments and
single- or double-quoted values with spaces. `WORLD_MODEL_NO_DOTENV=1` disables loading.
Values are never logged. `.env.example` documents the names.

Credentials are injected per request only. URLs in receipts, state and page logs go through
`fetch.public_url`, and recorded JSON bodies go through `fetch.public_body`. Both
case-insensitively redact `key`, `api_key`, `apikey`, `token`, `access_token`, `signature`,
`userid` (`UserID`), `subscription-key`, `registrationkey`, `x-api-key`, `client_secret`,
`password` and similar names anywhere, plus every declared credential query/body name. Header
credentials are never recorded. Before writing metadata the engine scans it for credential
values and refuses to write if one is found. Credentials echoed in `next` links are stripped,
then re-injected on the next request.

| Variable | Injection |
| --- | --- |
| `DATA_GOV_API_KEY`, `FEC_API_KEY`, `CONGRESS_API_KEY` | same api.data.gov key: `{"env": "FEC_API_KEY", "query": "api_key"}`; share a quota with `rate_limit.key: "api.data.gov"` |
| `CENSUS_API_KEY` | `{"env": "CENSUS_API_KEY", "query": "key"}` |
| `BEA_API_KEY` | `{"env": "BEA_API_KEY", "query": "UserID"}` |
| `BLS_API_KEY` | v2 POST body: `{"env": "BLS_API_KEY", "body": "registrationkey"}` |
| `USDA_NASS_API_KEY` | `{"env": "USDA_NASS_API_KEY", "query": "key"}` |
| `EIA_API_KEY` | `{"env": "EIA_API_KEY", "query": "api_key"}` |
| `FRED_API_KEY` | `{"env": "FRED_API_KEY", "query": "api_key"}` (fredgraph.csv needs none) |
| `SEC_USER_AGENT` | `"user_agent_env": "SEC_USER_AGENT"` (quoted in `.env`; contains spaces) |
| `UCDP_ACCESS_TOKEN` | optional header: `{"env": "UCDP_ACCESS_TOKEN", "header": "x-ucdp-access-token", "optional": true}` (check the publisher's header name) |

Other controls: `WORLD_MODEL_DOWNLOAD_BUDGET`, `WORLD_MODEL_RAW_VERIFY`, `WORLD_MODEL_DATA`,
`WORLD_MODEL_ENV_FILE`, `WORLD_MODEL_NO_DOTENV`.

## Sharded raw artifacts

```text
artifacts/raw/<artifact>/
  receipt.json      # schema_version 2, layout "shards", complete, stop_reason, source.acquisition
  shards/0 … shards/N-1
```

Each receipt shard entry has `index`, `path`, `sha256`, `bytes`, `role` (`data` or
`page_log`), `complete`, `retrieved_at`, `request` (redacted method/url/params/body) and
HTTP metadata. The receipt `sha256` is the digest of `[[sha256, bytes], …]`. The compact
index in `manifests/raw/` summarizes shards instead of listing them.
`Store.import_shards(dataset, shards, source, complete=..., stop_reason=..., method='link')`
and `Store.import_file(..., method='link'|'move')` avoid second copies.

Verification: `Store.artifact(ref)` hashes everything by default (`verify='full'`). Files of
64 MiB or more are re-hashed only when their size, mtime, ctime or inode change within one
`Store` instance, so a stage that verifies raw inputs several times hashes 5 GB once. For long
iterative work on trusted disks, `WORLD_MODEL_RAW_VERIFY=size` (or `Store(root,
raw_verify='size')`, or `artifact(ref, verify='size')`) checks only receipt digests and
file sizes. Run `wm verify` with the default mode before publishing results.

## Consuming full data in pipelines

```python
from worldmodel.util import digest

def run(context):
    coverage = context.raw_coverage()          # layout, sampled, complete, stop_reason, shards, bytes
    receipt = context.raw_receipt()
    for locator, row in context.raw_rows():    # reader options from acquisition.reader
        yield {'kind': 'observation', 'id': 'fred:' + digest([locator]),
               'observed_at': receipt['retrieved_at'],
               'subject': 'fred:CPIAUCSL', 'metric': 'consumer_price_index',
               'value': float(row['CPIAUCSL']), 'unit': 'index',
               'evidence': context.raw_evidence(locator),
               'attributes': {'complete_source': coverage['complete']}}
```

| Context API | Meaning |
| --- | --- |
| `raw_rows(index=0, **reader)` | Stream `(locator, row)`; keyword options override `acquisition.reader`; sample payloads default to `jsonl` |
| `raw_shards(index=0, role='data')` | Shard descriptors with absolute `path` (single payloads appear as shard 0) |
| `raw_receipt(index=0)` | Receipt (content address checked) |
| `raw_coverage(index=0)` | `{layout, sampled, complete, stop_reason, shards, bytes}` |
| `raw_path(index=0)` | Single-payload path; raises for sharded artifacts |
| `raw_evidence(locator, index=0)` | Evidence entry with the exact raw reference |

`worldmodel.source_records.full_rows(context, **reader)` yields
`(index, locator, row, receipt)` across all raw inputs. `worldmodel.raw_readers.iter_rows(shards,
config)` is the underlying generator.

Reader options: `format` (`csv`, `tsv`, `psv`, `jsonl`, `json`, `xlsx`), `delimiter`,
`encoding` (`utf-8-sig`), `compression` (`auto` detects gzip/ZIP by magic bytes, or `none`,
`gzip`, `zip`), `members` (ZIP glob list, matched on full name or basename; defaults by format,
e.g. `*.csv`/`*.txt`), `fieldnames` (headerless files), `skip_lines`, `quoting: "none"`,
`strict` (default true: a row whose field count differs from the header raises; false pads),
`max_field_bytes` (16 MiB), `records_path` and `table_header` (Census-style array of arrays)
for JSON, `unwrap_attributes`, `max_json_bytes` (256 MiB), `roles` (default `["data"]`), and
the XLSX options from `workbooks.xlsx_rows` (`archive_member`, `header_row`, `columns`, …).

Locators: `shard:3/member:foo.csv/line:1234` (physical line where the record starts; header
is line 1), `shard:0/line:17` (plain, gzip or JSONL), `shard:2/record:5` (0-based JSON array
index; with `table_header` 1 = first data row), `shard:1/line:4/record:0` (JSONL page lines),
`shard:0/member:xl/worksheets/sheet1.xml/row:12`.

## Stage scale

- `validation.max_rows` defaults to 100,000 and may be set up to 2,000,000,000 per stage.
- `schema.compression: "gzip"` writes deterministic `records.jsonl.gz`. `Store.records` reads
  either output transparently. Code that opens `records.jsonl` directly (derived sampling) does
  not read gzip outputs.
- Record ID and evidence audits use an on-disk SQLite table, so memory does not grow with row
  count. Pipelines must also avoid accumulating rows: several existing `pipeline.py` files use
  `read_text().splitlines()` or growing `seen` sets, which is fine for samples but not for
  multi-GB inputs.

## Limitations

- Single JSON documents and XLSX sheets are parsed in memory under caps. Nested archives
  (ZIP in ZIP), PBF/GIS/proprietary formats and range-based partial ZIP reads are not supported.
- gzip transfer encoding cannot be resumed mid-shard; resumed requests ask for `identity`.
- Rate limiting spaces requests per host key across processes; it is not a token bucket with
  bursts. Daily counts use UTC days and count retries.
- `desired_bytes` is an estimate that you maintain. Allocations are computed when a run starts,
  and concurrent runs with different `--budget` values are not reconciled with each other.
- Existing dataset pipelines were written for JSONL samples. Once `raw-latest.json` points at a
  full artifact, a pipeline must branch on `context.raw_coverage()['sampled']` or use
  `raw_rows()`.
