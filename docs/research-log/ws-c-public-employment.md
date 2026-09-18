# WS-C: the public-sector labor graph

The catalog had no government payroll or public-employment data. `lehd_lodes` counts jobs by census
block and `acs_pums` samples households, but nothing said what a government employs or pays. This
workstream adds two source datasets, `census_aspep` and `opm_fedscope`, and wires them into the existing
graph through published identifiers only.

The object is not a salary lookup. It is the longitudinal panel
`government unit x function x year x employment x payroll` for state and local government, and
`agency x occupation x pay grade x duty state x quarter x headcount x payroll` for the federal
executive branch, both joined to the geography, spending and election data already in the catalog.

## Sources: what is live, what the URLs actually are

### Census ASPEP (Annual Survey of Public Employment & Payroll)

The landing page per year is `https://www.census.gov/data/datasets/<YEAR>/econ/apes/annual-apes.html`.
Scraping those pages for every year 1992-2024 (all HTTP 200) gave the real file inventory, which the
`.../datasets.html` index does not, because that index is rendered client-side and contains no links in
its HTML.

Three publisher naming conventions, all under `https://www2.census.gov/programs-surveys/apes/datasets/`.
The 2023 URL pattern given in the brief does **not** generalise:

| Years | Path | Verified |
| --- | --- | --- |
| 1993-2014, 2016 | `<year>/annual-apes/<year>_downloadable_data.zip` | HEAD 200 with sizes for 1993-1995, 1997-2002, 2004-2006, 2008, 2010, 2012-2014 |
| 2015 | `2015/annual-apes/2015_individual_unit_files.zip` | from the 2015 landing page |
| 2017-2021, 2023-2024 | `<year>/<year>_individual_unit_files.zip` | HEAD 200 with sizes for 2017-2021, 2023, 2024 |
| 2022 | `2022/2022%20COG-E%20Individual%20Unit%20Files.zip` | from `https://www.census.gov/data/datasets/2022/econ/apes/2022.html` |

The Census of Governments years sit on different landing pages, which is why 2012, 2017 and 2022 are
absent from the `annual-apes.html` scrape. 2017 happens to answer at the flat path (37,652,063 bytes
measured); 2012 is under `annual-apes/2012_downloadable_data.zip`; 2022 uses a space-separated
filename, found only on the 2022 COG datasets page. A plausible-looking guess,
`2022/census-apes/2022_individual_unit_file.zip`, returns HTTP 200 to a HEAD request and then an F5 WAF
*"Request Rejected"* page to a GET — a reminder that a 200 on HEAD is not evidence a file exists.

**1996 has no individual unit file.** Its landing page offers only `96fedst.txt`, a federal summary
table, and `1996/1996_individual_unit_files.zip` is a hard 404. The acquisition declares the year with
`skip_statuses: [404]` so it is recorded as skipped rather than silently omitted.

