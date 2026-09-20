# Identity coverage: how much of the graph is joined, and why not more

The unified index holds 10,687,168 entity records under **9,177,244 distinct entity IDs**. A
cross-domain query, the influence panel or a graph-embedding model can use an entity as a bridge
only if evidence about it comes from **more than one dataset**. This page measures that share
before and after mining every asserted crosswalk the published records carry, says where the joins
come from, and says what blocks the rest.

The north-star target was **at least 25% of entities joined across at least two sources**.
Asserted identity does not reach it. The measured answer moved twice: **3.17%** before any bridge
was mined, **3.66%** after mining every published bridge, and **5.81%** once the index was rebuilt
so that it holds the Wikidata identifier records the bridge reads
([the rebuilt-index measurement](#measured-again-on-the-rebuilt-index-2026-09-19)). The sections
below show exactly which populations the remaining 94% are and why no published identifier joins
them.

> Everything here is **asserted identity**: published `same_as` rows, identifiers a publisher
> printed, and published crosswalk fields. No name is matched. Name matching was measured
> separately and does not work on this catalog - recall 0.0044, precision 0.023 against held-out
> published LEIs ([unified-graph.md](unified-graph.md)).

```sh
# the measurement (read-only; 5 min 0 s on the 139 GB index, and 3 min 22 s on the 131 GB one before it)
python3 -m worldmodel identity-coverage --workdir /tmp/jc --mentions
# the same for a candidate resolution, before it is attached
python3 -m worldmodel identity-coverage --workdir /tmp/jc --clusters /tmp/resolve/clusters.jsonl
# the population each dataset claims to cover, and how much of it is held
python3 -m worldmodel coverage-estimate --datasets fema_nri,sec_gleif
```

## Before and after

`unify-resolve` over the index's 96 pinned datasets, 52 min 57 s, 661 MiB peak RSS (the 2026-09-18 run;
the rebuilt index's own run is below). "Joined" means
the entity records of an entity's asserted-identity group (its cluster, or itself) come from at
least two datasets; "independent" additionally requires two publishers, so `sec_gleif` +
`gleif_parent_relationships`, or two Census products, do not count.

| | before | after | change |
| --- | ---: | ---: | ---: |
| entity IDs in an asserted-identity cluster | 53,507 | **100,488** | +46,981 |
| clusters | 28,008 | **49,705** | +21,697 |
| **joined across ≥2 datasets** | 272,127 (**3.17%**) | **314,178 (3.66%)** | +42,051 |
| of which joined by a shared entity ID alone, no resolution | 233,568 (2.72%) | 233,568 (2.72%) | 0 |
| joined across ≥2 **publishers** | 163,726 (1.91%) | **209,335 (2.44%)** | +45,609 |
| joined counting *mentions* (any record naming the entity) | 2,150,200 (25.03%) | 2,174,915 (25.32%) | +24,715 |
| mentions, ≥2 publishers | 194,601 (2.27%) | 222,894 (2.59%) | +28,293 |

The mentions column is the only one that reaches 25%, and it does so almost entirely inside one
publisher: 1,735,838 of the 1,831,417 mention-joined `companies` entities are LEIs that GLEIF's
own Level 2 file names in a relationship or a reporting exception. Counting two publishers, the
mention measure is 2.59%. **A second GLEIF file is not a second source.**

Where the joins moved, by dataset (only the datasets that changed):

| Dataset | entity IDs | joined before | joined after | independent after |
| --- | ---: | ---: | ---: | ---: |
| `ofac_sanctions` | 20,037 | 2,720 | **20,036** | 20,036 |
| `other_sanctions_lists` | 33,731 | 3,378 | **20,830** | 20,830 |
| `transport` | 409,898 | 5 | **3,400** | 3,400 |
| `airport_nodes` | 149,561 | 298 | **3,693** | 3,693 |
| `opensanctions` | 73,016 | 70,140 | 70,140 | 3,641 → **7,055** |
| `opensanctions_graph` | 1,316,124 | 73,235 | 73,116 | 6,584 → **10,023** |
| `sec_gleif` | 3,431,116 | 3,592 | 3,614 | 3,614 |
| `crossref_research` | 89,393 | 0 | **294** | 294 |
| `openalex_people` | 32,800 | 7 | **303** | 303 |

By domain, after: demographics 32.4% joined (a GEOID is published by every Census product),
macro 72.5%, energy_trade 8.2%, politics 3.8%, companies 2.3%, transport 1.1%.

By published entity type, over the resolution's own scope (10,081,152 entity records):

| entity type | entities | joined | share |
| --- | ---: | ---: | ---: |
| jurisdiction | 131,565 | 102,609 | 78.0% |
| business | 309,425 | 33,829 | 10.9% |
| person | 1,043,716 | 84,278 | 8.1% |
| product | 140,656 | 11,018 | 7.8% |
| organization | 3,153,851 | 31,433 | **1.0%** |
| location | 431,365 | 4,417 | 1.0% |
| investment_fund | 249,136 | 585 | 0.2% |
| office / economic_series / publication / resource_deposit / infrastructure / flow / facility / law | 2,296,318 | 15 | **0.001%** |

The last row is the shape of the problem: 2.3M of the index's entities are *records as entities* -
an EIA series, a Federal Register document, an IRS county-to-county flow, an MRDS occurrence, a
lobbying filing, a rail node. No second publisher describes them, and none ever will.

## Measured again on the rebuilt index (2026-09-19)

The two measurements above were taken on an index built before the Wikidata identifier dataset
existed, so the bridge could read Wikidata's claims but the index held none of its entity records.
`wikidata-bridge.md` filed that as follow-up #2: until the index was rebuilt, the bridge was worth
the +5,386 it moved *in the index* rather than the +215,062 it moved over the resolution's scope.
The index was rebuilt on 2026-09-19 — 112 datasets selected, 1,138,520,492 records read,
**126,880,144 indexed** (10,687,168 entity records, 34,471,880 assertions, 62,719,625 observations,
19,001,471 events), 139 GB, 86 minutes — and `unify-resolve` then attached over the whole of it in
**48.4 minutes at 1.24 GiB peak RSS**, reading 11,393,472 identifier claims. The six datasets that
earlier runs had to pass `--exclude` for are in the index now, so this is the first measurement over
the default scope with nothing excluded.

| | before any bridge | after mining bridges (old index) | **rebuilt index** |
| --- | ---: | ---: | ---: |
| distinct entity IDs | 8,590,782 | 8,590,782 | **9,177,244** |
| clusters | 28,008 | 49,705 | **148,123** |
| entity IDs in a cluster | 53,507 | 100,488 | **318,851** |
| **joined across ≥2 datasets** | 272,127 (3.17%) | 314,178 (3.66%) | **532,823 (5.81%)** |
| of which joined by a shared entity ID alone | 233,568 (2.72%) | 233,568 (2.72%) | 239,722 (2.61%) |
| joined across ≥2 **publishers** | 163,726 (1.91%) | 209,335 (2.44%) | **430,551 (4.69%)** |
| joined counting *mentions* | 2,150,200 (25.03%) | 2,174,915 (25.32%) | 2,369,981 (25.82%) |
| mentions, ≥2 publishers | 194,601 (2.27%) | 222,894 (2.59%) | **453,530 (4.94%)** |

**Read the denominator before reading the gain.** The entity population grew by 586,462 because the
index now holds datasets it did not hold before, 425,289 of them Wikidata's own entity records. Of
the +218,645 newly joined entities, **116,677 are Wikidata's own** — 27.4% of the records that
dataset published, every one of them joined to a second *publisher*. The rest are entities that
already existed and now meet a second dataset, mixed with whatever the newly indexed datasets
brought; those two are not separated here, and this page does not claim a split it did not measure.

What the number does establish is that the two routes to it now agree. Measuring the index's
resolved table gives **5.806%** over 9,177,244 entity IDs; a `unify-resolve --no-attach` pass over
the dataset records, run a few hours earlier with the six then-unindexed datasets excluded, gave
**5.821%** over 9,048,525. The gap follow-up #2 named — a bridge worth +5,386 in the index against
+215,062 over the resolution's scope — is closed, and the index is worth what the scope measurement
said it was.

By domain, the spread is as wide as ever, and it is the same story as before — places join, firms
and people do not:

| Domain | entities | joined | share |
| --- | ---: | ---: | ---: |
| `macro` | 132,150 | 95,069 | **71.9%** |
| `demographics` | 1,064,485 | 345,974 | **32.5%** |
| `energy_trade` | 2,590,330 | 219,804 | 8.5% |
| `companies` | 3,944,093 | 177,460 | 4.5% |
| `transport` | 630,206 | 26,843 | 4.3% |
| `politics` | 731,100 | 27,738 | 3.8% |

The pairs that carry the gain are visible in the top cluster shapes: `sec_gleif` + `wikidata_identifiers`
is now the third-largest at **50,336** groups, and `openalex_people` + `wikidata_identifiers` adds
**11,881** — the two joins the bridge was acquired for. The largest shape is still four Census-family
datasets over the same county (83,059 groups), which is agreement between products of one publisher,
not independent confirmation.

## The bridges, and what each one is read from

Every row is a field a publisher prints. `unify-resolve --no-bridges` turns the whole layer off.

| Bridge | Published field it reads | Claims | Met another publisher |
| --- | --- | ---: | ---: |
| `same_designation_as` (link predicate) | `other_sanctions_lists`: the CSL's `entity_number` *is* the OFAC Sanctions List Service profile ID; the UK list prints the UN reference number of the designation it implements | 20,774 | 19,776 `us_csl`↔`ofac:party`, 998 `uk:sanctions`↔`un:sanctions` |
| `sanctions_register_number` | OFAC/CSL identity documents whose `scheme` **and** `issuing_country` name a register: "Tax ID No."+RUS = INN, "Registration Number"/"Business Registration Number"+RUS = OGRN, "Company Number"+GBR = a Companies House number. Check digits recomputed | 13,898 | 13,721 |
| `labelled_register_number` | the UK list's free-text `Business Registration Number`, where the text labels the register: `OGRN 1027700035769INN 7708004767 OKPO 00044434` (LUKOIL) | 342 | 210 |
| `opensanctions_wikidata` | OpenSanctions entity IDs that *are* Wikidata QIDs (`opensanctions:Q672671`) | 486,342 | 2,008 |
| `opensanctions_uei` | `uniqueEntityId` (the US SAM Unique Entity ID) in OpenSanctions identifier values | 27,513 | 0 in this scope (the UEI's partner, `usaspending`, is a bulk dataset outside the index) |
| `bic` → `swift` (namespace alias) | GLEIF's BIC-to-LEI map publishes ISO 9362 codes as `bic`; the sanctions lists publish the same codes as `swift`. An eight-character BIC is the eleven-character form with branch code `XXX` | 39,347 | folded into the shared-identifier layer |
| entity IDs that are identifiers | `transport` keys an airport on `iata:`, `crossref_research` keys a researcher on `orcid:` and an institution on `ror:` | - | 3,395 IATA, 243 ORCID, 51 ROR clusters |
| `gleif_sec_cik`, `gleif_companies_house`, `gleif_isin_cusip` (already present) | GLEIF registration-authority entity IDs; the CUSIP inside a US ISIN | 4,971 / 112,177 / 2,291,868 | 592 / 5 / 9,288 |

## The refusals: identifiers that would have merged different things

A bridge that fires where it should not is worse than a missing bridge. Five rules were added and
each one is counted in the resolution report.

| Rule | What it refuses | Rows |
| --- | --- | ---: |
| **fraudulent values** | an identifier whose own publisher marks it fraudulently used (OFAC `"validity": "Fraudulent"`), *and* the same value on the CSL copy that drops the flag | 91 claims + 91 copies |
| **IMO's two number series** | an `imo` claim on a subject whose publisher types it as an organisation is an IMO *company* number, not a ship: retyped `imo_company` so the two series never meet | 3,825 |
| **codes one publisher prints for two things** | 518 INN and 337 OGRN values that `opensanctions_graph` prints for two or more of its own records - mostly *different* organisations ("Gazprom Dobycha Krasnodar" / "Gazprom Dobycha Vuktyl"; a company and a person) - plus 28 UN/LOCODEs on two World Port Index ports, 36 BICs on two LEIs, MMSIs on two hulls | 1,334 INN + 804 OGRN + 94 BIC + 56 UN/LOCODE + 34 MMSI rows |
| **check digits** | INN and OGRN values that fail the register's own check digit, including the placeholder `ru_ogrn:0000000000000` OpenSanctions prints for several organisations | 61 INN + 188 OGRN |
| **declared cardinality, per dataset** | a bridge value breaking the 1:1 its mapping specification declares *within one publisher* (two publishers printing the same number is agreement, not a conflict) | 7,325: UEI 7,115, Companies House 169, CIK 15, register numbers 24, labelled 2 |

The previous resolution merged on those INN and OGRN values; the new one does not, which is why
`opensanctions`-only clusters fall from 583 to 122 and `lei`+`opensanctions` from 1,634 to 1,553.
Those were wrong merges, not lost joins.

## What blocks the remaining 94%

The unjoined 8,644,421 entity IDs are not a backlog of unmined crosswalks. The table below was
measured on the pre-rebuild index, where the unjoined count was 8,276,604; the rebuild moved the
total and the shares above, and changed none of these blockers. Measured:

| Blocker | Entities | Evidence |
| --- | ---: | --- |
| **One publisher holds the whole population** | ~3.4M LEIs, 1.24M OpenSanctions records, 1.03M EIA series, 305k MRDS occurrences, 244k lobbying filings, 157k Federal Register documents, 151k IRS flows, 109k bills, 104k FDIC certificates and branches | No second dataset in the catalog publishes an entity record for them at all. GLEIF's only catalog partners are `iso_mic_venues` (1,071 LEIs), the sanctions lists (1,928 LEIs) and `sec_issuer_reference` (25 LEIs) |
| **Records as entities** | 2.3M (series, documents, filings, flows, deposits, runways, rail nodes) | Nothing joins a series to anything; see the entity-type table |
| **The join exists but its dataset is outside the index** | 112,177 GLEIF Companies House numbers, 95,562 of which are a company in the published UK register | `companies_house_uk`, `usaspending` and `sec_13f_history` are bulk datasets the default profile excludes. Indexing them *lowers* the percentage, because each adds millions of its own single-source entities |
| **The bridge is sound and the data still does not meet** | the 25,239 SAM UEIs OpenSanctions publishes meet **0** of the 118,689 `usaspending` and 101,949 `usaspending_assistance` recipient UEIs | Measured by streaming both bulk datasets. OpenSanctions' UEIs come from the SAM *exclusions* list, and an excluded party is not a recipient in the acquired award window. Indexing `usaspending` would add the bridge and no links |
| **No published crosswalk exists** | 104k FDIC entities, 152k 13F CIK/CUSIP entities, 70k AIS vessels, 30k HTS codes | Measured in [unified-graph.md](unified-graph.md): no GLEIF registration authority is the Federal Reserve (best numeric overlap 646 of 31,564, coincidence), and no FDIC certificate maps to a CIK or LEI anywhere in the catalog |
| **The publisher names no register** | 29,128 OpenSanctions `registrationNumber` and 121,134 `taxNumber` values with no country | Typing a bare number by guessing its register is inference, not a published identifier. The UK list's *labelled* free text is read precisely because the label names the register |

What would actually move the number: a **person and company register with global coverage**, or
**indexing the bulk registers and accepting their own unjoined mass**. Neither is an extraction gap
that better matching would close. The first of those was then acted on and is the largest single
move this number has ever made: acquiring Wikidata's identifier statements as a dataset took joins
from 3.66% to 5.81%, and 116,677 of Wikidata's own 425,289 entity records meet a second publisher.
It is also the shape of the ceiling — a hub that carries 27.4% of itself into the joined set still
leaves 94% of the graph single-sourced.

## Coverage of a population, which is a different question

"3.66% joined" is a property of this index, not of the world. `wm coverage-estimate` answers the
other half: what population does each dataset claim, and how much of it is held.

`wm coverage-estimate` states a curated population for **111 estimates over 106 datasets**
(2026-09-19; it was 47 over 44). The other 29 declarations are not guessed at: **18** are reported
under `derived`, because their content is produced inside this repository and their coverage is a
property of the inputs they name, and **11** stay under `uncurated`, each naming the specific thing
missing - four have acquired nothing, and seven publish no entity records for an entity-key rule to
select. For the 111 it measures two fractions:

- **`measured_over_declared`** - the entities the published output holds over the count the
  dataset's own `dataset.json` states. An acquisition check.
- **`covered_of_population`** - members of an independently sourced population list that the
  dataset holds. The lists are the dated Census county table and the ISO 3166-1 table shipped in
  `worldmodel/reference` (both with validity dates), TIGER tracts from `census_geography`, and the
  Nasdaq Trader directory from `nasdaq_listings`. **39 of the 111 estimates get one**, and 15 cite a
  declared count.

Measured over the whole catalog before the 2026-09-19 curation pass (14 min 7 s, 832 MiB peak RSS,
one pass per measured dataset). The rows below are from that run and cover the original 47
estimates only. Of the 64 entries added on 2026-09-19, **60 were measured one dataset at a time**
to check that their entity-key rule selects the records they claim; the four largest published
outputs (`cepii_baci_hs92`, `sec_financial_statements`, `usaspending`, `usaspending_assistance`)
were checked against a head sample of their output and their pipeline source rather than a full
pass, so their counts are not measured yet. None of the 64 is re-tabulated here:

| Dataset | Population it claims | Measured | Of declared | Of population |
| --- | --- | ---: | ---: | ---: |
| `sec_gleif` | legal entities holding an LEI | 3,431,064 LEIs | 1.00 | - (GLEIF publishes no count of entities *without* an LEI) |
| `companies_house_uk` | companies on the UK register | 5,689,367 companies | - | - (the register is its own population) |
| `sec_company_assets` | CIKs in the SEC companyfacts bulk file | 17,045 | **0.837** | - |
| `openalex_people` | 13,075 institutions + 12,998 authors | 32,800 | **1.258** | - |
| `iso_mic_venues` / `nasdaq_listings` / `fdic_bank_financials` / `sec_issuer_reference` | the published list | 2,883 / 13,199 / 27,833 / 8,022 | 1.00 | - |
| `alpaca_daily_bars` | symbols in the Nasdaq Trader directories | 11,594 | 1.00 | **0.878** of 13,199 |
| `acs_5yr_tables` | US counties | 3,222 | - | **0.997** of 3,222 |
| `bls_labor` | US counties with a BLS series | 3,293 | - | **0.999** (73 outside: state and national rollups) |
| `census_business` | US counties in CBP | 3,202 | - | **0.975** |
| `usda_agriculture` | US counties with a NASS estimate | 3,079 | - | **0.955** |
| `noaa_storm_events` | US counties named in Storm Events | 3,459 | - | **0.967** (342 outside: NOAA zone and legacy Alaska codes) |
| `acs_5yr_tables` (tracts) | US census tracts | 85,382 | - | **0.9998** of 85,396 |
| `lehd_lodes` (tracts) | US census tracts | 83,955 | - | **0.973** (877 outside the 2024 TIGER vintage) |
| `worldbank_wdi` / `imf_sdmx` / `faostat` / `vdem` / `oecd_sdmx` | countries | 217 / 229 / 217 / 183 / 51 | - | **0.863 / 0.892 / 0.871 / 0.703 / 0.205** of 249 ISO 3166-1 codes |

Two findings fall straight out of it. `sec_company_assets` publishes **17,045 of the 20,362
companyfacts CIKs its declaration claims** (0.837), which is an acquisition gap, not a coverage
statement. `openalex_people` publishes **more** than its declared scope counts (1.258) because
each institution's published lineage ancestors are emitted as entities too. Both are in the
report's `summary.measured_below_declared` / the per-row fractions rather than in prose only.

Nine of `census_geography`'s counties sit outside the 2020 reference list: they are Connecticut's
planning regions (`09110`…`09190`), which replaced its eight counties in 2022. That is the
vintage problem the reference table exists for, surfaced as data rather than as a caveat.

## What the rebuild changed in the published results

The new clusters were attached to `data/world_evidence/index.sqlite` with
`graph-attach-resolution` (109,801 resolved entity IDs, view digest `764630dd…`). The resolution
they replaced is **recoverable**: `unify-resolve` and `worldmodel.resolution.history` export what is
attached before replacing it, and the previous one is kept at
`data/world_evidence/resolution_history/2026-09-18-pre-bridges/` (28,008 clusters, view digest
`f8a31696…`), re-attachable with
`python3 -m worldmodel graph-attach-resolution --workdir <that directory>`. The resolution that is
attached now, its full report and the three measurement JSONs quoted on this page are kept beside
it in `data/world_evidence/resolution_history/2026-09-18-bridges/` (`clusters.jsonl`, `view.json`,
`report.json`, `identity-coverage-before.json`, `identity-coverage-after.json`,
`coverage-estimate.json`). Neither directory is in Git: they sit in the data root with the index.

Both example suites were re-run against the attached index and their saved outputs updated:

- `run_all.py --save`: **q1** now carries the UK listing in the LUKOIL cluster
  (`uk:sanctions:RUS3094`), and its counts move from 1,880 to **1,894** sanctions↔LEI clusters, 246
  to **341** clusters carrying an actual designation, 2 to **3** designation-list LEIs reaching a
  13F-named security; 175 → **170** sanctions-linked LEIs reach such a security, the five lost being
  the refused INN/OGRN merges. **q5** is unchanged (89 vessel clusters, all present in AIS, 2
  re-flagged hulls) - the fraudulent MMSI and IMO values it now refuses were never joining a real
  AIS vessel. q2, q3, q4 and q6 are unchanged.
- `resolution_evaluation.py --save`: **unchanged** - recall 0.0044, precision 0.0226, 103,211
  entities the name matcher would merge. It reads published records rather than the resolved table,
  so the rebuilt clusters cannot move it; only its timings differ (1,361.0 s against 1,364.7 s).
  Asserted identity going from 3.17% to 3.66% does not make name matching any more attractive.

### The 2026-09-19 rebuild, and the one answer it changed

The six graph queries were re-run against the rebuilt index. Five are unchanged in substance: the
Wikidata records join into existing clusters (a `wikidata:` member appears beside the OpenSanctions
and US CSL IDs in q1, q4 and q5) without changing what those queries can reach. q6 picked a
different anchor bank — Truist rather than US Bancorp — because the cluster it ranks first now
carries an FDIC certificate alongside the LEI and CIK.

**q1 is the one that changed, and it needs reading carefully.** The sanctions → LEI → SEC-filer leg
had been **zero** in every previous run, and this page and `unified-graph.md` both recorded it as
genuinely absent. It is now **99**. Before that is read as "sanctioned issuers are publicly listed",
here is the breakdown the query now prints, which is why it prints it:

| | count |
| --- | ---: |
| clusters joining a sanctions listing to an LEI | 1,897 |
| clusters joining an LEI to an SEC CIK | 2,315 (was 617) |
| clusters joining all three | **99** (was 0) |
| of those 99, whose listing is an actual **designation** | **1** |
| of those 99, holding together only through a `wikidata:` member | **99** |

So every one of the 99 exists because a Wikidata editor asserted that an item carries both an LEI
and a CIK, and 98 of them anchor on an ownership or politically-exposed-person record rather than a
designation. GLEIF itself still publishes no SEC EDGAR registration-authority entity ID for any of
the 1,928 sanctions-published LEIs — the finding that made this absent in the first place is
unchanged. The query's prose used to assert "the filer leg does not fire" as a fact; it now reports
what it measured and carries both breakdowns, because a count of 99 with no breakdown is exactly
the kind of number that turns into a claim nobody checked.

## Limits

- A joined entity is not a *correct* entity: clusters are exactly as reliable as the published
  identifiers behind them, and the refusals above are the only defence.
- Two datasets from one publisher are two datasets, not two sources; the `joined_independent`
  column is the honest one, and `top_dataset_combinations` in the JSON shows what each join is.
- The Consolidated Screening List republishes OFAC entries. `us_csl`↔`ofac:party` is two
  publishers by the catalog's own metadata, but one designation; 13,937 of the 49,705 clusters are
  exactly that pair, so the independent figure should be read with that in mind.
- Mentions are evidence *about* an entity (a GLEIF relationship, a 13F holding), not a description
  of it. They are reported separately for that reason.
- The measurement counts entity *records*. A dataset that references an entity only in an
  observation subject shows up under mentions, never under `joined`.
