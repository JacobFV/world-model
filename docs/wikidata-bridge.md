# The Wikidata bridge: turning QIDs into a crosswalk

[identity-coverage.md](identity-coverage.md) measures why 96% of this catalog's 8.59M entities meet
no second publisher, and ends by naming the one identifier that could change it:

> Wikidata's QIDs are the only cross-domain identifier in the catalog, and only 2,008 of 486,342
> of them meet a second publisher.

980,841 `wikidata` identifier claims are already in the index, and 486,342 OpenSanctions entity IDs
*are* QIDs. They join almost nothing, because nothing in the catalog maps a QID to anything else.
This page is that map: what was acquired, how, which bridges it supports, what is refused, and what
the measurement says it is worth.

TBD-SUMMARY

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

### Why not a dump, and why the paging is not optional

The full truthy dump (`latest-truthy.nt.gz`) is tens of gigabytes against a ~10 GB fair share, and
almost none of it is an identifier this catalog can join. The targeted extract is
TBD-BYTES.

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

The Freiburg **QLever** mirror of Wikidata was measured as an alternative and is much faster - the
whole LEI query with labels and classes in 10.1 s - but it answered repeated queries with HTTP 429,
and it is a third-party mirror with its own dump date rather than the publisher's own service. The
official service, paged, is the route.

### What the route cannot do

* It is a **snapshot of a wiki**, taken on the acquisition date. Wikidata has no publication
  calendar and no vintage; the retrieval date in every record's evidence is the only date there is.
* The label is the English (or `mul`) label at that moment. Labels are names, never merge evidence.
* Coverage is whatever Wikidata's editors have entered. It is not a register: an LEI absent from
  Wikidata says nothing about the entity.

## What was acquired

TBD-ACQUIRED

### Deliberately out of scope