**ASPEP runs back to 1957, but the machine-readable individual-unit files start at 1993.** The research
plan described this workstream as "ASPEP 1957–", and that is the survey's span, not the span of its
downloadable microdata. The historical-data page
(`https://www.census.gov/programs-surveys/apes/data/historical_data.html`, HTTP 200) was scraped in
full: for 1957-1987 it offers **scanned publications** — `1957-vol2-no1-emp-summary.pdf`,
`1957-vol2-no2-emp-compendium.zip`, `1962-vol5-loc-govt-in-metro-areas.zip` and so on — plus a set of
historical databases under `https://www2.census.gov/programs-surveys/gov-finances/datasets/historical/`
(`Public_Emp.zip`, `City_Govt_Emp.zip`, `County_Area_Emp.zip`, `County_Govt_Emp.zip`, `emp_est.zip`,
`hist_em.zipp` — that last one is the publisher's typo, not a transcription error here). Those
historical databases are **not acquired here and not claimed**: their layouts are undocumented on that
page, they are aggregates rather than individual-unit records, and one of the URLs is malformed at the
source. They are the obvious next extension and are listed here so the gap is a decision rather than
an omission. Pre-1993 coverage in this dataset is therefore **zero**, and the description says 1993.

Measured content-lengths (bytes, from HEAD):

| Year | Bytes | Year | Bytes | Year | Bytes |
| --- | ---: | --- | ---: | --- | ---: |
| 1993 | 2,591,877 | 2004 | 1,508,345 | 2018 | 10,146,099 |
| 1994 | 2,873,698 | 2005 | 1,488,640 | 2019 | 8,187,626 |
| 1995 | 2,890,284 | 2006 | 1,566,586 | 2020 | 8,341,775 |
| 1997 | 9,372,481 | 2008 | 7,010,003 | 2021 | 11,086,886 |
| 1998 | 2,658,832 | 2010 | 7,343,090 | 2023 | 10,892,609 |
| 1999 | 2,202,021 | 2014 | 2,625,598 | 2024 | 10,925,836 |
| 2000 | 2,213,906 | 2017 | 37,652,063 | | |
| 2001 | 2,113,423 | | | | |
| 2002 | 6,547,875 | | | | |

`www2.census.gov` is behind a Cloudflare zone rate limit that answers repeated requests with HTTP 429
and body `error code: 1015`. Probing 33 years x 4 candidate paths tripped it, and the first acquisition
attempt sat in backoff for seven minutes without writing a shard. The declared block now requests at
0.1/s with 12 retries and up to 10 minutes of backoff. This is a real operational constraint on this
host, not a source outage.

The **APES API** page (`https://www.census.gov/programs-surveys/apes/data/api.html`, HTTP 200) points at
one dataset, "Public Sector: Public Employment and Payroll", on the Census developer portal. It serves
aggregated tables, not the individual-unit file, so it is not the backbone here; the bulk archives are.

### OPM FedScope

`https://www.fedscope.opm.gov/` now **301-redirects to `https://data.opm.gov/`**, a Blazor server
application with no bulk download endpoint. `https://www.opm.gov/data/datasets/` returns 200 to a
plain-UA request (the 403 in the brief did not reproduce), and the actual archives are listed at
`https://www.opm.gov/data/datasets/Index.aspx?tag=FedScope` behind opaque paths of the form
`/data/datasets/Files/<id>/<uuid>.zip`. Each row on that page carries a PDF data dictionary and one ZIP;
71 ZIPs in total, with declared sizes summing to **1.372 GiB**.

| Kind | Count | Coverage | Declared bytes |
| --- | ---: | --- | ---: |
| Employment cube | 63 | Sep 1998-2009 annually, then quarterly Mar 2010 - Jun 2022 | 1.269 GiB |
| Accession cube | 4 | FY2005-FY2009, FY2010-FY2014, FY2015-FY2019, FY2020-FY2024 | ~0.05 GiB |
| Separation cube | 4 | the same blocks | ~0.05 GiB |

The published employment series stops at **June 2022**; the page was last revised 18 February 2023 and
offers nothing newer. That is a source limit, not a collection choice.

Verified by download and extraction:

* `fedscope_employment_202206.zip` — 19,988,523 bytes; `FACTDATA_JUN2022.TXT` is 153,206,927 bytes with
  **2,169,629 rows, one per employee**, plus 17 `DT*.txt` dimension tables and a documentation PDF.
* `fedscope_employment_199809.zip` — 19,510,199 bytes; `FACTDATA_SEP1998.TXT` 128,806,006 bytes. Its
  header has **no `PP` column** and salary is formatted `"$42,709"`, so the parser is header-driven
  rather than positional and strips currency formatting.
* `fedscope_accessions_fy2020_fy2024.zip` — 12,824,226 bytes; `ACCDATA_FY2020-2024.TXT` 81,163,017 bytes
  with **1,170,401 rows**, using `COUNT` instead of `EMPLOYMENT`, `EFDATE` instead of `DATECODE`, and an
  `ACC` action-type column decoded by `DTacc.txt`.

**FedScope rows are de-identified, and they carry no names — but they also carry no county.** The public
cube's `LOC` values are two-digit FIPS state codes for the 50 states and DC and two-letter FIPS 10-4
style codes for territories and foreign countries (220 distinct values in the June 2022 cube, out of 226
in `DTloc.txt`). Age and length of service are banded; salary is the exact annual basic pay rate.
**12.6%** of June 2022 rows (273,625 of 2,169,629) have a blank salary.

`openpayrolls.com` was not touched.

## Design: why the person-level rows are aggregated

Person-level FedScope rows raise no rights question — there is nothing identified in them. They raise a
volume question. 63 employment cubes hold roughly 137 million employee rows; re-emitting each as an
evidence record would produce an artifact two orders of magnitude larger than the 1.37 GiB source and
answer nothing that the cell-level panel does not. The pipeline aggregates each cube while streaming
into five marginal cells (measured: 48,577 occupied cells in the June 2022 cube, 6.7 s per cube) and
publishes headcount, the sum of published annual salaries, **and the count of employees whose salary was
published**, so a mean is computable instead of implied.

ASPEP is already unit-level, so its records are one-to-one with published values, minus zero-valued
measures under non-total function codes (68,786 of 328,284 measures in 2023 are zero).

Both declare `source.person_level_records` as `prohibited` — the restrictive default — with a note
saying why: neither publisher publishes an identified natural person, so no permission is claimed and
none is needed. The `usaspending_assistance` precedent for retaining highly-compensated-officer names
does not need to be invoked.

## The layout problem, and why it was worth finding

The first ASPEP build failed on `No ZIP members match ['*empid.txt']`, and the reason mattered more
than the fix. ASPEP ships its individual unit files in **three packagings and two record layouts**, and
announces neither in the file.

| Years | Packaging |
| --- | --- |
| 1993-2011 | a **nested ZIP** inside the archive, holding one `.DAT` (`01empid.zip!01empid.dat`) |
| 2012-2013 | a `.dat` buried in a subdirectory (`2012_downloadable_data/Individual Unit File/12cempid.dat`) |
| 2014-2024 | a plain `.txt`, in a subdirectory in 2015 |

Census-of-governments years prefix the name with `c`. The shared raw readers cannot descend into a ZIP
inside a ZIP, so the pipeline opens each archive itself and resolves exactly one nested level.

The dangerous part was the data record. Measured widths across the 31 acquired years:

| Width | Years | Layout |
| ---: | --- | --- |
| 84 | 1993-2006 | **unflagged** |
| 96 | 2007-2011 | flagged |
| 94 | 2012-2018 | flagged |
| 72 | 2019-2020 | flagged, no part-time hours or full-time equivalents |
| 80 | 2021-2024 | flagged, with the New Individual Unit ID |

The pre-2007 record carries **no data flags at all**, which puts every payroll and part-time field
**two positions to the left** of the documented layout. Reading it with the documented positions does
not raise: the straddled slices still parse as integers for 93-97% of rows (and only 53% in 2003,
which is what first drew attention to it). It silently returns wrong numbers. So the pipeline picks the
layout from the modal record width, refuses an unseen width instead of guessing its columns, and then
checks the choice against the bytes — a flagged width must carry letters where the flags belong and an
unflagged width must not.

Three things were validated across all 31 years **before** any of that was written:

- The **FIPS state column** agrees with the ASPEP state code for 51 codes in every year: 1.0000 of rows
  in 30 years, 0.9988 in 1993 (181 unparseable of 14,136).
- The **derived median monthly wage** rises smoothly from $2,269 (1993) to $5,795 (2024), with no step
  at any packaging or layout boundary. A mis-set column would have broken that curve.
- **Probability of selection is exactly 1.0 for every unit** in 1997, 2002, 2007, 2012, 2017 and 2022,
  and below 1 in every other year. The census-versus-sample distinction is therefore measured from the
  data, not asserted from the calendar.

## Acquisition and build

| | `census_aspep` | `opm_fedscope` |
| --- | ---: | ---: |
| Shards requested | 32 | 71 |
| Shards acquired | **31** (1996 skipped, HTTP 404) | **71** |
| Raw bytes | 258,896,973 = **0.2411 GiB** | 1,473,120,618 = **1.372 GiB** |
| Ledger `used`, `settled` | 258,923,778, yes | 1,473,178,741, yes |
| Normalized records | **19,116,854** | **11,724,597** |
| Normalized bytes (gzip) | 1,124,597,236 = 1.047 GiB | 680,501,834 = 0.634 GiB |
| `wm verify <id>/normalized` | `verified: true` | `verified: true` |

Together **1.613 GiB of raw** against a 25 GiB per-dataset fair-share cap, and 30.8M published records.

`census_aspep`: 18,905,284 observations, 105,487 entities, 106,083 assertions.

| Metric | Observations |
| --- | ---: |
| `government_employees` | 9,577,576 |
| `government_payroll` | 6,450,116 |
| `government_part_time_hours` | 2,353,928 |
| `government_unit_population` | 368,290 |
| `school_enrollment` | 155,374 |

Data-flag classes: 7,197,660 reported, 1,842,105 imputed, 105,605 unrecognized, and 9,236,250 with no
flag — the last being the 1993-2006 vintages, which publish none, plus the full-time-equivalent column,
which has no flag position in any vintage. 40 distinct function codes appear, against the 33 in the
2023 code list, because older vintages use codes the current list has retired.

`opm_fedscope`: 11,721,736 observations, 2,051 entities, 810 assertions, over **63 employment cubes
(September 1998 - June 2022)** and **19 fiscal years of flows (FY2005-FY2023)**.

| Cell | Observations | | Cube | Observations |
| --- | ---: | --- | --- | ---: |
| `agency_occupation` | 6,618,079 | | employment | 9,240,082 |
| `agency_location` | 2,538,604 | | separations | 1,313,154 |
| `agency_pay_grade` | 2,119,820 | | accessions | 1,168,500 |
| `agency_action` | 228,615 | | | |
| `agency` | 158,813 | | | |
| `location` | 57,805 | | | |

926 agency entities, 867 occupational-series entities, 51 state entities, 206 unjoined duty locations.

**Internal consistency check.** For June 2022 the `location` cells and the `agency` cells each sum to
**2,169,629** employees — exactly the row count of `FACTDATA_JUN2022.TXT`. Two independent marginals
reconstructing the source row count is the check that the streaming aggregation loses nothing. The
blank-salary share measured from the published records is **0.1261** (1,896,004 of 2,169,629 have a
salary), matching the 273,625 blank rows counted in the raw file, and the mean published annual salary
is $95,744.

### Per-year ASPEP coverage

`units` is distinct government units with at least one observation that year.

| Year | Basis | Units | Observations | Layout |
| --- | --- | ---: | ---: | --- |
| 1993 | annual sample | 14,137 | 548,462 | unflagged 84 |
| 1994 | annual sample | 13,997 | 555,344 | unflagged 84 |
| 1995 | annual sample | 13,710 | 558,130 | unflagged 84 |
| **1997** | **census of governments** | **87,503** | 1,939,896 | unflagged 84 |
| 1998 | annual sample | 13,562 | 535,466 | unflagged 84 |
| 1999 | annual sample | 10,625 | 376,353 | unflagged 84 |
| 2000 | annual sample | 10,909 | 383,636 | unflagged 84 |
| 2001 | annual sample | 10,951 | 346,715 | unflagged 84 |
| **2002** | **census of governments** | **89,997** | 1,405,753 | unflagged 84 |
| 2003 | annual sample | 10,988 | 267,192 | unflagged 84 |
| 2004 | annual sample | 10,955 | 284,721 | unflagged 84 |
| 2005 | annual sample | 10,946 | 279,693 | unflagged 84 |
| 2006 | annual sample | 10,946 | 279,532 | unflagged 84 |
| **2007** | **census of governments** | **90,690** | 1,724,423 | flagged 96 |
| 2008 | annual sample | 11,455 | 361,194 | flagged 96 |
| 2009 | annual sample | 10,464 | 384,509 | flagged 96 |
| 2010 | annual sample | 10,482 | 386,220 | flagged 96 |
| 2011 | annual sample | 10,515 | 385,893 | flagged 96 |
| **2012** | **census of governments** | **90,748** | 1,727,855 | flagged 94 |
| 2013 | annual sample | 10,838 | 390,896 | flagged 94 |
| 2014 | annual sample | 10,506 | 382,923 | flagged 94 |
| 2015 | annual sample | 10,542 | 384,246 | flagged 94 |
| 2016 | annual sample | 10,574 | 385,166 | flagged 94 |
| **2017** | **census of governments** | **91,274** | 1,766,153 | flagged 94 |
| 2018 | annual sample | 10,782 | 390,161 | flagged 94 |
| 2019 | annual sample | 11,397 | 277,812 | flagged 72 |
| 2020 | annual sample | 11,466 | 277,822 | flagged 72 |
| 2021 | annual sample | 11,516 | 276,713 | flagged 80 |
| **2022** | **census of governments** | **79,255** | 1,099,693 | flagged 80 |
| 2023 | annual sample | 11,213 | 270,990 | flagged 80 |
| 2024 | annual sample | 11,307 | 271,722 | flagged 80 |

The census years carry 79k-91k units and the sample years 10.5k-14k — an eight-fold difference that is
the whole reason `dimensions.collection_basis` exists on every record. **A sampled year is a sample of
roughly 12% of local governments, and comparing one to a census year counts different populations.**
The 2022 census enumerated 79,255 units against 91,274 in 2017; that is the published coverage, not a
loss in this pipeline.

Across the 31 years, **65 observations were summed from two source rows** the publisher printed
separately for the same unit and function: 59 in 1995 and 6 in 1999. Each records
`attributes.source_rows_merged: 2`.

## Joins established

| From | To | Basis | Deterministic? |
| --- | --- | --- | --- |
| `aspep:unit:<14-char ID>` | `geo:US:county:<5-digit FIPS>` | FIPS state (cols 110-111) + FIPS county (112-114) published in `<yy>empid.txt` | yes |
| `aspep:unit:<14-char ID>` | `geo:US:state:<2-digit FIPS>` | FIPS state, for units with no county | yes |
| `opm:agency:<AGYSUB>` | `geo:US:state:<2-digit FIPS>` | `DTloc.txt` `LOC` where it is a two-digit numeric state code | yes |
| `opm:agency:<AGYSUB>` | `opm:agency:<AGY>` | `DTagy.txt` publishes both the sub-element and its parent agency code | yes |

Those `geo:US:state:` and `geo:US:county:` keys are the ones `census_geography`, `census_population`,
`usaspending`, `openfema` and `mit_election_returns` already use, so no bridging table is needed.

The 14-character Individual Unit ID is **not** a FIPS code and its internal county code is not the FIPS
county: Baldwin County, Alabama is unit-ID county `002` and FIPS county `003`. Only the published FIPS
fields are used.

## What could not be joined, and why

* **FedScope agency to `usgov:agency:<CGAC>`.** `usaspending` keys federal agencies by Treasury CGAC
  code; FedScope uses OPM EHRI agency and sub-element codes (`AF`, `AF02`). Neither OPM's cube nor
  USAspending's files ship a crosswalk between them, and joining them on agency name is precisely the
  inferred name matching measured on this catalog at 0.4% recall and 2.3% precision. **No link is
  asserted.** Each `opm:agency` entity records `crosswalk_to_cgac: "none published…"` so the absence is
  visible in the data rather than only here.
* **FedScope territories and foreign duty stations.** `AQ`, `CQ`, `GQ`, `MQ`, `RQ`, `VQ` and the foreign
  country codes are FIPS 10-4 style, not FIPS 5-2 state codes or ISO 3166. The mapping exists in
  standards this catalog does not currently load, so these become `opm:duty_location:<code>` entities
  and are not folded into `geo:` keys.
* **Federal employment below the state level.** Not published in the public cube at all.
* **ASPEP unit to a `census_geography` place or county subdivision.** ASPEP publishes the county a
  municipality or township is *assigned to*, not a place GEOID. Municipal units therefore join at county
  granularity, not to `geo:US:place:`. Matching municipality names to place names would be name matching.
* **ASPEP unit to `usaspending` recipients.** ASPEP publishes no UEI, DUNS or EIN for the government
  unit, so a county government that receives federal awards cannot be tied to its award records by
  identifier. The shared county FIPS supports geographic comparison, not entity identity.

## Measured join rates into the existing graph

Every rate below is an exact string match on a `geo:US:state:<2-digit FIPS>` or
`geo:US:county:<5-digit FIPS>` key against the keys each target dataset's published normalized artifact
already carries. No name matching, no fuzzy matching, nothing inferred.

`census_aspep` emits **102,270 government units**. **102,269 of them (1.0000)** carry a `within`
containment; **102,218 (0.9995)** are contained at county granularity and 51 at state granularity
(the state governments). They point at **3,164 distinct county keys** and 50 state keys.

`opm_fedscope` emits 51 state keys and 206 duty-location keys it deliberately does not join.

| Target | ASPEP counties | ASPEP units reaching it | FedScope states |
| --- | --- | --- | --- |
| `census_geography` | 3,140 / 3,164 = **0.9924** | 102,019 / 102,270 = **0.9975** | 51 / 51 = **1.0000** |
| `census_population` | 3,148 / 3,164 = **0.9949** | 102,263 / 102,270 = **0.9999** | 51 / 51 = **1.0000** |
| `openfema` | 3,142 / 3,164 = **0.9930** | 102,264 / 102,270 = **0.9999** | 51 / 51 = **1.0000** |
| `lehd_lodes` | 3,139 / 3,164 = **0.9921** | 102,263 / 102,270 = **0.9999** | 51 / 51 = **1.0000** |
| `mit_election_returns` | 3,115 / 3,164 = **0.9845** | 102,093 / 102,270 = **0.9983** | 51 / 51 = **1.0000** |
| `usaspending` | 2,845 / 3,164 = **0.8992** | 96,824 / 102,270 = **0.9467** | 0 / 51 = **0.0000** |

Two notes on how those were counted, because getting them wrong would have overstated the result in one
direction and understated it in the other:

- `usaspending` and `mit_election_returns` **declare no `geo:` entities at all** — they reference
  geography keys from award and returns records without emitting an entity for each. Counting only
  declared entities scored both at 0.0000. The table counts keys each dataset declares *or* references.
- `usaspending`'s lower rate is coverage, not a key mismatch: it references 2,923 county keys because
  its acquired slice is one fiscal year of awards, and a county with no federal award in that slice has
  no key to match. The 319 ASPEP counties it misses are counties `usaspending` does not mention.
- `opm_fedscope` scores 0/51 against `usaspending` for the opposite reason: `usaspending`'s geography is
  county-level place of performance and it references no state keys, while FedScope publishes only
  states. Both are US federal spending and employment and they still do not meet, because neither
  publishes a key at the other's grain.

Federal geographic coverage: **2,185,536 of 2,596,409** FedScope observations carrying a duty location
point at a `geo:US:state:` key = **0.8418**. The remaining 15.8% are territories, foreign duty stations
and a `**` unspecified code, which get `opm:duty_location:<code>` entities rather than a guessed key.

## What could not be joined, and why (measured)

* **FedScope agency to `usgov:agency:<CGAC>`: absent, not asserted.** `usaspending` keys federal
  agencies by Treasury CGAC code; FedScope uses OPM EHRI agency and sub-element codes (`AF`, `AF02`).
  Neither source ships a crosswalk, and joining on agency name is exactly the inferred name matching
  measured on this catalog at 0.4% recall and 2.3% precision. All 926 `opm:agency` entities carry
  `crosswalk_to_cgac: "none published by OPM or Treasury in these files; no identity link to
  usgov:agency:<CGAC> is asserted"`, so the absence is visible in the data and not only here.
* **206 FedScope duty locations stay unjoined**, including US territories (`AQ` American Samoa, `CQ`
  Northern Marianas, `GQ` Guam, `RQ` Puerto Rico, `VQ` Virgin Islands), ~200 foreign country codes and a
  `**` unspecified code. These are FIPS 10-4 style codes; mapping them to FIPS 5-2 state codes or ISO
  3166 needs a table neither the cube nor `worldmodel/reference/` carries.
* **No federal employment below the state level exists in the public cube**, so there is no county-level
  federal figure to join at any rate.
* **24 of 3,164 ASPEP counties are not in `census_geography`.** These are vintage differences: ASPEP
  spans 1993-2024 and `census_geography` is the 2024 vintage, so counties that were renamed, merged or
  recoded (Connecticut's 2022 planning-region recode, Alaska borough changes, Virginia city
  independence) appear in ASPEP and not in the current gazetteer. `worldmodel.crosswalks.UsGeography`
  has dated county crosswalks for exactly this, and wiring them in is the obvious next step; this
  dataset does not silently reassign them.
* **ASPEP units do not reach `geo:US:place:`.** ASPEP publishes the county a municipality or township is
  *assigned to*, not a place GEOID, so a municipal government joins at county granularity. Matching
  municipality names to place names would be name matching.
* **ASPEP carries no UEI, DUNS or EIN**, so a county government that receives federal awards cannot be
  tied to its `usaspending` award records by identifier. The shared county FIPS supports geographic
  comparison, not entity identity.

## What this now answers that the catalog could not

### Q1 — Where is the federal workforce, next to the state and local one?

`opm_fedscope` `location` cells for June 2022, `census_aspep` function code `000` for the 2022 Census of
Governments, `census_population` state population for 2022. The query is
[`questions.py`](#the-queries); it joins purely on `geo:US:state:<FIPS>`.

```
state              federal  st+local   fed/1k    sl/1k  fed mean $    sl x12 $
geo:US:state:11    164,474    51,373   243.85    76.16     130,413      94,211   District of Columbia
geo:US:state:24    137,721   361,882    22.27    58.53     121,938      65,404   Maryland
geo:US:state:15     23,585    83,975    16.40    58.40      87,256      58,699   Hawaii
geo:US:state:51    140,487   537,017    16.18    61.86     106,736      54,975   Virginia
geo:US:state:02     11,426    57,989    15.57    79.04      88,906      65,055   Alaska
...
geo:US:state:36     50,526 1,303,591     2.56    66.13      93,141      74,730   New York
geo:US:state:09      8,264   210,718     2.28    58.23      99,353      68,498   Connecticut
geo:US:state:34     21,177   541,539     2.28    58.24     105,844      69,396   New Jersey

totals: federal 1,863,698   state+local 19,180,051   ratio 10.29 state+local per federal employee
```

**State and local government employs 10.29 people for every one the federal executive branch employs**,
and the federal footprint is 100 times more geographically concentrated than the state and local one
(243.85 per 1,000 in DC against 2.28 in Connecticut and New Jersey, a 107-fold spread, versus 52.5-100.1
for state and local, a 1.9-fold spread). Neither number was available in this catalog before: it has
`lehd_lodes` NAICS sector 92 job counts by block, which mixes all three levels of government and
carries no payroll at all.

Both currency columns are labelled for what they are. `fed mean $` divides the published annual salary
total by the 1,896,004 employees whose salary was published, not by all 2,169,629. `sl x12 $` multiplies
ASPEP's March monthly-equivalent payroll per employee by 12; it is an annualization, not a published
annual figure, and it includes part-time employees in both numerator and denominator.

### Q2 — How heavily is a county policed, and what does it pay?

`census_aspep` function code `062` (sworn officers with power of arrest) for 2023 against
`census_population` 2023, on `geo:US:county:<5-digit FIPS>`. Run two ways, because the first way has a
trap:

```
=== every local government assigned to the county
county                    officers         pop   per 1k  mean monthly $
geo:US:county:36061         35,056   1,597,451    21.94           8,629   New York County (NY)
geo:US:county:11001          3,997     678,972     5.89          10,178   District of Columbia
geo:US:county:42101          6,057   1,550,542     3.91           8,669   Philadelphia County (PA)
geo:US:county:17031         16,035   5,087,072     3.15           8,731   Cook County (IL)
all county areas with sworn police in 2023: 1,990; total sworn officers 501,322

=== county governments only (sheriff offices; the assignment is exact by construction)
county                    officers         pop   per 1k  mean monthly $
geo:US:county:51087            647     334,760     1.93           6,981   Henrico County (VA)
geo:US:county:36059          2,503   1,381,715     1.81          11,180   Nassau County (NY)
geo:US:county:24005          1,471     844,703     1.74          12,915   Baltimore County (MD)
geo:US:county:13121             65   1,079,105     0.06           5,144   Fulton County (GA)
all county areas with sworn police in 2023: 1,485; total sworn officers 170,816
```

**New York County's 21.94 per 1,000 is an artifact and is labelled as one.** ASPEP assigns each municipal
government to one county, so New York City books all 35,056 NYPD officers to New York County, whose
1.6M residents are a quarter of the city it polices. The officer count is right; the rate is not. The
county-government-only view avoids this by construction, because a county government's jurisdiction *is*
the county, and it shows a 32-fold spread in sheriff staffing among large counties (1.93 in Henrico
County, Virginia against 0.06 in Fulton County, Georgia, where the municipalities police instead).

The payroll column is what makes this new. **Baltimore County pays its deputies $12,915 a month against
Fulton County's $5,144** — a 2.5-fold difference in the price of the same published function, in the
same year, on the same measurement basis. No dataset in this catalog could state that before.

### Q3 — What did local government stop doing between two censuses?

`census_aspep` alone: full-time local-government employment by function, 2012 Census of Governments
against 2022, comparing two censuses rather than a census to a sample.

```
function                                                    2012        2022  change %
Solid Waste Management                                    96,629      92,125      -4.7
Education - Higher Education Instructional                88,339      84,709      -4.1
Electric Power                                            73,298      70,532      -3.8
Corrections                                              248,295     243,211      -2.0
Highways                                                 266,753     263,182      -1.3
Total - All Government Employment Functions            10,616,251  11,094,794      +4.5
Police Protection - Persons with Power of Arrest         611,274     624,372      +2.1
Hospitals                                                490,946     520,644      +6.0
Fire Protection - Firefighters                           295,050     321,534      +9.0
Transit                                                  182,244     202,230     +11.0
Parks and Recreation                                     148,525     164,922     +11.0
Financial Administration                                 210,342     238,317     +13.3
Fire Protection - Other                                   26,785      40,923     +52.8
```

Local government grew 4.5% over the decade while **shrinking in five functions**: solid waste,
higher-education instruction, electric power, corrections and highways. Sworn police grew 2.1%, less
than half the overall rate, while non-sworn police grew 8.9% and non-firefighter fire staff grew 52.8%.
That last figure is large enough to be a classification change rather than hiring, and is flagged as
such rather than reported as growth: the 2012 base of 26,785 is small, and ASPEP's split between
"Firefighters" and "Fire Protection - Other" depends on how a respondent classifies its own staff.

### The queries

All three questions, the join measurement and the layout validation are runnable scripts in the repo.
Each reads the published gzip artifacts directly through `manifests/latest.json` and joins only on
`geo:US:state:` and `geo:US:county:` keys.

| Script | Produces |
| --- | --- |
| [`examples/public-employment-queries.py`](../../examples/public-employment-queries.py) | Q1, Q2 and Q3 above (`q1`, `q2`, `q3` or no argument for all) |
| [`examples/public-employment-joins.py`](../../examples/public-employment-joins.py) | the coverage tables and every join rate in this log |
| [`examples/public-employment-layouts.py`](../../examples/public-employment-layouts.py) | the per-vintage record widths, FIPS agreement and median-wage curve |

To reproduce:

```sh
python3 -m worldmodel acquire census_aspep --allow-network     # 0.2411 GiB, 31 shards
python3 -m worldmodel acquire opm_fedscope --allow-network     # 1.372 GiB, 71 shards
python3 -m worldmodel run census_aspep                         # 19,116,854 records
python3 -m worldmodel run opm_fedscope                         # 11,724,597 records
python3 -m worldmodel verify census_aspep/normalized
python3 -m worldmodel verify opm_fedscope/normalized
python3 -m unittest tests.test_public_employment_datasets       # 12 offline fixture tests
```

## Operational notes worth carrying forward

- **`www2.census.gov` rate-limits per IP with Cloudflare error 1015.** Probing 33 years x 4 candidate
  paths tripped it, and it stayed tripped for **roughly 40 minutes** at one request per two minutes.
  The acquisition block now requests at 0.1/s with 12 retries and up to 10 minutes of backoff. Probe
  candidate URLs sparsely, or from the landing pages rather than by brute force.
- **A 200 on HEAD is not evidence a file exists.** `2022/census-apes/2022_individual_unit_file.zip`
  answers 200 to HEAD and then serves an F5 WAF *"Request Rejected"* page to GET.
- **Two long builds were discarded at publication** by `Code changed during execution; rerun with
  stable code`, because other agents were editing `worldmodel/*.py` during the run. Both datasets were
  finally built by copying `worldmodel/` to a scratch directory and running
  `python3 -m worldmodel --catalog-root <repo>/data --data-root <repo>/data run <id>` from there, so
  provenance captures a snapshot that cannot change mid-build. The captured hashes are of that copy,
  which is the code that actually ran.
