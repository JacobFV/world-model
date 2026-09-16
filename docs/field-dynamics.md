# Local spatial dynamics and geometry

`SpatialStore` persists explicit supports, topology and signed scalar/vector
fields in SQLite. `evolve_timeline` adds bounded local evolution and dated support
changes. These are synthetic scenario operations, not calibrated physical models.
The existing nonnegative scalar `FieldWorld` API remains available.

## Run the example

From the repository root:

```python
import json
from worldmodel.spatial_store import SpatialStore

example = json.load(open('examples/spatial-timeline.json'))
with SpatialStore(':memory:') as store:
    store.initialize(example['world'], coordinate_system=example['coordinate_system'])
    result = store.evolve_timeline(example['request'])
    print(result['conservation'])
```

Use a file path instead of `:memory:` to persist state and reopen later.

## Numerical contract

```python
from worldmodel.field_dynamics import evolve_fields
result = evolve_fields(world, request,
                       coordinate_system=coordinate_system,
                       component_frames=component_frames)
```

The standalone operation returns a detached state. Its request declares
`duration_seconds`, positive `step_seconds`, `boundary: "closed"`, and
`edge_units: {"conductance": "<measure_unit>/second", "transport_rate": "1/second"}`.
Edges declare nonnegative `conductance` and directed `transport_rate`; reversing
source/target reverses transport. Coefficients are supplied assumptions, never
inferred from coordinate distances or polygon edges.

For component integrals A and support measures m, each edge transfers
`dt * (conductance * (A_source/m_source - A_target/m_target)
+ transport_rate * A_source)`. Every transfer is added and subtracted
simultaneously. Extensive values already are integrals; intensive values are
multiplied by support measure. Signed values retain their signs without clipping.
Vectors evolve componentwise as passive quantities in an explicitly declared
`{"kind":"fixed_global","axes":["x","y"]}` frame per vector field. This is not
momentum dynamics, local tangent transport or a curved-surface PDE solver.

**Open fields (estimation hook).** A field may declare `decay_rate` (1/second,
first-order loss, signed) and `source_rate` (amount per support measure per second).
This works in `FieldWorld`, `evolve_fields` and `evolve_field_arrays`. After the edge
fluxes, every substep adds `dt * (source_rate * m - decay_rate * A)` to each cell
amount. Both backends evaluate this term with the same operation order, so results
stay bit-identical. Conservation then reports `external_input` (the `math.fsum` of
the added terms) and checks `final = initial + external_input`. The L1
non-increase check is skipped for open fields. Positive decay adds to the
outgoing rate that sets the stable step. These terms match the
`field_diffusion_transport` estimator (`include_decay`/`include_source`).
`FieldWorld(config, calibration=estimate_or_record, calibration_field='pm')`,
`simulate_fields(..., calibration=...)` and `evolve_fields(..., calibration=...)`
bind the estimated values:

- `edges[*].conductance` and `edges[*].transport_rate` go to every edge.
- The fitted decay and source terms go to the named field. A single-field world
  needs no name.

Results then carry `calibration` and `parameter_provenance`, which marks each value
estimated (with `estimate_id`/`record_id`) or assumed. The daily field process
accepts `calibration` and `calibration_field` parameters and always reports
`parameter_provenance`. Timelines loaded from `SpatialStore` stay closed.

Substeps reduce the requested interval until the maximum outgoing fraction is at
most 0.9. The solver checks finite arithmetic, componentwise integral conservation
and non-increasing component L1 integral within reported floating-point tolerance.
A failed conservation/stability check raises `ValueError`.

Request defaults are 10,000 substeps and 1,000,000 component updates; the ceilings
a request may ask for are the named limits `field_max_substeps` (10,000,000) and
`field_max_work` (10^12). A request that would exceed its own budget fails before
numerical execution. Worlds are bounded by `field_max_cells` (20,000,000 supports),
`field_max_fields` (1,000), `field_max_edges` (100,000,000), `field_max_values`
(200,000,000 support x components) and `field_max_input_bytes` (64 GiB canonical
JSON); vectors keep the domain rule of at most 32 components. See "Limits,
backends and scale" below.

