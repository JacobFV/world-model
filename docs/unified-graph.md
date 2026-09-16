# The unified graph

`python3 -m worldmodel unify` builds one queryable graph from the **published normalized outputs**
of the whole catalog. It streams: records go from each dataset's gzip artifact straight into the
disk-backed SQLite index in bounded batches, so peak memory is a function of the batch size, not of
the catalog.

The sample-era `unify` did the opposite. It normalized eleven bounded samples and accumulated every
record in a Python list (`records.extend(...)`). That path cannot express the catalog: **106
datasets now have published normalized outputs totalling 1,323,055,982 records** and about 800 GB
of record text. (The catalog is live: `epa_aqs_daily` published while this page was being written,
which is why the measured build below pins 96 inputs and 1,312,082,952 catalog records.
`python3 -m worldmodel unify-scope --inventory` always reports the current figure.)

> This page is the authority on what the unified graph contains, what it costs, and what it does
> not establish. [examples/graph-queries/](../examples/graph-queries/) holds the runnable queries.

## What the default build produced

```
131.5 GB   data/world_evidence/index.sqlite   (graph schema 3)
111,378,669 records     9,925,686 entities
                       33,282,618 assertions
                       51,517,125 observations
                       16,653,240 events
 26,409,127 edges       (entity-to-entity assertions, weighted, bitemporal)
     62,815 resolved entity IDs in 28,008 asserted-identity clusters
```

The ten largest edge predicates: `reported_holding` 10,416,598, `issuer_security` 2,291,868,
`supports_candidate` 1,935,688, `cosponsored_measure` 1,283,245, `holds_position` 996,250,
`within` 844,313, `associated_country` 645,486, `citizenship` 600,695, `contains_resource` 560,947,
`located_in` 555,907.

Queries on it are interactive: counting all records by kind takes 3.9 s (index-only), grouping all
26.4M edges by predicate 0.9 s, a point lookup on a subject 0.2 s.

## What is in the index

`data/world_evidence/index.sqlite`, graph schema 3:

| Table | Row | Populated from |
| --- | --- | --- |
| `records` | one published evidence record, with its `dataset`/`stage`/`version`, `kind`, `entity_id`, `metric`, `subject`, `object`, both time axes, and the record body | every selected record |
| `edges` | one entity-to-entity assertion, with weight and both time axes, pointing back at its record | `kind='assertion'` with both `subject` and `object` |
| `resolved` | one entity ID mapped to its canonical cluster ID | `unify-resolve` (asserted identity only) |
| `metadata` | `inputs` (every input pinned as `dataset@stage@version`), `schema_version`, `resolution` | the build |

Record bodies are stored deflated (`compress_bodies`). Record text dominates a catalog-scale index;
`Graph._decode` reads either form, so indexes built before this change stay queryable.

## Scopes, and what each one costs

Measured on this machine (20 cores, 121 GB RAM, NVMe, Python 3.12) on 2026-09-15, one process,
`--cache-mb 2048 --progress 20000000`.

| Scope | Datasets | Records read | Records indexed | Index size | Wall time | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **`--profile structure` (default), measured** | 96 | 882,062,001 | 111,378,669 | **131.5 GB** | **74 min** (63 read + load, 11 CREATE INDEX) | **2.49 GiB** |
| `--datasets sec_gleif`, measured | 1 | 6,157,843 | 6,157,843 | 7.7 GB | 2 min 53 s | 4.7 GiB at `--cache-mb 4096` |
| `--profile identity`, projected | 21 | 386,562,018 | ~285,800,000 | ~340 GB | ~3.2 h | ~2.5 GiB |
| `--profile structure_full`, projected | 106 | 1,323,055,982 | ~434,960,000 | ~515 GB | ~4.8 h | ~2.5 GiB |
| `--profile all` / `--all`, projected | 106 | 1,323,055,982 | 1,323,055,982 | ~1.56 TB | ~14 h | ~2.5 GiB |

Projections come from two constants measured on the default build, not from guesses:

- **1,181 bytes of index per indexed record** (131.5 GB / 111,378,669). Row columns, the
  `(dataset,stage,version,id)` primary key, the four `records` indexes and the `edges` row with its
  three indexes account for roughly 700 of those bytes *regardless of record size*, which is why
  `--all` is dominated by row count rather than by record text.
- **29.6k indexed records/s** end to end for the load, plus ~11 min of `CREATE INDEX` per 111M rows.

Reading is far cheaper than indexing: a dataset the profile filters out entirely streams at
300-500k records/s (byte-level prefilter, no JSON parse), and the whole 882M-record read accounted
for well under half the wall time. `cepii_baci_hs92` alone contributed 269.9M reads and 5,254
indexed records.

`--profile all` at ~1.56 TB **does not fit on this disk** (196 GB free after the default build). It
is supported and will fail on disk space rather than silently truncate. For anything larger than
the default, build a per-scope index instead: `--domain`/`--datasets` with `--index <path>` puts it
anywhere, and each index pins its own inputs.

