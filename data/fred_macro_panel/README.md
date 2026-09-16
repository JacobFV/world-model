# fred_macro_panel

Curated FRED/ALFRED macro-financial panel for the estimation layer. It covers national accounts, prices, labor, policy
rates and the Treasury yield curve, credit spreads, money and bank credit, the Fed balance sheet, housing, industrial
production, trade, fiscal series, FX and commodities, plus state-level series (unemployment, payrolls, participation,
claims, personal income, GDP, house prices, permits, population). Every observation carries real-time vintage metadata.

## Source and scope

- Publisher: Federal Reserve Bank of St. Louis, FRED API `series/observations`
  (https://fred.stlouisfed.org/docs/api/fred/).
- The series list lives in [config.json](config.json) (tracked): `national` rows are
  `[category, tier, series_id, metric]` and `state_families` rows are templates expanded over the 50 states and DC.
  `build_config.py` fetches FRED metadata (title, units, frequency, seasonal adjustment, copyright notes), converts
  units to explicit unit tokens and multipliers, splits vintage-tier history into request windows, and writes
  `config.json#series`, `config.json#dropped` (IDs FRED does not have) and `dataset.json#acquisition.combinations`.
- Two tiers:
  - `vintages`: every ALFRED vintage (`realtime_start=1776-07-04` to `9999-12-31`, windows of at most 1,900 vintage
    dates because FRED allows 2,000 per JSON request). For point-in-time backtests.
  - `as_of`: values in effect on `config.as_of` plus every later revision (`realtime_start=as_of`). A
    `realtime_start` equal to `as_of` is clipped and is not a first-publication date.
- Tier assignment: since the 100 GiB budget update **every series** (national and all 13 state families) is in the
  `vintages` tier. The `as_of` tier remains supported for future additions. Daily series need several request windows
  each; their last window fills at about 250 vintage dates a year. The series named by
  `worldmodel/estimation/requirements.json` are listed in `config.json#estimation_required`; the revisable ones
  (GDPC1, GDPPOT, PAYEMS, INDPRO, TOTALSL, DPSACBW027SBOG, RSAFS, RRSFS, CPIAUCSL, POPTHM) all have vintages.
- Estimation contract: `SeriesRequirement.matches` selects on exact `metric` and `unit` and the requirements use
  publisher-scale units. For the series in `config.json#estimation_overrides`, the pipeline uses the requirement's
  metric and unit and rescales values from base units by `unit_scale`. Examples: PAYEMS is `nonfarm_payroll_employment`
  in `thousand_persons`, TOTALSL is `consumer_credit_outstanding` in `billion_USD`, POPTHM is `resident_population`
  in `people`, and MPRIME is `prime_loan_rate` (DPRIME is `prime_rate_daily` so the two do not double count). All other
  series use base units with the multiplier applied. Every observation carries `attributes.source_series` and
  `attributes.series_id` (the FRED id).
- Metro-area labor series come from `bls_labor` (LAUS, CES state and metro), so they are not repeated here; the
  regional model's family requirements (QCEW, CBP, IRS migration, PEP, LAUS) do not use FRED series.
- Dropped from the spec (recorded in `config.json#dropped`): `ALMFG` and `DCMFG` (FRED has no such series) and
  `SP500` (exists only in FRED, not in ALFRED, so no real-time request is possible; it is S&P-licensed anyway).
- Credential: `FRED_API_KEY` (query `api_key`, injected per request, redacted in receipts). Rate limit 1.5 req/s,
  shared with the other `fred_*` datasets through `rate_limit.key = api.stlouisfed.org` (FRED allows about 120/min).
- No sample: the anchor datasets (`fred_cpi`, `fred_policy_rate`, ...) hold the schema-exploration samples.

## Evidence (`normalized`, gzip evidence JSONL)

- Entities: `geo:US` (country), `geo:US:state:<FIPS>` (state, `within geo:US`), and `fred:<SERIES>`
  (`economic_series`, with title, source units, multiplier, frequency, category, tier and copyright flags). Each series
  links to its geography with `describes_location`.
- Observations: `subject` = geography, `metric` from config (snake_case, shared with the anchor datasets), numeric
  `value` with the FRED scale applied (for example "Billions of Dollars" is multiplied by 1e9 and the unit is `USD`),
  `unit`, half-open `valid_from`/`valid_to` by frequency (week-ending series cover the seven days ending on the date),
  and `dimensions = {series_id, frequency, seasonal_adjustment, vintage}` with `vintage` = `realtime_start`.
  `attributes` hold `realtime_start`, `realtime_end`, `realtime_start_clipped`, `vintage_tier`, `retrieved_at` and,
  when relevant, `source_units`/`unit_multiplier` and `third_party_copyright`.
- Knowledge time: `observed_at` = `realtime_start`. Value `.` (the observation does not exist in that vintage) becomes
  `null` with `missing_reason = not_available_in_vintage`.
- Real-time periods cut at request-window boundaries are merged back. Responses whose `count` exceeds the rows returned
  are rejected, so truncation cannot pass silently.

## Per-vintage units (important for cross-vintage comparisons)

ALFRED observations carry no units, and FRED rebases chained-dollar and index series at benchmark
revisions, so each vintage is denominated in the base period current **at that vintage**. Labelling every
vintage with the series' present units makes levels incomparable across rebasings (it produced nonsense
output gaps of +22.5% for 1995Q4 during calibration). Some series also change scale: TOTALSL was published in
billions, then millions, so a single multiplier is wrong by 1000x for part of its history.