## Limits, backends and scale

All former hard caps are named fields of `worldmodel.limits.Limits`. Raise or lower
them per call (`FieldWorld(config, limits={...})`, `evolve_fields(..., limits=...)`,
`SpatialStore(path, limits=...)`, `store.evolve_timeline(request, limits=...)`,
`CompositionEvaluator(config, limits=...)`), for a block with
`with use_limits(field_max_cells=50_000_000): ...`, or process-wide with
`WORLD_MODEL_LIMITS='{"field_max_edges": 200000000}'`. Oversized requests raise
`LimitExceeded` (a `ValueError`) naming the limit and how to raise it; a lowered
limit still rejects.

| Former cap | Limit (default) |
|---|---|
| FieldWorld 100,000 cells; field_dynamics 1,000 supports; spatial import 100,000 cells; timeline `max_cells` <= 1,000 | `field_max_cells` (20,000,000) |
| FieldWorld 1,000 fields; field_dynamics/spatial 100 fields | `field_max_fields` (1,000) |
| cells x fields <= 1,000,000; support x components <= 100,000 | `field_max_values` (200,000,000) |
| FieldWorld/spatial 100,000 edges; field_dynamics 10,000 edges | `field_max_edges` (100,000,000) |
| 100,000 (FieldWorld) / 10,000 (spatial) claims; 1,000,000 memberships; 10,000 selected memberships; split expansion 100,000 | `field_max_claims` (1,000,000), `field_max_claim_memberships` (50,000,000) |
| `max_substeps` <= 100,000; process 10,000 daily substeps | `field_max_substeps` (10,000,000) |
| `max_work` <= 10,000,000; process 100,000 cumulative updates | `field_max_work` (10^12) |
| projection `limit` <= 100,000 | `field_max_projection_records` (100,000,000) |
| 8 MiB world input | `field_max_input_bytes` (64 GiB) |
| timeline `max_frames` <= 1,000; `max_snapshots` <= 100,000; 16 MiB output; 1 MiB request; 1,000 lifecycle operations; 100 batches / 100 events | `timeline_max_frames` (1,000,000), `timeline_max_snapshots` (500,000,000), `timeline_max_output_bytes` (16 GiB), `timeline_max_request_bytes` (256 MiB), `timeline_max_lifecycle_operations` (10,000,000), `spatial_max_lifecycle_batch` (1,000,000) |
| query `limit` <= 1,000; geometry candidates <= 10,000; neighborhood/incident/split edges <= 10,000; merge/split <= 1,000 cells; 1 MB row | `spatial_max_query_rows` (20,000,000), `spatial_max_candidates` (20,000,000), `spatial_max_edge_rows` (100,000,000), `spatial_max_lifecycle_cells` (1,000,000), `spatial_max_row_bytes` (1 GiB) |
| polygon 128 vertices; refinement/coarsening 100 parts | `geometry_max_vertices` (1,000,000), `spatial_max_refinement_parts` (1,000,000) |
| composition 20 steps, 100 supports, 100 accounts, 10,000 transfers, demand 1,000 kg, 1,000,000 lifecycle work, 8 MB output, 1 MB config | `composition_max_steps`, `composition_max_supports`, `composition_max_accounts`, `composition_max_transfers`, `composition_max_demand`, `composition_max_lifecycle_work`, `composition_max_output_bytes`, `composition_max_config_bytes` |
| spatial surface 500 cells, 1,000 edges, 200/1,000 frames, 32 MB input | `surface_max_cells`, `surface_max_frames`, `surface_max_input_bytes` |