Three datasets in the default scope contributed **zero** records - `fec_individual_contributions`,
`fec_individual_contributions_2022` and `fec_individual_contributions_2024` publish only
observations, which the default profile excludes - at a cost of 5.9M reads. Add them to `--exclude`
for a slightly faster build, or select them explicitly when you want contribution amounts.

### What the default profile selects

**Graph structure for every dataset** - entities, relationships, identifier assertions and events -
**plus observations only where the observations are the point of the dataset**
(`worldmodel.unify.OBSERVATION_DATASETS`: the FRED and Treasury anchors, Census population and
business patterns, ACS, LODES, FEMA NRI, NOAA nClimDiv and Storm Events, OpenFEMA, USDA NASS and
FAS PSD, Comtrade, the tariff schedules, the fund and venue reference tables, and the political and
governance series).

Nine datasets are excluded because each is a bulk transaction, holding, segment or article feed -
large, not less trustworthy, and one `--datasets` flag away (`worldmodel.unify.BULK_DATASETS`):

| Excluded dataset | Structural rows it would add | Record text | Why |
| --- | ---: | ---: | --- |
| `osm_topology`, `osm_us_south`, `osm_us_midwest`, `osm_us_northeast` | 98.6M | 62 GB | a road routing graph, not a cross-dataset join surface |
| `usaspending` | 66.7M | 49 GB | one record per contract transaction |
| `sec_13f_history` | 78.6M | 43 GB | one record per quarterly 13F holding line |
| `usaspending_assistance` | 49.0M | 38 GB | one record per assistance transaction |
| `companies_house_uk` | 52.3M | 27 GB | the full UK register plus every PSC entry |
| `gdelt_events` | 17.0M | 15 GB | one event record per news article |

`python3 -m worldmodel unify-scope` prints the resolved selection and **every skipped dataset with
its reason**, so an absent edge is always attributable:

```sh
python3 -m worldmodel unify-scope | python3 -m json.tool | head -40
python3 -m worldmodel unify-scope --inventory     # all 125 declarations, published or not
python3 -m worldmodel unify --dry-run             # the scope and its cost, without building
```

### Rebuilding

```sh
# default scope, into data/world_evidence/index.sqlite, publishing a pinned summary artifact
python3 -m worldmodel unify --cache-mb 2048 --progress 20000000

# one domain group (the docs/data groupings) into its own index
python3 -m worldmodel unify --domain transport --index data/world_evidence/transport.sqlite

# named datasets, including ones the default profile excludes
python3 -m worldmodel unify --datasets sec_13f_history,sec_gleif,sec_issuer_reference \
    --index data/world_evidence/holdings.sqlite

# everything, if you have the disk
python3 -m worldmodel unify --all --cache-mb 8192
```

Progress goes to stderr, one line per 20M records read and one per dataset, with the running rate,
peak RSS and an ETA. The JSON result on stdout is the machine-readable summary: per dataset,
`records_read`, `records_indexed`, `entities`, `assertions`, `observations`, `events`, `seconds`,
plus `skipped` with reasons, `totals`, `build` and `index`.

```sh
# publish (or re-publish) the pinned summary for an index that is already on disk
python3 -m worldmodel unify-publish
```

`unify-publish` recomputes the per-dataset counts from the index and publishes the summary artifact
against `metadata.inputs`. It exists because the publish step refuses to run if the unify source
changed while the build was in flight - `provenance.capture_code` will not sign a snapshot it cannot
reproduce, which is the correct behaviour and exactly what happened on the first default build here.
The index survived; only the summary had to be republished. `records_read` and per-dataset timings
are not recoverable that way, and the artifact says so (`reconstructed_from_index: true`).

### Provenance

Every selected dataset's **output checksums** are verified before it is read, and again by the
publish step. The published artifact's manifest pins:

- `inputs`: every source as `{dataset, stage, version}` - the same list the index stores in
  `metadata.inputs`;
- `code`: the unify code snapshot and its interpreter and package identity;
- `rights`: the inherited source-terms inventory for every input.

Raw acquisition payloads are **not** re-hashed by unify - `Store.verify(ref, recursive=True)` would
read the multi-hundred-gigabyte raw tree on every build. `python3 -m worldmodel verify <dataset>`
still performs the full recursive lineage check.

`records.jsonl` in the published artifact is deliberately empty: a record union at this scale would
duplicate hundreds of gigabytes of evidence. The union is `index.sqlite`. The default build's
artifact (`world_evidence@94edbff5…`) pins 96 input versions and inventories the rights of all 96
sources.

The load itself runs with `journal_mode=OFF` and `synchronous=OFF` into a temporary file that is
atomically renamed into place, which is faster than WAL for a single-writer bulk load and leaves no
partially-built index behind on failure. Readers open the finished file read-only; only
`attach_resolution` writes to it afterwards.

