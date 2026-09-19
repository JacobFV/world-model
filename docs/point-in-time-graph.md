# Publication dates in the index

**The defect.** `Graph` filtered every as-of query with `observed_at <= known_at`. For most of this
catalog `observed_at` is the moment a record was *ingested* — one unify run's wall clock, 2026-09-15
for the bulk of it — not the moment the fact became public. So `--known-at`, the agent perception
horizon and everything else built on that filter silently claimed a point-in-time view nobody had:
ask the index what was knowable in 2015 and it either answered with everything (because everything
was ingested before the cutoff) or with nothing (because nothing was), and in neither case was the
answer about 2015.

Some datasets did carry the real thing and the index threw it away. `sec_13f_history` sets
`observed_at` to the filing date. The vintaged FRED datasets carry `attributes.realtime_start`.
`county_panel` and `county_realtime_panel` publish an explicit `dimensions.available_at` on every
value. The embedding layer only became honest by building its own dated panels, precisely because
the index could not answer the question. This page is that gap closed at the index — and the
measurement below is mostly a report of how little of the catalog the gap can be closed *with*.

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

1. **`dimensions.available_at`** — the availability date the adapter itself established for that
   value. It is taken first because the adapter knows more about the value than the index does, but
   it inherits whatever the adapter did: `county_realtime_panel` derives it from an ALFRED
   `realtime_start`, so it is measured, while `county_panel` derives it from the *same declared lags*
   this file restates, so it is a rule wearing a different field's name. A date from this source is
   not automatically a recorded one.
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

Measured 2026-09-19 by streaming the published normalized records of **every one of the 112 datasets
in the default `unify` scope** (`--profile structure`) and applying `worldmodel.graph.publication` to
each record, with the kind filter the profile itself applies. No index was built and the 131 GB index
was not touched; 1,140,033,558 records were read, of which **126,880,144 are what the default profile
indexes**.

```
records in the default unify scope       126,880,144
  with a publication date                 10,907,576   8.60%
    from a declared dataset rule          10,817,953   8.53%   (99.18% of the dated rows)
    from attributes.realtime_start            89,623   0.07%   ( 0.82% of the dated rows)
    from dimensions.available_at                   0   0.00%
  unknown                                115,972,568  91.40%
datasets contributing any dated record             9   of 112
```

Per dataset, every one that contributes a dated record to the default scope:

| dataset | in scope | with a publication date | share | source |
| --- | ---: | ---: | ---: | --- |
| `noaa_climdiv` | 6,786,669 | 6,783,429 | 99.95% | rule (1 month) |
| `noaa_storm_events` | 5,716,763 | 3,641,356 | 63.70% | rule (4 months) |
| `openfema` | 2,801,582 | 393,168 | 14.03% | rule (0 months) |
| `fred_policy_rate` | 28,039 | 28,036 | 99.99% | `attributes.realtime_start` |
| `fred_treasury10y` | 16,889 | 16,886 | 99.98% | `attributes.realtime_start` |
| `fred_cpi` | 14,688 | 14,681 | 99.95% | `attributes.realtime_start` |
| `fred_treasury2y` | 13,130 | 13,127 | 99.98% | `attributes.realtime_start` |
| `fred_oil_price` | 10,711 | 10,708 | 99.97% | `attributes.realtime_start` |
| `fred_breakeven10y` | 6,188 | 6,185 | 99.95% | `attributes.realtime_start` |
| **the other 103 datasets** | **111,485,485** | **0** | **0.00%** | — |

**Read that table before believing the 8.60%.** Nearly all of it is one thing: three rule-covered
datasets whose observations the default profile happens to index, dated by a lag somebody declared.
The *measured* publication dates — real archive vintages — are 89,623 records, **0.07% of the default
scope**. And `dimensions.available_at`, the strongest of the three sources, contributes **nothing at
all** to the default index, for a reason worth stating plainly:

**The datasets that carry a publisher's own availability date are the ones the default profile
indexes only for structure.** Every `dimensions.available_at` in this catalog sits on an
*observation*, and `worldmodel.unify.OBSERVATION_DATASETS` does not include the dated panels, so
their observations are out of scope. The same is true of the deep ALFRED vintages. Measured over
every record of those datasets regardless of kind — what `--profile all`, or `--datasets <id>`, would
index:

| dataset | records | with a publication date | share | source | in the default scope? |
| --- | ---: | ---: | ---: | --- | --- |
| `bea_national_regional` | 13,716,499 | 13,699,301 | 99.87% | rule (12 months) | structure only: 17,198 rows, 0 dated |
| `fred_macro_panel` | 7,567,876 | 7,566,075 | 99.98% | `attributes.realtime_start` | structure only: 1,801 rows, 0 dated |
| `noaa_climdiv` | 6,786,669 | 6,783,429 | 99.95% | rule | **yes, all kinds** |
| `fred_county_vintages` | 5,319,601 | 5,250,026 | 98.69% | `attributes.realtime_start` | structure only: 69,575 rows, 0 dated |
| `county_panel` | 5,328,552 | 5,014,394 | 94.10% | `dimensions.available_at` | structure only: 314,158 rows, 0 dated |
| `noaa_storm_events` | 5,716,763 | 3,641,356 | 63.70% | rule | **yes, all kinds** |
| `irs_soi_migration` | 2,701,430 | 2,245,227 | 83.11% | rule (18 months) | structure only: 456,203 rows, 0 dated |
| `county_realtime_panel` | 1,257,175 | 1,257,175 | 100.00% | `dimensions.available_at` | no rows in scope at all |
| `fred_state_employment_vintages` | 949,207 | 947,898 | 99.86% | `attributes.realtime_start` | structure only: 1,309 rows, 0 dated |
| `openfema` | 2,801,582 | 393,168 | 14.03% | rule | **yes, all kinds** |
| the seven small FRED anchor series | 101,415 | 101,386 | 99.97% | `attributes.realtime_start` | yes, all kinds (except `fred_deposit_rates`, which has 7 structure rows in scope and no observations) |
| `bls_labor` | 60,872,022 | see below | | rule on observations; `realtime_start` **refused** | structure only: 307,387 rows, 0 dated |
| `bls_prices` | 4,217,151 | 0 | 0.00% | `realtime_start` **refused** | structure only: 10,816 rows, 0 dated |

Every other dataset in the catalog — the SEC filings, the trade flows, the sanctions lists, the
registries, the legislature, the road graphs — carries no publication date in any of the three
forms, and its records are `NULL`.

### One trap the measurement caught

`bls_labor` and `bls_prices` fill `attributes.realtime_start` with **the retrieval date** and say so
in `attributes.vintage = "current_at_retrieval"`, because the BLS flat files carry no vintage at all.
Taken at face value that is 64.8M records whose "publication date" is the day this catalog downloaded
them — the original defect, restored through a field named after the fix. `publication()` refuses a
`realtime_start` on a record marked with a retrieval vintage (`RETRIEVAL_VINTAGES`), counts it under
`refused:current_at_retrieval` in the census, and leaves the record unknown. The declared `bls_labor`
rule still applies to its observations, because that rule is anchored on the reference period rather
than on the download.

### Three panels the index cannot see at all

`firm_panel`, `influence_panel` and `trade_panel` publish records with **no `kind` field**, so `unify`
indexes none of them under any profile (1.5M records read, 0 indexed). `firm_panel` and `trade_panel`
do carry an availability date, but at the *top level* of the record rather than under `dimensions`,
which is the shape the evidence model and this column read. Nothing in this change alters that; it is
recorded here because it is why "the dated panels carry `available_at`" and "the index has no
`available_at`" are both true.

### Reproducing it

The sweep is not part of the package: it is a bounded read of the same artifacts `unify` reads, and
the figure it produces is written into every index at build time as `metadata.publication_coverage`,
which `Graph.publication_coverage()` returns. After the next rebuild, that call is the authority and
this section is the record of what the number was before it.

