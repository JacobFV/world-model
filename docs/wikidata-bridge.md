# The Wikidata bridge: turning QIDs into a crosswalk

[identity-coverage.md](identity-coverage.md) measures why 96% of this catalog's 8.59M entities meet
no second publisher, and ends by naming the one identifier that could change it:

> Wikidata's QIDs are the only cross-domain identifier in the catalog, and only 2,008 of 486,342
> of them meet a second publisher.

980,841 `wikidata` identifier claims are already in the index, and 486,342 OpenSanctions entity IDs
*are* QIDs. They join almost nothing, because nothing in the catalog maps a QID to anything else.
This page is that map: what was acquired, how, which bridges it supports, what is refused, and what
the measurement says it is worth.

**The short version.** 515,956 Wikidata statements over 31 external-identifier properties, acquired
from the query service in 115 paged requests for 43,169,561 raw bytes, published as 425,289 entity
records and 515,930 statements. 396,695 of those statements become identity claims; 117,610 are
published as evidence and deliberately never become identity; 2,575 more are refused by the
cardinality their mapping specification declares. Over the resolution's scope, **clusters go from
49,705 to 148,123**, entities joined across two datasets from **3.66% to 5.84%**, and across two
**publishers from 2.44% to 4.71%** - the first time either number has moved by more than half a
point. Measured on the existing index alone, without adding Wikidata's own 425,289 entities to it,
the same clusters join **5,386 more existing entities to a second publisher** purely as a hub.

## The route, and why

**The Wikidata Query Service** (`https://query.wikidata.org/sparql`), one request per
(property, page), acquired with the `url_list` strategy and read as CSV:

```sparql
SELECT ?item ?label ?value (GROUP_CONCAT(DISTINCT ?t;SEPARATOR="|") AS ?types) WHERE {
  { SELECT ?item ?value WHERE { ?item wdt:P1278 ?value . }
    ORDER BY ?item ?value LIMIT 10000 OFFSET 0 }
  OPTIONAL { ?item wdt:P31 ?c . BIND(STRAFTER(STR(?c),"/entity/") AS ?t) }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". ?item rdfs:label ?label. }
} GROUP BY ?item ?label ?value
```

Three properties of that query are what make it an acquisition rather than a script:

* `wdt:` is the **truthy** predicate. A statement Wikidata's editors marked *deprecated* - the
  wrong LEI, the identifier that turned out to belong to someone else - is never returned, and a
  property with a preferred-rank statement contributes only that one. The rank filter is the
  publisher's own judgement, applied at the source.
* The inner subquery is a **total order** over `(item, value)`, so `LIMIT`/`OFFSET` over it is a
  complete, disjoint partition of the property. Each page is independently re-runnable, and
  re-fetching one page cannot silently shift the others - which an unordered `OFFSET` would.
* Every property is followed by at least one deliberately **empty page**, which is what makes
  "the property ended here" distinguishable from "this response was cut short".

115 requests, 2 h 50 min at one request per five seconds with `Retry-After` honoured, 43,169,561
bytes. The declaration asked for 250,000,000; the fair-share pool was never a constraint.

### Why not a dump, and why the paging is not optional

The full truthy dump (`latest-truthy.nt.gz`) is tens of gigabytes against a ~10 GB fair share, and
almost none of it is an identifier this catalog can join.

The paging is not a politeness measure. **The query service answers a query that exceeds its
server-side time limit with HTTP 200 and a truncated body**, with no error and no marker. Measured
on 2026-09-19, against the 53,403 `P1278` (LEI) statements:

| query | wall time | rows returned |
| --- | ---: | ---: |
| `?item ?value`, no labels, unpaged | 4.6 s | 53,403 (complete) |
| `?item ?itemLabel ?value` via the label service, unpaged | 70.1 s | **46,702** (HTTP 200) |
| `?item ?label ?value` via `rdfs:label`, unpaged | 63.1 s | **15,942** (HTTP 200) |
| the paged query above, one page | 30-117 s | 10,000 (exactly the LIMIT) |