## Identity resolution over the real catalog

```sh
python3 -m worldmodel unify-resolve --workdir /tmp/resolve
python3 -m worldmodel graph-neighborhood ofac:party:20314 --hops 2 --resolved
```

`unify-resolve` attaches **only asserted identity**, from three sources:

1. **Published `same_as` assertions** - for example `congress_people` publishing the
   congress-legislators ID lists that link a `bioguide:` person to `icpsr:` and `fec:candidate:`.
2. **Shared unique identifiers** - entities from different sources carrying the same value in a
   namespace that names one thing at a time, via
   `worldmodel.resolution.shared_identifier_links`. Three published predicate shapes are read:
   `identifier_assignment` (`{namespace, value}`, the registries), `identifier` (`{id, value}`, the
   sanctions datasets) and `identified_by` (a `ns:value` literal, the transport datasets). A
   namespaced entity ID is itself treated as a published identifier claim, which is what lets an
   OpenSanctions record that publishes an LEI meet the GLEIF entity whose ID *is* that LEI.
3. **Published crosswalk fields** (`worldmodel.resolution.bridges`) - an identifier a source prints
   in a field that is not an identifier assertion. Three, each naming the published field it reads
   and the `MAPPING_SPECS` entry that says what a row means:

   | Bridge | Published field | Rows on this catalog |
   | --- | --- | ---: |
   | `gleif_sec_cik` | GLEIF LEI-CDF `Entity.RegistrationAuthority` where the authority is `RA000665` (the US SEC) and its entity ID is decimal, i.e. an EDGAR CIK | 4,971 |
   | `gleif_companies_house` | the same fields where the authority is `RA000585` (Companies House) | 112,177 |
   | `gleif_isin_cusip` | `issuer_security` edges to `isin:<US ISIN>`: by ISO 6166 the nine-character NSIN is the CUSIP, so the GLEIF mapping and a 13F information table name one security | 2,291,868 |

   Two rules stop a bridge manufacturing identity. **The authority code decides the namespace, never
   the value shape**: a numeric registration-authority entity ID under a *state* registry is not a
   CIK, and 2,851 of RA000063's 30,074 numeric IDs collide with real CIKs by coincidence. And a
   bridge row is held to **the cardinality its specification declares**: 15 CIKs and 169 UK company
   numbers are each printed by more than one LEI, and those 184 values are refused and reported in
   `bridge_conflicts` rather than merging two legal entities. `unify-resolve --no-bridges` turns the
   whole layer off.

Values are normalized before grouping (`sec_cik` `1750` and `0000001750` are one filer), identifier
rows spill to a SQLite work file so peak memory does not scale with the catalog, and only values
that actually collide across distinct entity IDs are materialized.

**Name similarity is never attached.** `resolution_evaluation.py` measures separately what a
name-based matcher would add, against held-out published LEIs.

### Measured over the whole default scope

`python3 -m worldmodel unify-resolve --workdir /tmp/resolve --exclude epa_aqs_daily`, one process:

| | |
| --- | ---: |
| wall time | 1,666.9 s (27.8 min) |
| peak RSS | 595 MiB |
| identifier claims read | 9,826,983 |
| of those, read by a published-crosswalk bridge | 2,409,016 |
| published `same_as` assertions read | 26,793 |
| identifier values colliding across distinct entity IDs | 31,107 |
| `same_as` links from shared identifiers | 34,469 |
| entities holding two concurrent values in one unique namespace (reported, not linked) | 324 |
| bridge rows refused for breaking their declared cardinality | 184 |
| clusters | 28,008 |
| entity IDs resolved | 62,815 |
| largest cluster | 22 |
| oversized components dropped | 0 |

The claim count is up 2,576,355 on the pre-bridge run: 2,409,016 bridge rows plus 130,745 `cusip:`
entity IDs, which are now read as the identifier claims they are. Peak memory is **unchanged** at
595 MiB, because the rows spill to the same work file; wall time is 357 s longer, on a catalog that
also gained `mit_election_returns` returns and rebuilt `bls_labor` since that run. Of the bridge
claims, 9,288 `gleif_isin_cusip` rows and 592 `gleif_sec_cik` rows met another publisher's entity.
`gleif_companies_house` contributed **0** here, because `companies_house_uk` is a bulk dataset
outside the default scope - add `--datasets companies_house_uk,sec_gleif` and 95,562 of its numbers
are a company in the published register.

What those clusters actually join (top shapes):

