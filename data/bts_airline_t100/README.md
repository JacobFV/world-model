# bts_airline_t100

BTS T-100 Segment (All Carriers): monthly passengers, freight, mail, seats, available payload, departures and
air time by carrier, aircraft type, service class and airport pair.

Status: **manual_download_required** (plan priority 3, deferred). T-100 has no bulk PREZIP file; TranStats
serves it only through an interactive ASP.NET form, which this project does not automate.

## Manual download

1. Open `https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45`
   (table "T-100 Segment (All Carriers)", database "Air Carrier Statistics (Form 41 Traffic) - All Carriers").
2. Choose the year and period, and select at least `YEAR`, `MONTH`, `ORIGIN`, `DEST`, `UNIQUE_CARRIER`,
   `UNIQUE_CARRIER_NAME`, `PASSENGERS`, `FREIGHT`, `MAIL`, `SEATS`, `PAYLOAD`, `DEPARTURES_PERFORMED`,
   `DEPARTURES_SCHEDULED`, `DISTANCE`, `AIRCRAFT_TYPE`, `CLASS`, `DATA_SOURCE` (optional: `AIR_TIME`,
   `RAMP_TO_RAMP`, `AIRCRAFT_CONFIG`, `AIRCRAFT_GROUP`, origin/destination airport IDs and countries).
3. Download the ZIP (one CSV inside) and import it:

```sh
wm import bts_airline_t100 ~/Downloads/T_T100_SEGMENT_ALL_CARRIER.zip
wm run bts_airline_t100
wm verify bts_airline_t100
```

Several downloads can be pinned together with repeated `--raw bts_airline_t100@ARTIFACT` options.

## Evidence

Observations with subject `iata:ORIGIN`, monthly `valid_from`/`valid_to`, and dimensions `destination`
(`iata:DEST`), `carrier` (`dot:carrier:XX`), `aircraft_type`, `aircraft_config`, `service_class`, `data_source`.
Metrics: `air_passengers` (passengers), `air_freight` / `air_mail` / `available_payload` (lb), `seats_offered`
(seats), `departures_performed` / `departures_scheduled` (departures), `air_time` / `ramp_to_ramp_time` (minutes).
Empty source cells are not emitted.

Licence: US federal government work (public domain). No credential is needed.

The On-Time Performance PREZIP files (`https://transtats.bts.gov/PREZIP/On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_YYYY_M.zip`,
about 32 MB per month) are scriptable but out of scope for this deferred dataset.

Tests: `python3 -m unittest data/bts_airline_t100/tests/test_pipeline.py`.
