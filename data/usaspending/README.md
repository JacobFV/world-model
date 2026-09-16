# usaspending

USAspending.gov prime contract **transactions** from the monthly award data archive for **all agencies**,
fiscal years 2026 and 2025 (FY2026 is listed first so it lands first within the budget allocation).

## Source and scope

- Files: `https://files.usaspending.gov/award_data_archive/FY2026_All_Contracts_Full_20260906.zip` (1,383,095,241 bytes)
  and `FY2025_All_Contracts_Full_20260906.zip` (1,914,255,016 bytes); CSV members of up to 1M rows, 297 columns.
  Archives are rebuilt monthly and the date suffix changes: list
  `https://files.usaspending.gov/award_data_archive/?prefix=FY2026_All_Contracts` and update `acquisition.files`.
- The earlier scope was `FY2026_097_Contracts_Full_20260906.zip` (DoD only, 750,348,575 bytes); FY2026 all-agency
  contains the same DoD transactions.
- Under the shared budget the allocation can fall below both files; the acquisition then publishes FY2026 as
  `complete: false, stop_reason: budget` and `--resume` adds FY2025 when the allocation grows.
- Licence: U.S. federal government work (public domain), no redistribution restriction.
- Credentials: none.

## Normalized evidence (`normalized`, gzip)

| Kind | Identity / metric | Notes |
| --- | --- | --- |
| observation | `federal_action_obligation` (USD) on `usaspending:award:{contract_award_unique_key}` | one per transaction, `valid_from` = `action_date`; dimensions: modification, action type, fiscal year, sub-agencies |
| entity | `usaspending:award:{key}` (`award`) | once per award; PIID, parent IDV PIID, pricing, competition, NAICS/PSC codes, performance dates |
| observation | `award_total_obligated`, `award_current_total_value`, `award_potential_total_value` (USD) | award-level cumulative values as of the archive snapshot date; never add them to transaction flows |
| entity / assertion | `uei:{UEI}` recipients, `subsidiary_of` `uei:{parent UEI}` | parent as reported on the first transaction row seen |
| entity / assertion | `usgov:agency:{code}` (`part_of` toptier), `awarded_by`, `funded_by` | CGAC toptier and FPDS subtier codes |
| assertion | `award_naics` → `naics2022:{code}`, `award_product_service_code` → `psc:{code}`, `place_of_performance` → `geo:US:county:{fips}` / `geo:US:state:{fips}` / `iso3:{country}`, `order_under_idv` → `usaspending:award:CONT_IDV_{PIID}_{agency}` | |

Highly compensated officer names, addresses and phone numbers are never emitted. Legacy JSONL
samples (spending_by_award API) still use the original sample adapter.

## Rebuild

```sh
python3 -m worldmodel acquire usaspending --dry-run
python3 -m worldmodel acquire usaspending --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run usaspending
python3 -m worldmodel verify usaspending
```

Offline tests: `tests/test_politics_procurement_datasets.py`. `artifacts/` and `scratch/` are ignored by Git.
