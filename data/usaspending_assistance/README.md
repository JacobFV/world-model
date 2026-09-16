# usaspending_assistance

USAspending.gov prime **financial assistance transactions** (grants, loans, direct payments, insurance) for all
agencies, fiscal years 2026 and 2025, from the monthly award data archive.

## Source and scope

- Files: `https://files.usaspending.gov/award_data_archive/FY2026_All_Assistance_Full_20260906.zip` (1,041,965,198 bytes)
  and `FY2025_All_Assistance_Full_20260906.zip` (1,459,847,018 bytes); CSV members of up to 1M rows, 112 columns.
  Archives are rebuilt monthly and the date suffix changes: list
  `https://files.usaspending.gov/award_data_archive/?prefix=FY2026_All_Assistance` and update `acquisition.files`.
- Licence: U.S. federal government work (public domain), no redistribution restriction. Credentials: none.
- Privacy: USAspending redacts individual recipients at source ("REDACTED DUE TO PII", record type 3) and publishes
  county/state aggregate records (record type 1). Recipient street addresses and highly compensated officer names are
  present in raw files and are never emitted.

## Normalized evidence (`normalized`, gzip)

| Kind | Identity / metric | Notes |
| --- | --- | --- |
| observation | `federal_action_obligation` (USD) on `usaspending:award:{assistance_award_unique_key}` | one per transaction, dated at `action_date`; dimensions: modification, action type, assistance type, fiscal year, record type |
| observation | `loan_face_value`, `loan_subsidy_cost` (USD) | loan transactions only (non-zero source values) |
| entity | `usaspending:award:{key}` (`award`) | once per award: FAIN/URI, assistance type, assistance listing, funding opportunity, business types, record type, performance dates |
| observation | `award_total_obligated`, `award_total_loan_face_value`, `award_total_loan_subsidy_cost` (USD) | award-level cumulative values as of the archive snapshot date; never add them to transaction flows |
| entity / assertion | `uei:{UEI}` recipients, `awarded_to`, `subsidiary_of` `uei:{parent}` | awards without a UEI get `recipient_basis` (redacted individual / aggregate record) instead of a synthetic recipient |
| entity / assertion | `usgov:agency:{code}` (`part_of` toptier), `awarded_by`, `funded_by` | same agency namespace as `usaspending` contracts |
| assertion | `award_assistance_listing` → `cfda:{NN.NNN}`, `place_of_performance` → `geo:US:county:{fips5}` / `geo:US:state:{fips}` / `iso3:{country}` | `place_of_performance_scope` literal when no location code is published |

## Rebuild

```sh
python3 -m worldmodel budget
python3 -m worldmodel acquire usaspending_assistance --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run usaspending_assistance
python3 -m worldmodel verify usaspending_assistance
```

Offline tests: `tests/test_politics_procurement_datasets.py`.