Request-level defaults are unchanged (`max_substeps` 10,000, `max_work` 1,000,000,
selection `limit` 1,000, timeline `max_frames` 100). Large runs must request larger
budgets explicitly. The daily field process adapter also preflights each call
against its `max_substeps`/`max_work` parameters, so unfit work fails before any
handler runs. Composition steps take an optional config `spatial_max_work`
(default 100,000).

**Backends.** `FieldWorld(..., backend=)`, `evolve_fields(..., backend=)`,
`store.load_arrays(backend=)` and timelines accept `'python'` (reference),
`'numpy'` (optional extra `pip install "worldmodel-substrate[fast]"`) or `'auto'`
(numpy when installed and cells + edges >= 50,000; `WORLD_MODEL_BACKEND` sets the
default). Both run the same array core (`worldmodel.field_arrays`): cells are
indexed, edges are parallel source/target index arrays, and fields are
per-component amount arrays. Results are **bit-identical**: the numpy backend
evaluates each edge transfer with the same operation order and accumulates net
changes with `bincount` over interleaved `[source, target]` indices, which
reproduces the Python loop's sequential sums; integrals use `math.fsum` in both.
The signed solver's outgoing-rate sum now adds `conductance/measure + rate` as one
term per edge endpoint (a floating-point-only change to the reference).

**Array API.** For grids whose dict form would not fit in memory, call
`worldmodel.field_arrays.evolve_field_arrays(measures, fields, sources, targets,
conductance, transport_rate, duration_seconds=..., step_seconds=..., max_substeps=...,
max_work=..., backend='numpy')`, where each field is `{'kind', 'values'}` with shape
(cells,) or (cells, components). It returns reported arrays, execution and
componentwise conservation without building per-cell dicts. `SpatialStore.load_arrays`
and `put_value_arrays` move a stored domain to and from the same core. Selections use
a TEMP id table, so they are no longer limited by SQLite variable counts, and imports
use batched `executemany`.

**Timeline history.** A timeline request may set `history`: `full` (default,
unchanged output), `every_n` with `history_every: N` (full frames at every Nth
frame and the final frame, summary frames otherwise), or `summary` (frames carry
time, lineage, selection sizes and per-field integrals; there are no snapshot rows,
`state` is `null`, and `state_summary` holds counts, integrals and an
`arrays-sha256-v1` state digest, also recorded in the completion audit). Summary
timelines evolve in memory and persist values only before dated events and at the
end, inside the same single transaction, so rollback is unchanged.

Measured on the GB10 (20-core aarch64, Python 3.12, numpy 2.5.3; the numbers are
single benchmark runs, see `benchmarks/scale/bench_fields.py`): 3 fields (4
components), 4 substeps on random-neighbor graphs.

| Cells / edges | Backend | Kernel s | Peak RSS | Component updates/s |
|---|---|---:|---:|---:|
| 100k / 400k | python | 2.3 | 0.17 GiB | 3.4M |
| 100k / 400k | numpy | 0.13 | 0.09 GiB | 63M |
| 1M / 4M | python | 43.9 | 1.44 GiB | 1.8M |
| 1M / 4M | numpy | 1.6 | 0.60 GiB | 52M |
| 10M / 40M | numpy | 26.5 | 5.5 GiB | 30M |

`SpatialStore` bulk operations (`benchmarks/scale/bench_spatial_store.py`, on-disk
SQLite, a grid with about 2 edges per cell and 2 scalar fields):

| Cells / edges | initialize s | load_arrays s | put_value_arrays s | select 10k cells s | Peak RSS | DB size |
|---|---:|---:|---:|---:|---:|---:|
| 100k / 0.2M | 1.9 | 0.5 | 0.8 | 0.16 | 0.23 GiB | 74 MiB |
| 1M / 2M | 20.8 | 5.6 | 8.0 | 0.29 | 1.57 GiB | 753 MiB |
| 5M / 10M | 111.3 | 31.8 | 45.0 | 0.88 | 7.69 GiB | 3.8 GiB |

