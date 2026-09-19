# fred_county_laus_monthly_vintages

Monthly LAUS county series with **every ALFRED real-time vintage**: two families, 6,282 series,
3,140 county-equivalents each, a median of 233 vintages per series reaching back to 2005-06-08. The deep half of
the county LAUS archive, and the sibling of
[`fred_county_vintages`](../fred_county_vintages/README.md), which holds the annual averages.

## Why it exists, and why it is a sibling rather than a tenth family

Two tracks were blocked on the same gap. `county_realtime_panel` — the first-release county panel the
places attempts now run on — carries LAUS only from reference year **2019**, because that is when the
structured `LAUCN…` annual series entered ALFRED. And the causal layer's wave-2 report named monthly
county outcomes as the next registration worth running, which needs a monthly county series dated by
publication rather than by revision. [`docs/county-vintages.md`](../../docs/county-vintages.md)
established that ALFRED holds exactly that — FRED's monthly LAUS **aliases**, the unemployment rate
(`…URN`) and the civilian labor force (`…LFN`) — and that they were left out of
`fred_county_vintages` on size alone.

They are published here rather than as new families in `fred_county_vintages` because:

1. **The annual dataset is one raw artifact.** Its 31,452 shards are declared as one
   `parameters.series_id` list; adding families changes that list, so the whole artifact would be
   re-acquired and its 5.3 M records rebuilt — for data its only consumer,
   `worldmodel/embedding/county_realtime.py`, does not read (it keys on `dimensions.family`).
2. **The byte envelopes differ by an order of magnitude** — 800 MiB against this dataset's declared
   2 GiB ceiling — and the fair-share ledger books bytes per dataset. Apart, each declaration stays
   honest and either can be re-acquired or dropped alone.
3. **Nothing joins them but the county code**, and both emit the same record contract, so a consumer
   that wants both simply reads both stages.

The record contract *is* the annual dataset's: `alfred.py` here is a copy of
`fred_county_vintages/alfred.py`, changed only in that reference periods are months and there is no
BEA combination-area code scheme. `worldmodel/embedding/realtime_panel.py` reads either stage
unchanged.

## What it publishes

One `observation` per (series, reference **month**, ALFRED real-time period):

| field | meaning |
| --- | --- |
| `subject` | `geo:US:county:<SSCCC>` (see *County codes*) |
| `metric` / `unit` | `unemployment_rate` / percent, `labor_force` / persons; `unit` is the unit **published in that vintage** |
| `valid_from`, `valid_to` | the reference month, half-open: `2008-09-01` .. `2008-10-01` |
| `observed_at` | the period's `realtime_start`: the day this value became public |
| `dimensions.vintage` | the period's ALFRED `realtime_start` |
| `attributes.realtime_end` | closes the period, so interval lookup recovers the value current on any date |
| `dimensions.first_release` | `true` on the earliest period of a reference month, when that period starts after the series' first ALFRED vintage |
| `attributes.realtime_start_clipped` | `true` on rows in the series' first ALFRED vintage: the archive's opening snapshot of already-revised history, never a first release |
| `attributes.series_first_vintage` | that first vintage |
| `dimensions.family`, `dimensions.program` | which of the two families, and `laus` |
| `attributes.fips_source` | `geofred`, `geofred_alias_base` or `release_structured_id` (see *County codes*) |
| `attributes.source_units`, `attributes.unit_multiplier` | what FRED called the units in that vintage, and what they were multiplied by |

Missing values (`.` in FRED) are kept as `value: null` with `missing_reason: not_available_in_vintage`.
The records also carry a `county` entity per code (`within` its state, `within` `geo:US`) and an
`economic_series` entity per series (`describes_location` its county).

| family | FRED id form | metric | unit | series | first vintage | vintages/series (measured) |
| --- | --- | --- | --- | ---: | --- | --- |
| `laus_monthly_unemployment_rate` | `TXHARR1URN` | `unemployment_rate` | percent | 3,141 | 2005-06-08 | 42-233-264 |
| `laus_monthly_labor_force` | `AKALEU0LFN` | `labor_force` | persons | 3,141 | 2005-06-08 | 43-233-264 |

