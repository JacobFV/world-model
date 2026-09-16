# global_fishing_watch

Global Fishing Watch AIS-derived events: port visits, vessel encounters and loitering (dark-fleet and
sanctions-evasion signals), 2024-01-01 to 2026-10-01.

Status: **awaiting_credentials / deferred**. The plan allots a budget of 0 bytes (`desired_bytes: 0`, priority 3)
until an approved token exists and non-commercial use is confirmed.

## Access and licence

- API: `https://gateway.api.globalfishingwatch.org/v3/events`. Documentation:
  https://globalfishingwatch.org/our-apis/documentation
- Credential: `GFW_API_TOKEN` (Bearer header). Request it at https://globalfishingwatch.org/our-apis/tokens/signup;
  the request must describe the intended use and approval is not instant. The variable is not yet listed in
  `.env.example`.
- Licence: **CC BY-NC 4.0**, non-commercial use only, with attribution. Do not use or redistribute the data
  commercially.

## Acquisition

`paged_api` over 3 datasets (`public-global-port-visits-events`, `public-global-encounters-events`,
`public-global-loitering-events`) x 11 quarterly windows. Offset pagination (`limit` 1,000), records at `entries`,
stop at `total`, 0.5 requests/second, stored as JSONL shards. After approval:

```sh
# add GFW_API_TOKEN=... to .env, raise acquisition.desired_bytes (about 50 MB estimated)
wm acquire global_fishing_watch --dry-run
wm acquire global_fishing_watch --allow-network
wm run global_fishing_watch && wm verify global_fishing_watch
```

## Evidence

Events `gfw:event:ID` with `event_type` `vessel_port_visit` (participants vessel and `gfw:anchorage:ID`),
`vessel_encounter` (both vessels) or `vessel_loitering`. Vessels are `mmsi:N` when the GFW `ssvid` is a 9-digit
MMSI (joinable with `marine_ais`), else `gfw:vessel:ID`. Attributes include end time, position, flags, port name and
flag, duration, confidence, encounter distance and speed, and loitering hours.

Tests: `python3 -m unittest data/global_fishing_watch/tests/test_pipeline.py`.
