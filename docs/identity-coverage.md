# Identity coverage: how much of the graph is joined, and why not more

The unified index holds 9,925,686 entity records under **8,590,782 distinct entity IDs**. A
cross-domain query, the influence panel or a graph-embedding model can use an entity as a bridge
only if evidence about it comes from **more than one dataset**. This page measures that share
before and after mining every asserted crosswalk the published records carry, says where the joins
come from, and says what blocks the rest.

The north-star target was **at least 25% of entities joined across at least two sources**.
Asserted identity does not reach it: the measured answer is **3.17% before and 3.66% after**, and
the sections below show exactly which populations the remaining 96% are and why no published
identifier joins them.

> Everything here is **asserted identity**: published `same_as` rows, identifiers a publisher
> printed, and published crosswalk fields. No name is matched. Name matching was measured
> separately and does not work on this catalog - recall 0.0044, precision 0.023 against held-out
> published LEIs ([unified-graph.md](unified-graph.md)).

```sh
# the measurement (read-only; 3 min 22 s on the 131 GB index, 566 MiB peak RSS)
python3 -m worldmodel identity-coverage --workdir /tmp/jc --mentions
# the same for a candidate resolution, before it is attached
python3 -m worldmodel identity-coverage --workdir /tmp/jc --clusters /tmp/resolve/clusters.jsonl
# the population each dataset claims to cover, and how much of it is held
python3 -m worldmodel coverage-estimate --datasets fema_nri,sec_gleif
```

## Before and after

`unify-resolve` over the index's 96 pinned datasets, 52 min 57 s, 661 MiB peak RSS. "Joined" means
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

## What blocks the remaining 96%

The unjoined 8,276,604 entity IDs are not a backlog of unmined crosswalks. Measured:

| Blocker | Entities | Evidence |
| --- | ---: | --- |
| **One publisher holds the whole population** | ~3.4M LEIs, 1.24M OpenSanctions records, 1.03M EIA series, 305k MRDS occurrences, 244k lobbying filings, 157k Federal Register documents, 151k IRS flows, 109k bills, 104k FDIC certificates and branches | No second dataset in the catalog publishes an entity record for them at all. GLEIF's only catalog partners are `iso_mic_venues` (1,071 LEIs), the sanctions lists (1,928 LEIs) and `sec_issuer_reference` (25 LEIs) |
| **Records as entities** | 2.3M (series, documents, filings, flows, deposits, runways, rail nodes) | Nothing joins a series to anything; see the entity-type table |
| **The join exists but its dataset is outside the index** | 112,177 GLEIF Companies House numbers (95,562 of which are a company in the published UK register), 27,513 SAM UEIs | `companies_house_uk`, `usaspending` and `sec_13f_history` are bulk datasets the default profile excludes. Indexing them *lowers* the percentage, because each adds millions of its own single-source entities |
| **No published crosswalk exists** | 104k FDIC entities, 152k 13F CIK/CUSIP entities, 70k AIS vessels, 30k HTS codes | Measured in [unified-graph.md](unified-graph.md): no GLEIF registration authority is the Federal Reserve (best numeric overlap 646 of 31,564, coincidence), and no FDIC certificate maps to a CIK or LEI anywhere in the catalog |
| **The publisher names no register** | 29,128 OpenSanctions `registrationNumber` and 121,134 `taxNumber` values with no country | Typing a bare number by guessing its register is inference, not a published identifier. The UK list's *labelled* free text is read precisely because the label names the register |

What would actually move the number: a **person and company register with global coverage**
(Wikidata's QIDs are the only cross-domain identifier in the catalog, and only 2,008 of 486,342 of
them meet a second publisher), or **indexing the bulk registers and accepting their own unjoined
mass**. Neither is an extraction gap that better matching would close.

## Coverage of a population, which is a different question

"3.66% joined" is a property of this index, not of the world. `wm coverage-estimate` answers the
other half: what population does each dataset claim, and how much of it is held.

`wm coverage-estimate` states a curated population for **47 estimates over 44 datasets** (the
other 81 declarations are listed as uncurated, with their declared scope, rather than guessed at)
and measures two fractions:

- **`measured_over_declared`** - the entities the published output holds over the count the
  dataset's own `dataset.json` states. An acquisition check.
- **`covered_of_population`** - members of an independently sourced population list that the
  dataset holds. The lists are the dated Census county table and the ISO 3166-1 table shipped in
  `worldmodel/reference` (both with validity dates), TIGER tracts from `census_geography`, and the
  Nasdaq Trader directory from `nasdaq_listings`. **29 of the 47 estimates get one.**

Measured over the whole catalog (14 min 7 s, 832 MiB peak RSS, one pass per measured dataset):

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
`python3 -m worldmodel graph-attach-resolution --workdir <that directory>`.

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