Metric names match `fred_county_vintages`, `bls_labor` and `bea_national_regional`, so a consumer can
swap a revised feature for its real-time counterpart without renaming. There is **no monthly county
series for employed or unemployed persons with a deep archive**: those exist at county level only in
the structured `LAUCN…` form, whose archive opens on 2019-08-28, which is the same wall the annual
LAUS families hit.

## The units change that a vintaged reader must not miss

**FRED re-expressed county labor force from "Thousands of Persons" to "Persons" on 2016-03-18, and
the published observations changed with the label.** `CTFAIR1LFN` for reference month 2015-06 is
`489.537` in the 2016-03-01 vintage and `489537` in the 2016-03-18 one — the same estimate, written
two ways. A reader that takes the series' *current* units and applies them to every vintage
understates every pre-2016 labor force by a factor of 1000.

`build_config.py` therefore fetches the units history of **every** series in that family (not one per
state) and `alfred.py` picks the multiplier by the row's own `realtime_start`, exactly as the annual
dataset does for real GDP's rebasing. Seven series do not follow the family's history and carry their
own in `config.json`:

| series | what it does instead | why |
| --- | --- | --- |
| `AKWADE0LFN`, `KYHANC1LFN`, `SDSHAN3LFN` | thousands in every vintage | discontinued in 2015, before the change |
| `AKPRIN1LFN`, `AKSKAG1LFN`, `AKWRAN0LFN` | switch on **2018-05-02** | Alaska areas discontinued in 2009; FRED relabelled them late |
| `VABEDF5LFN` | switches on **2015-02-04** | Bedford City, VA was absorbed into Bedford County in 2013 |

A series' own ALFRED opening date is *not* an exception: it only moves the first run's left edge, and
`vintage_units` falls back to the first run for anything earlier, so keying on it would have called
1,679 identically-behaving series exceptions. The unemployment rate is in percent in every vintage of
every series, and its units history is the sampled one (51 series, one per state, plus the release
catalogue agreeing on `Percent` for all 3,141).

## County codes

The alias id carries **no area code**: `CTFAIR1URN` names a state and an abbreviation, not a FIPS. No
code is ever read out of a title. Codes come from three sources, strongest first, and
`attributes.fips_source` says which:

1. **`geofred`** (3,129 / 3,126 series) — GeoFRED's published county groups, 1224 *Unemployment Rate*
   and 656 *Civilian Labor Force*, both monthly, list these alias ids directly. (This is why the
   annual LAUS families in `fred_county_vintages` had to fall back on their own ids: GeoFRED
   publishes the alias, not `LAUCN…`.)
2. **`geofred_alias_base`** (0 / 3 series) — `SDSHAN3URN` and `SDSHAN3LFN` are one BLS area under one
   FRED alias base, so where only one of the two groups lists the base, the code carries across. An
   id-level join. It rescues `AKWADE0LFN`, `SDSHAN3LFN` and `KYHANC1LFN`, which group 656 omits and
   group 1224 lists.
3. **`release_structured_id`** (12 series in each family) — the structured `LAUCN<fips>…` series that
   release 116 publishes under the **same place label**. The code still comes from an id; the label
   is only the join key, and the join is *checked*, not assumed: for the **3,122 series in each
   family** where GeoFRED and a structured twin both speak, **they agree 3,122 times with no
   disagreement**, and no place label in the release is claimed by two FIPS codes. This is what puts
   the eight pre-2023 Connecticut counties, Broomfield County CO and three discontinued Alaska areas
   into the dataset; GeoFRED's county layer publishes only current geography and omits them at every
   reference date from 1976 on.

Where GeoFRED publishes a code Census has retired — **46113** (Shannon County, SD) and **02270**
(Wade Hampton Census Area, AK) — `CODE_CHANGES` maps it to the successor (46102, 02158) with the
Census county-changes list as its source, exactly as the annual dataset does: a rename leaves the
territory unchanged, and a place should have one subject across datasets.

