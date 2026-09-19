# The influence panel

`influence_model` was the one estimation target blocked purely on assembly: LDA filings, FEC
money, BILLSTATUS and Voteview positions were all published here, and nothing put them in one
place. `influence_panel` does. It is a derived, versioned dataset
([declaration](../data/influence_panel/dataset.json), [README](../data/influence_panel/README.md),
builder [`worldmodel/panels/influence.py`](../worldmodel/panels/influence.py)) with one row per
legislator x Congress x chamber, and a companion stage with one row per measure.

Built 2026-09-18 from commit `9032f8b` (clean tree), 7 min 0 s wall clock, peak RSS 676 MiB.

| Stage | Version | Rows | Bytes (gzip) |
| --- | --- | --- | --- |
| `bills` | `878caedfd464…` | 113,703 measures + 1 construction row | 26.9 MB |
| `panel` (output) | `4562e7f9219e…` | 7,688 member-Congress-chamber rows (6,248 House, 1,440 Senate) + 1 construction row | 6.0 MB |

Pinned inputs (every one is recorded in both manifests):

| Dataset | Version | What the panel takes from it |
| --- | --- | --- |
| `congress_people` | `79e40ffa` | bioguide -> ICPSR and bioguide -> FEC candidate `same_as`; names; current committee membership |
| `voteview_rollcalls` | `fce7b752` | ICPSR -> bioguide `same_as`; service, party code, district; Nokken-Poole and DW-NOMINATE as published; roll calls and member positions (110th-119th) |
| `govinfo_billstatus` | `bd0448be` | sponsors, cosponsors, committee referrals (113th-119th) |
| `congress_gov_api` | `44cd88fe` | House roll-call events that name a measure (118th-119th) |
| `lda_lobbying` | `3eba146b` | LD-2 filings (2025-2026), their client and registrant IDs, amounts and specific-issue text |
| `fec` | `93722c10` | weball candidate receipts by source; pas2 committee-to-candidate money; committee type and interest-group category |
| `fec_candidates` | `a46824fd` | principal campaign committee per candidate and cycle |
| `fec_individual_contributions` / `_2022` / `_2024` | `4c5960d6` / `1f44d0ca` / `83a01f42` | itemized individual receipts of principal committees by the adapter's occupation proxy and size band (2026, 2022, 2024 cycles) |

Rebuild command, with every dependency pinned: [data/influence_panel/README.md](../data/influence_panel/README.md#rebuild).

## What a row holds

`panel` row `influence_panel:{bioguide}:{congress}:{chamber}`:

- `identity` — ICPSR IDs, FEC candidate IDs (all, and the chamber-matched ones by the ID's office
  letter), principal committees in the cycle, and which publisher asserted the ICPSR link.
- `roll_calls` — counts of yea / nay / present / not voting while a member, party-unity votes cast
  and party-line defections. A party-unity roll call is one where a majority of voting Democrats
  (Voteview 100) opposed a majority of voting Republicans (200) on yea/nay; a defection is a yea or
  nay against the member's own party majority.
- `ideal_points` — Nokken-Poole and DW-NOMINATE as Voteview publishes them.
- `receipts_by_source` — FEC weball cycle totals: individuals, other committees, parties, the
  candidate's own money and loans, total receipts, disbursements.
- `committee_contributions` — pas2 money to the member's chamber-matched candidate IDs: direct
  (24K, 24Z) by the giver's FEC committee type and by its FEC interest-group category (corporation,
  labor, membership, trade association, cooperative, corporation without capital stock),
  business-PAC direct money, and independent expenditures for (24E) and against (24A).
- `individual_itemized` — principal committees' itemized individual receipts by occupation proxy
  and size band. The occupation categories are the adapter's keyword rules, **not a published
  industry code**; the FEC publishes none.
- `sponsorship` — measures sponsored and cosponsored (withdrawn cosponsorships excluded).
- `lobbying_on_linked_bills` (119th only) — for the measures the member sponsored, cosponsored or
  voted on, and their union: the measures with at least one citing filing, distinct filings,
  clients and registrants, and attributed dollars.
- `committee_assignments` (119th only) — seats, titles, rank and side from the current file.
- `linked` — one flag per block, which the coverage below counts.
- `evidence` — per input `dataset@stage@version`: records contributing, SHA-256 over their ids,
  a sample of ids. 7,683 rows cite `congress_people`, 7,688 `voteview_rollcalls`, 7,596 `fec`,
  7,608 `fec_candidates`, 3,855 the `bills` stage, and 548-551 each itemized-individual dataset.

## Coverage

