# gdelt_events

GDELT 1.0 daily event exports (CAMEO-coded actor-actor actions with Goldstein scale, tone,
mention counts and action geolocation) for **2026-03-25 .. 2026-09-14** (174 daily files, ~1.06 GB raw).

GDELT is fully machine-coded from online news: expect duplicates, misclassification, coding
of old events reported today, and media-volume bias. Treat it as a weak, high-frequency tension
signal next to UCDP (and ACLED when available), never as ground truth.

## Source and scope
- Files: `https://data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip` (tab-delimited, headerless, 58 columns;
  codebook http://data.gdeltproject.org/documentation/GDELT-Data_Format_Codebook.pdf).
- Window sized to ~1 GiB raw (shared budget raised to 50 GiB, 10% per-dataset cap). The GDELT 2.0 15-minute exports were not used: they are
  ~8-9 MB/day (larger than the 1.0 daily file) and need 96 requests/day. GKG is out of scope.
- Extend by editing the `day` list in `dataset.json` and re-running `wm acquire gdelt_events --allow-network`.

## Stages
- `normalized` (default): one compact event per row. `event_type` `cameo:<EventCode>`, `occurred_at` = SQLDATE,
  participants = Actor1/Actor2 country (`iso3:XXX`, or `cameo:region:XXX` for CAMEO regional codes such as EUR/AFR),
  attributes: GLOBALEVENTID, DATEADDED, root flag, base/root codes, QuadClass, Goldstein, mentions/sources/articles,
  tone, actor codes/types/names, action geo (FIPS country, ADM1, lat/lon). SOURCEURL is not copied (raw locator kept).
- `daily_dyads` (`wm run gdelt_events --stage daily_dyads`): per DATEADDED day x Actor1 place x Actor2 place (or `none`)
  x QuadClass: `gdelt_event_count`, `gdelt_root_event_count`, `gdelt_mentions`, `gdelt_goldstein_mean`
  (`goldstein_scale_-10_10`), `gdelt_avg_tone_mean` (`tone_score`).

## Licence
GDELT data are free for unlimited use and redistribution with citation of The GDELT Project.

## Rebuild
```sh
wm acquire gdelt_events --dry-run && wm acquire gdelt_events --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run gdelt_events && wm run gdelt_events --stage daily_dyads
wm verify gdelt_events
python3 -m unittest data/gdelt_events/tests/test_pipeline.py
```

## Built outputs (2026-09-15)
Raw 1,059,197,823 bytes (174 shards). `normalized`: 17,002,367 records, 1,690,839,664 gzip bytes (1.60x raw).
`daily_dyads`: 3,633,842 records, 182,789,745 gzip bytes. The published stage versions were built from the
declaration before its `status` was set to `complete` (a metadata-only difference; rerunning rebuilds them).
An earlier 421 MB raw artifact (19f528de..., the 2026-07-06..09-14 window) is retained and superseded; its 71
daily files are byte-identical to the corresponding shards of the current artifact but are separate copies.