3,141 series carry 3,140 codes in each family because FRED publishes **two aliases for Hancock
County, KY** (`KYHAURN`/`KYHALFN` and `KYHANC1URN`/`KYHANC1LFN`). They are listed in
`config.json`'s `shared_codes` and both are emitted, not merged: the archives differ, and picking one
would be a truth selection this dataset does not make.

**18 series are skipped per family and listed in `config.json`**: the release's Federal Reserve
district aggregates (`D1URN` … `D12LFN`, `DKYURN` …), which are not counties and have no code from
any source. Nothing else is dropped. There are **no Puerto Rico aliases** — the 78 municipios appear
only in the annual structured rate — so coverage is the 50 states and DC.

## Measured coverage

Published stage `4b58e747`, raw artifact `6b5c8ae3`: **6,282 shards, 1,541,203,717 bytes downloaded
(1.44 GiB on disk against a declared 2 GiB ceiling), 16,157,146 records (16,138,199 observations,
1,459,534 of them first releases), 365,322,814 bytes gzip**. FRED refused **no** series: every
configured alias is in ALFRED. Everything below is counted from the published records by
`measure_coverage.py` (`coverage.json`), not from metadata.

| family | series | county-equivalents | states+DC | vintages/series (min-median-max) | first vintage | latest vintage | refused |
| --- | ---: | ---: | ---: | --- | --- | --- | ---: |
| laus_monthly_unemployment_rate | 3,141 | 3,140 | 51 | 42-233-264 | 2005-06-08 | 2026-09-02 | 0 |
| laus_monthly_labor_force | 3,141 | 3,140 | 51 | 43-233-264 | 2005-06-08 | 2026-09-02 | 0 |

The median county has **233 vintages of every month it has ever been published for**; the annual LAUS
families in `fred_county_vintages` have 10 to 12. A series with 42-43 vintages is one FRED created
recently (Chugach and Copper River, the Connecticut planning regions have no alias at all).

### County-equivalents with a first release, by reference month

A month counts a county-equivalent when its value for that month was published for the first time in
a vintage **after** the series' archive opened. Zeros are not gaps in the data: those months are
present, but only as the opening snapshot of already-revised history, and a real-time panel must not
read them as releases.

The archive opens in three steps, and the middle one is the finding:

| reference months | rate | labor force | published on | what it is |
| --- | ---: | ---: | --- | --- |
| 1976-01 .. 1989-12 | 1 | 0 | **2016-03-18** | `DCDIST5URN` (District of Columbia). Not early real-time data: FRED *extended* that one series back to 1976 in 2016, so these are first releases of forty-year-old months. Excluded from any panel that wants a contemporaneous release |
| 2005-04 .. 2006-06 | 339 | 339 | from **2005-07-06** | the **Federal Reserve Eighth District**: every county of AR (75), IL (44), IN (24), KY (64), MS (39), MO (72) and TN (21), all under FRED's short aliases (`ARARURN`, `KYADLFN`). FRED is the St. Louis Fed and archived its own district two years before the rest |
| 2006-07 .. 2007-04 | 346 | 346 | from 2006-09-07 | seven more join |
| **2007-05** | **3,137** | **3,138** | **2007-07-05** | the national cross-section arrives |
| 2007-06 .. 2008-02 | 3,138 | 3,138 | 2007-08-08 on | |
| 2008-03 onwards | 3,140 | 3,140 | 2008-05-07 on | every county-equivalent in the dataset |

The archive's opening snapshot reaches back to **1990-01** in both families; those months are in the
records with `realtime_start_clipped = true` and are never marked as releases. A reference month is
published on the **fifth to eighth day of the second following month** (2007-05 on 2007-07-05,
2008-03 on 2008-05-07), which is the real publication lag a panel should use rather than a declared
one.

and then holds, losing only counties FRED discontinues:

| reference year | months with a first release | rate: counties (median month) | labor force: counties (median month) |
| --- | ---: | ---: | ---: |
| 2005 | 9 of 12 | 339 | 339 |
| 2006 | 12 | 342 | 342 |
| 2007 | 12 | 3,138 | 3,138 |
| 2008 | 12 | 3,140 | 3,140 |
| 2009 | 12 | 3,140 | 3,140 |
| 2010 | 12 | 3,139 | 3,139 |
| 2011-2014 | 12 | 3,137 | 3,137 |
| 2015 | 12 | 3,136 | 3,136 |
| 2016-2021 | 12 | 3,134 | 3,134 |
| 2022-2024 | 12 | 3,133 | 3,133 |
| 2025 | 11 of 11 | 3,125 | 3,125 |
| 2026 | 7 of 7 | 3,125 | 3,125 |

Per-month numbers for all 423 months with a first release, and counties-with-any-value beside them,
are in `coverage.json`.

### What this unblocks

* **A real-time monthly county panel can take its first origin on 2005-07-06** — the day the first
  releases for reference months 2005-04 and 2005-05 were published — for the 339 Eighth District
  counties, and **on 2007-07-05** for the whole country (3,131 of 3,140 that day, 3,140 by
  2008-05-07).
* **A monthly county outcome can be scored from reference month 2005-04** for those 339 counties and
  from **2007-05** for 3,137 of 3,140. Against `county_realtime_panel`'s LAUS, which starts at
  reference *year* 2019, that is **twelve more years and 148 more monthly observations per county**
  before the first year the annual panel can score.
* It does **not** reach QCEW employment, which FRED does not carry at county level in any form, or
  monthly employed/unemployed persons, which exist only in the structured ids from 2019-08-28.

## Building a monthly first-release panel

```python
from worldmodel.store import Store
store = Store(data_root)
records = store.records(store.latest('fred_county_laus_monthly_vintages', 'normalized'))

# 1. First releases: the value as first published, with the day it was published.
first = {(r['subject'], r['dimensions']['family'], r['valid_from'][:7]):
         (r['value'], r['dimensions']['vintage'])
         for r in records if r.get('kind') == 'observation'
         and r['dimensions']['first_release'] and r['value'] is not None}

# 2. As of a date: what a forecaster standing on `day` could have read for any reference month.
def as_of(record, day):
    return record['attributes']['realtime_start'] <= day <= record['attributes']['realtime_end']
```

`worldmodel/embedding/realtime_panel.py:first_releases` indexes a period by the **calendar year** of
its `valid_from`, so pointing it at this dataset unchanged would collapse twelve months onto one
year and keep whichever came first. A monthly panel wants a month key; that is a change to
`worldmodel/embedding/**`, which this track does not own and reports instead.

## Rebuild

```sh
FRED_API_KEY=... python3 data/fred_county_laus_monthly_vintages/build_config.py [--cache DIR]
python3 data/fred_county_laus_monthly_vintages/build_config.py --emit-parameters   # dataset.json series list
python3 -m worldmodel acquire fred_county_laus_monthly_vintages --allow-network    # 6,282 requests, ~1 h at 1.9/s
python3 -m worldmodel run fred_county_laus_monthly_vintages
python3 -m worldmodel verify fred_county_laus_monthly_vintages/normalized
python3 data/fred_county_laus_monthly_vintages/measure_coverage.py --markdown      # coverage.json
```

`build_config.py` re-fetches the labor force units history for every series, which is most of its
wall time; `--cache DIR` makes a rerun free.

ALFRED membership is not probed per series: every request asks for the whole real-time window, and
FRED answers `400 "does not exist in ALFRED"` for a series it does not archive, which the declaration
records as skipped (`skip_statuses: [400]`) and `coverage.json` counts.

## Rights

FRED API Terms of Use; the underlying series are US federal statistics in the public domain. Cite
FRED/ALFRED and the Bureau of Labor Statistics. No third-party copyright series are included.
