# WS-F: rebuild FEC under the declared non-commercial purpose

Commit `93208b6` added the rights mechanism (`docs/use-policy.md`, `worldmodel/rights.py`) that
lets a source declare `person_level_records = {policy: conditional, condition: non_commercial_use}`
and an operator declare `WM_COMMERCIAL_USE=0`. The mechanism decides what a pipeline *writes*, so
it changes nothing already on disk. This workstream rebuilds the three FEC contribution datasets
under the declared purpose, verifies the result, measures what it cost and shows what it answers.

Units are GiB throughout (`1 GiB = 2^30` bytes).

## 1. The declaration, before touching anything

```
$ python3 -m worldmodel use-policy
declared_purpose: non_commercial          (WM_COMMERCIAL_USE=0, loaded from .env)
```

| Dataset | `policy` | `condition` | `identified_persons_retained` |
| --- | --- | --- | --- |
| `fec_individual_contributions` (2026 cycle) | conditional | non_commercial_use | **true** |
| `fec_individual_contributions_2022` | conditional | non_commercial_use | **true** |
| `fec_individual_contributions_2024` | conditional | non_commercial_use | **true** |

Authority on all three: `52 U.S.C. 30111(a)(4)`.

## 2. A bug found before the rebuild: two datasets declared a rule their pipeline never read

`wm use-policy` reported `identified_persons_retained: true` for all three datasets, and all three
`dataset.json` descriptions already said "contributor-level rows are emitted only under a declared
non-commercial purpose". But only `data/fec_individual_contributions/pipeline.py` actually called
`worldmodel.rights`. The 2022 and 2024 pipelines were byte-identical to each other and to the
*pre-mechanism* version of the 2026 pipeline (`md5 5a300b8f…` for both, against `c345b581…`): they
had no `rights` import, no `contribution` family and no `rights_decision` attribute. Rebuilding
them would have produced the same aggregate-only output and reported success.

So the declaration and the behaviour disagreed for two of the three datasets. Because the reporting
path (`use-policy`, which reads `dataset.json`) is separate from the writing path (the pipeline),
nothing caught it.

Fix (commit `11117e2`): port the 2026-cycle pipeline verbatim to both, so all three are now
`md5 c345b581…`, and add
`tests.test_politics_procurement_datasets.FecTests.test_every_contribution_cycle_dataset_honours_the_same_declared_purpose`,
which builds both per-cycle datasets under `WM_COMMERCIAL_USE=0` and `=1` and asserts the
contributor rows appear and disappear while every aggregate value stays identical. That test is the
regression guard for the class of bug, not just this instance.

The same commit raises `stages[0].validation.max_rows` from 40,000,000 to 100,000,000 on all three,
because contributor rows push two of the stages past the old ceiling (measured counts in §3).

## 3. What the raw files contain (measured, not estimated)

Counted by streaming each `indivYY.zip` member `itcont.txt` through the pipeline's own field,
memo and entity-type filters:

| Dataset | raw rows | memo (`MEMO_CD=X`) excluded | non-individual excluded | malformed | **contributor rows to emit** |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fec_individual_contributions` (indiv26) | 31,894,597 | 220,044 | 16,245 | 0 | **31,658,308** |
| `fec_individual_contributions_2024` (indiv24) | 58,208,756 | 199,406 | 23,259 | 0 | **57,986,091** |
| `fec_individual_contributions_2022` (indiv22) | 63,885,795 | 146,976 | 20,475 | 0 | **63,718,344** |

Aggregate rows already on disk, for reference (these are what the whole artifact was before):

| Dataset | `committee_state_month` | `zip3_month` | `committee_occupation` | `committee_size_band` | total |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026 cycle | 1,475,492 | 90,886 | 135,980 | 61,950 | 1,764,308 |
| 2022 | 1,712,458 | 111,208 | 141,292 | 61,812 | 2,026,770 |
| 2024 | 1,806,600 | 93,412 | 138,748 | 62,076 | 2,100,836 |

So the rebuilt stages carry 33,422,616 / 60,086,927 / 65,745,114 records — 19x, 29x and 32x the row
count of the aggregate-only builds.

_(sections 4-7 are filled in as each rebuild lands)_
