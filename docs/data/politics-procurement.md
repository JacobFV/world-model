# Politics, legislation, lobbying and procurement datasets

Cross-dataset notes for `fec`, `fec_candidates`, `fec_individual_contributions` (2026 cycle), `fec_individual_contributions_2024`,
`fec_individual_contributions_2022`, `congress_people`, `voteview_rollcalls`,
`govinfo_billstatus`, `congress_gov_api`, `lda_lobbying`, `federal_register_documents`,
`mit_election_returns`, `parlgov`, `usaspending` (contracts) and `usaspending_assistance`. Each dataset README has its source, scope, licence
and rebuild commands; offline fixtures live in `tests/test_politics_procurement_datasets.py`.

Acquisition state (`wm catalog`, 2026-09-15): all of them have a published normalized artifact, with two partial
builds. `mit_election_returns` is `partial_manual_download_required`: the published build is the scripted state
President and Senate returns only (10,292 observations, 1976–2024); the county presidential and U.S. House files
are still missing. `lda_lobbying` is `partial_acquisition_in_progress`: 165,501 filings for filing years 2025–2026,
with 2026 Q3 (809) and Q4 (2) still open.

## Shared identifiers (join keys)

| Namespace | Emitted by | Referenced by |
| --- | --- | --- |
| `bioguide:X000000` | `congress_people` (entities, terms) | `govinfo_billstatus` (sponsors/cosponsors), `voteview_rollcalls` (`same_as`), `congress_people` committee membership |
| `icpsr:NNNNN` | `voteview_rollcalls` (entities, scores, vote positions as integers in `positions`) | `congress_people` `same_as` (from `id.icpsr` / `id.icpsr_prez`) |
| `fec:candidate:X########` | `fec_candidates` | `fec` (linkages, summaries, flows), `congress_people` `same_as` |
| `fec:committee:C########` | `fec` | `fec_candidates` (`principal_campaign_committee`), `fec_individual_contributions*` (aggregate subjects, one dataset per cycle) |
| `congress:bill:{congress}-{type}-{number}` | `govinfo_billstatus` | `congress_gov_api` House votes (participants), BILLSTATUS `related_measure` |
| `congress:amendment:{congress}-{type}-{number}` | `congress_gov_api` | `congress_gov_api` House votes |
| `congress:committee:{systemCode}` | `congress_people` (`thomas_id` lower + `00`, subcommittee suffix), `congress_gov_api` | `govinfo_billstatus` referrals and actions |
| `us:congress:house`, `us:congress:senate` | `congress_people` | `voteview_rollcalls`, `congress_gov_api` event participants |
| `lda:registrant:{id}`, `lda:client:{id}`, `lda:lobbyist:{id}`, `lda:government_entity:{id}` | `lda_lobbying` | — |
| `uei:{UEI}`, `usaspending:award:{key}`, `usgov:agency:{code}` | `usaspending`, `usaspending_assistance` | shared namespaces: one recipient or agency can appear in contracts and assistance |
| `cfda:{NN.NNN}` (assistance listing) | referenced by `usaspending_assistance` | — |
| `federalregister:{document_number}`, `federalregister:agency:{id}` | `federal_register_documents` | — |
| `geo:US:zip3:{NNN}` | `fec_individual_contributions*` | — |
| `geo:US:state:{fips}`, `geo:US:county:{fips5}`, `geo:US:state:{fips}:cd:{NN}`, `iso3:XXX`, `naics2022:{code}`, `psc:{code}` | referenced only | geography/classification entities come from the reference datasets (`census_geography`, `classifications`) |

Measured join coverage on the full builds (2026-09-15):

| Link | Resolved |
| --- | --- |
| `congress_people` `same_as` → `fec_candidates` entities | 1,719 / 1,739 (98.9%; the rest are IDs absent from 1980–2026 candidate masters) |
| `congress_people` `same_as` → `voteview_rollcalls` `icpsr:` entities | 12,340 / 12,340 |
| BILLSTATUS (113–119) sponsor/cosponsor `bioguide:` → `congress_people` persons | 1,391,954 / 1,391,954 |
| BILLSTATUS (113–119) `referred_to_committee` → committees (`congress_people` ∪ `congress_gov_api`) | 175,853 / 175,853 |
| congress.gov House roll-call bill participants → BILLSTATUS measures | 1,221 / 1,221 |

