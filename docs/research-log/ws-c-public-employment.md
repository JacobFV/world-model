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

## Acquisition and build

*(measured figures below are filled in as each run completes; see the closing section)*

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

## Coverage, records and measured join rates

*(filled in below as runs complete)*
