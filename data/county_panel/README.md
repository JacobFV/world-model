# county_panel

A dated US county panel and county graph, assembled by `worldmodel.embedding.county_panel`
from published normalized datasets (QCEW and LAUS from `bls_labor`, BEA county income and
GDP, nClimDiv, NOAA storm events, OpenFEMA declarations, Census county geography, IRS SOI
county-to-county migration, OMB CBSA delineations).

Every observation is one county × feature × year and carries `dimensions.available_at`: the
date on which the value is treated as public, from a declared per-source publication lag
(the rule for each source is in `report.json` → `sources`). Edges are assertions
(`migration_flow`, `within_cbsa`) with their own `attributes.available_at`. Every record's
evidence points at the source record ids it was built from.

```sh
python3 -m worldmodel embed-panel            # collect and publish a new version
```

What it does not establish: values are the current vintage at retrieval, so sources that
revise (`dimensions.revisions` = `minor` or `major`) leak later revisions into any as-of view;
`available_at` is a declared rule, not a measured release timestamp; static geography is not
versioned for boundary changes. See [docs/world-state-embeddings.md](../../docs/world-state-embeddings.md).
