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

## 4. A second obstacle: a 27-minute build killed by a concurrent edit

The first rebuild attempt ran 27m17s and then failed with
`{"error": "Code changed during execution; rerun with stable code"}`. `worldmodel.provenance.
verify_code_snapshot` re-hashes every `worldmodel/**/*.py` at publish time, and one of the five
agents working in this tree edited a module during the run. Nothing was published; the staging
directory was cleaned up, so the cost was 27 minutes and no disk.

The fix is the pattern already present in this session's scratchpad: build against a frozen copy
of `worldmodel/` and of this dataset's own `dataset.json`/`pipeline.py`, writing into the real data
root. One detail matters for *this* workstream specifically — freezing bypasses the CLI startup
that loads `<project>/.env`, and `WM_COMMERCIAL_USE` is the entire point of the build, so the
builder loads the project env explicitly, prints the resulting decision before starting, and
refuses to run if `identified_persons_retained` is false. Every rebuild below logged

```
{"declared_purpose": "non_commercial", "identified_persons_retained": true}
```

before writing a byte. (`worldmodel.env.load_project_env` returns names, never values, so nothing
is printed that should not be.)

## 5. Size delta per dataset (measured)

The store is content-addressed, so a rebuild under a different declared purpose publishes a
*new* artifact and the aggregate-only one is retained — which is exactly what `docs/use-policy.md`
says should happen. The delta is therefore an addition, not a replacement.

| Dataset | aggregate-only artifact | rebuilt artifact | delta | dataset dir before → after |
| --- | ---: | ---: | ---: | --- |
| `fec_individual_contributions` | 0.0309 GiB | **1.4472 GiB** | **+1.4473 GiB** | 2.0673 → 3.5146 GiB |

Rebuilt `fec_individual_contributions` = version `4c5960d6…` (the aggregate-only one is
`75a9e71b…`), `records.jsonl.gz` = 1,551,024,762 bytes for 33,422,616 rows, build time 27m18s.

Cost per contributor row: **about 48 bytes gzipped**. That is far less than the ~1,649 bytes each
record occupies uncompressed, because the ~700-byte `rights_decision` and `use_restriction` block
repeats verbatim on every row and gzip's window absorbs it. The honest reading is that carrying the
decision on every record is nearly free on disk, not that the rows are small.

**The fair-share budget does not move.** `worldmodel.budget.scan_bytes` counts only
`artifacts/raw/` plus in-progress acquisition staging, so normalized output is outside the 25 GiB
per-dataset fair share entirely. `wm budget` reports the same 2.036 / 4.858 / 3.953 GiB for the
three datasets before and after. This rebuild is a disk cost, not a budget cost, and any claim that
it "fits in the budget" would be measuring the wrong thing.

Transient cost worth knowing: the runner's duplicate-ID audit is a SQLite table with one row per
record ID, which reached ~5 GB of staging for the 2026 cycle and is deleted before publish.

## 6. Verification

For each dataset, a SHA-256 is taken over every aggregate row's `id`, `subject`, `metric`, `value`,
`unit`, `dimensions`, `valid_from`, `valid_to`, `observed_at` and `evidence`, before and after.
`attributes` is deliberately excluded from the digest, because the mechanism *does* add
`rights_decision` to aggregate attributes — the claim under test is that no aggregate **number or
key** moved.

| Dataset | contributor rows | aggregate rows | aggregate digest before | after | unchanged |
| --- | ---: | ---: | --- | --- | --- |
| `fec_individual_contributions` | **31,658,308** | 1,764,308 | `34f17ebd…` | `34f17ebd…` | **yes** |

Contributor rows for the 2026 cycle, all checks passing with zero exceptions:

- `attributes.rights_decision` present on **all 33,422,616 rows**, aggregate and contributor alike,
  and exactly **one distinct decision** across the whole artifact (policy `conditional`, condition
  `non_commercial_use`, authority `52 U.S.C. 30111(a)(4)`, declared purpose `non_commercial`).
- Every contributor row is a plain `observation` with metric `individual_contribution_amount` and
  subject `fec:committee:C########`. **No row has a person subject, a `same_as` predicate, a
  resolved contributor id, or any `person`/`contributor_id` field.**
- Identity travels only as reported strings in `dimensions`: `contributor_name`, `contributor_city`,
  `contributor_zip`, `contributor_employer`, `contributor_occupation`, `contributor_state`, plus
  `month`, `transaction_type`, `cycle`.
- `attributes.identity_basis` = "contributor fields as reported by the filer; no persistent person
  identity is asserted" on every contributor row.
- The manifest records `rights.declared_purpose: non_commercial`.

**No entity resolution was added over contributor names, and none should be.** The measured result
on this catalog is 0.4% recall at 2.3% precision, wrongly merging 103,211 entities. The same name in
two cycles remains two reported strings here.

## 7. What the contributor rows answer that the aggregates could not

All numbers below are from the rebuilt 2026-cycle artifact `4c5960d6…`: 31,658,308 contributions
totalling **$5,883,031,103**, of which 31,170,821 rows (98.5%) carry an employer string and
31,637,531 rows (99.93%) carry a usable five-digit ZIP.

