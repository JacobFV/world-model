# congress_people

Community-maintained [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators)
directory (not an official Congress publication). Licence: CC0 1.0. No credentials.

## Source and scope

All published JSON files from `https://unitedstates.github.io/congress-legislators/`:
`legislators-current`, `legislators-historical`, `executive`, `committees-current`,
`committees-historical`, `committee-membership-current`, `legislators-social-media` (~17 MB).

## Normalized evidence

- entities `bioguide:{ID}` (`person`; executive-only people without a bioguide use `govtrack:{ID}`)
- `identifier_assignment` for bioguide, wikidata, fec, govtrack, lis, thomas, opensecrets, votesmart, icpsr,
  cspan, ballotpedia, maplight, google_entity_id, social accounts (undated)
- `same_as` crosswalks: `fec:candidate:{ID}`, `icpsr:{N}` (joins `fec_candidates`, `fec`, `voteview_rollcalls`)
- dated `holds_role` / `role_in_organization` / `role_affiliation` from published terms (House, Senate,
  President, Vice President); aliases with name-change dates
- committees `congress:committee:{thomas_id lower}00` and subcommittees `…{code}{sub}` (`institution`,
  `part_of`, `active_in_congresses`) — identical to congress.gov/BILLSTATUS `systemCode`
- `committee_member` bioguide → committee (current file, undated; rank, title, side)

Names are aliases and never merge evidence. Office phone numbers and addresses in terms are dropped.
Legacy JSONL samples (one legislator per line) use `sample`.

## Rebuild

```sh
python3 -m worldmodel acquire congress_people --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run congress_people
```
