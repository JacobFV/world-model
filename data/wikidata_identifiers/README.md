# wikidata_identifiers

Wikidata items and the external identifiers they carry, one published statement per Wikidata
property.

Identity is this catalog's measured bottleneck: after mining every published crosswalk, 3.66% of
8.59M entities join across two datasets and 2.44% across two publishers
([identity-coverage.md](../../docs/identity-coverage.md)). The reason named there is that
**Wikidata QIDs are the only cross-domain identifier in the catalog** - 486,342 OpenSanctions
entity IDs are QIDs, and 980,841 `wikidata` identifier claims sit in the index, but nothing maps a
QID to anything else. This dataset is that map.

## Route

The **Wikidata Query Service** (`https://query.wikidata.org/sparql`), one request per (property,
page), CSV results:

```sparql
SELECT ?item ?label ?value (GROUP_CONCAT(DISTINCT ?t;SEPARATOR="|") AS ?types) WHERE {
  { SELECT ?item ?value WHERE { ?item wdt:P1278 ?value . }
    ORDER BY ?item ?value LIMIT 10000 OFFSET 0 }
  OPTIONAL { ?item wdt:P31 ?c . BIND(STRAFTER(STR(?c),"/entity/") AS ?t) }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". ?item rdfs:label ?label. }
} GROUP BY ?item ?label ?value
```

`wdt:` is the **truthy** predicate, so deprecated-rank statements never appear, and a property with
a preferred-rank statement contributes only that one. The inner subquery is a total order over
`(item, value)`, so `LIMIT`/`OFFSET` over it is a complete, disjoint partition of the property: a
page is independently re-runnable and re-fetching one page cannot silently shift the others.

Why not the full dump: `latest-truthy.nt.gz` is tens of gigabytes against a ~10 GB fair share, and
99% of it is not identifiers this catalog can join.

**The service silently truncates.** A single un-paged query for the 53,403 LEI statements with
labels returned HTTP 200 with 46,702 rows after 70 s, and the label-only form returned 15,942 rows
after 63 s. That is why every page declares its own row count and the pipeline refuses a page that
came back short before its property was exhausted (see `pipeline.py`); each property is followed by
at least one deliberately empty page so "the property ended" is distinguishable from "the response
was cut".

## Scope

31 properties, chosen because some dataset in this catalog publishes the same identifier:

| Wikidata property | namespace | statements (2026-09-19) |
| --- | --- | ---: |
| P1278 Legal Entity Identifier | `lei` | 53,403 |
| P5531 Central Index Key | `sec_cik` | 3,693 |
| P1157 US Congress Bio ID | `bioguide` | 13,059 |
| P12644 GovTrack person ID | `govtrack` | 64 |
| P3344 Vote Smart candidate ID | `votesmart` | 5,357 |
| P2686 OpenSecrets people ID | `opensecrets` | 729 |
| P2390 Ballotpedia ID | `ballotpedia` | 16,444 |
| P7057 FEC Campaign Committee ID | `fec_committee` | 99 |
| P946 ISIN | `isin` | 10,272 |
| P458 IMO ship number | `imo` | 96,525 |
| P587 MMSI | `mmsi` | 38,198 |
| P6782 ROR ID | `ror` | 135,973 |
| P238 IATA airport code | `iata` | 9,352 |
| P239 ICAO airport code | `icao` | 20,610 |
| P7534 MIC market code | `mic` | 1,932 |
| P2627 ISO 9362 SWIFT/BIC code | `swift` | 2,565 |
| P3347 PermID | `permid` | 6,110 |
| P2622 Companies House company ID | `gb_company_number` | 8,175 |
| P11175 FDIC Certificate ID | `fdic_cert` | 117 |
| P10682 EIA plant ID | `eia_plant` | 11,929 |
| P10712 EIA utility ID | `eia_utility` | 9 |
| P2771 D-U-N-S number | `duns` | 628 |
| P1297 IRS Employer Identification Number | `ein` | 17,260 |
| P1937 UN/LOCODE | `unlocode` | 45,717 |
| P1320 OpenCorporates organization ID (`gb/` only) | `opencorporates` | ~15,000 of 578,070 |
| P297/P298/P299 ISO 3166-1 alpha-2/alpha-3/numeric | `iso3166_1_*` | 262 / 274 / 270 |
| P300 ISO 3166-2 code | `iso3166_2` | 5,459 |
| P882 FIPS 6-4 ID | `fips_county` | 3,793 |
| P5087 FIPS 5-2 numeric code | `fips_state` | 69 |

Counts are `SELECT (COUNT(*) AS ?c) WHERE { ?i wdt:<P> ?v }` measured against WDQS on 2026-09-19
and are recorded in `parameters.properties`; the pipeline holds the acquired total for each
property to at least 98% of them, so a truncated acquisition fails instead of publishing quietly.

### Deliberately out of scope

| Property | statements | why not |
| --- | ---: | --- |
| P496 ORCID iD | 2,065,439 | 3.9x the whole rest of the extract. At the measured ~45 s per 10,000-row page that is ~2.6 h of service time and ~250 MB for one namespace |
| P1566 GeoNames ID | 4,089,782 | no catalog dataset publishes a GeoNames ID |
| P402 OpenStreetMap relation ID | 601,057 | same |
| P590 GNIS Feature ID | 883,567 | same |
| P213 ISNI / P214 VIAF / P227 GND | 2.6M / 4.8M / 3.1M | library authority files; no catalog partner |
| P1616 SIREN / P4156 Czech IČO / P3608 EU VAT | 463,448 / 77,299 / 18,219 | no catalog partner |
| P2427 GRID ID | 103,415 | redundant: `openalex_people`, the only catalog publisher of GRID IDs, already publishes the Wikidata QID for the same institution |
| P10283 OpenAlex ID | 210,912 | redundant for the same reason |
| P1320 non-`gb` jurisdictions | ~563,000 | OpenCorporates' jurisdiction prefix is only a register this catalog holds for `gb` |

Identifiers this catalog holds that **Wikidata has no property for at all**: ICPSR legislator IDs
(24,978 claims held), FEC *candidate* IDs (54,575), CUSIP (2,422,613, proprietary), SAM UEI
(27,513), FRB RSSD (27,566), FIGI. Those populations cannot be bridged from here.

## Published records

One `entity` record per item (`entity_id` = `wikidata:Q102673`, the label, an `entity_type` from
the item's own `P31` classes) and one `external_identifier` assertion per statement, carrying the
property that published it:

```json
{"kind": "assertion", "predicate": "external_identifier", "subject": "wikidata:Q102673",
 "value": {"property": "P1278", "property_label": "Legal Entity Identifier",
           "namespace": "lei", "value": "213800FD9J2IHTA7YX78"}}
```

The statements are **not** published as `identifier_assignment` rows. Wikidata is a wiki: its
identifier statements are community assertions, not a registry's own assignment, so the resolution
layer reads them as *bridges*
([worldmodel/resolution/bridges.py](../../worldmodel/resolution/bridges.py)), which means every
family is held to the 1:1 its mapping specification declares, values that break it are refused and
counted, and `unify-resolve --no-bridges` turns the whole layer off. See
[docs/wikidata-bridge.md](../../docs/wikidata-bridge.md).

## Rights

Wikidata content is **CC0 1.0 Universal** (public domain dedication),
<https://www.wikidata.org/wiki/Wikidata:Licensing>. No attribution is required; the publisher and
the retrieval date are recorded in every record's evidence anyway. The query service is used under
the Wikimedia user-agent policy: a contact User-Agent is sent, requests are rate limited to one
every five seconds, and `Retry-After` is honoured by the acquisition runner.