Imports run at about 80k-100k cells per second including indexes, foreign keys and
the source hash. Dict-based `select` of whole large domains and `history: "full"`
timelines stay O(state) in JSON per retained frame; use `summary`/`every_n` and
the array API at national scale.

## Atomic timelines

`store.evolve_timeline(request)` accepts `start`, `end`, `sample_seconds`,
`step_seconds`, the same boundary/units declarations, `component_frames`, and
optional `events: [{"time": "...", "events": [lifecycle_operation, ...]}]`.
Times use microsecond precision. Birth, death, merge and split use the existing
`store.apply` contract. A nonzero birth requires `external_input: true`; deaths
must transfer nonzero integrals. Splits require explicit replacement topology.

Evolution reaches each event time before applying that time's ordered batches.
Events run before that timestamp's output frame. Event times appear even between
regular samples. Frames contain time, phase, complete selected state, selection
and provenance. `snapshots` expose time/entity/variable/value/unit with synthetic
origin and empty evidence; the result also includes final state, source hashes,
conservation, dated event audits, numerical segments and execution counts.

All numerical updates, lifecycle batches, audits and continuation metadata commit
in one SQLite transaction. Failure restores the entire previous state. Reopened
stores require the next start to match the persisted simulation time and retain
the declared component frames. Optional `cells` selects a bounded domain;
`allow_boundary_cut` must explicitly permit dropping external topology.

Defaults: 100 frames, 100,000 snapshots, 1,000 selected cells. Hard limits: 1,000
frames, 100 event batches, 1,000 lifecycle operations, and a 16 MiB output cap.
Work/substep budgets are cumulative across segments. Birth imports are separately
reported in conservation accounting.

## Explicit planar geometry

An optional support `geometry` stores a simple polygon:

```json
{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]],
 "crs":{"id":"LOCAL:plane","kind":"cartesian","axes":["x","y"],"unit":"m"},
 "simplified":false}
```

The store pins one exact polygon CRS; no implicit reprojection occurs. Rings have
3–128 distinct vertices plus a closing point. Holes, multipolygons,
self-intersections and zero area are rejected. Coordinates are finite and within
±10^12. If `simplified` is true, provide `simplification.method` and
`simplification.source_geometry_hash`. Declared support measure remains explicit;
it is not silently replaced by geometric area.

`store.geometry_bbox(bounds, crs=...)` intersects actual polygons;
`store.containing(point, crs=...)` includes their boundaries;
`store.geometry_neighbors(cell_id, crs=...)` requires a shared edge of positive
length without overlapping interiors. These differ from the original `bbox`
point-coordinate query. Queries use an indexed bounding-box candidate selection,
then exact planar predicates, with `limit` at most 1,000 and `max_candidates` at
most 10,000. Returned `truncated` and `candidate_truncated` expose incomplete
searches. Predicates use binary floating-point comparisons with no snapping.
Geodetic latitude/longitude point storage and explicit distance remain supported;
these polygon utilities require Cartesian coordinates.

## Deterministic explicit refinement

`worldmodel.spatial_geometry.rectangle_refinement(cell, axis='x', parts=2,
child_ids=[...], edges=[...])` returns an auditable existing `split` event.
It divides an axis-aligned rectangle uniformly, preserving declared total measure;
`store.apply([result['event']])` conserves extensive values and intensive integrals.
Replacement edges must be supplied. `rectangle_coarsening(cells, target_id=...)`
requires a complete nonoverlapping rectangular tiling and returns a merge event.
Both support 2–100 cells and preserve geometry/CRS metadata.

`refinement_candidates(world, {"field":"signal","unit":"signal",
"operator":"gte","value":2}, limit=100)` returns matching IDs in sorted order,
with truncation. Vector criteria require a component index. These utilities make
selection and uniform-allocation assumptions explicit; they do not infer fine
structure, remesh arbitrary polygons or automatically adapt numerical topology.
