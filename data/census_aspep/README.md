# census_aspep

Annual Survey of Public Employment & Payroll (ASPEP) **individual unit files** from the U.S. Census
Bureau: what every surveyed state and local government employs and pays, by functional category, year
by year.

## Source and scope

One `files` acquisition ([dataset.json](dataset.json)), one shard per survey year, named
`aspep_<year>.zip` so the pipeline reads the year from the shard rather than guessing it from the
two-digit member names (`23empid.txt` is 2023; `94empid.txt` is 1994).

The publisher has used three naming conventions, all under
`https://www2.census.gov/programs-surveys/apes/datasets/`:

| Years | Path |
| --- | --- |
| 1993-2014, 2016 | `<year>/annual-apes/<year>_downloadable_data.zip` |
| 2015 | `2015/annual-apes/2015_individual_unit_files.zip` |
| 2017-2021, 2023-2024 | `<year>/<year>_individual_unit_files.zip` |
| 2022 | `2022/2022 COG-E Individual Unit Files.zip` |

`skip_statuses: [404]` lets a year the Bureau has not published be skipped instead of failing the run.
**1996 is the only such year** — it offers a federal summary table and no individual unit file — so the
acquisition is 31 shards from 32 requests, totalling **258,896,973 bytes (0.2411 GiB)**, measured.
`www2.census.gov` sits behind a Cloudflare zone rate limit that answers repeated requests with HTTP 429
and body `error code: 1015`, so the block spaces requests 10 s apart and backs off up to 10 minutes.

Each archive holds two fixed-width ASCII members plus that year's technical documentation:

* `<yy>empid` — the unit directory: 14-character Individual Unit ID, name, unit type, Census region,
  county name, **FIPS state (positions 110-111) and FIPS county (112-114)**,
  population/enrollment/activity code, school level, probability of selection. 206 characters through
  2020, 213 from 2021 (which adds a 6-digit *New Individual Unit ID*); every field this pipeline reads
  sits at the same position in both.
* `<yy>empst` — the data: one record per unit and item code (functional category), with full-time and
  part-time employees and payroll, part-time hours and full-time-equivalent employees.

### Three packagings and two layouts, neither announced in the file

| Years | Packaging |
| --- | --- |
| 1993-2011 | a **nested ZIP** inside the archive, holding one `.DAT` (`01empid.zip!01empid.dat`) |
| 2012-2013 | a `.dat` in a subdirectory (`2012_downloadable_data/Individual Unit File/12cempid.dat`) |
| 2014-2024 | a plain `.txt`, in a subdirectory in 2015 |

Census-of-governments years prefix the member name with `c`. The shared raw readers cannot descend into
a ZIP inside a ZIP, so the pipeline opens each archive itself and resolves exactly one nested level.

| Data record width | Years | Layout |
| ---: | --- | --- |
| 84 | 1993-2006 | **unflagged** |
| 96 | 2007-2011 | flagged |
| 94 | 2012-2018 | flagged |
| 72 | 2019-2020 | flagged, no part-time hours or full-time equivalents |
| 80 | 2021-2024 | flagged, with the New Individual Unit ID |

The pre-2007 record publishes **no data flags at all**, so every payroll and part-time field sits two
positions to the left of the documented layout. Reading it with the documented positions does not
raise — the straddled slices still parse as integers for 93-97% of rows — it silently returns wrong
numbers. The pipeline therefore picks the layout from the modal record width, **refuses an unseen width
rather than guessing its columns**, and checks the choice against the bytes: a flagged width must carry
letters where the flags belong and an unflagged width must not.
`examples/public-employment-layouts.py` reproduces the validation that established this.

**Payroll is the 31-day monthly equivalent for the month of March**, not an annual figure. Employment
is a March headcount. `valid_from`/`valid_to` are that March, and `dimensions.reference_period` says so.

**Census years enumerate; other years sample.** The Census of Governments covers every government unit
in years ending in 2 and 7 (1997, 2002, 2007, 2012, 2017, 2022 in this series). Every other year is a
probability sample of local governments — state governments are always fully enumerated. Every record
carries `dimensions.collection_basis` (`census_of_governments` or `annual_sample`), and the unit's own
`attributes.probability_of_selection` and `attributes.unit_enumerated_not_sampled`. A sampled year is
not a census and this dataset never presents one as one; the published probability of selection is
carried through rather than turned into a weight the Bureau did not publish.

The difference is eight-fold and measured: census years carry **79,255-91,274 units** (2022 and 2017)
and sample years **10,464-14,137**. Probability of selection is exactly 1.0 for every unit in the six
census years and below 1 in all 25 others, which is how the distinction was confirmed from the data
rather than from the calendar.

## Normalized evidence (`normalized`, gzip JSONL)

**19,116,854 records** measured: 18,905,284 observations, 105,487 entities, 106,083 assertions, over
**102,270 government units** and 31 survey years.