`build_config.py --units-history` records each series' published units per real-time period in
`config.json#series[*].units_history` (`realtime_start`, `realtime_end`, `source_units`, `unit`, `multiplier`,
`base_period`), and the pipeline resolves every observation against its own vintage:

- `attributes.source_units` is the units string published in that vintage, and `attributes.unit_multiplier`
  is the multiplier actually applied to that value.
- `attributes.base_period` and `dimensions.base_period` name the base period (e.g. `1987`, `chained_2012`
  becomes `2012`, `1982_1984`).
- `attributes.units_change_across_vintages` is true for the 189 series whose units change (of 849).
- The `unit` token is per vintage: a vintage matching the estimation requirement's base keeps the requirement
  name (`billion_chained_2017_USD`), while a rebased vintage is named after its own published units
  (`billion_USD_chained_2012`, `billion_USD_1987`, `index_1992_100`). Cross-base levels therefore cannot be
  selected or aggregated as if they shared a unit. **Compare levels only within one `base_period`**, or use
  base-invariant transforms (growth rates, gaps).

Required series whose units change across vintages: CPIAUCSL, GDPC1, GDPPOT, INDPRO, PCEPILFE, TOTALSL.

## Licence

FRED API Terms of Use (https://fred.stlouisfed.org/docs/api/terms_of_use.html). Most series are US government data.
Series whose FRED notes name a third-party copyright holder (Moody's, ICE, S&P, CBOE, University of Michigan,
Freddie Mac, NASDAQ, ...) are flagged in `config.json` and on each observation. Use them internally only and do not
redistribute them.

## Rebuild

```sh
python3 data/fred_macro_panel/build_config.py      # only after editing the series list (needs FRED_API_KEY)
python3 data/fred_macro_panel/build_config.py --refresh   # re-derive units, re-check copyright notes
python3 data/fred_macro_panel/build_config.py --validate  # probe as_of series; drop FRED-only (non-ALFRED) series such as SP500
wm acquire fred_macro_panel --dry-run
wm acquire fred_macro_panel --allow-network        # add --resume after an interruption
WORLD_MODEL_RAW_VERIFY=size wm run fred_macro_panel
wm verify fred_macro_panel
python3 -m unittest discover -s data/fred_macro_panel/tests
```

Changing `as_of`, the series list or the windows changes the acquisition content digest, which means a new
acquisition. Raise `acquisition.desired_bytes` if the dry run or a `budget` stop shows the estimate is too low.
