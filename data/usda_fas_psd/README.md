# usda_fas_psd

USDA Foreign Agricultural Service **Production, Supply and Distribution (PSD)**
balance sheets: production, imports, exports, consumption and stocks for 63
commodities (grains, oilseeds, meals, oils, cotton, sugar, coffee, meat, dairy, fruit)
by country and local marketing year.

## Source and scope

- File: `https://apps.fas.usda.gov/psdonline/downloads/psd_alldata_csv.zip`
  (one ZIP, `psd_alldata.csv`, about 10 MB compressed and 200 MB uncompressed, refreshed
  monthly after WASDE).
- Acquisition: `files` strategy, one shard. No credential.
- Normalization keeps market years `>= parameters.min_year` (default 2000). The raw
  artifact keeps the full history back to 1960.

## Evidence emitted (`normalized`, gzip JSONL)

- Entities: `iso3:XXX` (`country`) for current ISO 3166 countries. Historical units
  (USSR, Yugoslavia, Czechoslovakia, ...) and aggregates (`E4` EU, `E2`/`E3`, `ZZ`
  Other, `BE` Belgium-Luxembourg) use `usda_psd:country:<code>` (`jurisdiction`,
  `aggregate` flag). Commodities are `usda_psd:commodity:<7-digit code>` (`commodity`).
- Observations: subject country, `metric` = PSD attribute (`production`,
  `area_harvested`, `yield`, `beginning_stocks`, `imports`, `exports`,
  `domestic_consumption`, `feed_domestic_consumption`,
  `food_seed_industrial_consumption`, `crush`, `ending_stocks`, `total_supply`,
  `total_distribution`, `trade_year_imports`/`exports`, ...). Units: `1000 t`, `t`,
  `1000 ha`, `t/ha`, `1000 head`, `1000 480-lb bales`, `1000 60-kg bags`,
  `1000 t carcass weight equivalent`, `percent`, `ratio`. Dimensions:
  `commodity`, `marketing_year`, `frequency=annual`.
  Attributes: `attribute_id`, `estimate_as_of` (PSD calendar year and month of the
  value), `period_basis`.
- Time: `[marketing_year-01-01, marketing_year+1-01-01)`. This is an approximation.
  PSD marketing years start in different months for different commodities and countries,
  and the exact start month is not in the file.
- Blank values become `value: null` with `missing_reason: source_blank`. Zeros are kept.
- The file holds only the latest estimate for each cell. Revision history needs monthly
  snapshots (re-acquire each month).

## Licence

US federal government work, public domain. Please cite USDA FAS PSD Online.

## Rebuild

```sh
wm acquire usda_fas_psd --dry-run
wm acquire usda_fas_psd --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run usda_fas_psd
wm verify usda_fas_psd
python3 -m unittest data/usda_fas_psd/tests/test_pipeline.py
```

`artifacts/` and `scratch/` are ignored by Git.
