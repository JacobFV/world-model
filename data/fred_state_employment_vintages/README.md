# fred_state_employment_vintages

BLS **CES State and Area (SAE)** payroll employment by state-equivalent and CES supersector,
monthly, not seasonally adjusted, with **every ALFRED real-time vintage**.

## Why it exists

`docs/calibration-status.md` recorded `regional_model` failing `no_revision_leakage` on both the
CBP and the QCEW panel, and called the failure structural: *"no published employment source in
this catalog carries vintages, so `regional_model` cannot pass that criterion on CBP or QCEW
whatever the intervals do."*

That is true of the catalog and false of the world. ALFRED archives the state-by-supersector SAE
series: `TXMFGN` ("All Employees: Manufacturing in Texas", NSA monthly) has **229 vintage dates
from 2007-06-19 to 2026-08-21**, one per state employment release. The audit behind this dataset,
with every source checked and its HTTP status, is
[`docs/research-log/ws-b-employment-vintages.md`](../../docs/research-log/ws-b-employment-vintages.md).

## What it publishes

One `observation` per (series, observation month, ALFRED real-time period):

| field | meaning |
| --- | --- |
| `subject` | state geography, `geo:US:state:48` |
| `metric` / `unit` | `employment` / `jobs` (published thousands of persons x 1000) |
| `valid_from`, `valid_to` | the observation month, half-open |
| `dimensions.vintage` | the period's ALFRED `realtime_start` — **the publication date** |
| `attributes.realtime_end` | closes the period, so interval lookup recovers any vintage |
| `dimensions.industry` | CES supersector, e.g. `manufacturing`, `government` |
| `dimensions.ces_industry_code` | the BLS 8-digit CES industry code |
| `dimensions.panel_role` | `panel` (used to build the shift-share panel) or `residual_check` |
| `attributes.realtime_start_clipped` | true where the row's period starts at the archive boundary, i.e. the value is a 2007 snapshot of already-revised history and **not** a first release |

## Two things it deliberately does not publish

**The tenth industry.** Mining/logging/construction has no aliased FRED series for Delaware, DC,
Hawaii, Maryland or Nebraska, and the structured `SMS<fips>0000015000000001` form that does cover
them only enters ALFRED in 2014 — which would cut the real-time window from nineteen years to
twelve. So the panel carries the nine universally archived supersectors plus total nonfarm, and
the residual `total_nonfarm - sum(nine)` is formed **inside one vintage** by
`worldmodel/estimation/loaders.py::ces_sae_regional_data`. The residual is exact, not
approximate; checked at 2019-06 against published levels:

| state | total nonfarm | Σ nine | residual | published Construction + Mining&Logging |
| --- | ---: | ---: | ---: | ---: |
| Texas | 12,836.3 | 11,805.3 | **1,031.0** | 778.6 + 252.4 = **1,031.0** |
| Wyoming | 298.9 | 253.8 | **45.1** | 23.9 + 21.2 = **45.1** |

`construction` and `mining_logging` are carried where published (`panel_role =
'residual_check'`) so the identity can be re-checked per vintage instead of trusted.

**Annual averages.** An annual average is twelve monthly values drawn from *one* vintage; that is
what keeps it real time. Only a consumer that knows which vintage it is standing in can form it,
so it is not precomputed here.

## Vintage depth is not uniform, and the panel is only as deep as its shallowest member

Each series records `vintage_count` and `first_vintage`, and `config.json`'s `coverage` block
reports the binding constraint. As of 2026-09-17 that is **Information**, whose state NSA series
enter ALFRED on **2011-11-22**; every other supersector starts 2007-06-19. Any protocol declared
against this dataset must start after the binding first vintage, not after the optimistic one.

## Rebuild

```sh
FRED_API_KEY=... python3 data/fred_state_employment_vintages/build_config.py    # discover + verify series
python3 data/fred_state_employment_vintages/build_config.py --emit-combinations # paste into dataset.json
python3 -m worldmodel acquire fred_state_employment_vintages --allow-network
python3 -m worldmodel run fred_state_employment_vintages
python3 -m worldmodel calibrate-all --attempt regional_model.ces_sae_realtime
```

`build_config.py` discovers ids from the release-112 catalogue by title and then verifies each one
against `series/vintagedates`; it never constructs an id from a template and assumes it exists.
ALFRED coverage is patchy in both directions — `TXINFO` has 229 vintages while `CAINFO` is not in
ALFRED at all, and `SMU48000003000000001` answers *"does not exist in ALFRED"* where its alias
`TXMFGN` has the full archive.

## Rights

FRED API Terms of Use; the underlying series are US federal statistics in the public domain. Cite
FRED and BLS CES State and Area. No third-party copyright series are included.
