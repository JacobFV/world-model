# airport_nodes

OurAirports global aviation reference: all airports, heliports and seaplane bases (coordinates, elevation, IATA/ICAO
codes), runways, radio frequencies, navaids, and country/region jurisdictions. The `sampling` block still describes
the bounded California medium/large airport excerpt.

## Source and licence

- Files (nightly export mirrored on GitHub Pages): `https://davidmegginson.github.io/ourairports-data/`
  `airports.csv`, `runways.csv`, `navaids.csv`, `airport-frequencies.csv`, `countries.csv`, `regions.csv`
  (20.0 MB in total on 2026-09-15).
- Licence: released into the public domain by OurAirports. Community-edited data with no accuracy warranty, not for
  navigation. No schedules or flights. No credential is needed.

## Evidence

| Records | IDs / metrics |
| --- | --- |
| Airports (`airport`) | `ourairports:ID` (same identity as the sample), attributes ident/type/codes/municipality; observations `latitude`, `longitude` (degrees) and `elevation` (ft); `within iso3166-2:XX-YY`; `identified_by` literals `iata:XXX`, `icao:XXXX` |
| Runways (`infrastructure`) | `ourairports:runway:ID`, `located_in` airport; `runway_length`, `runway_width` (ft); surface, lighting and threshold attributes |
| Frequencies | observation `radio_frequency` (MHz) on the airport, `dimensions.frequency_type` |
| Navaids (`infrastructure`) | `ourairports:navaid:ID`; `navaid_frequency`, `dme_frequency` (kHz), `elevation` (ft); `serves_airport` when the associated ident resolves |
| Countries (`country`) | `iso3:XXX` (alpha-2 mapped with the local `countries.py`; unknown codes become `ourairports:country:XX`); US territories `same_as geo:US:state:NN` |
| Regions (`jurisdiction`) | `iso3166-2:CODE`, `within iso3:XXX`; US states `same_as geo:US:state:NN` |

Observations are snapshot facts: `valid_from` is the retrieval date and there is no `valid_to`. Blank source values
are omitted, not invented.

## Rebuild

```sh
wm acquire airport_nodes --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run airport_nodes
wm verify airport_nodes
python3 -m unittest data/airport_nodes/tests/test_pipeline.py
```
