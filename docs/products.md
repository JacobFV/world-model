# Evidence products

Four commands turn the cross-dataset queries in
[examples/graph-queries/](../examples/graph-queries/) into things a non-author can run against
the unified graph ([unified-graph.md](unified-graph.md)) without reading adapter code:

| Command | Answers | Generalises |
| --- | --- | --- |
| `wm dossier <entity ID or name>` | Everything the index holds about one entity: its asserted identity cluster, identifiers, every edge by predicate and dataset, observations, events, counterparties one and two hops out, where the evidence runs out, and the rights of every contributing dataset | q1, q4, q5, q6 |
| `wm screen <file>` | For each name or ID in a list: the shortest asserted ownership or control paths, within N hops, to anything on OFAC, the US Consolidated Screening List, the UK or UN lists, or OpenSanctions | q1, q5 |
| `wm place-brief <FIPS or "Name, ST">` | One US county: employment, jobs, population, demographics, agriculture, hazard risk, climate, storm events, disaster declarations and assistance | q2 |
| `wm serve --port N` | The three products plus graph neighbourhood and paths as read-only JSON on 127.0.0.1 | - |

`wm products-index` builds the companion index the name search and two of the place-brief legs
need (below). All five are `python3 -m worldmodel <command>` too. The package is
`worldmodel/products/`, standard library only.

```sh
python3 -m worldmodel products-index                         # once per unified index, ~7 min
python3 -m worldmodel dossier LUKOIL --html /tmp/lukoil.html
python3 -m worldmodel dossier sec:cik:0000036104
python3 -m worldmodel screen examples/products/screen_known_listed.txt
python3 -m worldmodel screen list.txt --groups ownership,control,holdings --hops 3
python3 -m worldmodel place-brief "Harris County, TX" --html /tmp/harris.html
python3 -m worldmodel serve --port 8765                      # then GET /dossier?q=LUKOIL
```

In a worktree, prefix with `WORLD_MODEL_DATA=<checkout>/data`. `--index` and
`--products-index` (or `WORLD_MODEL_INDEX`, `WORLD_MODEL_PRODUCTS_INDEX`) point at other indexes.

## The conventions, and how the products keep them

Every answer, from every command and every endpoint, has the same envelope:

| Field | What it carries |
| --- | --- |
| `answer` | the product itself; every edge, observation and event in it has `from_dataset` and `record_id` |
| `where_the_evidence_runs_out` | computed for this answer: bounds that bit, identity that is not asserted, datasets out of scope, joins the index cannot make |
| `what_this_does_not_establish` | the product's standing limits, the conditional ones this answer triggered (a sanctions listing, GLEIF Level 2, 13F, a natural person), and four that apply to everything |
| `datasets_used` | derived from the answer, never hand-written; only datasets that contributed evidence |
| `rights` | for each dataset used, at the version the index pins: licence, licence status, redistribution, attribution, terms URL, whether redistribution review is required, and the identified-persons decision from `docs/use-policy.md`; plus notices when a non-commercial-only source contributed under a commercial declared purpose |
| `index` | the index path, schema, the digest of its pinned inputs and the resolution view digest |
| `timings_s` | wall time per step |

- **Every edge names its dataset.** Edges are annotated from the assertion record behind them
  (`records.rowid`), never from a guess about which dataset prints a predicate.
- **Asserted and inferred identity are never mixed.** Identity comes only from the `resolved`
  table that `unify-resolve` attaches. A dossier shows *why* the cluster holds - the identifier
  values its members share and the published links between them. Wherever a product uses a name
  (a name typed by the user, the FEMA county-name leg), the answer says `INFERRED, not asserted`,
  and a name that matches several clusters never merges them: they are listed as candidates.
- **Rights travel with every output**, including the HTML reports, whose first sections are the
  caveats, the rights table and the provenance. The products never gate on rights: the repository's
  policy is `metadata_only_no_local_execution_gate` ([use-policy.md](use-policy.md)), so the
  terms are carried for the reader to act on. A screen carries the rights of the four sanctions
  datasets for every input, including the ones with no path, because a "no path" rests on them.
