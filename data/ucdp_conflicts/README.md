# ucdp_conflicts

Uppsala Conflict Data Program bulk releases (no API token needed).

## Source files (one shard each, https://ucdp.uu.se/downloads/)
GED 26.1 (`ged/ged261-csv.zip`, 1989-2025), GED Candidate `GEDEvent_v26_01_26_06.csv` (2026-01..06) and
`GEDEvent_v26_0_7.csv` (2026-07), UCDP/PRIO Armed Conflict 26.1, Dyadic 26.1, Non-State 26.1, One-Sided 26.1,
Battle-Related Deaths (dyadic) 26.1, Actor list 26.1 (the actor CSV is Latin-1 encoded).

## Outputs (`normalized`)
- Entities: `ucdp:conflict:<id>` and `ucdp:dyad:<id>` (`conflict`), `ucdp:actor:<id>` (`organization`), countries
  `iso3:XXX` from Gleditsch-Ward codes via `country_codes.json` (`gw_code` attribute; unmapped GW codes keep `gw:<code>`).
- Relations: `participates_in_conflict`, `dyad_of_conflict`, `dyad_side_a/b`, `conflict_in`, `located_in` (actor location).
- Events `ucdp_ged:state_based|non_state|one_sided` per GED event: date range, lat/lon, adm1/2, PRIO-GRID cell,
  precision codes, deaths by side and low/best/high, `release` (26.1 or candidate) and `bounds_consistent`.
- Annual observations: `conflict_intensity_level` (conflict and dyad), `battle_related_deaths[_low|_high]` (dyad),
  `non_state_conflict_deaths[_low|_high]`, `one_sided_violence_deaths[_low|_high]` (deaths).
- Monthly country aggregates from GED (by date_start month, type of violence and release): `organized_violence_events`,
  `organized_violence_deaths[_low|_high]` (`aggregate: true`).

Candidate releases are preliminary and revised in the next annual release. The sample adapter path (single JSONL payload)
is unchanged.

## Licence
CC BY 4.0 with citation (Sundberg & Melander 2013 for GED; Davies, Pettersson & Öberg for UCDP/PRIO ACD).

## Rebuild
```sh
wm acquire ucdp_conflicts --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run ucdp_conflicts && wm verify ucdp_conflicts
python3 -m unittest data/ucdp_conflicts/tests/test_pipeline.py
```
