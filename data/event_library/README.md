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

## Measured counts (version `dbf0c37f15cb...`, 2026-09-18)

255,012 events: 127,180 natural hazard, 68,792 disaster policy, 33,075 sanctions, 25,965 trade policy.
Built in 4m24s at 1.4 GB peak RSS. Counts by the year of `date`:

| Type | Total | pre-1990 | 1990-94 | 1995-99 | 2000-04 | 2005-09 | 2010-14 | 2015-19 | 2020-24 | 2025-26 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FEMA major disaster (DR), county | 45,733 | 7,506 | 3,050 | 5,099 | 5,424 | 5,362 | 4,930 | 4,584 | 8,488 | 1,290 |
| FEMA emergency (EM), county | 21,194 | 1,682 | 938 | 861 | 811 | 5,162 | 1,733 | 1,722 | 6,745 | 1,540 |
| FEMA fire management (FM), county | 1,865 | 0 | 0 | 79 | 408 | 338 | 291 | 280 | 306 | 163 |
| Sanctions designation (OFAC 20,632; UK, UN, BIS, State 12,443) | 33,075 | 192 | 95 | 206 | 1,450 | 1,417 | 2,732 | 5,327 | 16,171 | 5,485 |
| NOAA storm events (damage >= $1M or casualties; 57 subtypes) | 53,410 | 7,151 | 1,742 | 7,169 | 6,878 | 8,242 | 8,331 | 6,109 | 6,418 | 1,370 |
| Earthquake, M >= 5 | 62,499 | 0 | 8,258 | 6,368 | 7,188 | 10,046 | 10,155 | 8,256 | 8,689 | 3,539 |
| Tropical cyclone (NA, EP) | 1,679 | 364 | 181 | 157 | 182 | 186 | 169 | 204 | 203 | 33 |
| Tropical cyclone US county exposure | 9,592 | 1,362 | 211 | 1,484 | 1,078 | 1,258 | 727 | 1,374 | 2,085 | 13 |
| MFN tariff increase >= 0.5 pp (reporter x HS6) | 7,214 | | | | | | | 2,206 | 5,008 | |
| MFN tariff decrease >= 0.5 pp (reporter x HS6) | 18,751 | | | | | | | 4,579 | 14,172 | |

The largest storm subtypes are tornado 11,537, thunderstorm wind 7,091, flash flood 4,122, lightning
3,890, flood 3,143, heat 2,520 and hail 2,427. 15,762 of the DR rows carry no Public Assistance
intensity (no PA obligation published for that disaster). Sanctions events carry no intensity.
Only earthquakes, cyclone county exposures and tariff changes carry an `ex_ante` intensity.

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