## Limits

- **A declared lag is not a measured release.** `PUBLICATION_RULES` states when a publisher's release
  calendar implies a value was out. It is not a record of the release. Where a publisher was late,
  or early, or restated a series off-calendar, the rule is wrong by exactly that amount, and nothing
  in the index detects it. `attributes.realtime_start` is a recorded date - the archive says so.
  `dimensions.available_at` is recorded only when the adapter that emitted it had a recorded date to
  emit: `county_realtime_panel`'s comes from an ALFRED vintage, `county_panel`'s comes from the same
  declared lags, so the strongest-looking source is not uniformly the strongest.
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
  `data/sec_13f_history`, and it is one of the highest-value ones available: 78.6M rows with a real,
  measured filing date. It is outside the default unify profile (a bulk dataset), so it does not move
  the headline number, but it moves `--datasets sec_13f_history` from 0% to nearly 100%.
- **91.40% of the default index is `NULL`, and the three biggest wins are not in the index yet.**
  The measurement above is a statement about `worldmodel.unify.OBSERVATION_DATASETS`, not only about
  the publishers: the records that carry a real availability date are observations, and the default
  profile indexes observations for the 54 in-scope members of `OBSERVATION_DATASETS`, none of them
  the dated panels or the deep ALFRED vintages. Adding `county_panel`, `county_realtime_panel`,
  `fred_county_vintages`, `fred_macro_panel`, `fred_state_employment_vintages`,
  `bea_national_regional` and `irs_soi_migration` to that set would add about 36.0M dated records
  (13.7M + 7.6M + 5.3M + 5.0M + 2.2M + 1.3M + 0.9M), taking the dated share of the default scope from
  8.60% to roughly 29% - at the cost of about 36M more indexed rows. That is a scope decision about
  what the index is for, not a defect in this column, and it is not taken here.
- **The reference period is not always the publication anchor.** A rule anchored on `valid_to` assumes
  the record's period end is what the publisher's calendar counts from. For an annual county series
  that is right. For a record whose `valid_to` is an administrative end date rather than a reference
  period end, it is not, and the rule will be wrong in a direction this index cannot check.
- **Nothing here is validated against a held-out release calendar.** No rule in this file has been
  checked against an archive of actual release dates. `docs/county-vintages.md` measures what ALFRED
  archives at county level and is the nearest thing to such a check that exists in this repo.

## Cost

Two indexed columns on the two largest tables, plus a small integer on every edge row. `published_idx`
on `records(published_at)` and `edge_published_idx` on `edges(published_at)` are the affordable path
to `published_at <= ?`; they add one index entry per row to a build that already carries four on
`records` and three on `edges`. Note that at 8.60% coverage the *NULL* side of those indexes is most
of the table, so `published_at IS NULL` is not made selective by them and SQLite may well prefer a
scan: the index earns its place on the dated lookup, not on the exclusion count.

The per-dataset census is computed during the load from data already in hand, so it costs a counter
per dataset and nothing else, and `Graph.publication_coverage()` then reads it as one metadata row
rather than scanning 131 GB.

A query with no `--known-at` pays nothing new. An as-of query runs one extra aggregate to produce its
exclusion count. For a bounded query (`neighbors`, `observations`, a traversal) that aggregate is
bounded by the same index as the query itself. For a whole-table aggregate (`degree_centrality`,
`pagerank`, `flow_aggregate`) it is a second pass over the same filtered edge set the query already
scans, so it roughly doubles an already-expensive query.

None of this is measured at catalog scale, because measuring it needs the rebuild it is waiting on.

## See also

- [unified-graph.md](unified-graph.md) — what the index contains and what it costs.
- [county-vintages.md](county-vintages.md) — what ALFRED actually archives at county level.
- [firm-panel.md](firm-panel.md), [influence-panel.md](influence-panel.md) — panels that carry
  `available_at` per value because the index could not.
