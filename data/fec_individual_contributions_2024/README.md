# fec_individual_contributions_2024

FEC itemized **individual contributions** (`indivYY.zip` → `itcont.txt`, pipe-delimited, headerless, latin-1),
normalized as aggregates, plus contributor-level rows when the deployment declares a non-commercial purpose.
Raw rows name individual contributors (name, city, ZIP, employer, occupation). **52 U.S.C. 30111(a)(4)** bars
selling that information or using it to solicit contributions or for any commercial purpose; it does not bar
research use. So `source.person_level_records` declares `policy: conditional, condition: non_commercial_use`, and
whether contributor rows are written is decided by `WM_COMMERCIAL_USE` against that rule -- see
`docs/use-policy.md`. Under `WM_COMMERCIAL_USE=1` (and when unset) the output is aggregate-only, exactly as before.

## Source and scope

- `https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip` (4,244,259,029 bytes; 2024 two-year cycle).
- One dataset per cycle keeps each under the 5 GiB per-dataset budget cap. The pipeline is identical to
  `fec_individual_contributions` (2026 cycle); join cycles on `fec:committee:*` subjects and `dimensions.cycle`.
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

The `contribution` family is added only under a declared non-commercial purpose. It is keyed by the FEC `SUB_ID`,
emits `individual_contribution_amount` (USD, singular -- the aggregate metric is `individual_contributions_amount`),
and carries the contributor's identity **as reported** in `dimensions`: `contributor_name`, `contributor_city`,
`contributor_zip` (full ZIP, not ZIP3), `contributor_employer`, `contributor_occupation`, `contributor_state`.

No persistent person identity is asserted. The subject stays `fec:committee:C########`; the same name in two cycles
is two reported strings, not one resolved human. Do not add entity resolution over these names: measured on this
catalog it gives 0.4% recall at 2.3% precision and wrongly merges 103,211 entities.

Every record -- aggregate and contributor alike -- carries `attributes.rights_decision` recording the rule, the
declared purpose and the authority, so a filtered and an unfiltered build are told apart by provenance rather than
by inspecting rows. Because the store is content-addressed they are different artifacts with different hashes; a
rebuild under a new purpose does not replace the old one.

Exclusions: memo lines (`MEMO_CD = X`, e.g. conduit earmark duplicates) and non-individual entity types. Rows
with an unusable date fall back to the cycle window (`month = null`). Evidence cites the first raw row of each group.
Unitemized contributions (under $200 aggregate per contributor) are not in the source.

## Rebuild

```sh
python3 -m worldmodel budget
python3 -m worldmodel acquire fec_individual_contributions_2024 --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run fec_individual_contributions_2024
python3 -m worldmodel verify fec_individual_contributions_2024
python3 -m worldmodel use-policy --dataset fec_individual_contributions_2024   # what the run will write, and why
```

Offline tests: `tests/test_politics_procurement_datasets.py`.