Share of rows with at least one record of each block linked, measured on `panel@4562e7f9`. A
blank-by-design block (outside a source's span) counts as not linked, so the per-Congress table is
the one to read.

**All rows (7,688) and all legislators (1,569):**

| Block | Rows | Legislators (linked in any of their rows) |
| --- | --- | --- |
| FEC candidate ID (chamber-matched) | 99.1% | 97.4% |
| FEC receipts by source (weball) | 98.4% | 96.9% |
| Committee-to-candidate money (pas2) | 98.7% | 97.1% |
| Roll-call positions | 71.9% | 85.6% |
| Sponsorship | 50.1% | 66.9% |
| Itemized individual money | 21.5% | 46.1% |
| Lobbying on linked bills | 7.2% | 35.4% |
| Committee assignments | 6.9% | 33.8% |

**By Congress, within each source's span** (House / Senate):

| Congress | Rows | FEC receipts | pas2 money | Positions | Sponsorship | Itemized individual | Lobbying | Committees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 106th-109th | 1,759 / 404 | 95.7-97.5% / 92.1-97.1% | 96.1-98.2% / 92.1-97.0% | not published | not published | not acquired | not acquired | not published |
| 110th-112th | 1,349 / 314 | 99.1-99.8% / 96.4-99.0% | 99.8% / 94.5-99.0% | 100% / 100% | not published | not acquired | not acquired | not published |
| 113th-116th | 1,783 / 412 | 99.3-99.5% / 98.1-100% | 99.3-100% / 98.1-100% | 100% / 100% | 99.3-100% / 100% | not acquired | not acquired | not published |
| 117th | 455 / 102 | 98.9% / 99.0% | 100% / 99.0% | 100% / 100% | 99.8% / 99.0% | 99.1% / 98.0% | not acquired | not published |
| 118th | 451 / 104 | 98.7% / 98.1% | 99.6% / 97.1% | 100% / 100% | 100% / 100% | 98.9% / 98.1% | not acquired | not published |
| 119th (in progress) | 451 / 104 | 99.1% / 98.1% | 99.3% / 98.1% | 100% / 100% | 99.6% / 100% | 99.6% / 98.1% | 100% / 100% | 95.8% / 95.2% |

Lobbying in the 119th, by relation (rows with at least one citing filing, of 451 House / 104 Senate
rows): sponsored 442 / 101 (median 10 / 27 measures), cosponsored 448 / 104, voted on 450 / 104.
"Voted on" is near-universal because almost every member voted on H.R. 1, which 3,216 clients cite, so the
union is 100% and is not informative on its own; the sponsored block is the discriminating one.

**Identity.** Every Voteview service row in the 106th-119th Congresses has a published bioguide
link (0 unmatched). One ICPSR ID (`icpsr:99883`) maps to two bioguide IDs across the two publishers
and is excluded rather than guessed. 1,738 FEC candidate IDs are crosswalked. The FEC gaps
(0.4-8% of rows by Congress) are members with no chamber-matched FEC ID in congress-legislators
or no weball row for that ID in the cycle; they are gaps, not zeros.

**The lobbying link.** 153,497 LD-2 activity reports for 2025-2026 were read (registrations
excluded) and 4,982 were superseded by a later amendment for the same registrant, client and
period, leaving 148,515. Among them 62,112 specific-issue
records cite at least one bill number: 132,474 filing-measure citations of 8,540 distinct measures.
2,385 citations (1.8%) state their Congress in the text (a public-law number or an ordinal right
after the number); the rest are **inferred** to be the 119th from the filing period. 8,280
BILLSTATUS 119th-Congress measures are cited. 76 cited measure IDs have no BILLSTATUS record: 72
in the 119th that this BILLSTATUS version does not hold, and 4 whose stated Congress lies outside
its 113th-119th span (one of them `199`, from a mistyped public-law number).

## Loaded for the influence family

`worldmodel.estimation.loaders.influence_data` turns the panel into the family's fit contract
(`unit` = bioguide, `period` = Congress, `date` = the day it ends). The two pre-registered attempts
read House rows of the 110th-118th Congresses: 3,971 rows on 1,072 members for the PAC-share
exposure (3,972 on 1,073 for the business-PAC exposure), 436-447 per Congress. Skipped: 2,210
rows outside the declared Congresses, 33 with fewer than 20 party-unity votes, 6 not of a single
major party, 27-28 without positive receipts. `python3 -m worldmodel estimation-load` reports
`influence: available`.

Both attempts **fail** (reports `1b210017…` and `8046310d…`, full record in
[calibration-status.md](calibration-status.md#influence-wave-the-panel-is-built-and-the-association-does-not-forecast)):
the exposure's within-member association with party-line defection is statistically distinguishable
from zero in sample and adds no out-of-sample skill; the two-way fixed-effects forecast is itself
beaten by persistence; and `no_revision_leakage` fails by declaration because FEC totals include
later amendments.

**What the family's forecaster needs that the panel cannot identify:** a lobbying exposure. The
family is declared on LDA filings, and the forecaster scores a next-period outcome given the
period's realized exposure, refitting at each origin on earlier periods. `lda_lobbying` holds filing
years 2025-2026 only, which is one Congress (the 119th) and an incomplete one, so there is no
earlier period to fit on and no complete period to score. At least three complete Congresses of
LD-2 filings (2019-2024 for a 116th-118th panel) would have to be acquired before a lobbying
exposure could be pre-registered.

## The brief: who moves on this bill

[`examples/graph-queries/q7_who_moves_on_this_bill.py`](../examples/graph-queries/q7_who_moves_on_this_bill.py),
saved output [`outputs/q7_who_moves_on_this_bill.json`](../examples/graph-queries/outputs/q7_who_moves_on_this_bill.json).
For one measure it lists the sponsor and cosponsors, the committees of referral and who holds their
seats, every roll call with the party tally and the members who voted against their own party
majority, the LDA clients and registrants whose filings cite the number, and the committee money
the positioned members received. Without `--bill` it picks the 119th-Congress measure cited by the
most LDA clients that has a roll call: H.R. 1 (8,111 filings, 3,216 clients, 1,399 registrants,
47 roll calls; 2 House Republicans against their party on passage, 3 Senate Republicans on Senate
passage). It carries `which_dataset_supplied_which_edge` and `what_this_does_not_establish`.

## What the panel does not establish

- **No influence and no causation.** Rows put money, lobbying and votes side by side. A filing that
  cites a bill the member sponsored is not a contact with the member; a contribution is not a vote.
- **Lobbying and money are never joined to each other.** No published crosswalk links an LDA client
  to an FEC committee (the connected-organization field is a name), so the panel does not say which
  lobbying client also gave money.
- **Lobbying covers the 119th Congress only** and committee assignments cover the 119th only.
  Earlier rows carry `null`, meaning *not acquired / not published*, not *none*.
- **The Congress of a cited bill number is inferred** for 98.2% of citations. A filer citing an older
  bill with the same number is counted against the 119th-Congress measure.
- **Attributed lobbying dollars are an allocation**: equal split of a filing's reported income or
  expenses across the distinct measures it cites; filings under $5,000 report no amount. Filing and
  client counts need no such assumption.
- **Industry is not published.** Occupation categories are the adapter's keyword proxy; the FEC
  interest-group category describes the connected organization of a committee, not an industry.
- **FEC values are current-file values**, amendments included; weball totals are cycle-to-date to the
  coverage end date recorded on each row (the 2026 cycle runs to 2026-06-30).
- **Senators' receipts are per two-year cycle**, so a senator not up for election in a cycle can show
  small totals; nothing in the panel adjusts for the six-year term.
- **Party-unity status is chamber- and Congress-specific** and moves with the agenda.
- **Rows are not a sample of anything**; they are the full membership each source publishes.

## Stretch: a grounded congressional society scored against Voteview

Not run. `tensorcode` is **not installed** in this machine's Python 3.12 (`import tensorcode` fails;
the `worldmodel` venv at `.venv` does not have it either; only a uv cache archive of
`tensorcode-0.1.0a1` exists, with no environment). `wm society-run` therefore raises the install
hint, and every agent test skips. What running and scoring it would take, exactly:

1. **The dependency.** `pip install "worldmodel-substrate[agents]"` (which pins `tensorcode>=0.1.0a1`,
   requires Python >= 3.11) in the environment that runs `wm`.
2. **A vote act, which does not exist yet.** `examples/society-congress.json` grounds six Budget
   Committee members, two committees and a firm, and `Society.run` produces an observatory report
   (belief divergence, propagation, decision structure). No agent casts a roll-call vote: persons
   *perceive* their own past positions (`grounding.RollCalls`) but `Person.tick` has no yea/nay
   decision. Scoring needs a new act — given a roll call on the agenda (question, measure, published
   NOMINATE midpoints), each person agent returns yea / nay / abstain with a probability — and a
   society config whose population is a whole chamber (435 House or 100 Senate persons), not six.
3. **A leakage-safe grounding.** Agents must be seeded with `known_at` before the held-out Congress
   (e.g. seeded as of 2023-01-02 for the 118th House), so no position from the scored Congress is
   in any store.
4. **A pre-registered scoring harness**, registered in `real_data_plan.json` before any run: target =
   each member's position on each held-out roll call (yea/nay only); metrics = Brier, log score,
   accuracy; baseline = **party line** (each member votes with the realized majority of their own
   party on that roll call, which the panel's party codes and Voteview positions give), plus the
   `legislative` family's revealed-yea-share baseline; acceptance = Brier skill > 0 against the
   party-line baseline with a DM test. The party-line baseline is very strong (on this panel 94.2% of House and 92.8% of Senate yea/nay votes on
   party-unity roll calls in the 110th-118th Congresses were cast with the member's party majority), so
   this is a hard test by design.
5. **Compute.** A 435-agent society run over ~1,200 roll calls must fit the declared 30-minute
   budget, or be recorded as `not_run` like `legislative_model.voteview`.

## Follow-ups

- Acquire LD-2 filings for 2019-2024 (the same LDA.gov filings endpoint is partitioned by
  `filing_year`) so a lobbying exposure can be pre-registered on complete Congresses.
- `ModelFamilyEstimator` reports `standard_errors: {influence_coefficient: null}` in the run summary
  because the family returns its SE as the structured `influence_standard_error`; the report's
  `final_estimate` carries it. The summary could read it from there.
- `elections_model.medsl_house_districts` declares `revisions: none` while its conditional input is
  also FEC weball; the influence attempts declare `fec_amendments_possible` for the same kind of
  input. One of the two declarations should be reconciled (see calibration-status).
- congress-legislators publishes current committee membership only; a dated source of historical
  membership would have to be acquired to extend `committee_assignments` beyond the 119th.
