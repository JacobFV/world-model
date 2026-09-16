# govinfo_billstatus

GPO GovInfo **BILLSTATUS** bulk XML (Library of Congress data) for Congresses 113–119, all eight measure
types (`hr`, `s`, `hjres`, `sjres`, `hconres`, `sconres`, `hres`, `sres`):
`https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{type}/BILLSTATUS-{congress}-{type}.zip` (56 ZIPs, ~351 MB).

Licence: U.S. government work (public domain). No credentials. User guide:
https://github.com/usgpo/bill-status/blob/main/BILLSTATUS-XML_User_User-Guide.md

## Normalized evidence

- entity `congress:bill:{congress}-{type}-{number}` (`law` with explicit `measure_status`; title, policy area,
  latest action, public law numbers). Introduction never implies enactment.
- `sponsored_measure` / `cosponsored_measure` from `bioguide:{ID}` (cosponsor `valid_from` sponsorship date,
  `valid_to` withdrawal date)
- `referred_to_committee` → `congress:committee:{systemCode}` with activity history
- `policy_area`, `legislative_subjects`, `related_measure`, `committee_report_citation`, `became_law` (dated)
- event `legislative_action` per distinct action (date, code, type, source system, text, recorded roll calls;
  participants: the measure and referenced committees)

The XML is parsed member by member with the standard library (`zipfile` + `xml.etree`). A few documents still use the
legacy schema (`billNumber`/`billType`, `committees/billCommittees`); both are read. Repeated items (e.g. the same
cosponsor twice) are emitted once.

## Rebuild

```sh
python3 -m worldmodel acquire govinfo_billstatus --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run govinfo_billstatus
```