Known gaps: no published crosswalk links LDA clients or USAspending recipients (UEI) to FEC
committees' connected organizations; `fec` keeps `connected_organization` names as literals only.
LD-203 payee/honoree names are not resolved to FEC IDs. Federal Register agency ids
(`federalregister:agency:*`) and USAspending agency codes (`usgov:agency:*`) are different code systems.

## Time semantics

- FEC cycle `YYYY` covers `[YYYY-1-01-01, YYYY+1-01-01)`. `weball`/`webk` summaries are cycle-to-date through
  `coverage_end_date` (`valid_to` = day after coverage end). Itemized flows are calendar-month aggregates.
- Congress term windows: Congress *n* starts 1935-01-03 + 2(n−74) years (March 4 before the 74th).
- LDA quarters `[Jan 1, Apr 1)` … `[Oct 1, Jan 1)`; LD-203 `mid_year` `[Jan 1, Jul 1)`, `year_end` `[Jul 1, Jan 1)`.
- USAspending (contracts and assistance) transaction obligations are dated flows; award totals are snapshot stocks (archive date).
  Assistance individual recipients are redacted at source; those awards carry `recipient_basis`, not a recipient entity.
- MIT regular general elections are dated to election day; other contests to the election year.

## Metrics for estimation

| Dataset | Metrics (unit) |
| --- | --- |
| `fec` | `total_receipts`, `total_disbursements`, `cash_on_hand_beginning`, `cash_on_hand_end`, `individual_contributions`, `other_committee_contributions`, `party_committee_contributions`, `candidate_self_contributions`, `candidate_loans`, `other_loans`, `loans_received`, `debts_owed`, transfer/refund/repayment series, `independent_expenditures`, `party_coordinated_expenditures`, `contributions_to_other_committees`, `nonfederal_*` (USD, cycle-to-date); `general_election_vote_percent` (percent); `committee_to_candidate_amount`, `committee_itemized_receipts_amount`, `committee_itemized_disbursements_amount`, `committee_itemized_other_transactions_amount`, `operating_expenditures_amount` (USD, monthly) |
| `voteview_rollcalls` | `dw_nominate_dim1`, `dw_nominate_dim2`, `party_nominate_dim1_median`, `party_nominate_dim2_median` (dw_nominate_score); `nokken_poole_dim1`, `nokken_poole_dim2` (nokken_poole_score); `scaled_roll_call_votes` (votes); `party_member_count` (members) |
| `fec_individual_contributions`, `_2024`, `_2022` | `individual_contributions_amount` (USD), `individual_contribution_count` (contributions) by committee × state × month, ZIP3 × month, committee × occupation category, committee × size band (`dimensions.cycle` distinguishes cycles) |
| `lda_lobbying` | `lobbying_income`, `lobbying_expenses` (USD, quarter); `lobbyist_contribution_amount` (USD, day) |
| `usaspending` | `federal_action_obligation` (USD, day); `award_total_obligated`, `award_current_total_value`, `award_potential_total_value` (USD, snapshot) |
| `usaspending_assistance` | `federal_action_obligation`, `loan_face_value`, `loan_subsidy_cost` (USD, day); `award_total_obligated`, `award_total_loan_face_value`, `award_total_loan_subsidy_cost` (USD, snapshot) |
| `mit_election_returns` | `votes_received`, `total_votes_cast` (votes) |
| `parlgov` | `election_vote_share` (percent), `election_seats_won`, `parliament_seats_total`, `parliament_seats_held` (seats), `party_*_position` (parlgov_scale_0_10) |
| `fec_candidates`, `congress_people`, `govinfo_billstatus`, `congress_gov_api`, `federal_register_documents` | relationships and events only (no numeric series) |

## Licence and use caveats

- FEC: 52 U.S.C. 30111(a)(4) — no solicitation or commercial use of contributor information. Individual
  contributions are normalized only as aggregates (no names, employers, cities or full ZIP codes).
- Voteview: research use with citation; no open licence; do not redistribute raw files.
- ParlGov: CC BY-SA 4.0 (share-alike applies to derived databases).
- MIT Election Lab: CC0; the county presidential and House files require a Dataverse guestbook response
  (manual download + `wm import`).
- LDA.gov and congress.gov/api.data.gov: public records with API terms and keys.
