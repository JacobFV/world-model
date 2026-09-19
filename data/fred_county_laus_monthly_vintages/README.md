# fred_county_laus_monthly_vintages

Monthly LAUS county series with **every ALFRED real-time vintage**: two families, 6,282 series,
3,140 county-equivalents each, 216-264 vintages per series from 2005-06-08 and 2007-06. The deep half of
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

| family | FRED id form | metric | unit | series | first vintage | vintages (sampled) |
| --- | --- | --- | --- | ---: | --- | --- |
| `laus_monthly_unemployment_rate` | `TXHARR1URN` | `unemployment_rate` | percent | 3,141 | 2005-06-08 | 216-264 |
| `laus_monthly_labor_force` | `AKALEU0LFN` | `labor_force` | persons | 3,141 | 2005-06-08 | 216-264 |

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

_Filled by `measure_coverage.py` after acquisition; see below._

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
