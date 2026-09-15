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

Substeps reduce the requested interval until the maximum outgoing fraction is at
most 0.9. The solver checks finite arithmetic, componentwise integral conservation
and non-increasing component L1 integral within reported floating-point tolerance.
A failed conservation/stability check raises `ValueError`.

Default limits are 10,000 substeps and 1,000,000 component updates; hard maxima are
100,000 and 10,000,000. A request that would exceed them fails before numerical
execution. Selected worlds are limited to 1,000 supports, 100 fields, 10,000 edges,
32 vector components and 100,000 support/components, with an 8 MiB input cap.

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