| Clusters | Joins | Example |
| ---: | --- | --- |
| 10,696 | `bioguide` ↔ `icpsr` | `bioguide:A000001` ↔ `icpsr:1` |
| 9,288 | `cusip` ↔ `isin` | `cusip:00032Q104` ↔ `isin:US00032Q1040` - **new**, the ISO 6166 bridge |
| 1,828 | `ofac:party` ↔ `opensanctions` ↔ `us_csl` | one designated party on three lists |
| 1,634 | `lei` ↔ `opensanctions` | `lei:06ZODLC132CY1O2Y7D77` ↔ `opensanctions:NK-SgXwijobPzasWcTxShpXGL` |
| 1,475 | `bioguide` ↔ `fec:candidate` ↔ `icpsr` ↔ `opensanctions` | a legislator and their OpenSanctions PEP record |
| **617** | `lei` ↔ `sec:cik` | `lei:07Q4EPZRU8XATVYVY545` ↔ `sec:cik:0001472215` - **was 25** before the GLEIF registration-authority bridge |
| 350 | `opensanctions` ↔ `uk:sanctions` | the same party on the OpenSanctions and UK lists |
| 315 | `mmsi` ↔ `mmsi` | two radio identities for one hull, joined by a shared IMO number |
| 244 | `lei` ↔ `ofac:party` ↔ `opensanctions` ↔ `us_csl` | a designated legal entity and its LEI |
| 51 | `geo:US:state` ↔ `iso3166-2` | `geo:US:state:01` ↔ `iso3166-2:US-AL` |
| 50 | `mmsi` ↔ `opensanctions` | a sanctioned vessel and its AIS identity |

### The sanctions-to-SEC join: what was missing, and what is genuinely absent

Queries 1 and 6 both used to stop at the same place, so it is worth stating exactly which part of
that was our extraction and which part is the published data. Every figure below is measured on the
pinned artifacts of this catalog.

**What was missing, and is now read.** `sec_issuer_reference` publishes an LEI for **25 of its 8,022
CIK entities** (0.3%), which is where the old 25 `lei`↔`sec:cik` clusters came from, and all 25 are
foreign private issuers. Meanwhile GLEIF's golden copy prints, for **every one of 3,431,064 LEIs**,
the register that registered the entity and that register's own identifier for it, and **3,138,388
(91.5%) carry an identifier**. Nothing read that field, because it is an entity attribute rather
than an `identifier_assignment` row. `RA000665` is the US SEC: **27,931 LEIs** name it, of which
**4,971 print a decimal CIK** (the other 22,948 print an `S…` registered-fund series ID and 12 print
an `805-…` investment-adviser file number, both published SEC identifiers but neither a CIK).
Likewise `RA000585` is Companies House: **112,177 LEIs** print a UK company number over **112,006**
distinct numbers, and **95,562 of the 111,837** that survive the 1:1 check are a company in the
published UK register of 5,689,367.

Validating the CIK bridge against the SEC's own names: **596** of those CIKs exist as `sec:cik:`
entities in this index (592 after the 1:1 refusal, which is exactly the 617 - 25 increase in
`lei`↔`sec:cik` clusters), and of the 596, **548 have exactly the same normalised legal name** on
both sides, 9 have a containment match, and the 39 that differ are visibly renames (`Cinedigm Corp`
→ `Cineverse Corp.`, `& ` → ` and `). The GLEIF ISIN mapping bridges similarly cleanly: all
**2,291,868** of its US ISIN rows recompute their ISO 6166 check digit, no ISIN carries two LEIs,
and **9,288** of them are a CUSIP that the 13F information tables also name - giving 6,085 LEIs a
security whose institutional holders are in the index.

**What is genuinely absent, and must not be engineered around.**

| Gap | Measured | Verdict |
| --- | --- | --- |
| a sanctioned legal entity that is also an SEC filer | sanctions datasets publish **1,928** distinct LEI values (1,927 present in GLEIF). **Zero** carry an SEC registration authority. By jurisdiction: RU 546, US-DE 145, CN 137, CY 92, DE 73, GB 63 …; the 241 US ones carry Delaware and other state file numbers (`RA000602` 139), not CIKs | **different populations.** A US entity on a sanctions or ownership list is a state-registered company, not an SEC registrant. No bridge can create this |
| an actually *designated* party whose corporate group reaches an SEC filer | OFAC SDN and the other designation lists publish **183** distinct LEIs. Zero are SEC registrants, their whole published ownership neighbourhood within two GLEIF/OpenSanctions hops is **122** further LEIs, and **zero of those** are SEC registrants either | **genuinely absent.** The wider `opensanctions_graph` population *does* reach SEC filers - 87 at one ownership hop and 436 within two - but those anchors are ownership and politically-exposed-person records, not designations, and the query says so |
| an FDIC certificate or FED_RSSD for a CIK or an LEI | `fdic_bank_financials` publishes 27,833 certificates, 27,566 `rssd` claims (26,574 distinct) and `regulatory_high_holder` edges to `rssd:<RSSDHCR>`. **No** GLEIF registration authority is the Federal Reserve: the best numeric overlap between any authority's entity IDs and those RSSDs is **646 of 31,564 (2.0%)** under `RA000484`, and `RA000665` gives 26 - coincidence between two dense numeric ID spaces | **not published.** Nothing in the catalog crosses FDIC to SEC or GLEIF |
| OpenSanctions `registrationNumber` as a company-register key | **29,128** claims, published with `scheme: registrationNumber` and **no register named**, so the pipeline assigns them no namespace | **not attached.** Typing a bare number by guessing its register is inference, not a published identifier |
| a sanctioned party's ISIN back to the GLEIF issuer | sanctions lists publish **167** distinct ISINs (CN 134, XS 11, US 6, HK 6 …). **3** are in GLEIF's US ISIN map, and all 3 issuers (China Mobile, CNOOC, China Communications Construction) **already publish their LEI** on the sanctions side | **zero marginal links.** Also the wrong assertion: OFAC may list a bond issued by a financing subsidiary under its parent |
| `market_corporate_actions` ticker↔CIK↔FIGI | the dataset republished **after** this index was built, so its 30,727 `issuer_listing` and 16,659 `listing_security` edges are not in the index at all. Separately, `ticker:US:<T>` (market_prices 15,983, alpaca 11,594) shares **0** entity IDs with `ticker:<MIC>:<T>` (sec_issuer_reference 9,859, nasdaq_listings 13,199), which its own `primary_listing` edges are what would join | **an index rebuild, not a resolution change.** A ticker is a listing, never issuer identity, so this belongs in the graph as edges and `MAPPING_SPECS` keeps `sec_cik_ticker` as `listed_as` |

