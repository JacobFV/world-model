# county_realtime_panel

First releases of `fred_county_vintages`: for each county, feature and year, the value from the
earliest vintage the ALFRED archive holds, carrying that vintage date as `dimensions.available_at`.
Such a value never changes afterwards, so `dimensions.revisions` is `none` and an as-of reader of
this panel sees no later revision of any number. Static geography (internal point, land and water
area) is carried from `county_panel`.

This is the panel a places attempt needs in order to pass `no_revision_leakage`; the dated
`county_panel` cannot, because it holds the current vintage of every value.

What it does not establish: a first release is what the agency published then, not its best current
estimate, and a series that entered the archive late is dated later than it was truly public, never
sooner. See [docs/world-state-embeddings.md](../../docs/world-state-embeddings.md).
