# parlgov

[ParlGov](https://www.parlgov.org/) development release: parties, elections and cabinets for EU/OECD
democracies. Licence: **CC BY-SA 4.0** (attribution; derived databases must be shared alike). Cite
Döring, Huber and Manow, *ParlGov database*. No credentials.

## Source and scope

`https://www.parlgov.org/data/parlgov-development_csv-utf-8/{view_party,view_election,view_cabinet}.csv`
(the directory listing is 403; the files themselves are public).

## Normalized evidence

- entity `parlgov:party:{party_id}` (`organization`; family, names), `registered_in` → `iso3:{country}`,
  external identifiers (CMP, CHES, …)
- observations `party_left_right_position`, `party_state_market_position`, `party_liberty_authority_position`,
  `party_eu_anti_pro_position` (unit `parlgov_scale_0_10`, time-invariant expert means; no valid time)
- event per election (`parliament_election` / `ep_election`), observations `election_vote_share` (percent),
  `election_seats_won` (seats) per party and `parliament_seats_total` per country election (dated to election day)
- entity `parlgov:cabinet:{cabinet_id}` (`institution`), `cabinet_member_party` party → cabinet from cabinet start
  to the successor cabinet start, observation `parliament_seats_held` at cabinet formation

## Rebuild

```sh
python3 -m worldmodel acquire parlgov --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run parlgov
```
