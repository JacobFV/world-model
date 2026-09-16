# fec_individual_contributions

FEC itemized **individual contributions** (`indivYY.zip` → `itcont.txt`, pipe-delimited, headerless, latin-1),
normalized only as aggregates. Raw rows contain contributor PII (name, city, ZIP, employer, occupation);
no contributor-level record is ever emitted.

## Source and scope

- `https://www.fec.gov/files/bulk-downloads/2026/indiv26.zip` (2026 cycle to date, ~2.19 GB).
- The 2024 cycle (`indiv24.zip`, 4.24 GB) was requested, but a single file must fit in one budget reservation, and
  the fair-share allocation under the shared 50 GiB pool was ~2.58 GB. To switch when budget allows, change
  `acquisition.files` to `…/2024/indiv24.zip` and `desired_bytes` to ~4.26 GB (the pipeline reads any `indivYY.zip`).
- Licence: U.S. government work, **52 U.S.C. 30111(a)(4)**: information about contributors may not be sold or used
  for soliciting contributions or for any commercial purpose. No credentials.

## Normalized evidence (`normalized`, gzip)

Every aggregate is emitted twice: `individual_contributions_amount` (USD) and `individual_contribution_count`
(contributions); `attributes.aggregation` / `dimensions.aggregation` name the family:

| Family | Subject | Dimensions | Valid time |
| --- | --- | --- | --- |
| `committee_state_month` | `fec:committee:C########` | contributor state, month | calendar month |
| `zip3_month` | `geo:US:zip3:NNN` | contributor state, month | calendar month |
| `committee_occupation` | committee | `occupation_category` (keyword proxy, `OCCUPATION_RULES` v1) | cycle |
| `committee_size_band` | committee | `size_band` (under_200 … 3300_and_over, per transaction) | cycle |

Exclusions: memo lines (`MEMO_CD = X`, e.g. conduit earmark duplicates) and non-individual entity types. Rows
with an unusable date fall back to the cycle window (`month = null`). Evidence cites the first raw row of each group.
Unitemized contributions (under $200 aggregate per contributor) are not in the source.

## Rebuild

```sh
python3 -m worldmodel budget
python3 -m worldmodel acquire fec_individual_contributions --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run fec_individual_contributions
python3 -m worldmodel verify fec_individual_contributions
```

Offline tests: `tests/test_politics_procurement_datasets.py`.
