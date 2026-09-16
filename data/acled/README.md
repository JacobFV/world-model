# acled

ACLED (Armed Conflict Location & Event Data) disaggregated political violence, protest and strategic
development events. **Status: awaiting_credentials** (budget 0; no data acquired).

## Access (user action)
1. Register at https://acleddata.com/register/. The free "myACLED" tier only gives aggregated data;
   disaggregated API access requires emailing access@acleddata.com for an organisation-based tier.
2. ACLED's API uses OAuth (password grant). The acquisition engine cannot perform that token exchange, so obtain a
   token manually (valid ~24 h) and export it without logging it:
   `curl -s -X POST https://acleddata.com/oauth/token -d username=$ACLED_EMAIL -d password=$ACLED_PASSWORD -d grant_type=password -d client_id=acled`
   then set `ACLED_ACCESS_TOKEN` (sent as `Authorization: Bearer ...`). Needed shared-code feature: an OAuth
   password-grant credential type that refreshes tokens from `ACLED_EMAIL`/`ACLED_PASSWORD`.
3. Raise `acquisition.desired_bytes` to ~150000000 and run `wm acquire acled --allow-network`.

## Scope and outputs
Read API `api/acled/read`, JSON, 5,000 rows/page, one page sequence per year 2018-2026.
`normalized`: events `acled:<event_type>` with participants country (`iso3:` if the row carries `iso3`, else
`iso3166num:NNN`) and actors (`acled:actor:<slug>`), attributes: event id, sub-event/disorder type, interaction,
civilian targeting, admin1/2, location, lat/lon, precision codes, fatalities. Country-month observations:
`acled_events` (events) and `acled_fatalities` (deaths) by event type. Notes/source text are never copied.

## Licence
ACLED Terms of Use: attribution required; redistribution of raw event data prohibited; commercial use needs a licence.
Keep derived outputs internal.

## Tests
`python3 -m unittest data/acled/tests/test_pipeline.py`
