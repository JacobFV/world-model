# Publication dates in the index

**The defect.** `Graph` filtered every as-of query with `observed_at <= known_at`. For most of this
catalog `observed_at` is the moment a record was *ingested* — one unify run's wall clock, 2026-09-15
for the bulk of it — not the moment the fact became public. So `--known-at`, the agent perception
horizon and everything else built on that filter silently claimed a point-in-time view nobody had:
ask the index what was knowable in 2015 and it either answered with everything (because everything
was ingested before the cutoff) or with nothing (because nothing was), and in neither case was the
answer about 2015.

Some datasets did carry the real thing and the index threw it away. `sec_13f_history` sets
`observed_at` to the filing date. The vintaged FRED datasets carry `attributes.realtime_start`. The
dated panels — `county_panel`, `county_realtime_panel`, `influence_panel`, `firm_panel` — publish an
explicit `dimensions.available_at`. The embedding layer only became honest by building its own dated
panels, precisely because the index could not answer the question. This page is that gap closed at
the index.

> **What this does not establish.** A declared lag is not a measured release: it says when a
> publisher's calendar implies a value was out, not when it appeared. A null is not evidence of
> lateness: it means this index cannot date the record at all. And a first-vintage date says nothing
> about revisions — the value under a 1991 publication date is the value as it stands today, not the
> number that was printed in 1991. The [Limits](#limits) section is the full list.

## The schema

Graph schema **4**. Schema 2 and 3 indexes stay readable.

| Table | Column | Holds |
| --- | --- | --- |
| `records` | `published_at TEXT` | the date the fact became public, or `NULL` for unknown. Indexed (`published_idx`). |
| `records` | `published_source TEXT` | which of the three sources supplied it: `dimensions.available_at`, `attributes.realtime_start`, `rule`, or `NULL`. |
| `edges` | `published_at TEXT` | the same date, copied onto the edge row. Indexed (`edge_published_idx`). |
| `edges` | `dataset_id INTEGER` | position of the edge's dataset in `metadata.inputs`, so an exclusion can be attributed to a dataset without joining 26M+ edges back to `records` by rowid. |
| `metadata` | `edge_datasets` | `dataset_id` → `{dataset, stage}`. |
| `metadata` | `publication_rules` | every declared rule actually applied in this build. |
| `metadata` | `publication_coverage` | the per-dataset census: records, records with a publication date, edges, edges with one, and the counts per source. `Graph.publication_coverage()` reads it in one row rather than scanning the index. |

## The three sources, in priority order

`worldmodel.graph.publication(record, rule)` returns `(date, source)` and takes the first that
applies. There is no fourth branch: what is left is `NULL`.

1. **`dimensions.available_at`** — the publisher's own availability date, where the adapter emits
   one. This is the strongest signal in the catalog and the dated panels all carry it.
2. **`attributes.realtime_start`** — the ALFRED vintage date of a real-time-vintaged row. A
   *measured* publication date: the archive records when that number was actually on the wire.
3. **A declared dataset-level rule** — `worldmodel.graph.PUBLICATION_RULES`, in the same shape
   `worldmodel.embedding.county_panel.SOURCES` already uses: months after the end of the record's
   own reference period at which the value is treated as public, the revision class, and the release
   fact the lag rests on. The lags are not reinvented here; each entry names the adapter declaration
   it comes from, and `tests/test_graph_publication.py` fails if the two drift apart.

   | dataset | lag | revisions | from |
   | --- | ---: | --- | --- |
   | `bea_national_regional` | 12 months | major | `county_panel.SOURCES["bea"]` |
   | `bls_labor` | 9 months | major | `county_panel.SOURCES["qcew"]` |
   | `irs_soi_migration` | 18 months | none | `county_panel.SOURCES["migration"]` |
   | `noaa_climdiv` | 1 month | minor | `county_panel.SOURCES["climdiv"]` |
   | `noaa_storm_events` | 4 months | minor | `county_panel.SOURCES["storms"]` |
   | `openfema` | 0 months | none | `county_panel.SOURCES["fema"]` |

   A rule needs an anchor, so it fires only on a record that carries a reference period (`valid_to`,
   else `valid_from`). An entity record or a standing assertion has none and stays unknown. `valid_to`
   is the exclusive end of the period, so a rule anchored on it lands one day later than county_panel's
   own end-of-period anchor; that direction is deliberate, because a publication date that is a day
   late withholds a record and never leaks one.

   Two of county_panel's declarations are **not** carried over. Its `geography` source treats county
   internal points as "available from 1980" and its `cbsa` source dates delineations at their OMB
   bulletin; both are panel-local conventions about how to fill a panel cell, not statements about
   when `census_geography` or `cbsa_delineations` became public, and asserting them index-wide would
   be a fabrication. `bls_labor` is the one place a dataset-level rule generalises: county_panel
   declares 9 months for QCEW and 4 for LAUS within the same dataset, and a dataset-level rule cannot
   tell the two series apart, so the longer lag is applied to both and stated as such in the rule text.

4. **Otherwise `NULL`.** Unknown. Never the ingestion time.

## The default query policy

`known_at` filters on `published_at`. A record whose publication date is unknown is **excluded**,
because the index does not know when it became public and therefore cannot honestly show it to a
reader asking what was knowable on a date.

Every as-of result — `neighbors`, `observations`, `resolved_entity`, `neighborhood`, `paths`,
`degree_centrality`, `pagerank`, `flow_aggregate` — now carries a `publication` block:

```json
{"known_at": "2022-01-01T00:00:00+00:00",
 "policy": "exclude_unknown_publication",
 "graph_schema": "4",
 "publication_dates_available": true,
 "filtered_on": "published_at",
 "excluded_unknown_publication": 1,
 "included_unknown_publication": 0,
 "unknown_publication_by_dataset": {"undated": 1},
 "excluded_by_dataset": {"undated": 1},
 "disclosure": "1 candidate rows were excluded because no publication date could be established ..."}
```

The counts are of **candidate** rows in the query's own scope, before any result limit: a `LIMIT`
narrows what is returned, never what the policy withheld. For a traversal the count covers edges
incident to the nodes the traversal actually reached — an edge reachable only *through* a withheld
edge is not counted, because the traversal never got to its endpoint. That makes the traversal
number a floor, and it says so.

**The named option.** `include_unknown_publication=True` (CLI: `--include-unknown-publication` on
`neighbors`, `observations`, `graph-neighborhood`, `graph-paths`, `graph-centrality` and
`graph-flow`) brings the unknown rows back under the old rule, `observed_at <= known_at`. The result
then reports `policy: include_unknown_publication_as_ingested`, counts those rows under
`included_unknown_publication`, and its `disclosure` says in the same breath that they were dated by
when this catalog indexed them and may not have been knowable on the date asked for.

**Older indexes.** A schema-2 or schema-3 index has no publication date. It stays readable for
everything else, and an as-of query against it is **refused** with an error naming the schema and
the rebuild, rather than answered from ingest time. The named option still works there and is
labelled `filtered_on: observed_at`, `publication_dates_available: false`.

**The agent horizon.** `worldmodel.agents.grounding.EdgeIndex` filters on the same rule, with the
same option on its constructor, and `SeedReport.publication` records which policy was in force when
an agent was grounded with a pinned `known_at`.

## Measured coverage

<!--COVERAGE-->

## Limits

- **A declared lag is not a measured release.** `PUBLICATION_RULES` states when a publisher's release
  calendar implies a value was out. It is not a record of the release. Where a publisher was late,
  or early, or restated a series off-calendar, the rule is wrong by exactly that amount, and nothing
  in the index detects it. Only `attributes.realtime_start` and a publisher's own
  `dimensions.available_at` are dates somebody actually recorded.
- **A null is not evidence of lateness.** `published_at IS NULL` means *this index cannot date this
  record*, not *this record was not public*. An excluded row may have been public for decades. The
  exclusion is a statement about the index's knowledge, which is why every result reports the count
  and the datasets rather than quietly returning a shorter list.
- **First availability is not the value as it stood.** A publication date says when a number first
  appeared. The number stored against it is the current vintage at retrieval. For the revising series
  — BEA, LAUS, QCEW, climdiv — an as-of query returns today's value under yesterday's date. That is
  the leakage the dated panels also cannot remove; `dimensions.revisions` in
  `worldmodel.embedding.county_panel.SOURCES` classifies which sources revise.
  `worldmodel.embedding.realtime_panel` is the one construction in this repo where the value *is* the
  first release.
- **`sec_13f_history` encodes its filing date in `observed_at`, not in a publication field.** Under
  the default policy its rows are therefore unknown-publication, even though the honest date is
  sitting in the record. Giving it `dimensions.available_at` is an adapter change, in
  `data/sec_13f_history`, and it is the single highest-value one available. It is also outside the
  default unify profile (a bulk dataset), so it does not move the headline number.
- **The reference period is not always the publication anchor.** A rule anchored on `valid_to` assumes
  the record's period end is what the publisher's calendar counts from. For an annual county series
  that is right. For a record whose `valid_to` is an administrative end date rather than a reference
  period end, it is not, and the rule will be wrong in a direction this index cannot check.
- **Nothing here is validated against a held-out release calendar.** No rule in this file has been
  checked against an archive of actual release dates. `docs/county-vintages.md` measures what ALFRED
  archives at county level and is the nearest thing to such a check that exists in this repo.

## Cost

Two indexed columns on the two largest tables. `published_idx` on `records(published_at)` and
`edge_published_idx` on `edges(published_at)` are what make the exclusion count affordable
(`published_at IS NULL` is served by the index rather than a table scan), and they are what the extra
build time and index bytes buy. The per-dataset census is computed during the load, from data already
in hand, so it costs nothing beyond a counter per dataset.

An as-of query runs one extra aggregate to produce its exclusion count. For a bounded query
(`neighbors`, `observations`, a traversal) that aggregate is bounded by the same index as the query
itself. For a whole-table aggregate (`degree_centrality`, `pagerank`, `flow_aggregate`) it is a second
pass over the same filtered edge set the query already scans.

## See also

- [unified-graph.md](unified-graph.md) — what the index contains and what it costs.
- [county-vintages.md](county-vintages.md) — what ALFRED actually archives at county level.
- [firm-panel.md](firm-panel.md), [influence-panel.md](influence-panel.md) — panels that carry
  `available_at` per value because the index could not.