| Property | statements | why not |
| --- | ---: | --- |
| P496 ORCID iD | 2,065,439 | 3.9x the whole rest of the extract. At the measured ~45 s per 10,000-row page that is ~2.6 h of service time and ~250 MB for one namespace. The largest known gap; see [follow-ups](#follow-ups) |
| P1566 GeoNames ID | 4,089,782 | no catalog dataset publishes a GeoNames ID |
| P590 GNIS Feature ID | 883,567 | same |
| P402 OpenStreetMap relation ID | 601,057 | same |
| P213 ISNI / P214 VIAF / P227 GND | 2,571,307 / 4,825,193 / 3,058,528 | library authority files; no catalog partner |
| P1616 SIREN / P4156 Czech IČO / P3608 EU VAT | 463,448 / 77,299 / 18,219 | no catalog partner |
| P2427 GRID ID | 103,415 | redundant: `openalex_people`, the only catalog publisher of GRID IDs, already publishes the Wikidata QID of the same institution |
| P10283 OpenAlex ID | 210,912 | redundant for the same reason |
| P1320 outside the `gb` jurisdiction | ~563,000 | OpenCorporates' jurisdiction prefix names a register this catalog holds only for `gb` |

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

| Bridge | Cardinality | What it reads | Namespaces |
| --- | --- | --- | --- |
| `wikidata_identifier` | 1:1 | an `external_identifier` statement of a property whose issuer assigns one value per thing | `lei`, `sec_cik`, `bioguide`, `govtrack`, `votesmart`, `opensecrets`, `imo`, `ror`, `iata`, `icao`, `permid`, `gb_company_number`, `fdic_cert`, `eia_plant`, `eia_utility`, `duns`, `iso3166_1_alpha2/alpha3/numeric`, `iso3166_2`, `fips_county`, `fips_state` |
| `wikidata_identifier_series` | 1:n | the same statements for the families where one thing legitimately holds several values | `isin`, `mic`, `swift`, `fec_committee` |
| `geo_entity_id` | 1:1 | catalog **entity IDs** that are geographic codes: `geo:US:county:NNNNN`, `geo:US:state:NN`, `iso3:XXX`, `geo:XX`, `iso3166-2:XX-YYY` | `fips_county`, `fips_state`, `iso3166_1_alpha3`, `iso3166_1_alpha2`, `iso3166_2` |

`geo_entity_id` is the other half of the FIPS and ISO 3166 statements. `unify.ENTITY_ID_NAMESPACES`
already reads a namespaced entity ID as a published identifier claim - a source that calls a thing
`iata:AAE` has published that IATA code - and the catalog's geographic IDs are the same kind of
statement: `census_geography` and every ACS product key a county on its five-digit FIPS code,
`airport_nodes` and `bis_bulk` key a country on its ISO 3166-1 alpha-3 code. On its own the bridge
joins nothing (those IDs are already shared between the Census products that use them, which is
measurable: TBD-GEOALONE). It exists so the Wikidata codes have a second
publisher to meet.

### Check digits and formats, recomputed

A wiki has typos. Every value is held to the shape or check digit its own standard defines before
it can become identity:

| Family | Rule |
| --- | --- |
| LEI | ISO 17442: 18 characters plus MOD 97-10 (ISO 7064) check digits, recomputed |
| ISIN | ISO 6166: the modulus-10 double-add-double check digit, recomputed |
| IMO ship number | IMO A.600(15): `7d1+6d2+5d3+4d4+3d5+2d6 mod 10` equals the last digit |
| SEC CIK | decimal digits, leading zeros stripped, never all-zero |
| Bioguide | one letter and six digits |
| IATA / ICAO / MIC / BIC | `[A-Z]{3}` / `[A-Z]{4}` / `[A-Z][A-Z0-9]{3}` / ISO 9362, with the eight-character form folded onto branch `XXX` |
| Companies House | eight characters: eight digits, or a two-letter register prefix and six |
| FIPS / ISO 3166 | five and two digits; `[A-Z]{2}`, `[A-Z]{3}`, three digits, `XX-YYY` |
| FEC committee | `C` and eight digits |
| ROR | the nine-character `0`-prefixed form (the checksum algorithm is not recomputed; format only) |

Two of these earn their place immediately on the real extract: TBD-CHECKDIGITS

### The refusals

A bridge that fires where it should not is worse than a missing bridge. Four properties are
acquired and published as evidence and **never** turned into identity, for the reason `dff5b81`
already refuses OGRN, UN/LOCODE and MMSI values one dataset prints twice:

| Property | statements | Why it is refused |
| --- | ---: | --- |
| P587 MMSI | 38,198 | An MMSI is a radio identity assigned to a station, not to a hull, and is reassigned when a ship is re-flagged or scrapped. A Wikidata item for a 1990s ship and the AIS record for the hull that now holds the number are not the same vessel |
| P1937 UN/LOCODE | 45,717 | A UN/LOCODE names a locality, not a facility; 28 of them in this catalog are already printed for two different World Port Index ports |
| P1297 IRS EIN | 17,260 | A taxpayer ID a parent and its subsidiaries share - EIN 850019030 is published here for both Public Service Co of New Mexico and its holding company. `unify.NON_UNIQUE_IN_PRACTICE` keeps `ein` out of clustering already |
| P2390 Ballotpedia ID | 16,444 | A wiki page title, renamed whenever the page moves, carrying whatever disambiguator that wiki needed; four of the first four values sampled were election pages, not people |

On top of those, three mechanical refusals run on every acquired value:

TBD-REFUSALS

## The measurement

TBD-MEASUREMENT

## What this does not establish

* **That Wikidata is right.** A bridge row says a Wikidata editor published this identifier against
  this item. Where two publishers disagree - one value on two items, one item with two values of a
  1:1 family - the row is refused, not adjudicated. Nothing here checks a statement against the
  register that issued the identifier.
* **That an unjoined entity has no identifier.** Wikidata's coverage is whatever its editors
  entered. 53,403 of GLEIF's 3.43M LEIs are in Wikidata; the other 3.38M are not evidence of
  anything.
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

TBD-FOLLOWUPS