- **Read-only.** The index is opened `mode=ro`. Every statement is short: long scans are paged in
  20,000-row windows, because in rollback-journal mode a long read holds the shared lock a
  concurrent `unify-resolve` attach must wait for. Reads retry on a locked database, and every
  answer records the resolution view digest at its start and end; if another process attached a
  new resolution in between, the answer carries `resolution_changed_during_answer`.

## The products companion index

The unified index has no index on labels, and two county publishers anchor their records on the
event rather than the county. `wm products-index` reads the unified index once and writes
`products.sqlite` beside it:

| Table | Rows on the default index | What it is |
| --- | ---: | --- |
| `labels` (+ FTS5) | 10,443,084 entity labels + 306,119 sanctions aliases | name search; mirrors `wm search-entities` (case- and accent-insensitive label and alias match, grouped by asserted cluster) over the unified index |
| `place_events` | 1,303,048 storm events and 68,792 disaster declarations | events whose publisher names a county GEOID among the participants |
| `place_named` | 144,538 groups from 2,697,024 OpenFEMA rows | assistance rows that publish a county *name* and a state, summed per (state, county name, disaster, metric) |

Rebuilt on 2026-09-19 against the 139 GB index: **5 min 4 s wall, 2.9 GB on disk** (labels 117 s,
aliases 7 s, places 57 s, FTS and B-tree indexes 123 s), reading 10,687,168 entity records. The
2026-09-18 build against the 131.5 GB index took 6 min 33 s at 485 MiB peak RSS. Only the label
count moved: the place tables are identical, because the datasets that feed them did not change.
763,671 storm events filed against NWS forecast zones name no county and are counted, not
placed. The file pins the unified index's input digest; a product refuses a companion built for
a different index. Without it, dossier and screen still accept entity IDs and place-brief still
accepts a FIPS code or `"Name, ST"`; the storm and assistance legs then say they were not read.

## Measured latency on the real index

Each command run in a fresh process, twice: the first run on an input this session had not
queried ("first touch"), then immediately again ("warm"). The machine is shared, has 121 GB of
memory for a 131 GB index and cannot drop the page cache without root, so "first touch" is not a
guaranteed-cold disk: it is the realistic first query. Wall time includes interpreter start-up
(~0.1 s); product time is the answer's own `timings_s.total`. Peak RSS stayed under 115 MiB.

