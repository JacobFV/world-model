# Spatial and temporal surfaces

`worldmodel.spatial_surfaces` adapts explicit spatial results into standalone
interactive reports. All drawing and controls run locally, without network
requests, remote scripts or a basemap.

```python
from worldmodel.spatial_surfaces import render_spatial_surface

html = render_spatial_surface(
    timeline_result,
    {'field': 'velocity', 'component': 0},
    materialization_ref={'dataset': 'my_timeline', 'version': 'immutable-version'},
)
```

Scalar selection is `{'field': 'stock'}`. A vector requires either a zero-based
`component` or `magnitude: True`; the report labels the selected transformation and
retains original components, units and component frames. Arrows appear only for
two-component `fixed_global` vectors matching Cartesian x/y coordinates. Arrows
use one shared normalized length scale; they are not geographic trajectories.

`adapt_spatial_result(result, selection, ...)` returns inputs for the existing
`render_surface(data, spec)` API. The convenience renderer supplies spatial, active
topology and, when events exist, lifecycle table panels. Custom specifications can
use `kind: "spatial"` and `kind: "graph", source: "spatial"`, with
`interactive: true`. Static store selections require an explicit `time` alongside
`state`, `source` and `coordinate_system`.

## What the view preserves

Each frame retains cell IDs, measures and measure units, point coordinates or
polygon geometry/CRS, selected field values and units, component frames, source
revision/hashes, selection metadata and lifecycle audits. Polygon territories are
drawn as polygons. Point supports are drawn as points. Missing locations remain
available in the value table and abstract topology; no coordinates are invented.

The slider selects the latest explicit frame through its cutoff. Both support and
edge sets come from that frame. A split removes the parent and its replaced edges;
a merge removes its sources. Omitted supports are never interpreted as deaths.
The entity selector includes supports from every retained frame. Inspecting a
support retains its source details when the support disappears during playback.

The spatial panel colors the selected scalar or transformed vector value. Blue is
negative, orange is positive. Vector arrows and topology edges can be toggled
independently under **Panels and layers**. Panels can be hidden and reordered.
These display changes do not mutate the simulation or its provenance.

## Bounds and omissions

Defaults retain the first 100 frames, first 200 support IDs per frame and 500
resolved edges per frame, in deterministic ID order. Configurable hard maxima are
200 frames, 500 supports and 1,000 edges. At most 1,000 input frames and 32 MiB of
input are accepted; final HTML retains the existing 8 MiB surface limit. Each frame
reports omitted supports and edges, and supports missing locations. Frame omission
counts are displayed. The adapter retains up to 1,000 lifecycle audit rows and
reports omitted event counts. Incomplete views expose truncation rather than
claiming an empty world.

## Export and replay

**Export view specification** includes the exact panel order and visibility,
vector/edge layer flags, selected entity, filter, time index/time, field
transformation, view bounds and materialization reference. If no reference is
supplied, the adapter records a hash of the entire result and its final source.
A custom reference must identify the caller's actual immutable materialization.
The export is a view specification, not a copy of the materialization itself.

To replay, load that materialization and pass the exported specification as `spec`,
its `spatial_selection` as the selection, its `materialization_ref`, and its
`spatial_bounds` as keyword bounds. The report restores selection, filter, time,
layout and layer settings. Source claims remain in the underlying result.

## Local example and verification

```sh
python3 -m examples.render_spatial_timeline /tmp/worldmodel-spatial.html
```

The provenance-preserving CLI route publishes both the timeline and its view:

```sh
wm spatial-timeline --request examples/spatial-timeline.json --dataset spatial_timeline
wm surface spatial_timeline --spec examples/spatial-surface.json --output /tmp/spatial.html
```

The surface spec accepts `spatial_selection` and optional `spatial_bounds` alongside
ordinary panel settings. With only a selection it uses the default spatial panels.

This example executes the signed/vector timeline with a dated split and explicitly
supplied fictional rectangle geometry. Open the output locally. No observed
territory or geodetic boundary is implied by the rectangles.

Focused tests cover explicit vector selection, finite values, truncation,
missing geometry, frame topology, detached input data, hostile-label escaping and
source references. Headless Chrome checks slider playback, support/edge replacement,
source inspection after disappearance, panel and edge toggles, clear selection and
exported state. The browser check also generates a screenshot for visual review.