### 7a. Geographic concentration at full ZIP — deterministic, nothing inferred

This is the one that needs no join and no inference: the published aggregate was
`zip3_month`, and the contributor rows carry `contributor_zip` as reported.

| Level | units with dollars | HHI | effective units (1/HHI) |
| --- | ---: | ---: | ---: |
| ZIP3 (what the aggregate published) | 988 | 0.010801 | 92.6 |
| Full ZIP (what the rebuild retains) | 35,990 | 0.001557 | 642.4 |

Only **29 rows in 31.7 million** had three usable ZIP digits but not five (a further 20,748 had no
usable ZIP at all), so the ZIP3 aggregate was discarding precision it already held on 99.93% of rows.

What that hid, concretely. The largest ZIP3 is **100** (Manhattan) at $327,265,220 — and it is
genuinely diffuse: 55 full ZIPs, the largest (10010) only 9.95% of it. But the largest *full* ZIP in
the country is **19004** (Bala Cynwyd, PA) at **$94,613,605 — 1.617% of every itemized individual
dollar in the cycle, from one five-digit ZIP**, nearly three times the largest Manhattan ZIP. ZIP3
`190` reported it mixed in with the rest of suburban Philadelphia. The next two are 60044
(Lake Forest, IL, $67.9M) and 78734 (Lakeway, TX, $61.4M). A national top-ten-ZIP list is a
different object from a national top-ten-ZIP3 list, and only one of them was previously derivable.

Caveat, stated rather than hidden: a full ZIP is **not** a census geography. `docs/identity-units-
crosswalks.md` lists the population-weighted ZCTA↔county crosswalk as *declared for acquisition, not
loaded*, so these ZIPs cannot yet be apportioned to counties or CBSAs without assuming a mapping.
The concentration figures above are over ZIP codes as reported, which is what the data supports.

### 7b. The employer distribution — and why 38% of the money has no employer to join

Before any join: the seven largest normalized employer keys are not employers.

| Reported employer (normalized) | dollars | contributions |
| --- | ---: | ---: |
| NOT EMPLOYED | $816,596,199 | 7,317,017 |
| RETIRED | $615,866,804 | 11,709,168 |
| SELF EMPLOYED | $344,231,378 | 1,048,183 |
| N A | $189,311,972 | 1,160,348 |
| SELF | $122,865,595 | 395,410 |
| NONE | $98,020,157 | 1,136,279 |
| HOMEMAKER | $31,241,645 | 45,107 |

Those plus UNEMPLOYED account for **$2,227,724,391 = 37.9% of all itemized individual dollars and
72.1% of all contribution rows.** A further 487,487 rows ($155.8M) report no employer string at all.
So the ceiling on *any* employer-based analysis of this source is roughly 60% of the dollars, before
join quality enters at all. This is the single most important number in this section and it is not
an artifact of our method — it is what filers report.

Across the remaining rows there are **403,453 distinct normalized employer keys**.

### 7c. Employer → public-company and federal-contractor joins — INFERRED, and quantified

**The join is inferred.** There is no published crosswalk from an FEC-reported employer string to a
CIK or a SAM.gov UEI. The method is: uppercase, strip punctuation, drop a trailing legal suffix
(`INC`, `LLC`, `CORP`, `CO`, `LP`, `LTD`, `PLC`, `HOLDINGS`, `GROUP`, …) repeatedly, drop a leading
`THE`, collapse whitespace — then require exact equality of the resulting key. It is a documented,
lossy folding, not an identity assertion, and nothing from it was written into the catalog.

Coverage:

