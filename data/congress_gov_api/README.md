# congress_gov_api

Library of Congress [congress.gov API v3](https://github.com/LibraryOfCongress/api.congress.gov) list
endpoints that fill gaps not covered by BILLSTATUS and Voteview.

## Source and scope

`https://api.congress.gov/v3/{endpoint}/{congress}?format=json&limit=250` with next-link pagination:
`amendment`, `nomination`, `committee-report`, `treaty` for Congresses 117–119; `house-vote` for 118–119;
`committee` for 119. Whole pages are stored as JSONL lines (~9 MB, 134 requests).

Credential: `CONGRESS_API_KEY` (api.data.gov key; the same key as `DATA_GOV_API_KEY`/`FEC_API_KEY`), sent as
the `api_key` query parameter; the request quota is shared through `rate_limit.key = "api.data.gov"`.
Licence: U.S. government data; api.data.gov terms apply.

## Normalized evidence

- `congress:amendment:{congress}-{type}-{number}` (`law`, `measure_kind: amendment`; description/purpose and latest
  action when the list record has them)
- event `presidential_nomination_received` (citation, organization, civilian/military, latest action)
- `congress:committee_report:{congress}-{type}-{number}-{part}` (`publication`)
- `congress:treaty:{congress}-{number}{suffix}` (`law`, ratification not inferred)
- event `house_roll_call_vote` with participants `us:congress:house` and `congress:bill:…` / `congress:amendment:…`
- `congress:committee:{systemCode}` (`institution`) with `part_of`

List records only; item detail endpoints (nominees, amendment sponsors) are not crawled — BILLSTATUS carries
bill-level amendments, cosponsors and actions.

## Rebuild

```sh
python3 -m worldmodel acquire congress_gov_api --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run congress_gov_api
```
