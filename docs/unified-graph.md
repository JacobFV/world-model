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
     43,055 resolved entity IDs in 18,128 asserted-identity clusters
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

`unify-resolve` attaches **only asserted identity**, from two sources:

1. **Published `same_as` assertions** - for example `congress_people` publishing the
   congress-legislators ID lists that link a `bioguide:` person to `icpsr:` and `fec:candidate:`.
2. **Shared unique identifiers** - entities from different sources carrying the same value in a
   namespace that names one thing at a time, via
   `worldmodel.resolution.shared_identifier_links`. Three published predicate shapes are read:
   `identifier_assignment` (`{namespace, value}`, the registries), `identifier` (`{id, value}`, the
   sanctions datasets) and `identified_by` (a `ns:value` literal, the transport datasets). A
   namespaced entity ID is itself treated as a published identifier claim, which is what lets an
   OpenSanctions record that publishes an LEI meet the GLEIF entity whose ID *is* that LEI.

Values are normalized before grouping (`sec_cik` `1750` and `0000001750` are one filer), identifier
rows spill to a SQLite work file so peak memory does not scale with the catalog, and only values
that actually collide across distinct entity IDs are materialized.

**Name similarity is never attached.** `resolution_evaluation.py` measures separately what a
name-based matcher would add, against held-out published LEIs.

### Measured over the whole default scope

`python3 -m worldmodel unify-resolve --workdir /tmp/resolve --exclude epa_aqs_daily`, one process:

| | |
| --- | ---: |
| wall time | 1,309.8 s (21.8 min) |
| peak RSS | 596 MiB |
| identifier claims read | 7,250,628 |
| published `same_as` assertions read | 26,793 |
| identifier values colliding across distinct entity IDs | 21,227 |
| `same_as` links from shared identifiers | 24,589 |
| entities holding two concurrent values in one unique namespace (reported, not linked) | 324 |
| clusters | 18,128 |
| entity IDs resolved | 43,055 |
| largest cluster | 22 |
| oversized components dropped | 0 |

What those clusters actually join (top shapes):

| Clusters | Joins | Example |
| ---: | --- | --- |
| 10,696 | `bioguide` ↔ `icpsr` | `bioguide:A000001` ↔ `icpsr:1` |
| 1,634 | `lei` ↔ `opensanctions` | `lei:06ZODLC132CY1O2Y7D77` ↔ `opensanctions:NK-SgXwijobPzasWcTxShpXGL` |
| 1,475 | `bioguide` ↔ `fec:candidate` ↔ `icpsr` ↔ `opensanctions` | a legislator and their OpenSanctions PEP record |
| 350 | `opensanctions` ↔ `uk:sanctions` | the same party on the OpenSanctions and UK lists |
| 315 | `mmsi` ↔ `mmsi` | two radio identities for one hull, joined by a shared IMO number |
| 51 | `geo:US:state` ↔ `iso3166-2` | `geo:US:state:01` ↔ `iso3166-2:US-AL` |
| 50 | `mmsi` ↔ `opensanctions` | a sanctioned vessel and its AIS identity |
| 25 | `lei` ↔ `sec:cik` | `lei:213800J4SKZAMUEPGW34` ↔ `sec:cik:0002039972` |

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

### Two namespaces the published data shows are not one-to-one

Measured on this catalog, and therefore excluded from clustering
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

`q1_sanctioned_to_listed_holders.py` - datasets: opensanctions_graph, sec_gleif,
gleif_parent_relationships.

OpenSanctions' Global Energy Monitor ownership record `opensanctions:gem-own-e100000002326`
("SunCoke Energy") and GLEIF's `lei:1KF1J2NXQE2PI0QOB943` ("SUNCOKE ENERGY, INC.") are **one
asserted-identity cluster**, because both publishers print the same LEI. From there:

| Edge | Dataset |
| --- | --- |
| `lei:5493002789CX3L0CJP65` **owns** `lei:1KF1J2NXQE2PI0QOB943` | opensanctions_graph |
| 199 funds **fund_managed_by** `lei:5493002789CX3L0CJP65` | gleif_parent_relationships |
| 3 ISINs (`isin:US86722A1034`, `isin:US86722AAD54`, `isin:USU86651AB92`) **issuer_security** of the entity | sec_gleif |

**Where the evidence runs out, quantified.** 1,880 clusters join a sanctions listing to a GLEIF LEI
and 25 join an LEI to an SEC CIK, but **0 join all three**, so the 13F holder leg does not fire for
any sanctioned entity in this index. That is a gap in the published identifiers, not a finding that
no such holding exists.

**Does not establish:** not a sanctions determination (a listing applies to the named party, and
group membership does not transfer it); GLEIF Level 2 records accounting consolidation, not control
or beneficial ownership; the default profile excludes `sec_13f_history` and `companies_house_uk`, so
holdings and UK PSC ownership are only as complete as `sec_ownership_datasets`.

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

`sec:cik:0000769218` ("AEGON LTD.") and `lei:O4QK7KMMK83ITNTHUG69` ("Aegon Ltd.") are one
asserted-identity cluster, because sec_issuer_reference publishes the LEI that *is* the GLEIF entity
ID. From there:

| Edge | Dataset |
| --- | --- |
| **classified_as** `sic:6311` (life insurance) | sec_issuer_reference |
| **issuer_listing** `ticker:XNYS:AEG`, `ticker:OTC:AEGOF`, `ticker:XNYS:AEFC` | sec_issuer_reference |
| **insider_of** filings by directors and officers | sec_ownership_datasets |
| 200 subsidiaries **ultimately_consolidated_by** the LEI | gleif_parent_relationships |

**The FDIC leg is the honest failure.** No published crosswalk links an FDIC certificate to a CIK or
an LEI, so the only thing to try is a name string. Across all 27,833 FDIC-insured institutions in
the index and all 25 CIK↔LEI clusters, **0 name matches** were found - correctly, since none of
those filers is a US insured depository. The output labels any such candidate
`name string equality only - INFERRED, not asserted`.

**Does not establish:** `fdic_cert` ↔ `rssd` is declared 1:1 by `MAPPING_SPECS` but 943 certificate
pairs share a FED_RSSD in the published directory, so `unify-resolve` does not cluster on it; GLEIF
Level 2 is accounting consolidation, not control; `sec_financial_statements` contributes structure
only under the default profile, so its 50.5M XBRL facts need `--datasets sec_financial_statements`.

## Limitations of the whole construction

- **Nothing is merged.** Records are copied verbatim from each dataset's published output stage.
  Contradictions stay in the index as separate evidence; `worldmodel.reconciliation.materialize_beliefs`
  is where a single current value gets selected, under an explicit policy.
- **Scope is not coverage.** A scope narrower than `--all` indexes a documented subset. The build
  summary's `skipped` list names every dataset left out and why, so "no edge" is never silently
  confused with "no evidence".
- **Identity is asserted, not inferred.** Clusters come from published links and published unique
  identifiers. Where only a name matches - the FDIC-to-SEC leg of query 6 - the result says
  `INFERRED, not asserted`.
- **Joining on a code is joining on a vintage.** GEOIDs, NAICS revisions, HS revisions and ticker
  symbols are all reused or redefined over time. `worldmodel.crosswalks` exists for this;
  string equality in a graph query does not do it.
- **Units are not reconciled by the index.** `flow_aggregate` sums edge weights as recorded and
  says so; agreement of units is the caller's responsibility.
