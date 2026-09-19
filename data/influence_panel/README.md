# influence_panel

A derived, versioned panel of U.S. legislators by Congress that puts campaign money, lobbying,
committee assignments and roll-call positions side by side. It is built by
[`worldmodel/panels/influence.py`](../../worldmodel/panels/influence.py) from ten published
datasets and is the input of the `influence` model family's loader
(`worldmodel.estimation.loaders.influence_data`). Measured row counts, coverage and limits:
[docs/influence-panel.md](../../docs/influence-panel.md).

## Stages

| Stage | Unit | What a row holds |
| --- | --- | --- |
| `bills` | measure (`congress:bill:{congress}-{type}-{number}`) | BILLSTATUS sponsor, cosponsors (withdrawn ones kept apart) and committee referrals; the Voteview roll calls whose published `bill_number` names the measure, and congress.gov House roll-call events that name it; the LD-2 activity reports whose specific-issue text cites its number, with client and registrant IDs, issue codes and an attributed amount |
| `panel` (output) | `(bioguide, congress, chamber)` | ICPSR and FEC candidate IDs from published crosswalks; Voteview position counts and party-line defections; Nokken-Poole and DW-NOMINATE scores as published; FEC weball receipts by source (individuals, other committees, parties, the candidate); committee-to-candidate money by the giving committee's FEC type and interest-group category; itemized individual money by the adapter's occupation proxy and size band (2022, 2024, 2026 cycles); sponsorship counts; lobbying on the bills the member sponsored, cosponsored or voted on (119th Congress); committee assignments (119th Congress) |

Both stages are gzip JSONL. Each row carries `evidence`: per input `dataset@stage@version`, the
number of contributing records, a SHA-256 over their record ids and a sample of ids. The
manifest pins every input version, the full code snapshot and the parameters in `dataset.json`.

## Joins

Only published identifiers join rows: bioguide, ICPSR, FEC candidate and committee IDs, LDA
client and registrant IDs, and bill numbers as Voteview, congress.gov and LDA filers publish them.
Nothing is matched by name. One link is weaker and labelled on every mention: a bill number cited
in LDA free text carries no Congress, so the Congress is **inferred** from the filing period
(`congress_basis: inferred_from_filing_period`) unless a public-law number or a Congress ordinal
follows the citation (`stated_in_text`).

## What it does not establish

- **No influence.** A lobbying filing that cites a bill the member sponsored is not a contact
  with the member, and a contribution is not a vote. Rows put quantities side by side; they do
  not connect them causally.
- **Lobbying covers the 119th Congress only**, because `lda_lobbying` holds filing years 2025-2026.
  Earlier rows carry `lobbying_on_linked_bills: null`, which means *not acquired*, not *none*.
- **Committee assignments cover the 119th Congress only**: the membership file is the current one
  and publishes no start dates. Earlier rows carry `null`.
- **Industry is not published** by the FEC. The occupation categories are a keyword proxy written
  by the `fec_individual_contributions` adapter, not a publisher code; the FEC's own interest-group
  category (corporation, labor, membership, trade, cooperative) is used for committee money.
- **Attributed lobbying dollars are an allocation**: a filing's reported income or expenses are split
  equally across the distinct bills it cites. Filing and client counts need no such assumption.
- **FEC totals are current-file values** that include amendments filed after the period.
- Member-level roll-call positions exist for the 110th-119th Congresses; BILLSTATUS covers the
  113th-119th; FEC cycles 2000-2026 cover the 106th-119th. Rows outside a source's span carry `null`
  for that block and `linked` says so.

## Rebuild

Every dependency must be pinned, or the runner would rebuild the source datasets:

```sh
WORLD_MODEL_DATA=/path/to/data WORLD_MODEL_RAW_VERIFY=size nice -n 10 python3 -m worldmodel run influence_panel \
  --input congress_people/normalized@<v> --input voteview_rollcalls/normalized@<v> \
  --input govinfo_billstatus/normalized@<v> --input congress_gov_api/normalized@<v> \
  --input lda_lobbying/normalized@<v> --input fec/normalized@<v> --input fec_candidates/normalized@<v> \
  --input fec_individual_contributions/normalized@<v> --input fec_individual_contributions_2022/normalized@<v> \
  --input fec_individual_contributions_2024/normalized@<v>
```

The pinned versions of the published build are listed in its manifest and in
[docs/influence-panel.md](../../docs/influence-panel.md). Tests: `tests/test_influence_panel.py`
(builder on fixtures), `tests/test_influence_loader.py` (family loader and plan).
