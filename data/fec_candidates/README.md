# fec_candidates

FEC candidate master files for every two-year cycle 1980–2026 (`cn80.zip` … `cn26.zip`,
`https://www.fec.gov/files/bulk-downloads/{YYYY}/cn{YY}.zip`, ~4.8 MB).

Licence: U.S. government work; 52 U.S.C. 30111(a)(4) bars solicitation/commercial use. No credentials.

## Normalized evidence

- entity `fec:candidate:{ID}` (`person`; label from the newest cycle; one person may hold separate IDs per office)
- assertion `fec_candidacy` per cycle (`valid_from` = Jan 1 of the odd year, `valid_to` = Jan 1 after the
  election year): office, state, district, party, incumbent/challenger, candidate status
- assertion `principal_campaign_committee` → `fec:committee:{ID}` per cycle

Candidate mailing addresses are not emitted. Legacy OpenFEC API JSONL samples use `sample`.

## Rebuild

```sh
python3 -m worldmodel acquire fec_candidates --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run fec_candidates
```
