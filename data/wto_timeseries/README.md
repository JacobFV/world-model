# wto_timeseries

WTO Timeseries API indicators: tariff profile averages (MFN applied simple and trade-weighted, bound) and
merchandise / commercial services trade values, all reporting economies, 2015-2026.

## Status: awaiting credentials

`api.wto.org` returns 401 without a subscription key (verified 2026-09-15).

1. Sign up at <https://apiportal.wto.org/signup>, subscribe to the **Timeseries** product and copy the
   primary key.
2. Add `WTO_API_KEY=...` to the project `.env` (never commit it). It is sent as the
   `Ocp-Apim-Subscription-Key` header, which is never recorded.
3. Verify indicator codes before the first real run (codes in `dataset.json` are from WTO Stats and were not
   verifiable without a key): `https://api.wto.org/timeseries/v1/indicators?lang=1` — adjust
   `acquisition.parameters.indicator`; unknown codes are skipped (400/404) rather than failing.
4. `python3 -m worldmodel acquire wto_timeseries --dry-run`, then `--allow-network`, then
   `python3 -m worldmodel run wto_timeseries`.

## Evidence

- Entities: reporters as `iso3:XXX` (WTO numeric codes mapped via local `countries.json`, taken from
  `worldmodel/reference/countries_iso3166.csv`); groups such as `918` (European Union) or `000` (World) as
  `wto:economy:<code>` jurisdictions with `aggregate: true`.
- Observations: `mfn_applied_tariff_simple_avg_all_products`, `mfn_applied_tariff_trade_weighted_avg_all_products`,
  `bound_tariff_simple_avg_all_products` (percent), `merchandise_exports_value`, `merchandise_imports_value`,
  `commercial_services_exports_value`, `commercial_services_imports_value` (e.g. `million_USD`); any other
  indicator is emitted as `wto_<code>`. Dimensions: `frequency` (annual/quarterly/monthly), `partner`,
  `product_or_sector`, `classification`, `indicator`; valid windows from `Year` + `PeriodCode`.

## Licence

WTO terms of use: non-commercial use with attribution ("Source: WTO"); redistribution restricted. Recorded
as `non_commercial: true` in `dataset.json`.

## Local files

`artifacts/` and `scratch/` are ignored by Git.