Government units are `government_agency` entities keyed `aspep:unit:<14-character Individual Unit ID>`.
Shards are read newest year first, so each unit's single entity record carries its most recent
published name; `attributes.first_survey_year`, `last_survey_year` and `survey_years_present` record the
span it was actually observed in, and `label_from_survey_year` says which year the label came from.

| Metric | Unit | Dimensions |
| --- | --- | --- |
| `government_employees` | `people` | `function_code`, `function`, `employment_status` (`full_time`, `part_time`, `full_time_equivalent`), `survey_year`, `collection_basis`, `government_type`, `government_level`, `reference_period` |
| `government_payroll` | `USD` | the same; `attributes.payroll_basis` is `31_day_monthly_equivalent_for_march` |
| `government_part_time_hours` | `hours` | the same; published only through 2016 |
| `government_unit_population` | `people` | `reference_year`, for state, county, municipal and township units |
| `school_enrollment` | `people` | `school_level`, for independent school districts and education service agencies |

Zero-valued measures are omitted except under item code `000` (the all-functions total), which is
always emitted; `attributes.zero_values_omitted_except_total` records the rule. Every value keeps its
publisher data flag in `attributes.data_flag` and its class in `attributes.data_flag_class`
(`reported`, `imputed` or `unknown`, per technical documentation section 2.4). ASPEP imputes rather than
suppresses, so no cell is withheld — but an imputed value is not a response, and the flag is the only
thing that tells them apart. Measured: **7,197,660 reported, 1,842,105 imputed, 105,605 unrecognized**,
and 9,236,250 with no flag — the 1993-2006 vintages, which publish none (`data_flags_published: false`),
plus the full-time-equivalent column, which has no flag position in any vintage. 40 distinct function
codes appear against the 33 in the 2023 code list, because older vintages use codes since retired; an
unknown code keeps its raw value and a null description rather than failing the build.

Fifteen `(unit, item code)` pairs — 14 in 1995 and one in 1999 — are printed on two source lines with
different values. They are components of one unit-function cell, not competing estimates of it, so they
are summed and the merge is recorded in `attributes.source_rows_merged`; 65 published observations carry
a value of 2 there. Emitting them separately would have left two observations sharing a subject, metric
and dimensions, which belief materialization reads as a conflict.

Special districts publish a function/activity code in the same field where other unit types publish
population, so that value is kept on the unit entity as
`attributes.special_district_activity_code` and never emitted as a count.

## The joins

The deterministic join is the **FIPS state and county published in the unit ID file**, not the internal
county code inside the 14-character ID (for Baldwin County, Alabama those are `002` and `003`
respectively). Each unit gets a `within` assertion to `geo:US:county:<5-digit FIPS>` where a county is
published and `geo:US:state:<2-digit FIPS>` otherwise, dated by the span of years the unit was observed
in that geography. Those keys are exactly the ones `census_geography`, `census_population`,
`usaspending`, `openfema` and `mit_election_returns` already use.

No name matching is done anywhere. A unit whose ID file omits a FIPS county gets a state-level
containment and nothing is guessed.

Measured join rates, exact key matches against each target's published normalized artifact:

| | Units contained | County keys matched |
| --- | --- | --- |
| any containment | 102,269 / 102,270 = 1.0000 | 3,164 distinct county keys, 50 state keys |
| at county granularity | 102,218 / 102,270 = 0.9995 | |
| `census_population` | 102,263 = 0.9999 | 3,148 / 3,164 = 0.9949 |
| `openfema` | 102,264 = 0.9999 | 3,142 / 3,164 = 0.9930 |
| `lehd_lodes` | 102,263 = 0.9999 | 3,139 / 3,164 = 0.9921 |
| `census_geography` | 102,019 = 0.9975 | 3,140 / 3,164 = 0.9924 |
| `mit_election_returns` | 102,093 = 0.9983 | 3,115 / 3,164 = 0.9845 |
| `usaspending` | 96,824 = 0.9467 | 2,845 / 3,164 = 0.8992 |

The 24 counties missing from `census_geography` are vintage differences, not failed joins: ASPEP spans
1993-2024 and `census_geography` is the 2024 vintage, so counties renamed, merged or recoded in between
(Connecticut's 2022 planning regions, Alaska borough changes, Virginia city independence) exist in ASPEP
and not in the current gazetteer. `worldmodel.crosswalks.UsGeography` carries dated county crosswalks for
exactly this; this dataset does not silently reassign them. `usaspending`'s lower rate is coverage: its
acquired slice is one fiscal year of awards, so a county with no award in it has no key to match.

ASPEP publishes no UEI, DUNS or EIN for a government unit, so a county government that receives federal
awards cannot be tied to its award records by identifier. The shared county FIPS supports geographic
comparison, not entity identity.

## Rebuild

```sh
python3 -m worldmodel acquire census_aspep --allow-network
python3 -m worldmodel run census_aspep
python3 -m unittest tests.test_public_employment_datasets.CensusAspepTests
```
