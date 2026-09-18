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

`skip_statuses: [404]` lets a year the Bureau has not published (1996 has no individual unit file, only
a federal summary table) be skipped instead of failing the run. `www2.census.gov` sits behind a
Cloudflare zone rate limit that answers repeated requests with HTTP 429 and body `error code: 1015`, so
the block spaces requests 10 s apart and backs off up to 10 minutes.

Each archive holds two fixed-width ASCII members plus that year's technical documentation:

* `<yy>empid.txt` — the unit directory: 14-character Individual Unit ID, name, unit type, Census
  region, county name, **FIPS state (positions 110-111) and FIPS county (112-114)**,
  population/enrollment/activity code, school level, probability of selection. 2021 onward adds a
  6-digit *New Individual Unit ID*.
* `<yy>empst.txt` — the data: one record per unit and item code (functional category), with full-time
  and part-time employees and payroll, each with a data flag. Records through 2016 are 94 characters
  and also carry part-time hours and full-time-equivalent employees; 2017 onward are 80 characters and
  reuse positions 75-80 for the New Individual Unit ID.

**Payroll is the 31-day monthly equivalent for the month of March**, not an annual figure. Employment
is a March headcount. `valid_from`/`valid_to` are that March, and `dimensions.reference_period` says so.

**Census years enumerate; other years sample.** The Census of Governments covers every government unit
in years ending in 2 and 7 (1997, 2002, 2007, 2012, 2017, 2022 in this series). Every other year is a
probability sample of local governments — state governments are always fully enumerated. Every record
carries `dimensions.collection_basis` (`census_of_governments` or `annual_sample`), and the unit's own
`attributes.probability_of_selection` and `attributes.unit_enumerated_not_sampled`. A sampled year is
not a census and this dataset never presents one as one; the published probability of selection is
carried through rather than turned into a weight the Bureau did not publish.

## Normalized evidence (`normalized`, gzip JSONL)

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
thing that tells them apart.

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

## Rebuild

```sh
python3 -m worldmodel acquire census_aspep --allow-network
python3 -m worldmodel run census_aspep
python3 -m unittest tests.test_public_employment_datasets.CensusAspepTests
```
