# mit_election_returns

MIT Election Data + Science Lab (MEDSL) returns from Harvard Dataverse. Licence: CC0 1.0 (verified on
each dataset); please cite MEDSL.

## Scripted acquisition

- `1976-2024-president.csv` — U.S. President state returns, doi:10.7910/DVN/42MVDX (datafile 13887042)
- `1976-2024-senate-state.tab` — U.S. Senate statewide returns, doi:10.7910/DVN/PEJ5QU (datafile 13887039)

## Manual download required (Dataverse guestbook)

These files return *"You may not download this file without the required Guestbook response"* to
scripts. Download them in a browser (accepting the guestbook), then import:

- `countypres_2000-2024.tab` — County Presidential Election Returns 2000–2024, doi:10.7910/DVN/VOQCHQ
  (https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/VOQCHQ)
- `1976-2024-house.tab` — U.S. House 1976–2024, doi:10.7910/DVN/IG0UN2
  (https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/IG0UN2)

```sh
python3 -m worldmodel import mit_election_returns ~/Downloads/countypres_2000-2024.tab \
  --source-json '{"publisher":"MIT Election Data and Science Lab","doi":"10.7910/DVN/VOQCHQ","license":"CC0-1.0"}'
python3 -m worldmodel import mit_election_returns ~/Downloads/1976-2024-house.tab \
  --source-json '{"publisher":"MIT Election Data and Science Lab","doi":"10.7910/DVN/IG0UN2","license":"CC0-1.0"}'
# build from every raw artifact (the acquisition plus both imports):
python3 -m worldmodel run mit_election_returns --raw mit_election_returns@<acquired> \
  --raw mit_election_returns@<county import> --raw mit_election_returns@<house import>
```

Importing moves `raw-latest.json` to the imported file, so pin all raw artifacts with `--raw` as above.

## Normalized evidence

- observation `votes_received` (votes) per candidate row on `geo:US:state:{fips}`, `geo:US:county:{fips5}` or
  `geo:US:state:{fips}:cd:{NN}`; dimensions: office, election year, stage, special, mode, district, candidate,
  party, simplified party, write-in
- observation `total_votes_cast` (votes) once per geography × office × year × stage × special × mode × district
- regular even-year general elections are dated to election day; other contests use the election year window

## Rebuild

```sh
python3 -m worldmodel acquire mit_election_returns --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run mit_election_returns
```