### What inferred, name-based matching would add: measured, and it is bad

`examples/graph-queries/resolution_evaluation.py` holds out published LEIs and asks whether a
name-similarity matcher would find the same pairs. OpenSanctions, OFAC and the other sanctions
lists publish an LEI for 2,277 of 195,582 sanctioned organizations; 2,275 of those LEIs are in the
GLEIF pool the matcher sees. The matcher is fed the entity records only, with identifier assertions
withheld, so scoring is genuinely held out.

```sh
python3 examples/graph-queries/resolution_evaluation.py --workdir /tmp/resolve-eval \
    --gleif-sample 250000 --workers 4 --save
```

| | |
| --- | ---: |
| records matched (195,582 sanctions orgs + 251,908 GLEIF entities) | 447,490 |
| training | unsupervised EM (no identifier supervision available) |
| wall time | 1,364.7 s, of which blocking 1,269.3 s |
| entities the matcher merged | 103,211 |
| labelled pairs | 2,275 |
| labelled entities linked to *some* GLEIF entity | 442 |
| linked to the **published** LEI | 10 |
| linked to a **different** LEI | 432 |
| **recall** | **0.0044** |
| **precision on the labelled subset** | **0.0226** |

That is the measurement that justifies the design: on real cross-source data with ~1% identifier
coverage, unsupervised name matching merges 103,211 entities and gets the answer right 2% of the
time on the cases where the answer is known. The `docs/identity-units-crosswalks.md` figures
(precision 0.9992, recall 0.870) are for *identifier-supervised* training on fictional data with
30% LEI coverage, and that document already warns that unsupervised mode "is much weaker on hard
data". This quantifies how much weaker. **`unify-resolve` therefore attaches no inferred link.**

Caveats on the numbers: recall is bounded by blocking (a pair whose names share no blocking key is
never compared); precision is measured only where a published LEI exists on the sanctions side, so
it is a proxy; and the 251,908-entity GLEIF pool is a bounded pool taken in LEI order, not a uniform
sample of all 3,431,064, which if anything flatters precision.

### Identifiers the published data shows are not one-to-one

Two namespaces are excluded from clustering entirely
(`worldmodel.unify.NON_UNIQUE_IN_PRACTICE`) while the identifier assertions themselves stay in the
graph as evidence:

- **`ein`** - SEC filers share a taxpayer ID across a group. EIN `850019030` is published for both
  `sec:cik:0000081023` (Public Service Co of New Mexico) and `sec:cik:0001108426` (TXNM Energy Inc),
  a subsidiary and its holding company. Clustering on EIN merged 9 distinct filer pairs.
