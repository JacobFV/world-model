# fred_deposit_rates

Long-history U.S. deposit rates, percent, with every ALFRED real-time vintage.

## Why this dataset exists

`deposit_rate_pass_through` declares FRED **SNDR** (National Rate: Savings) as its deposit
rate. SNDR begins **2021-04** and has 65 monthly observations. The component needs 36 to fit
and declares 24 holdout forecasts, so SNDR can only just satisfy both, and it cannot be
extended backwards because the series does not exist earlier. This dataset publishes the
longer deposit rates that a *substituted* attempt can use:

| Series | Object | Coverage | Native frequency | ALFRED vintages | Rows |
| --- | --- | --- | --- | --- | --- |
| `SAVNRNJ` | FDIC National Rate on Non-Jumbo Deposits (< $100,000): **Savings** | 2009-05-18 .. 2021-03-29 | weekly (as of Monday) | 365, from 2014-03-10 | 624 |
| `MMNRNJ` | FDIC National Rate on Non-Jumbo Deposits (< $100,000): **Money Market** | 2009-05-18 .. 2021-03-29 | weekly (as of Monday) | 365, from 2014-03-10 | 624 |
| `M2OWN` | St. Louis Fed **M2 own rate**: weighted average of the rates received on the interest-bearing assets in M2 (other checkable deposits, thrift savings deposits, money market mutual fund holdings, small time deposits) | 1959-02 .. 2019-06 | monthly | 203, from 2014-03-07 | 10,515 |

`SAVNRNJ` and `MMNRNJ` are SNDR's direct predecessors: the same FDIC National Rates
programme, discontinued in 2021 when FDIC replaced a simple average over a sampled branch
panel with a deposit-weighted average over all institutions (credit unions included). Levels
and pass-through estimates are therefore **not comparable across the 2021 break**, which is
why nothing here is spliced onto SNDR. `M2OWN` is a realised average cost of the M2
interest-bearing assets, not an advertised retail rate, and it includes money-fund holdings
that are not bank deposits — a third distinct object.

Only `SAVNRNJ` had a row count small enough to reveal it, but note the shape of these
series: the FDIC national rates are never revised (one real-time period per weekly value,
`realtime_start` equal to the publication week from 2014-03 onward), while `M2OWN` is revised
heavily (up to 118 vintages for a single month).

## Rebuild

```sh
wm acquire fred_deposit_rates --dry-run                # plan and allocation, no network
wm acquire fred_deposit_rates --allow-network          # 3 requests, ~1.5 MiB
wm run fred_deposit_rates && wm verify fred_deposit_rates
python3 -m unittest discover -s data/fred_deposit_rates/tests -t data/fred_deposit_rates/tests
```

- Credential: `FRED_API_KEY` (query `api_key`, injected per request and redacted in receipts).
  Rate limit 1.5 req/s shared with the other `fred_*` datasets (`rate_limit.key =
  api.stlouisfed.org`).
- One shard per series: all vintages fit in a single request each (365, 365 and 203 vintage
  dates against FRED's 2,000-vintage JSON limit; 624, 624 and 10,515 rows against the 100,000
  row limit). All three series are discontinued, so no window will grow.
- No sampling adapter: the pipeline raises unless given the full sharded artifact.

## Evidence

`normalized` (gzip evidence JSONL), emitted by the shared `fred_alfred.py` helper (a byte
copy of the one in `fred_policy_rate` and `fred_macro_panel`; each dataset keeps its own copy
because code snapshots capture only local files):

- entities `geo:US` (country) and `fred:<SERIES>` (economic_series), plus one
  `fred:<SERIES> describes_location geo:US` assertion per series;
- observations `subject=geo:US`, `metric` per the table below, `unit=percent`,
  `valid_from`/`valid_to` the half-open native period (a weekly Monday value spans
  `[Monday, Monday+7)`), `dimensions = {series_id, frequency, seasonal_adjustment, vintage}`
  with `vintage` the ALFRED `realtime_start`;
- `attributes.realtime_start`/`realtime_end` give the real-time period the value was current
  in, and `observed_at` is `realtime_start` — the knowledge time a point-in-time backtest
  reads. Real-time periods are never split here because each series fits one request window.
- History published before ALFRED began archiving a series carries that series' first vintage
  date as `realtime_start`, so under a strict vintage policy nothing from these series is
  visible before **2014-03**.
- `.` (not available in that vintage) becomes `null` with `missing_reason`.

| Series | `metric` |
| --- | --- |
| `SAVNRNJ` | `national_rate_non_jumbo_savings` |
| `MMNRNJ` | `national_rate_non_jumbo_money_market` |
| `M2OWN` | `m2_own_rate` |

## Rights

FRED API Terms of Use apply. `SAVNRNJ`/`MMNRNJ` are FDIC compilations of rates gathered by
RateWatch; `M2OWN` takes its money-fund rate input from iMoneyNet (Informa Financial
Intelligence), recorded on every `M2OWN` observation as
`attributes.third_party_copyright`. Internal use; cite FRED and the originating agency and do
not redistribute the third-party inputs.
