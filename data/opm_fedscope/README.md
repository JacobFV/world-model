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

Declared sizes on the OPM page total **1.372 GiB**. Each archive holds one fact file
(`FACTDATA_<MMMYYYY>.TXT`, `ACCDATA_FY…​.TXT` or `SEPDATA_FY…​.TXT`, 81-153 MB uncompressed), its
`DT*.txt` dimension tables and a documentation PDF. The public employment series stops at June 2022 as
published; nothing newer is offered on that page.

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

Person-level rows are not a rights problem here; they are a volume problem. 63 employment cubes hold
roughly 137 million employee rows, so the pipeline aggregates each cube **while streaming** into
marginal cells and never re-emits a row. Every cell carries the headcount, the sum of published annual
salaries and the number of employees whose salary was published.

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
  numeric state codes become `geo:US:state:<FIPS>`. Territories and foreign duty stations get
  `opm:duty_location:<code>` entities rather than a guessed FIPS or ISO code, because neither OPM nor
  this catalog publishes that crosswalk. No county-level federal employment is available from this
  source.
* **FedScope agency codes are not Treasury CGAC codes.** `usaspending` keys agencies as
  `usgov:agency:<CGAC>`; FedScope uses OPM EHRI agency and sub-element codes. No crosswalk between them
  ships with either source, and matching them by agency name would be exactly the inferred name matching
  this catalog measured at 0.4% recall and 2.3% precision. No identity link is asserted; the entity
  attributes say so explicitly.

## Rebuild

```sh
python3 -m worldmodel acquire opm_fedscope --allow-network
python3 -m worldmodel run opm_fedscope
python3 -m unittest tests.test_public_employment_datasets.OpmFedScopeTests
```