- **`rssd`** - `worldmodel.resolution.MAPPING_SPECS` declares `fdic_cert` ↔ `rssd` as 1:1, but
  **943 FDIC certificate pairs** in the published institution directory share one FED_RSSD, for
  example `fdic:cert:10005` ("Valley Bank, Green Bay", closed 1988) and `fdic:cert:21710` ("M&I
  Bank Northeast", closed 2001), both carrying `rssd` 736943. The specification and the data
  disagree, so we do not merge on it.

Individual **values** are refused the same way, per bridge, against the cardinality its mapping
specification declares. On this catalog `unify-resolve` refuses **184**: 15 CIKs printed as the SEC
registration-authority entity ID by two different LEIs, and 169 UK company numbers printed by two
(for example `gb_company_number` `00032743` under both `lei:213800XQNGMW2ST7FQ03` and
`lei:549300WUNTT0B3TVIT69`). Each appears in the report's `bridge_conflicts` with the identifiers it
collided with, so a refused link is attributable rather than silent.

## Six questions no single dataset can answer

Each script in [examples/graph-queries/](../examples/graph-queries/) prints the dataset behind every
edge and a `what_this_does_not_establish` list. Run them with:

```sh
python3 examples/graph-queries/run_all.py --save
```

Real output, run against the 131.5 GB default index with the resolution attached, is saved under
`examples/graph-queries/outputs/`. Each script discovers its own anchor, so the answers below are
what the catalog actually supports today, including where it stops.

### 1. A sanctions listing to a legal entity, its corporate group, and securities holders

`q1_sanctioned_to_listed_holders.py` - datasets: ofac_sanctions, other_sanctions_lists,
opensanctions(_graph), sec_gleif, gleif_parent_relationships, sec_ownership_datasets.

**This chain now completes.** `ofac:party:17248`, `us_csl:17248` and
`opensanctions:NK-T3oRNWY3XhL72vfsVMcXzX` - all three labelled "LUKOIL OAO" - and GLEIF's
`lei:549300LCJ1UJXHYBWI24` (`Публичное акционерное общество "Нефтяная компания "ЛУКОЙЛ"`) are **one
asserted-identity cluster**, because all four publishers print the same LEI. From there:

| Edge | Dataset |
| --- | --- |
| 200 subsidiaries **directly_consolidated_by** / **ultimately_consolidated_by** the LEI, dated from 2016-12-08 | gleif_parent_relationships |
| 7 ISINs **issuer_security** of the LEI, including `isin:US69343P1057` | sec_gleif (GLEIF/ANNA ISIN-LEI mapping) |
| `isin:US69343P1057` ≡ `cusip:69343P105` | **asserted by ISO 6166** (`resolution.bridges.gleif_isin_cusip`) |
| **24 13F filers reported holding `cusip:69343P105`** as of 2026-06-30, filed 2026-08-06 to 2026-08-14 (e.g. `sec:cik:0001481986`, `sec:cik:0001992110`, `sec:cik:0001050470`) | sec_ownership_datasets |

An OFAC-designated issuer to the institutional managers reporting a position in its security, with
every hop a published identifier. That was the hop that used to be missing.

**Where the evidence still runs out, quantified.** The holder leg fires through the *security*, not
through the filer. 1,880 clusters join a sanctions listing to a GLEIF LEI, and **175** of those LEIs
issue a security the 13F tables also name; 246 of the clusters carry a listing from an actual
designation list and **2** of those reach such a security (LUKOIL is one). The **filer** leg still
does not fire: 617 clusters join an LEI to an SEC CIK - up from 25 - but **0 join all three**, and
that is a true finding rather than an extraction gap. Zero of the 1,928 sanctions-published LEIs
carry an SEC EDGAR registration-authority entity ID, and for the 183 LEIs on the designation lists
proper, neither they nor the 122 LEIs in their published two-hop ownership neighbourhood are SEC
registrants. See the diagnosis table above.

**Does not establish:** not a sanctions determination (a listing applies to the named party, and
group membership does not transfer it; `opensanctions_graph` also carries ownership and
politically-exposed-person records that are not designations); the ISIN-to-CUSIP bridge is *security*
identity only and says nothing about who the issuer is; a 13F manager reports what it had discretion
over at a past quarter end, which is neither a current position nor beneficial ownership of the
issuer; GLEIF Level 2 records accounting consolidation, not control; GLEIF's published ISIN mapping
here covers US ISINs only, so an issuer with only European ISINs has no reachable security; the
default profile excludes `sec_13f_history` and `companies_house_uk`, so holdings are only as complete
as `sec_ownership_datasets` and UK PSC ownership is out of scope.

### 2. One US county across employment, jobs, population, demographics, agriculture, hazard and climate

`q2_county_economy_hazard_assistance.py --county 01001` - **9 datasets meeting on one GEOID**:

| Leg | Dataset | Real value |
| --- | --- | --- |
| containment | census_geography / acs_5yr_tables | `geo:US:county:01001` **within** `geo:US:state:01`, 55 tracts within the county |
| geography | census_geography | land area 1,539,631,459 m², 32.532237 N, -86.64644 E (2024 vintage) |
| employment | census_business | employment 11,510 people (pay period incl. 12 Mar 2019), 867 establishments, annual payroll 385,755 thousand USD |
| jobs | lehd_lodes | jobs_count 12,369 |
| population | census_population | births 162, deaths 175, births_rate 11.617 per 1,000 (2020) |
| demographics | acs_5yr_tables | total population 59,947 (B01001_001), male 29,126 |
| agriculture | usda_agriculture | 1 active gin, 8 acres bearing and non-bearing, plus 9 more metrics |
| hazard | fema_nri | building value exposure 10,241,406,107 USD, agriculture exposure 27,630,646 USD, area 610.47 mi² (2025 vintage) |
| climate | noaa_climdiv | average temperature 62.633 °F (monthly series from 1895) |
| migration | irs_soi_migration | 56 `flow_source`/`flow_destination` edges into the county |

**Missing by construction, not by absence:** NOAA Storm Events anchors its records on
`noaa:storm_event:<id>` and OpenFEMA on `fema:disaster:<id>`, so the county travels in `dimensions`
and event `participants` rather than as a subject or an edge - a GEOID join never reaches them.
`epa_aqs_daily` published after this index was built.

**Does not establish:** joining on a GEOID joins a geography *vintage* (Connecticut replaced its
counties with planning regions in 2022); the periods differ (a March pay period, a 1 July stock, a
five-year average, a monthly series, a single 2025 hazard vintage); suppressed CBP cells are null
with a `missing_reason`, not zero; and nothing here is causal.

### 3. Wheat from field production, through country balances, to reported trade

`q3_commodity_production_trade_price.py --commodity wheat` - datasets: usda_agriculture,
usda_fas_psd, un_comtrade, classifications.

| Leg | Dataset | Real value |
| --- | --- | --- |
| production | usda_agriculture (`nass:commodity:wheat`, subject `geo:US`) | 53,959,000 BU (2019), yield 39.1 BU/ACRE (2007), area harvested 24,769,000 ACRES (2018), value 12,245,482,000 $ (2012) |
| country balance | usda_fas_psd (`usda_psd:commodity:0410000`, subject `iso3:USA`) | production 60,641, exports 28,904, imports 2,445, domestic consumption 36,184, ending stocks 23,846 (thousand t, 2000) |
| reported trade | un_comtrade (`dimensions.product = hs:12`, subject `iso3:USA`) | trade_value 3,704,967,325 USD, exports, Jan 2024 |
| code bridge | classifications | `scheduleb2025:1001110000` "DURUM WHEAT SEED" **classified_as** `naics2022:111140` "Wheat Farming", `sitc4:04110`, `enduse:10140` - 24 edges over 19 Schedule B wheat codes |

**Two joins here are not published and are labelled as such in the output.** The crop identity
across NASS, PSD and HS is a **label match**, and Schedule B → HS is **derived by truncation**
(a Schedule B code's first six digits are its HS6 code) because the published `classified_as` edges
reach NAICS, SITC and end-use but not HS.

**Does not establish:** this is not a commodity balance - bushels, thousand tonnes and kilograms use
different commodity definitions, marketing years and coverage, and `worldmodel.units.convert`
refuses bushels-to-mass without an explicit commodity; Comtrade is *reported* trade with importer
and exporter reports disagreeing; a price received by farmers is not a futures or spot price;
`maps_to` carries no split weights, so quantities must not be summed across a mapping.

### 4. One legislator across committees, bills, party, campaign structure and a PEP record

`q4_legislator_bills_votes_money.py --bioguide A000055` - **3,000 edges on one resolved person from
4 datasets**. The cluster `['bioguide:A000055', 'fec:candidate:H6AL04098', 'icpsr:29701',
'opensanctions:Q672671']` (Robert B. Aderholt) is asserted: congress_people publishes the
congress-legislators ID lists, and the OpenSanctions record shares a Wikidata QID.

| Dataset | Edges |
| --- | --- |
| congress_people | `holds_role` 15, `committee_member` 4, `same_as` 2 |
| govinfo_billstatus | `cosponsored_measure` 311, `sponsored_measure` 13 |
| fec | `supports_candidate` 2,622, `authorized_committee_of` 14, `opposes_candidate` 3 |
| fec_candidates | `principal_campaign_committee` 16 |

**Does not establish:** no influence claim - a contribution is not a vote, and a lobbying registrant
reporting contact with a committee is not contact with this person; Voteview issued new ICPSR IDs on
party switches, so one person can hold several; FEC candidate IDs are per office. Campaign finance
*amounts* are absent by scope, not by absence (`fec` contributes structure only under the default
profile; its 7.7M observations need `--datasets fec`). An OpenSanctions entry for a sitting
legislator is a politically-exposed-person record, not a designation.

### 5. A sanctioned vessel, its AIS identity, and the transport networks around it

`q5_sanctioned_vessel_to_port_network.py` - datasets: ofac_sanctions, opensanctions,
other_sanctions_lists, marine_ais, transport.

**89 asserted-identity clusters join a sanctions listing to an AIS vessel identity, and all 89 of
those vessels are present in marine_ais.** Example: `mmsi:215193000` ↔ `ofac:party:58121` ↔
`opensanctions:NK-8fSPGSst7ThBkX5Q7kduoM` ↔ `us_csl:58121`, joined because marine_ais publishes
`identified_by imo:9506693` and OFAC publishes `identifier imo:9506693` for the listed vessel;
OFAC adds `VESSEL TYPE: Chemical/Oil Tanker`, `Vessel Year of Build: 2011`.

**2 clusters hold two MMSI values for one hull** (`mmsi:477732100` + `mmsi:563302200`, and
`mmsi:636023226` + `mmsi:636090799`), joined only by the shared IMO number - a re-flagged ship, which
is exactly the case a name or MMSI join gets wrong.

Transport reference and networks: 150 `usace:port:`, 400 `wpi:` and 18 `marad:strategic_seaport:`
port entities; `usace:waterway_node:` 6,853 edges and `ntad:rail_node:` 302,771 edges.

**Does not establish:** an MMSI is a reassigned radio-station identity, so an MMSI match across
disjoint periods can be two ships (IMO is stable, MMSI is not); AIS is self-reported and can be
spoofed or switched off; **ports carry no edges in this index**, so they are not nodes of the
waterway or rail graphs and no port call is asserted; AIS position reports are event records keyed
on the report, not the vessel, so a traversal from the vessel ID does not reach them; the OSM road
graphs and the 31.1M FAF freight rows are outside the default profile.

### 6. A listed issuer from SEC filing identity to GLEIF identity to its corporate group

`q6_bank_filings_to_identity_to_group.py` - datasets: sec_issuer_reference, sec_gleif,
sec_ownership_datasets, gleif_parent_relationships, fdic_bank_financials.

With 617 CIK↔LEI clusters instead of 25, the query can pick an anchor that is actually a bank: 5 of
them carry a depository or bank-holding SIC code, and the deepest is `sec:cik:0000036104`
("US BANCORP \DE\") ≡ `lei:N1GZ7BBF3NP8GI976H15` ("U.S. BANCORP"), **classified_as** `sic:6021`
(national commercial bank), with **116** GLEIF Level 2 consolidation edges. The result reports the
`identity_basis` for the hop - here GLEIF's registering authority is `RA000602` (Delaware), so this
particular link came from the LEI `sec_issuer_reference` publishes, not from the RA000665 bridge.

**The FDIC leg is still the honest failure, and now it is diagnosed rather than just observed.** No
published crosswalk links an FDIC certificate to a CIK or an LEI, so the only thing to try is a name
string; across all 27,833 FDIC-insured institutions and all 617 CIK↔LEI clusters, **0 name matches**
were found. That is correct: U.S. Bancorp's insured subsidiary is on the register as "U.S. Bank
National Association", and a holding company and its charter do not share a name. GLEIF does not
close the gap either - **no registration authority in the golden copy is the Federal Reserve**, and
the best numeric overlap between any authority's entity IDs and the 26,574 published FED_RSSDs is
646 of 31,564 (2.0%) under `RA000484`, which is coincidence rather than a crosswalk. The output
labels any name candidate `name string equality only - INFERRED, not asserted`.

**Does not establish:** `fdic_cert` ↔ `rssd` is declared 1:1 by `MAPPING_SPECS` but 943 certificate
pairs share a FED_RSSD in the published directory, so `unify-resolve` does not cluster on it; GLEIF
records the register of *incorporation*, so the RA000665 bridge reaches SEC-registered funds and
advisers far more often than operating banks; GLEIF Level 2 is accounting consolidation, not control;
`sec_financial_statements` contributes structure only under the default profile, so its 50.5M XBRL
facts need `--datasets sec_financial_statements`.

## Limitations of the whole construction

- **Nothing is merged.** Records are copied verbatim from each dataset's published output stage.
  Contradictions stay in the index as separate evidence; `worldmodel.reconciliation.materialize_beliefs`
  is where a single current value gets selected, under an explicit policy.
- **Scope is not coverage.** A scope narrower than `--all` indexes a documented subset. The build
  summary's `skipped` list names every dataset left out and why, so "no edge" is never silently
  confused with "no evidence".
- **Identity is asserted, not inferred.** Clusters come from published links, published unique
  identifiers and published crosswalk fields, and a bridge fires only where the publisher names the
  register the value belongs to. Where only a name matches - the FDIC-to-SEC leg of query 6 - the
  result says `INFERRED, not asserted`.
- **A missing edge is either a scope gap or a real absence, and the two are distinguished by
  measurement.** The diagnosis table above states, for each join that does not exist, which of the
  two it is and the counts behind that verdict. Where it is a real absence - a sanctioned party that
  is also an SEC filer, an FDIC certificate for a CIK - no amount of matching will supply it, and
  attempting it is what the held-out name-matching measurement rules out.
- **Joining on a code is joining on a vintage.** GEOIDs, NAICS revisions, HS revisions and ticker
  symbols are all reused or redefined over time. `worldmodel.crosswalks` exists for this;
  string equality in a graph query does not do it.
- **Units are not reconciled by the index.** `flow_aggregate` sums edge weights as recorded and
  says so; agreement of units is the caller's responsibility.
