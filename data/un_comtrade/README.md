# un_comtrade

UN Comtrade monthly merchandise trade from 2024-01 to the latest published month. It
overlaps the last BACI year (2024) and extends past it into recent months.

## Scope

UN Comtrade v1 `get` API (`https://comtradeapi.un.org/data/v1/get/C/M/HS`). Each request batches
10 reporters x the twelve comma-separated periods of one calendar year (2024, 2025, 2026;
months not yet published return no rows). Both flows (`M,X`) are requested, with totals over
partner2, customs procedure and transport mode:

- HS2 chapters (`cmdCode=AG2`) x World for 59 reporters: the G20 (USA, CHN, DEU, JPN, GBR, FRA,
  IND, ITA, KOR, CAN, MEX, RUS, BRA, AUS, IDN, TUR, SAU, ARG, ZAF) plus 40 other large traders
  (NLD, BEL, ESP, CHE, SGP, HKG, VNM, MYS, THA, POL, ARE, SWE, AUT, IRL, CZE, DNK, NOR, ISR, PHL,
  CHL, HUN, SVK, PRT, FIN, ROU, GRC, NZL, KAZ, QAT, KWT, EGY, NGA, COL, PER, UKR, BGD, PAK, IRQ,
  SVN, LUX). This takes 18 requests.
- All commodities (`cmdCode=TOTAL`) x every partner for the 19 G20 reporters. This takes 6 requests.

That is 24 requests, well under the 450/day cap. The largest batch (10 G20 reporters x ~240
partners x 2 flows x 12 months) stays under the 100,000-record response limit. Rows are
about 1 KB each, so the grid is about 480 MB. The plan's HS4 x all-partner grid would need
several GB.

Reporter codes are Comtrade's reporter M49 variants, not plain ISO numeric codes: India 699,
Norway 579, Switzerland 757 (356/578/756 return no rows). "Other Asia, nes" (490) is a
partner-only area and is not requested.

### Resuming and refreshing

If the daily quota or a `Retry-After` stops a run, it ends `incomplete`/`rate_limited` and
keeps its staging. Continue on a later UTC day with
`python3 -m worldmodel acquire un_comtrade --allow-network --resume`. To pick up newly
published months, rerun with `--restart`. The earlier 2025-2026 artifact
(`b21e1049...`, 172 MB) is kept per the retention rule.

### Acquired 2026-09-15

460,283,083 bytes, 24 shards, 25 requests (one retry), 474,070 source rows, 52 reporters with
data, covering 2024-01 to 2026-08. No response hit the 100,000-record cap.

Coverage is limited by what reporters publish, not by request errors:
- China has only 2024 (12 months); Kuwait only 2024.
- Russia (643), Vietnam (704), UAE (784) and Austria (40) return no monthly rows at all.
- France, Korea, Turkey, Singapore and Nigeria stop at 2025-12; Peru at 2025-04; Ukraine at 2025-09.
- Most large reporters run to 2026-05/2026-07; Norway reaches 2026-08.

The AG2/TOTAL rows have no quantity unit, so no `trade_quantity` observations are emitted.
The earlier 2025-2026 grid (artifact `b21e1049...`, 172.1 MB, 100 requests) is retained.

## Credential

`COMTRADE_API_KEY` (free subscription key: sign in at https://comtradedeveloper.un.org/ and
subscribe to "comtrade - v1"). It is sent only as the `Ocp-Apim-Subscription-Key` header, so
it never appears in receipts. The rate limit is 0.5 requests/s and 450 requests/day, shared
under the key `comtradeapi.un.org`.

## Licence

UN Comtrade terms of use (https://comtradeplus.un.org/Legal): data are free to use with
attribution ("United Nations Comtrade Database"). Bulk redistribution or resale of the
downloaded data is not permitted, so keep raw and normalized payloads local.

## Output (`normalized`)

Subject = reporter. Countries use `iso3:XXX`. Statistical areas use
`comtrade:area:<M49 code>` (World = `comtrade:area:0`, 490 = "Other Asia, nes").
Products are `hs:TOTAL`, `hs:NN` or `hs:NNNN`, and the HS edition of each reporter is kept in
`dimensions.classification`. Dimensions: partner, product, flow (import/export/re_import/
re_export), `frequency=monthly`, classification, customs_code, mode_of_transport,
partner2, mode_of_supply. Valid window = calendar month.

Metrics:
- `trade_value` (USD; primaryValue = CIF for imports, FOB for exports; both values are in attributes)
- `trade_net_weight` (kg; the estimation flag is in attributes)
- `trade_quantity` (publisher unit, only when a quantity unit exists)

## Rebuild

```sh
python3 -m worldmodel acquire un_comtrade --dry-run
python3 -m worldmodel acquire un_comtrade --allow-network     # --resume on later days if the daily cap stops it
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run un_comtrade
python3 -m worldmodel verify un_comtrade
python3 -m unittest data/un_comtrade/tests/test_pipeline.py
```
