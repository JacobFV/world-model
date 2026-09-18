# opm_fedscope

OPM FedScope cubes: the federal civilian workforce as OPM publishes it, one source row per employee
(employment cubes) or per hire and separation (accession and separation cubes).

## Source and scope

One `files` acquisition ([dataset.json](dataset.json)), 71 shards from the OPM datasets library at
`https://www.opm.gov/data/datasets/Index.aspx?tag=FedScope`. `https://www.fedscope.opm.gov/` now
redirects to `https://data.opm.gov/`, a Blazor application with no bulk endpoint; the datasets library
is where the cube archives actually live, behind opaque UUID paths, so each URL is pinned in the
declaration and each shard is renamed to `fedscope_<kind>_<period>.zip`.

| Kind | Shards | Period |
| --- | --- | --- |
| Employment cube | 63 | September 1998-2009 annually, then quarterly March 2010 - June 2022 |
| Accession cube | 4 | FY2005-FY2009, FY2010-FY2014, FY2015-FY2019, FY2020-FY2024 |
| Separation cube | 4 | the same blocks |

Acquired: **71 shards, 1,473,120,618 bytes = 1.372 GiB**, matching the sizes declared on the OPM page.
Each archive holds one fact file (`FACTDATA_<MMMYYYY>.TXT`, `ACCDATA_FY….TXT` or `SEPDATA_FY….TXT`,
81-153 MB uncompressed), its `DT*.txt` dimension tables and a documentation PDF. The public employment
series stops at June 2022 as published; nothing newer is offered on that page.

## What OPM publishes, and what it does not

A fact row is **one employee**, with agency and sub-element, duty location, occupational series, pay
plan and grade, banded age, education level, banded length of service, supervisory status, type of
appointment, work schedule, work status and **annual basic pay**. There is no name, no date of birth, no
duty address and no identifier of any kind: the public cube is OPM's de-identified extract of EHRI.
`source.person_level_records` is therefore declared `prohibited` — the restrictive default — because
there is nothing identified to retain and no permission is needed. Named federal salary rosters come
from FOIA aggregators rather than OPM and are out of scope.

Salary is blank for a material share of rows: **12.6%** of the 2,169,629 rows in the June 2022
employment cube, measured. Those rows count toward headcount and not toward the salary total, and the
denominator is published as its own metric so a mean can be computed rather than implied.

## Normalized evidence (`normalized`, gzip JSONL)

**11,724,597 records** measured: 11,721,736 observations, 2,051 entities, 810 assertions, over 63
employment cubes (September 1998 - June 2022) and 19 fiscal years of flows (FY2005-FY2023). 926 agency
entities, 867 occupational-series entities, 51 state entities and 206 deliberately unjoined duty
locations.

Person-level rows are not a rights problem here; they are a volume problem. 63 employment cubes hold
roughly 137 million employee rows, so the pipeline aggregates each cube **while streaming** into
marginal cells and never re-emits a row. Every cell carries the headcount, the sum of published annual
salaries and the number of employees whose salary was published.

The aggregation is lossless at the margin, and that is checked rather than asserted: for June 2022 the
`location` cells and the `agency` cells **each sum to 2,169,629 employees**, exactly the row count of
`FACTDATA_JUN2022.TXT`. The blank-salary share measured back out of the published records is **0.1261**
(1,896,004 of 2,169,629 carry a salary), matching the 273,625 blank rows counted in the raw file, and the
mean published annual salary is $95,744.

| Cell (`dimensions.cell`) | Key | Subject |
| --- | --- | --- |
| `agency_location` | agency sub-element x duty location | `opm:agency:<AGYSUB>` |
| `agency_occupation` | agency sub-element x occupational series | `opm:agency:<AGYSUB>` |
| `agency_pay_grade` | agency sub-element x pay plan and grade (employment cubes only) | `opm:agency:<AGYSUB>` |
| `agency_action` | agency sub-element x accession or separation type (flow cubes only) | `opm:agency:<AGYSUB>` |
| `agency` | agency sub-element total | `opm:agency:<AGYSUB>` |
| `location` | duty location total | `geo:US:state:<FIPS>` or `opm:duty_location:<code>` |

| Cube | Count metric | Salary total | Published-salary denominator |
| --- | --- | --- | --- |
| Employment | `federal_employees` (`people`) | `federal_annual_salary_total` (`USD`) | `federal_employees_with_published_salary` |
| Accession | `federal_accessions` | `federal_accession_salary_total` | `federal_accessions_with_published_salary` |
| Separation | `federal_separations` | `federal_separation_salary_total` | `federal_separations_with_published_salary` |

Employment cells are dated to their snapshot month. Flow cubes span five fiscal years, so their cells
are keyed by fiscal year (from the cube's own `DTefdate.txt`) and dated 1 October to 1 October.

Codes are decoded from the `DT*.txt` tables **inside each archive**, never from a table cached across
vintages: the 1998 cube has no `PP` column, formats salary as `"$42,709"`, and its `DTocc.txt` differs
from the 2022 one. Agencies (`opm:agency:<code>`, with `part_of` to the department code), occupational
series (`occ:opm:<series>`) and duty locations become entities.

## What does not join, and why

* **Duty location is a state, not a county.** `DTloc.txt` publishes two-digit FIPS state codes for the
  50 states and DC, and two-letter FIPS 10-4 style codes for territories and foreign countries. Only the
  numeric state codes become `geo:US:state:<FIPS>`, and all **51 of them match `census_geography`,
  `census_population`, `openfema`, `lehd_lodes` and `mit_election_returns` exactly (1.0000)**.
  Territories (`AQ` American Samoa, `CQ` Northern Marianas, `GQ` Guam, `RQ` Puerto Rico, `VQ` Virgin
  Islands), roughly 200 foreign country codes and a `**` unspecified code get
  `opm:duty_location:<code>` entities rather than a guessed FIPS or ISO code, because neither OPM nor
  this catalog publishes that crosswalk: **206 unjoined keys**, covering the 15.8% of geographic
  observations that do not reach a `geo:US:state:` key (2,185,536 of 2,596,409 do = 0.8418).
  **No county-level federal employment exists in this source at all**, so there is nothing to join at
  county grain at any rate.
* **FedScope agency codes are not Treasury CGAC codes.** `usaspending` keys agencies as
  `usgov:agency:<CGAC>`; FedScope uses OPM EHRI agency and sub-element codes. No crosswalk between them
  ships with either source, and matching them by agency name would be exactly the inferred name matching
  this catalog measured at 0.4% recall and 2.3% precision. No identity link is asserted; all 926 agency
  entities carry `crosswalk_to_cgac` saying so. The measured consequence: **0 of 51** FedScope state keys
  match anything in `usaspending`, because `usaspending`'s geography is county-level place of performance
  and it references no state keys. Two federal datasets covering federal spending and federal employment
  do not meet, because neither publishes a key at the other's grain.

## Rebuild

```sh
python3 -m worldmodel acquire opm_fedscope --allow-network
python3 -m worldmodel run opm_fedscope
python3 -m unittest tests.test_public_employment_datasets.OpmFedScopeTests
```
