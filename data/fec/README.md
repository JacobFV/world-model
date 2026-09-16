# fec

Federal Election Commission bulk data: committees, candidate linkages, cycle financial summaries and
aggregated money flows.

## Source and scope

`https://www.fec.gov/files/bulk-downloads/{YYYY}/{file}{YY}.zip` (pipe-delimited, headerless, latin-1):

- `cm`, `ccl`, `weball`, `webk`, `pas2` for every two-year cycle 2000–2026 (70 files, ~211 MB)
- `oth26` (committee/organization itemized transactions, 2026 cycle, ~213 MB zipped / 1.8 GB)
- `oppexp24`, `oppexp26` (operating expenditures, ~108 MB)

Individual contributions (`indiv`, 2–4 GB per cycle) are **excluded** from this scope.

Licence: U.S. government work, but **52 U.S.C. 30111(a)(4)** forbids selling or using information copied
from FEC reports for soliciting contributions or for commercial purposes. No credentials.

## Normalized evidence (`normalized`, gzip)

| Kind | Identity / metric |
| --- | --- |
| entity | `fec:committee:C########` (`political_committee`, newest cycle name) |
| assertion | `fec_committee_registration` (cycle value: designation, type, party, connected org, mailing state) |
| assertion | `authorized_committee_of` committee → `fec:candidate:…` (cycle window, linkage id) |
| observation (USD, cycle-to-date through coverage end) | `total_receipts`, `total_disbursements`, `cash_on_hand_beginning`, `cash_on_hand_end`, `individual_contributions`, `other_committee_contributions`, `party_committee_contributions`, `candidate_self_contributions`, `candidate_loans`, `debts_owed`, transfers and refunds (candidates: `weball`; PACs/parties: `webk`, plus `independent_expenditures`, `party_coordinated_expenditures`, `contributions_to_other_committees`) |
| observation (percent) | `general_election_vote_percent` |
| observation (USD, calendar month) | `committee_to_candidate_amount` per committee × candidate × transaction type (`24K`, `24E`, `24A`, …) |
| assertion | `supports_candidate` / `opposes_candidate` per cycle |
| observation (USD, calendar month) | `committee_itemized_receipts_amount` / `committee_itemized_disbursements_amount` per filer × counterpart (FEC ID, else entity type + state) × transaction type |
| observation (USD, calendar month) | `operating_expenditures_amount` per committee × category × payee state |

Aggregates carry `attributes.transaction_count` and cite the first raw row of the group. Memo lines
(`MEMO_CD = X`) are excluded from sums. Rows with unparseable dates use the cycle window
(`period: cycle_date_unknown`). Names of individuals, payees and addresses are not emitted.

## Rebuild

```sh
python3 -m worldmodel acquire fec --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run fec
python3 -m worldmodel verify fec
```

Offline tests: `tests/test_politics_procurement_datasets.py`.