A silently short answer is the worst possible failure for an identity layer: it looks like
"Wikidata does not have that identifier". So the pipeline holds every page to its declared row
count, requires that no page after a short one is non-empty, and holds each property's acquired
total to at least 98% of the count measured against the service at declaration time. A truncated
acquisition fails the build instead of publishing quietly.

It worked: **all 31 properties came back at exactly 1.000 of their declared count.** The only
property whose declared count changed was `P1320`, whose `gb`-jurisdiction subset had never been
counted (the counting query itself 502'd during declaration); it is now the measured 7,609.

The Freiburg **QLever** mirror of Wikidata was measured as an alternative and is much faster - the
whole LEI query with labels and classes in 10.1 s - but it answered repeated queries with HTTP 429,
and it is a third-party mirror with its own dump date rather than the publisher's own service. The
official service, paged, is the route.

### What the route cannot do

* It is a **snapshot of a wiki**, taken on the acquisition date (2026-09-19). Wikidata has no
  publication calendar and no vintage; the retrieval date in every record's evidence is the only
  date there is.
* The label is the English (or `mul`) label at that moment. Labels are names, never merge evidence.
* Coverage is whatever Wikidata's editors have entered. It is not a register: an LEI absent from
  Wikidata says nothing about the entity. 53,403 of GLEIF's 3.43M LEIs are here.

## What was acquired

31 properties, chosen because some dataset in this catalog publishes the same identifier.
"Published" is the statement count in the output; "claims" is how many became identity claims after
the shape and check-digit rules.

| Wikidata property | namespace | published | claims |
| --- | --- | ---: | ---: |
| P6782 ROR ID | `ror` | 135,973 | 135,971 |
| P458 IMO ship number | `imo` | 96,517 | 96,082 |
| P1278 Legal Entity Identifier | `lei` | 53,403 | 53,348 |
| P1937 UN/LOCODE | - | 45,716 | **0 (refused)** |
| P587 MMSI | - | 38,196 | **0 (refused)** |
| P239 ICAO airport code | `icao` | 20,602 | 19,653 |
| P1297 IRS Employer Identification Number | - | 17,259 | **0 (refused)** |
| P2390 Ballotpedia ID | - | 16,439 | **0 (refused)** |
| P1157 US Congress Bio ID | `bioguide` | 13,059 | 13,059 |
| P10682 EIA plant ID | `eia_plant` | 11,929 | 11,929 |
| P946 ISIN | `isin` | 10,272 | 10,214 |
| P238 IATA airport code | `iata` | 9,351 | 9,346 |
| P2622 Companies House company ID | `gb_company_number` | 8,175 | 8,131 |
| P1320 OpenCorporates organization ID (`gb/` only) | `gb_company_number` | 7,609 | 7,575 |
| P3347 PermID | `permid` | 6,110 | 6,107 |
| P300 ISO 3166-2 code | `iso3166_2` | 5,459 | 5,447 |
| P3344 Vote Smart candidate ID | `votesmart` | 5,357 | 5,357 |
| P882 FIPS 6-4 ID | `fips_county` | 3,793 | 3,793 |
| P5531 Central Index Key | `sec_cik` | 3,693 | 3,691 |
| P2627 ISO 9362 SWIFT/BIC code | `swift` | 2,565 | 2,559 |
| P7534 MIC market code | `mic` | 1,932 | 1,928 |
| P2686 OpenSecrets people ID | `opensecrets` | 729 | 728 |
| P2771 D-U-N-S number | `duns` | 628 | 614 |
| P298 / P297 / P299 ISO 3166-1 alpha-3 / alpha-2 / numeric | `iso3166_1_*` | 274 / 262 / 270 | 274 / 262 / 270 |
| P11175 FDIC Certificate ID | `fdic_cert` | 117 | 117 |
| P7057 FEC Campaign Committee ID | `fec_committee` | 99 | 98 |
| P5087 FIPS 5-2 numeric code | `fips_state` | 69 | 69 |
| P12644 GovTrack person ID | `govtrack` | 64 | 64 |
| P10712 EIA utility ID | `eia_utility` | 9 | 9 |
| **total** | | **515,930** | **396,695** |

425,289 entity records, one per item, typed from the item's own `P31` classes: 96,790 vessels,
85,044 businesses, 70,348 other organisations, 49,267 jurisdictions, 33,855 institutions, 21,473
people, 20,803 facilities, 11,957 airports, 7,481 government agencies, 6,534 locations, 3,148
counties, 910 ports, 232 states, 229 countries, and 17,218 typed `entity` because no class
Wikidata gives them maps to anything in this ontology. 25 items were refused outright: Wikidata
types them as a Wikimedia page - a duplicate item, a category, a list article - which is not a
thing in the world.

The class map ([entity_types.py](../data/wikidata_identifiers/entity_types.py)) is **generated, not
written**: every class the extract uses on 20 or more items was asked, through the query service,
which of a small set of anchor classes it is a subclass of (`?class wdt:P279* ?anchor`). Wikidata's
own subclass statements decide the type, which is why 95,679 of the 96,525 IMO ship-number
statements land on an item typed `vessel` - the typing the identity layer uses to keep IMO's ship
and company number series apart.

### Deliberately out of scope

| Property | statements | why not |
| --- | ---: | --- |
| P496 ORCID iD | 2,065,439 | 4.0x the whole rest of the extract. At the measured ~45 s per 10,000-row page that is ~2.6 h of service time for one namespace. The largest known gap; see [follow-ups](#follow-ups) |
| P1566 GeoNames ID | 4,089,782 | no catalog dataset publishes a GeoNames ID |
| P590 GNIS Feature ID | 883,567 | same |
| P402 OpenStreetMap relation ID | 601,057 | same |
| P213 ISNI / P214 VIAF / P227 GND | 2,571,307 / 4,825,193 / 3,058,528 | library authority files; no catalog partner |
| P1616 SIREN / P4156 Czech IČO / P3608 EU VAT | 463,448 / 77,299 / 18,219 | no catalog partner |
| P2427 GRID ID | 103,415 | redundant: `openalex_people`, the only catalog publisher of GRID IDs, already publishes the Wikidata QID of the same institution |
| P10283 OpenAlex ID | 210,912 | redundant for the same reason - and the measurement bears it out: 11,881 `openalex`+`wikidata` clusters formed without it |
| P1320 outside the `gb` jurisdiction | ~570,000 | OpenCorporates' jurisdiction prefix names a register this catalog holds only for `gb` |

Identifiers this catalog holds that **Wikidata has no property for at all**, so no amount of
acquisition would bridge them: ICPSR legislator IDs (24,978 claims held), FEC *candidate* IDs
(54,575), CUSIP (2,422,613; proprietary), the SAM Unique Entity ID (27,513), the Federal Reserve
RSSD (27,566), and FIGI.

## The bridges

Wikidata's statements are **not** published as `identifier_assignment` rows. Wikidata is a wiki;
its identifier statements are community assertions, not a register's own assignment. They are
published as `external_identifier` assertions carrying the property that made them, and the
resolution layer reads them as *bridges*
([worldmodel/resolution/wikidata.py](../worldmodel/resolution/wikidata.py)). That is not a
formality: it means each family is held to the cardinality its mapping specification declares, a
value that breaks it is deleted and counted rather than merged, and `unify-resolve --no-bridges`
turns the entire layer off in one flag.

| Bridge | Cardinality | Claims | Rows that met another publisher | Refused |
| --- | --- | ---: | ---: | ---: |
| `wikidata_identifier` | 1:1 | 381,896 | 121,090 | 2,421 |
| `wikidata_identifier_series` | 1:n | 14,799 | 3,686 | 154 |
| `geo_entity_id` | 1:1 | 184,213 | 181,669 (0 before) | 0 |

`wikidata_identifier` covers the families whose issuer assigns one value per thing: `lei`,
`sec_cik`, `bioguide`, `govtrack`, `votesmart`, `opensecrets`, `imo`, `ror`, `iata`, `icao`,
`permid`, `gb_company_number`, `fdic_cert`, `eia_plant`, `eia_utility`, `duns`, `iso3166_1_alpha2`,
`iso3166_1_alpha3`, `iso3166_1_numeric`, `iso3166_2`, `fips_county`, `fips_state`.
`wikidata_identifier_series` covers the four where one thing legitimately holds several values - a
company has an ISIN per share class, an exchange a MIC per segment, a bank several BICs, a
candidate several authorized committees - so only the value side is held to 1.

Eleven namespaces had to be added to `UNIQUE_NAMESPACES` for the claims to cluster at all:
`govtrack`, `votesmart`, `opensecrets`, `eia_utility`, `iso3166_1_alpha2`, `iso3166_1_alpha3`,
`iso3166_1_numeric`, `iso3166_2`, `fips_county`, `fips_state`. On their own they changed nothing:
the before run below, with all of this code and none of this data, reproduces the published
2026-09-18 baseline exactly.

### `geo_entity_id`, and why it is here

`unify.ENTITY_ID_NAMESPACES` already reads a namespaced entity ID as a published identifier claim -
a source that calls a thing `iata:AAE` has published that IATA code - and the catalog's geographic
IDs are the same kind of statement: `census_geography` and every ACS product key a county on its
five-digit FIPS code (`geo:US:county:13209`), a state on its two-digit FIPS code and a subdivision
on its ISO 3166-2 code; `airport_nodes` and `bis_bulk` key a country on its ISO 3166-1 alpha-3 code
(`iso3:USA`). Only those five exact shapes are read; the rest of the `geo:` tree - tracts, ZCTAs,
CBSAs, places - is left alone.

On its own the bridge joins **nothing**, and that is measured rather than argued: in the before run
its 184,213 claims produced **0** rows that met another publisher, because those IDs are already
shared between the Census products that use them. It exists so the Wikidata FIPS and ISO 3166
statements have a second publisher to meet, and with them present 181,669 of its rows do.

### Check digits and formats, recomputed

A wiki has typos. Every value is held to the shape or check digit its own standard defines before
it can become identity; **1,625 statements failed**, and the largest groups are exactly where a
standard defines a check digit or a fixed length:

| Family | Rule | refused |
| --- | --- | ---: |
| ICAO airport code | `[A-Z]{4}` | 949 |
| IMO ship number | IMO A.600(15): `7d1+6d2+5d3+4d4+3d5+2d6 mod 10` equals the last digit | 435 |
| ISIN | ISO 6166 modulus-10 double-add-double check digit, recomputed | 58 |
| LEI | ISO 17442: 18 characters plus MOD 97-10 (ISO 7064) check digits, recomputed | 55 |
| Companies House | eight characters: eight digits, or a two-letter register prefix and six | 44 |
| OpenCorporates `gb/` | the same, applied to the number inside the ID | 34 |
| D-U-N-S | nine digits | 14 |
| ISO 3166-2 | `XX-YYY` (Wikidata carries bare `VI`, `WBK`) | 12 |
| BIC / IATA / MIC / PermID / CIK / ROR / OpenSecrets / FEC committee | ISO 9362; `[A-Z]{3}`; `[A-Z][A-Z0-9]{3}`; digits; digits; the nine-character `0`-prefixed form; `N` and eight digits; `C` and eight digits | 6 / 5 / 4 / 3 / 2 / 2 / 1 / 1 |

The ROR checksum is not recomputed - only its format is checked - because getting that algorithm
subtly wrong would silently drop valid IDs, which is worse than accepting a malformed one.

### The refusals

A bridge that fires where it should not is worse than a missing bridge.

**Four properties are acquired, published as evidence, and never turned into identity**, for the
reason `dff5b81` already refuses OGRN, UN/LOCODE and MMSI values one dataset prints twice:

| Property | statements | Why it is refused |
| --- | ---: | --- |
| P587 MMSI | 38,196 | An MMSI is a radio identity assigned to a station, not to a hull, and is reassigned when a ship is re-flagged or scrapped. A Wikidata item for a 1990s ship and the AIS record for the hull that now holds the number are not the same vessel |
| P1937 UN/LOCODE | 45,716 | A UN/LOCODE names a locality, not a facility; 28 of them in this catalog are already printed for two different World Port Index ports |
| P1297 IRS EIN | 17,259 | A taxpayer ID a parent and its subsidiaries share - EIN 850019030 is published here for both Public Service Co of New Mexico and its holding company. `unify.NON_UNIQUE_IN_PRACTICE` keeps `ein` out of clustering already |
| P2390 Ballotpedia ID | 16,439 | A wiki page title, renamed whenever the page moves and carrying whatever disambiguator that wiki needed; four of the first four values sampled were election pages, not people |

**2,575 more claims are refused by cardinality**, and the published statements show exactly what
those are. 1,711 values are carried by two or more items - 600 IMO numbers, 263 IATA codes, 241
ICAO codes, 198 LEIs, 97 ISO 3166-2 codes, 73 BICs, 66 ISINs, 58 UK company numbers, 41 CIKs - and
906 items carry two or more values of a 1:1 family - 301 LEIs, 224 ROR IDs, 177 UK company numbers,
90 CIKs, 72 PermIDs. They are editing conflicts on a wiki, and merging on them would be a wrong
merge:

```
fdic_cert     32541      wikidata:Q133806411 'Flagstar Bank' | wikidata:Q5457038 'Flagstar Bank'
fec_committee C00580100  wikidata:Q113184455 'Make America Great Again PAC'
                       | wikidata:Q20121517 'Donald Trump 2016 presidential campaign'
fips_county   14460      wikidata:Q1190137 'Greater Boston'
                       | wikidata:Q123564369 'Boston-Cambridge-Newton metropolitan statistical area'
eia_plant     1313       wikidata:Q116696781 'Osage City' | wikidata:Q4688016 'Kerr Dam'
lei           wikidata:Q1022711 'CACI' -> 969500BB5ZH7LFB2BL43 and SYRPI2D1O9WRTS2WX210
```

The `fips_county` row is the interesting one: `14460` is a *metropolitan statistical area* code, not
a FIPS 6-4 county code, on two items that are the same metro area under different names. The 1:1
rule catches it without anybody having to know that.

Finally, the two refusals the identity layer already had keep working on this data. An `imo` claim
on a subject the publisher does not type as a vessel is retyped `imo_company`, so the 243 offshore
platforms and the ship managers in the extract never meet a hull; and a value a publisher flags as
fraudulent is never read.

## The measurement

Two measurements, because they answer two different questions. Both were run with
`unify-resolve --no-attach`; **nothing was attached to the shared index** (see
[below](#nothing-was-attached)). The before run is the same code over the same scope with
`--exclude wikidata_identifiers`; it reproduces the published 2026-09-18 baseline exactly
(49,705 clusters, 314,178 joined, 209,335 independent), which is the check that none of the new
code moves anything by itself.

### Over the resolution's scope, with the dataset indexed

`unify-resolve --exclude census_aspep,epa_aqs_daily,fred_deposit_rates,fred_state_employment_vintages,market_corporate_actions,opm_fedscope`,
103 datasets before and 104 after, 1.08 billion rows, 46 min per run, 1.3 GiB peak RSS.

| | before | after | change |
| --- | ---: | ---: | ---: |
| entity IDs in scope | 8,591,784 | 9,017,073 | +425,289 (the new dataset's own) |
| clusters | 49,705 | **148,123** | +98,418 |
| entity IDs in a cluster | 100,488 | **318,851** | +218,363 |
| **joined across ≥2 datasets** | 314,183 (**3.66%**) | **526,674 (5.84%)** | +212,491 |
| **joined across ≥2 publishers** | 209,340 (**2.44%**) | **424,402 (4.71%)** | +215,062 |
| joined by a shared entity ID alone | 233,573 (2.72%) | 233,573 (2.59%) | 0 |

Of the +212,491, **116,677 are the Wikidata entities themselves** (they are in a cluster with
another dataset, so they count) and **95,814 are entities the catalog already had** and that now
have a second publisher behind them. Both halves are real; the first is a bigger graph, the second
is a better-joined one.

By domain, the two that move are the two the extract is about:

| domain | joined before | joined after | independent before | independent after |
| --- | ---: | ---: | ---: | ---: |
| companies | 90,686 (2.3%) | **158,598 (4.1%)** | 44,727 | **120,757** |
| transport | 7,235 (1.1%) | **26,843 (4.3%)** | 7,235 | **26,843** |
| energy_trade | 211,825 (8.2%) | 219,804 (8.5%) | 85,514 | 93,761 |
| politics | 27,458 (3.8%) | 27,738 (3.8%) | 27,458 | 27,738 |
| demographics | 344,793 (32.4%) | 344,828 (32.4%) | 291,590 | 291,639 |
| macro | 95,024 (71.9%) | 95,024 (71.9%) | 95,003 | 95,003 |

By published entity type, the rows that changed (the `before` column is blank where that type sat
below the report's top-15 cutoff):

| entity type | entities after | joined before | joined after |
| --- | ---: | ---: | ---: |
| organization | 3,224,199 | 31,433 | **88,446** |
| person | 1,065,189 | 84,278 | **111,561** |
| business | 394,469 | 33,829 | **97,473** |
| jurisdiction | 180,832 | 102,609 | **109,918** |
| vessel | 174,044 | - | **27,693** |
| facility | 154,925 | 0 | **1,398** |
| investment_fund | 249,136 | 585 | **1,863** |
| office | 208,621 | 7 | **111** |

Vessels are the clearest single result. `marine_ais` keys a hull on its MMSI, and nothing else in
this catalog knows an MMSI: before, **404** of 49,705 clusters contained an `mmsi:` member; after,
**9,459** do. The IMO ship numbers are what reach them - Wikidata's MMSIs, which would have reached
far more, are refused, because that is the number a re-flagged hull loses to somebody else.

### On the existing index alone, as a pure hub

The same candidate clusters, measured against the 131 GB index *without* adding the new dataset to
it (`wm identity-coverage --clusters … --mentions`, 8,590,782 entity IDs both times, so the
denominator is identical and the Wikidata entities contribute no dataset of their own):

| | before | after | change |
| --- | ---: | ---: | ---: |
| clusters | 49,705 | 148,123 | +98,418 |
| entity IDs in a cluster | 100,488 | 202,174 | +101,686 |
| joined across ≥2 datasets | 314,178 (3.657%) | 317,749 (**3.699%**) | +3,571 |
| joined across ≥2 publishers | 209,335 (2.437%) | 214,721 (**2.499%**) | **+5,386** |
| joined counting mentions | 2,174,915 (25.32%) | 2,177,206 (25.34%) | +2,291 |
| mentions, ≥2 publishers | 222,894 (2.595%) | 228,274 (**2.657%**) | +5,380 |

This is the honest floor: **a QID that carries one identifier joins nothing until the dataset that
carries it is indexed.** 101,686 existing entity IDs are now in a cluster, but for most of them the
only other member is a Wikidata item, which the index does not hold an entity record for. The
+5,386 are the entities where the QID is a genuine hub between two identifiers the index already
had - an LEI and a CIK on one item, a Bioguide ID and an ICPSR number, an IATA code and an
OurAirports row.

Per dataset, on the index measure (so these are entities that gained a second *indexed* publisher,
not a Wikidata one):

| dataset | entities | independent before | independent after |
| --- | ---: | ---: | ---: |
| `sec_gleif` | 3,431,116 | 3,614 | **5,776** |
| `sec_issuer_reference` | 17,881 | 6,953 | **8,188** |
| `openalex_people` | 32,800 | 303 | **1,353** |
| `crossref_research` | 89,393 | 294 | **369** |
| `iso_mic_venues` | 3,954 | 1,076 | **1,154** |
| `fdic_bank_financials` | 103,930 | **0** | **57** |
| `airport_nodes` | 149,561 | 3,693 | **3,743** |
| `opensanctions_graph` | 1,316,124 | 10,023 | **10,032** |

`fdic_bank_financials` is the one that was a stated blocker: identity-coverage.md recorded "no
published crosswalk exists ... 104k FDIC entities" and "no FDIC certificate maps to a CIK or LEI
anywhere in the catalog". Wikidata publishes 117 FDIC certificate numbers, and 57 of them are the
first crosswalk those entities have ever had. It is a rounding error against 103,930, and it is
also the only thing in the catalog that has ever joined them.

### What the clusters look like

116,552 of the 148,123 clusters contain a QID. The shapes, before → after:

| cluster shape | before | after |
| --- | ---: | ---: |
| `lei` + `wikidata` | - | **50,483** |
| `openalex` + `wikidata` | - | 11,881 |
| `bioguide` + `icpsr` (+ `wikidata`) | 10,696 | 10,695 **with** `wikidata` |
| `mmsi` + `wikidata` | - | 9,250 |
| `opensanctions` + `wikidata` | - | 8,096 |
| `ourairports` + `wikidata` | - | 6,810 |
| `iata` + `ourairports` (+ `wikidata`) | 3,395 | 3,299 **with** `wikidata` |
| `iso3166-2` + `wikidata` / `geo` + `wikidata` | - | 3,263 / 3,124 |
| `lei` + `sec` + `wikidata` | - | 1,083 |
| `ofac` + `opensanctions` + `us_csl` + `wikidata` | - | 851 |

The namespaces a Wikidata cluster reaches, counted over clusters: `lei` 53,046, `openalex` 12,988,
`bioguide` 12,781, `icpsr` 12,591, `opensanctions` 11,858, `ourairports` 10,120, `mmsi` 9,334,
`iso3166-2` 3,348, `geo` 3,329, `iata` 3,319, `sec` 2,530, `mic` 1,879, `fec` 1,626, `ofac` 1,199,
`us_csl` 1,199, `uk` 647, `ror` 387, `iso3` 253, `fdic` 110, `govtrack` 2, `un` 1.

And the headline claim of [identity-coverage.md](identity-coverage.md) is now answered on its own
terms: of the 486,342 OpenSanctions QIDs, the ones meeting a second publisher go from **2,008 to
9,288**. That is a 4.6x improvement and still only 1.9% of them, for a reason the numbers make
plain - the properties worth acquiring are company, vessel and infrastructure identifiers, and
OpenSanctions' QIDs are mostly politically exposed persons, whom Wikidata identifies with
properties (VIAF, ISNI, national parliament IDs) that this catalog does not hold.

### Nothing was attached

`data/world_evidence/index.sqlite` and its `resolved` table are **unchanged**. Both runs used
`unify-resolve --no-attach`, and `identity-coverage` opens the index read-only. The candidate
clusters, the two reports and the two coverage JSONs are in the session scratchpad, not in the data
root, so nothing under `data/world_evidence/resolution_history/` was added or replaced either.

Attaching is the lead agent's call, and it is a two-command job with the previous resolution kept
recoverable exactly as the identity track did it:

```sh
# rebuild the index so it holds the new dataset's entity records, then attach
wm unify --exclude census_aspep,epa_aqs_daily,fred_deposit_rates,fred_state_employment_vintages,market_corporate_actions,opm_fedscope
wm unify-resolve --workdir data/world_evidence/resolution_history/<date>-wikidata \
  --exclude census_aspep,epa_aqs_daily,fred_deposit_rates,fred_state_employment_vintages,market_corporate_actions,opm_fedscope
```

`unify-resolve` exports what is attached before replacing it (`report['previous_resolution']`), so
the 2026-09-18 resolution stays re-attachable with `wm graph-attach-resolution --workdir <that
directory>`. **The index must be rebuilt first**: the scope measurement above is what the numbers
become once it holds the 425,289 Wikidata entity records, and the index measurement is what they
become if it does not.

## What this does not establish

* **That Wikidata is right.** A bridge row says a Wikidata editor published this identifier against
  this item. Where two publishers disagree - one value on two items, one item with two values of a
  1:1 family - the row is refused, not adjudicated. Nothing here checks a statement against the
  register that issued the identifier.
* **That an unjoined entity has no identifier.** Wikidata's coverage is whatever its editors
  entered. 53,403 of GLEIF's 3.43M LEIs are in Wikidata; the other 3.38M are not evidence of
  anything, and the `companies` domain is still only 4.1% joined.
* **That the remaining 94% is now a smaller problem.** It is not. The blockers
  [identity-coverage.md](identity-coverage.md) measured are unchanged: 2.3M of this index's
  entities are *records as entities* - an EIA series, a Federal Register document, an IRS flow -
  and no second publisher describes them. `economic_series` is still 8 joined out of 977,610;
  `publication`, `resource_deposit`, `infrastructure` and `flow` are still 0.
* **That a join is a fact about the world.** The measurement counts entities whose evidence comes
  from two datasets. `join_coverage`'s own caveats apply unchanged: a republished list is not an
  independent source, and a joined entity is not a completely described one.
* **That the identifier is current.** Wikidata does not date an external-identifier statement, so
  none is dated here. A FIPS county code is reassigned - Connecticut replaced its eight counties
  with planning regions in 2022 - and this layer joins codes; the catalog's own vintage reference
  table is what says which year a code belongs to.
* **That the bridge raises coverage of any population.** This is a share of the entities this scope
  holds, not of the world; `wm coverage-estimate` answers the other question.

## Follow-ups

1. **ORCID (P496, 2,065,439 statements).** The largest identified gap and the only one where the
   cost is the reason rather than the absence of a partner. `crossref_research` holds 89,393
   researchers keyed on `orcid:` and only 294 of them join anything. Acquiring it is ~2.6 h of
   query-service time at the page size this dataset already uses; the declaration extends by one
   row and ~207 pages.
2. **Rebuild the index and attach.** The scope measurement is only realised once
   `data/world_evidence/index.sqlite` holds the new entity records; until then the bridge is worth
   the +5,386 in the index measurement rather than the +215,062 in the scope measurement.
3. **The `gb`-only OpenCorporates restriction is a register question, not a Wikidata one.** 570,000
   more OpenCorporates statements are one query away, and each one carries a jurisdiction code and
   that register's own number. They become joins the moment this catalog acquires a second national
   company register.
4. **The rights receipt.** The acquisition receipt records `license_status: CC0-1.0` and the
   publisher, but predates the fuller `license_id` / `terms_url` / `redistribution` fields now in
   the declaration, so `rights.terms_unspecified` is still true on the published output. The next
   re-acquisition records them and clears it.
5. **17,218 items are typed `entity`** because no `P31` class they carry maps to this ontology, and
   1,625 statements were refused on shape. Both are listed per property and per class in the
   pipeline and its generated class map; neither blocks a join, but both are where a reader should
   look before trusting a type.