| Command | Input | First touch (wall / product) | Warm (wall / product) | Peak RSS |
| --- | --- | ---: | ---: | ---: |
| dossier | `bioguide:P000197` (a legislator; 3,651 edges on a 4-ID cluster) | 1.04 s / 0.87 s | 0.19 s / 0.05 s | 74 MiB |
| dossier | `iso3:RUS` (a hub node) | 2.24 s / 2.10 s | 0.47 s / 0.37 s | 95 MiB |
| dossier | `geo:US:county:17031` (Cook County) | 0.98 s / 0.84 s | 0.24 s / 0.12 s | 89 MiB |
| dossier | `sec:cik:0001067983` (Berkshire Hathaway) | 0.31 s / 0.16 s | 0.16 s / 0.03 s | 43 MiB |
| dossier | name `Sovcomflot` (FTS search, then the dossier) | 0.26 s / 0.14 s | 0.14 s / 0.02 s | 35 MiB |
| screen | `screen_known_listed.txt`, 6 inputs, ownership + control | 0.38 s / 0.24 s | 0.17 s / 0.02 s | 43 MiB |
| screen | same, + holdings (13F managers fan out to thousands of securities) | 2.56 s / 2.46 s | 0.65 s / 0.52 s | 115 MiB |
| place-brief | `48201` (Harris County) | 0.87 s / 0.74 s | 0.26 s / 0.14 s | 75 MiB |
| place-brief | `06037` (Los Angeles County) | 0.79 s / 0.66 s | 0.25 s / 0.13 s | 73 MiB |
| place-brief | `"Yakima County, WA"` (name resolved through the products index) | 0.56 s / 0.55 s | - | - |
| place-brief | `"Kittitas, WA"` without the products index (name resolved from one state's entity records) | 0.52 s / 0.52 s | 0.11 s / 0.11 s | - |

One measurement is worth keeping for what it taught. The first version of the no-products-index
name lookup took **28.1 s**: its `dataset IN (...)` filter made SQLite choose the primary key and
walk every record of `census_geography` and `acs_5yr_tables` instead of the few county entity
records. Every product query now pins its index choice (`+dataset`, `+kind`, `+metric`), and the
same lookup takes 0.03 s. The same trap would have made the companion-index build scan the whole
`kind` index once per 20,000-row window.

`wm serve`, each endpoint requested twice in a row (first / repeat); "first" is the first request
for that input in the session, except the HTML report, which followed the JSON for the same county:

| Endpoint | Bytes | First | Repeat |
| --- | ---: | ---: | ---: |
| `/health` | 1,380 | 0.017 s | 0.001 s |
| `/search?q=Sovcomflot` | 4,237 | 0.003 s | 0.002 s |
| `/dossier?q=Rosneft` | 255,367 | 0.806 s | 0.067 s |
| `/screen?q=lei:213800FXBZXOXNXKWP95&q=Gazprom Neft` | 22,004 | 0.015 s | 0.008 s |
| `/place-brief?place=12086` (Miami-Dade) | 177,216 | 1.097 s | 0.210 s |
| `/place-brief.html?place=12086` | 512,617 | 0.239 s | 0.306 s |
| `/graph/neighborhood?entity=lei:549300LCJ1UJXHYBWI24&hops=2&limit=500` | 248,230 | 0.893 s | 0.612 s |
| `/graph/paths?source=lei:213800FXBZXOXNXKWP95&target=ofac:party:17248` | 1,363 | 0.002 s | 0.002 s |

## Dossier

Resolution: an entity ID the index knows is used as given. Anything else is searched on published
labels and sanctions aliases; the best candidate (exact normalised label first, then the number of
publishers that match) is chosen, the choice is labelled `INFERRED, not asserted`, and every other
candidate is listed (`--pick N` chooses another). Then, for every member of the asserted cluster:

- **descriptions** - each publisher's entity record, with its attributes;
- **identifiers, aliases, other literal claims** - identity assertions (deduplicated, with a
  record count), sanctions aliases, and the other literal predicates grouped by dataset;
- **identity basis** - identifier values carried by two or more members, and published
  `same_as` / `same_designation_as` edges between members;
- **edges by predicate and dataset** - exact counts per predicate and direction; datasets are
  counted exactly up to 5,000 edges per member and predicate, and flagged when sampled;
- **observations and events** - per dataset and metric, with the latest value and its dimensions;
- **counterparties** - one and two hops out through non-reference predicates, taken in turn from
  each predicate (so 246 OpenSanctions `owns` edges cannot crowd out the one `issuer_security`
  edge that leads to 13F holders); countries, programmes, lists and codes are reported at hop 1
  and never expanded, and hubs above 5,000 edges are named rather than expanded.

**Example: `wm dossier LUKOIL`** ([output](../examples/products/dossier_lukoil.json)). The search
returns 10 candidate clusters (the default limit). The first is the asserted cluster `lei:549300LCJ1UJXHYBWI24` =
`ofac:party:17248` = `opensanctions:NK-T3oRNWY3XhL72vfsVMcXzX` = `us_csl:17248`, held together by
the LEI all four publishers print. 310 edges over 13 predicates: 246 `owns` (opensanctions_graph),
24 GLEIF Level 2 consolidation edges, 7 `issuer_security` (sec_gleif), 20 programme memberships
from four datasets. At hop 2, through `cusip:69343P105` ≡ `isin:US69343P1057`, it reaches the 13F
managers that reported holding the ADR (LSV Asset Management, Sterling Capital Management).

The second candidate is where the evidence runs out: **`uk:sanctions:RUS3094`, the UK's
designation of the same company, is its own singleton cluster.** Its record prints
`Business Registration Number: OGRN 1027700035769INN 7708004767 OKPO 00044434` - the OGRN and INN
that OFAC publishes as separate identifiers - but as one unparsed string, so no shared identifier
links it. The dossier shows it as a separate candidate; it does not merge it.

**Does not establish:** a dossier is an inventory of published claims, not a verified profile;
an edge states only its predicate; two entities two hops apart are not related in any stated way;
plus the conditional limits (not a sanctions determination; GLEIF Level 2 is consolidation, not
control; a 13F line is not beneficial ownership; person-level records are retained only under each
source's rule).

## Screen

Each line of the input file is an entity ID or a name (`#` comments). A name is searched and the
three best clusters are screened separately, each labelled as a text match. For each subject:

1. **hop 0** - does its own asserted cluster contain a restrictive-list entry? OFAC (SDN or
   consolidated), the US CSL (with its source list and a category: sanctions designation, export
   control, debarment), the UK and UN lists, OpenSanctions targets with topic `sanction`,
   `export.control` or `debarment`. OpenSanctions topics such as `sanction.linked`, `role.pep`
   or `poi` are reported as `flags`, never as listings.
2. otherwise a bounded breadth-first search over asserted clusters through the selected predicate
   groups, stopping at listed clusters (they are endpoints, not waypoints):

   | Group | Predicates | Default |
   | --- | --- | --- |
   | ownership | owns, controls, directly/ultimately_consolidated_by, parent_reporting_exception, subfund_of, feeder_fund_of, fund_managed_by, branch_of, international_branch_of, regulatory_high_holder, property_in_interest_of | yes |
   | control | director_of, insider_of, significant_role_in, significant_control_over, principal_executive_officer_of, leader_or_official_of, acts_for_or_on_behalf_of | yes |
   | holdings | issuer_security, reported_holding (holder → security only) | no |
   | associates | family_member_of, associate_of, linked_to, provides_support_to, represents | no |
   | lineage | successor_entity, succeeded_by | no |

   The search follows `Graph.paths(resolved=True)` conventions - members expanded together,
   canonical endpoints, every same-level parent kept so all shortest paths are recoverable, each
   step naming its edge, direction and dataset. It is its own loop rather than a call to
   `Graph.paths` for two reasons: it has thousands of targets (every listed cluster) rather than
   one, and it honours a direction per predicate. `reported_holding` runs holder → security only,
   because two managers holding one security are not related by it; without that rule a 13F
   manager's three-hop neighbourhood is every other manager in the market. `Graph.neighborhood` and
   `Graph.paths` are what `wm serve` exposes for arbitrary queries.
3. **status**: `listed`, `path_found` (with the hop count, the nearest five listed clusters and up
   to three paths each), `inconclusive_search_truncated` (the edge budget or a per-node cap bit
   before anything was found), `no_path_within_bound` or `not_resolved`. Every result carries
   `absence_of_a_path_is_not_clearance: true`.

`coverage_gaps` is attached to every screen: absence of a path is not clearance; no beneficial
ownership below reporting thresholds (13D/13G at 5%, UK PSC at 25%, 13F only above USD 100M, no
nominees or trusts); 13F is long-only US equity; OpenSanctions is CC BY-NC 4.0, non-commercial use
only; GLEIF Level 2 covers only LEI holders that report parents; a name is a text match; lists are
as of the pinned versions; OpenSanctions includes other jurisdictions' counter-sanctions; the
default scope excludes UK PSC, the 13F history and USAspending.

**Example** ([input](../examples/products/screen_known_listed.txt),
[output](../examples/products/screen_known_listed.json),
[with holdings](../examples/products/screen_known_listed_with_holdings.json)):

| Input | Status | Path |
| --- | --- | --- |
| `ofac:party:17248` | listed, hop 0 | OFAC SDN, US CSL SSI list, OpenSanctions `sanction` |
| `mmsi:215193000` (the AIS identity of the tanker PS AUGUSTA) | listed, hop 0 | the MMSI and the OFAC vessel share an IMO number |
| `lei:213800FXBZXOXNXKWP95` LUKOIL SECURITIES LIMITED | path, 1 hop | `directly_consolidated_by` → LUKOIL (gleif_parent_relationships) |
| `sec:cik:0001050470` LSV Asset Management | no path (ownership, control); **path, 2 hops with holdings** | `reported_holding` → `cusip:69343P105` (sec_ownership_datasets), `issuer_security` ← LUKOIL (sec_gleif) |
| `lei:N1GZ7BBF3NP8GI976H15` U.S. Bancorp | no path within 3 hops (557 clusters reached); **path, 2 hops with holdings** | reported holdings of VSE, Illumina and ViaSat, which OpenSanctions lists from *China Sanctions Research* |
| `Sovcomflot` (a name) | three candidates: the Russian PJSC `listed`; SOVCOMFLOT (UK) LTD 1 hop (`owns`, opensanctions_graph); **`uk:sanctions:RUS1097` listed but a separate cluster** | |

The U.S. Bancorp row is the reason every OpenSanctions listing now carries
`opensanctions_sanctions_sources`: with holdings traversed, a US bank holding company "reaches"
US defence and biotech issuers that China's counter-sanctions name. That is a true path through
published claims and a meaningless finding for most readers; the source list is what tells them
apart. The Sovcomflot row repeats the UK-list gap found in the LUKOIL dossier.

**Does not establish:** not a sanctions determination; proximity does not transfer a listing
(the OFAC 50 percent rule extends some restrictions by rule, which this does not evaluate); a
path is a chain of claims each as of its own date, which may never have held at one time.

## Place brief

A FIPS code, `geo:US:county:<FIPS>`, or a name with its state (`"Travis County, TX"`,
`"Autauga, AL"`); a name is matched to the Census county label and says `INFERRED`. Legs:

| Leg | Dataset | Join |
| --- | --- | --- |
| geography | census_geography | GEOID subject |
| employment and business | census_business | GEOID subject |
| jobs | lehd_lodes | GEOID subject |
| population (and a single-vintage series) | census_population | GEOID subject |
| demographics | acs_5yr_tables | GEOID subject (named ACS variables plus the list of codes) |
| agriculture | usda_agriculture | GEOID subject (largest latest value per commodity; NASS has no single headline) |
| hazard risk | fema_nri | GEOID subject |
| weather and climate | noaa_climdiv | GEOID subject; calendar-year means **computed here** from complete years, and a 1991-2020 mean |
| containment, migration | census_geography / acs_5yr_tables, irs_soi_migration | `within`, `flow_source` / `flow_destination` edges |
| storms | noaa_storm_events | products index: GEOID in the event's participants (published by NOAA) |
| disaster declarations | openfema | products index: GEOID in the declaration's participants (published by FEMA) |
| household and public assistance | openfema | products index: county **name** matched to FEMA's own label for the GEOID - `INFERRED, not asserted` |

Each metric shows a headline - the latest observation with the fewest non-total dimensions - with
its dimensions, so a reader can see what it measures. The population series takes the newest
vintage only; a plot that spliced vintages would draw a revision as a change.

**Example: `wm place-brief 48201`** ([output](../examples/products/place_brief_48201.json)),
re-run against the 2026-09-19 index. Its containment leg is also where a cap turned into a wrong
answer: the query took the first 20 `within` edges, and Harris County has 57 of which 50 say "in
Texas", so once enough datasets were indexed to fill the cap the brief silently stopped reporting
the county's CBSA and CSA. The cap is 200 now — the widest subject in the index carries 65 — and
the answer reports `containment_truncated` rather than leaving a missing container to be inferred.
Harris County, Texas: population 5,009,302 (1 July 2024, vintage 2024), employment 2,182,164
(March 2023 pay period), NRI national risk index score 99.94 with expected annual loss of
USD 2.22 bn a year, half of it inland flooding. 1,941 storm events name the county from 1950 to
May 2026; flash floods account for 218 of them, 70 deaths and USD 10.3 bn of nominal property
damage, USD 10 bn of which is one event (27 August 2017, Harvey). 41 disaster declarations name it
(12 flood, 12 hurricane, the latest DR-4798-TX, Hurricane Beryl, July 2024). FEMA's assistance
rows matched by name sum to USD 1.87 bn of approved household assistance over 14 disasters and
USD 3.09 bn of public assistance obligated over 19. The 2025 mean temperature, computed from
nClimDiv's monthly values, is 74.1 °F against a 1991-2020 mean of 71.2 °F. The HTML report adds
population, employment and temperature plots, a coordinate plot of 150 storm begin points (no
basemap), and the tables.

**Does not establish:** a GEOID join is a join on a geography vintage; the legs' periods differ;
suppressed cells are null, not zero; no causal claim; storm damage is nominal USD as reported,
not insured loss, and pre-1996 reporting is incomplete; the NRI is modelled; zone-filed storm
events are not counted; a county FEMA spells differently is undercounted, not zero.

## Local API

`wm serve [--host 127.0.0.1] [--port 8765]`, `http.server` from the standard library.

- `GET /`, `/health`, `/search?q=`, `/dossier?q=&pick=`, `/dossier.html?q=`, `/screen?q=&q=&hops=&groups=`,
  `/place-brief?place=`, `/place-brief.html?place=`,
  `/graph/neighborhood?entity=&hops=&limit=&predicates=&direction=&resolved=&valid_at=&known_at=`,
  `/graph/paths?source=&target=&max_hops=&limit=&predicates=&resolved=&valid_at=&known_at=`.
- **No write paths.** GET and HEAD only; POST, PUT, PATCH, DELETE and OPTIONS return 405. The
  index is opened read-only per request.
- **Bounded.** hops 1..3 and limit 1..2,000 for neighbourhoods; max_hops 1..6, at most 50 paths and
  100,000 expansions for paths; 1..50 screen inputs of at most 500 characters; at most four
  requests at once (503 beyond); a response over 16 MiB is refused with 413, never truncated.
- Bound to 127.0.0.1 by default. Binding elsewhere prints a warning: the API serves
  non-commercial-only and redistribution-restricted evidence to anyone who can reach it.
- Graph endpoints annotate every edge with `from_dataset` and `record_id`, and resolve through
  asserted clusters unless `resolved=0`.

## Where the products stop

- **Names.** Search is text on published labels and sanctions aliases. It finds spelling variants
  that share tokens; it does not transliterate (a Cyrillic legal name and its Latin alias match only
  when a publisher prints both), and it does not rank by importance.
- **The UK list is not linked to the rest.** OFSI prints Russian registration numbers as one
  free-text string, so a UK designation joins the OFAC / OpenSanctions / GLEIF cluster only where
  OpenSanctions carries an identifier both sides share. Parsing that field into `ru_ogrn` /
  `ru_inn` claims would be a resolution change (`worldmodel/resolution`, not owned here).
- **Participants are not indexed.** Only the county-keyed storm and disaster events were added to
  the companion index. AIS position reports, legislative events and other participant-anchored
  events remain unreachable from an entity ID.
- **Dossier counterparties stop at two hops,** with at most 200 edges per member and direction at
  hop 1 and a degree cap of 5,000 before expanding a hop-1 node.
- **Screening lists are the pinned versions**, and OpenSanctions mixes jurisdictions; nothing here
  filters by issuing authority yet.
- **Latency figures are first-touch, not guaranteed-cold**, on a shared machine.

Tests: `tests/test_products.py` (products index, dossier, screen, place brief, rights, read-only)
and `tests/test_products_server.py` (the API over HTTP), all on a fictional compressed-body
schema-3 index built in a temporary directory.
