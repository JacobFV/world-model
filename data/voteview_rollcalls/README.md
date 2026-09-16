# voteview_rollcalls

[Voteview](https://voteview.com/data) congressional roll-call data.

## Source and scope

- `HSall_members.csv` (all congresses; ICPSR ids, bioguide ids, DW-NOMINATE, Nokken-Poole)
- `HSall_rollcalls.csv` (every House/Senate roll call), `HSall_parties.csv`
- `{H,S}{110..119}_votes.csv` member positions for Congresses 110–119 (~167 MB)

Licence: no explicit open licence; free for research with citation — Lewis, Jeffrey B., Keith Poole,
Howard Rosenthal, Adam Boche, Aaron Rudkin, and Luke Sonnet. *Voteview: Congressional Roll-Call Votes
Database*. https://voteview.com/. Do not redistribute raw files. No credentials.

## Normalized evidence

- entity `icpsr:{N}` (`person`), `same_as` `bioguide:{ID}`
- `congressional_service` (value: congress, chamber, state, district, party code, occupancy) and
  `party_affiliation` → `voteview:party:{code}`, valid for the congress term dates
- observations per member × congress × chamber: `dw_nominate_dim1`, `dw_nominate_dim2` (unit
  `dw_nominate_score`), `nokken_poole_dim1/2` (`nokken_poole_score`), `scaled_roll_call_votes` (votes)
- party observations: `party_member_count`, `party_nominate_dim1_median`, `party_nominate_dim2_median`
- event `roll_call_vote` per roll call (date, yea/nay counts, bill number, result, question, NOMINATE midpoints)
- event `roll_call_member_positions` per roll call (110–119): ICPSR ids grouped by position
  (`yea`, `paired_yea`, `announced_yea`, `announced_nay`, `paired_nay`, `nay`, `present`, `present_paired`,
  `not_voting`, `not_member`); locator is the first vote row of the roll call

## Rebuild

```sh
python3 -m worldmodel acquire voteview_rollcalls --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run voteview_rollcalls
```
