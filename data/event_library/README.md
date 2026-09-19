# event_library

Typed, dated shocks for natural-experiment designs (`worldmodel.causal`). Each record is an
`event` naming the affected unit, the event date and what that date means, the intensity
where the source publishes one, and a locator back to the exact source record. The library
selects and restates source events; it does not reinterpret dates. See
[docs/natural-experiments.md](../../docs/natural-experiments.md).

## Record schema

`event_type` equals `attributes.library_type`. Every record's `attributes` carries:

| Field | Meaning |
| --- | --- |
| `library_version` | `1` |
| `shock_family` | `disaster_policy`, `natural_hazard`, `sanctions` or `trade_policy` |
| `unit_type`, `unit` | the affected unit: `us_county_fips` (`geo:US:county:SSCCC`), `nws_zone_or_location`, `reporter_x_hs6` (`iso3:XXX\|hs:NNNNNN`), `listed_party`, `epicenter`, `storm` |
| `date`, `date_semantics` | the event date (also `occurred_at`) and the source's meaning of it |
| `intensity` | `{value, unit, definition, timing}` or null. `timing: ex_ante` is known at the event date; `ex_post` is measured afterwards and must not define treatment or exposure |
| `source` | `{dataset, record_id, locator}`: the input record id and its raw locator |

Record-level `evidence` cites the exact input `{dataset, stage, version}` and record ids; the
runner verifies that every cited record exists in the pinned input.

## Types and selection rules

| `library_type` | Source | Unit | Date | Intensity | Selection |
| --- | --- | --- | --- | --- | --- |
| `fema_major_disaster_declaration`, `fema_emergency_declaration`, `fema_fire_management_declaration` | `openfema` declarations | county | FEMA `declarationDate` (incident begin in `incident_begin_date`) | disaster-wide Public Assistance obligation, `ex_post` | every county-designated row; 1,610 statewide/tribal rows without a county are left out |
| `sanctions_designation` | `ofac_sanctions`, `other_sanctions_lists` | listed party | the list owner's published date (OFAC entry event date, UK date designated, UN listed-on, BIS/State CSL start date) | none | every designation event; `countries` from the source's located-in/nationality/citizenship assertions |
| `storm_event_<type>` | `noaa_storm_events` | county, or NWS zone when the source has no county | NWS begin time | property + crop damage, nominal USD, `ex_post` | damage >= $1M or any death or injury |
| `earthquake` | `usgs_earthquakes` | epicentre | USGS origin time | preferred magnitude, `ex_ante` | magnitude >= 5.0 |
| `tropical_cyclone` | `ibtracs` | storm | first main-track fix | lifetime maximum sustained wind, `ex_post` | NA and EP basins |
| `tropical_cyclone_county_exposure` | `ibtracs` + `census_geography` internal points | county | first fix with wind >= 34 kt within 100 km of the county internal point | max wind of fixes within 100 km, `ex_ante` | NA and EP basins |
| `tariff_mfn_increase`, `tariff_mfn_decrease` | `wits_trains_tariffs` | reporter x HS6 | 1 January of the first reporting year at the new rate | change in the simple-average MFN applied rate, percentage points | abs(change) >= 0.5 pp between consecutive reporting years in the same HS revision |

### What is deliberately not here

* **OpenSanctions listing dates.** `opensanctions` publishes only its crawler's first-seen time
  ("not the official designation date"), 2018 onward and bunched in 2023. A first-seen date is
  not a treatment date, so those events are excluded.
* **USITC HTS changes.** `usitc_hts_tariffs` holds one release snapshot (2026 Revision 19), so no
  change can be dated from it.
* **Section 232/301 and other non-MFN duties.** TRAINS MFN rates do not include them; the tariff
  events are MFN schedule changes only.
* **Delisted sanctions parties.** Both sanctions sources are current lists; parties removed before
  retrieval are absent (survivorship, recorded on every sanctions event).

## Rebuild

Pin every dependency so the runner does not try to rebuild a source:

```sh
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data python3 -m worldmodel run event_library \
  --input openfema/normalized@VERSION --input ofac_sanctions/normalized@VERSION ... (all eight)
python3 -m unittest tests.test_causal_event_library
```

## Rights

Derived from public-domain US federal sources (OpenFEMA, NOAA, USGS, OFAC, BIS/State), the UK and
UN sanctions lists, IBTrACS (NOAA NCEI) and UNCTAD TRAINS via WITS (attribution required, no resale,
redistribution restricted). The manifest's inherited rights block is authoritative; nothing here
authorises republishing the payload.

`artifacts/` and `scratch/` are ignored by Git.