| Reference corpus | keys in corpus | FEC employer keys matched | contributions | dollars | share of all itemized $ |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sec_issuer_reference` (8,022 listed issuers) | 8,003 | 2,633 (32.9%) | 1,036,904 | $339,588,078 | 5.77% |
| `usaspending` prime-contract recipients | 113,442 | 12,826 (11.3%) | 1,496,515 | $497,711,288 | 8.46% |
| `lda_lobbying` clients | 23,402 | 8,037 (34.3%) | 2,119,654 | $886,968,833 | 15.08% |

**Measured precision.** Two samples of matched keys were drawn and adjudicated by hand against the
reported employer strings and contributor states, for the SEC join:

- *Dollar-weighted*, n=40 (seed 7): **40/40** keys map to the right issuer. Two (`FOX`,
  `AMERICAN FINANCIAL`) are correct for the issuer but aggregate some raw strings generic enough to
  plausibly denote a different firm — within-key contamination, not a wrong key.
- *Uniform over matched keys*, n=40 (seed 11): **39/40 correct, 1 wrong.** The failure is `AGI`
  → `sec:cik:0002081206` "AGI Inc": 87 contributions from WA and CA folded onto a newly registered
  issuer that shares a three-letter acronym. Two more (`BOX`, `DOVER`) show within-key contamination.
  Key-level precision 0.975; Wilson 95% interval roughly 0.87–1.00 at n=40.

So the SEC join is about **97-100% precise at the key level on a sample of 80**, which is a small
sample and should be read as "the errors are rare and are of a specific kind", not as a tight
estimate. The specific kind is short keys: the 123 matched keys of ≤3 characters carry 3.95% of the
matched dollars and are where essentially all the risk sits. A length floor would remove most of it.

Two structural bounds that need no sampling:

- **Name collisions inside the reference corpus.** 10 of 8,003 SEC keys (0.12%) map to more than one
  CIK. For USAspending it is 2,718 of 113,442 (**2.4%, twenty times worse**), because the recipient
  corpus is 14x larger and full of similarly-named small firms.
- **Short keys.** For USAspending, keys of ≤5 characters carry **26.5% of the matched dollars**
  against 9.3% for SEC. The USAspending join is materially the weaker of the two and its numbers
  should carry a wider error bar.

**A demonstrated false positive, in full.** The key `CITADEL` is fed by FEC employer strings
`CITADEL` (109), `CITADEL LLC` (14), `THE CITADEL` (7) and `CITADEL, LLC` (4), from contributors in
FL (46), NY (40), IL (21) and CT (10) — Citadel LLC, the investment firm. The USAspending match is
`uei:XYZHM6HA92E5` labelled **"THE CITADEL"**, the military college in Charleston, South Carolina,
which supplies only 5 of the 134 contributions. $8,675,481 is attributed to the wrong organisation.
The *same* key joins **correctly** to `lda:client:149114` "CITADEL, LLC". The lesson is that an
inferred name join's precision is a property of the reference corpus it is pointed at, not of the
key function — so one measured precision figure cannot be carried from one join to another.

**What is not measured, and cannot be from this source.** Whether a contributor actually worked
where they said they did: the FEC does not validate the employer field, so every figure here is
"dollars from people who *reported* this employer". And recall is unknown in the direction that
matters — only 32.9% of listed issuers matched anything, and an employee who writes a subsidiary's
name, a trading name or an abbreviation is silently absent.

**The finding the aggregates could not produce.** Employer-level giving splits into two regimes that
`committee_size_band` (keyed by committee, not employer) could never separate:

| Employer (inferred → SEC issuer) | dollars | contributions | mean |
| --- | ---: | ---: | ---: |
| UNITED PARCEL SERVICE INC | $1,501,041 | 50,746 | **$30** |
| BOEING CO | $3,626,125 | 48,124 | **$75** |
| HONEYWELL INTERNATIONAL INC | $4,840,474 | 54,025 | **$90** |
| LOCKHEED MARTIN CORP | $3,519,637 | 25,535 | $138 |
| MICROSOFT CORP | $4,624,468 | 22,547 | $205 |
| SPACE EXPLORATION TECHNOLOGIES CORP | $42,127,756 | 26,243 | $1,605 |
| Blackstone Inc. | $34,841,962 | 1,442 | $24,162 |
| Energy Transfer LP | $12,681,859 | 103 | $123,125 |
| LAS VEGAS SANDS CORP | $25,468,629 | 101 | **$252,165** |

A four-orders-of-magnitude spread in mean contribution size, from $30 to $252,165. The low-mean /
high-count employers are payroll-style employee giving across a large workforce; the high-mean /
low-count ones are a handful of principals reporting a company they are associated with. They are
different phenomena that sum to the same kind of total, and only contributor-level rows tell them
apart. The raw employer leaderboard makes the same point more bluntly: `ADELSON DRUG CLINIC` is
$40,000,000 over **2 contributions**, and `SIG` is $71,340,082 over **75**.

### 7d. Contribution timing against lobbying filing dates — a null result

Question: within the `lda_lobbying` window, do contributions from an employer spike in months when
a lobbying filing naming that client was posted? Join: the same **inferred** employer-name → LDA
client key as above, 8,037 matched keys, 8,015 usable after requiring at least one month of each
kind. Window 2025-01 to 2026-08, per-client-month.

| | dollars | client-months | mean per month |
| --- | ---: | ---: | ---: |
| months with a filing posted | $245,201,415 | 45,573 | $5,380 |
| all other months | $640,958,699 | 114,727 | $5,587 |

**Ratio 0.963 — no elevation.** 2,879 of 8,015 clients (35.9%) gave at a higher rate in filing
months, i.e. fewer than half, which is what no effect looks like. Individual clients move in both
directions and by a lot (Citadel Investment Group $2,095,633/mo in filing months against
$464,286/mo otherwise; OpenAI $95,786 against $1,862,896), so the aggregate null is not hiding a
uniform small effect.

Two reasons to treat this as a null result rather than evidence of no relationship. **The confound
is severe:** LD-2 filings are quarterly and post in calendar clusters, so "filing month" is close to
a fixed third-of-the-calendar indicator (45,573 of 160,300 client-months, 28.4%), and contribution
seasonality around quarter ends is confounded with it. A real test needs filing dates compared
against a same-period baseline, not against the rest of the calendar. **And the join is inferred**,
with the LDA corpus's own collision problem: 23,402 normalized client keys cover 32,355 client
entities, because the same client name is registered by multiple registrants. The honest summary is
that this question is now *askable* on this catalog — it was not before, since aggregates carry no
employer — and that the naive version of it returns nothing.


