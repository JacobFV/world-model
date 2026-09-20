# county_monthly_realtime_panel

Monthly first releases of [`fred_county_laus_monthly_vintages`](../fred_county_laus_monthly_vintages/README.md):
for each county, LAUS family and **reference month**, the value from the earliest vintage that
released it, carrying that vintage date as `dimensions.available_at`. Such a value never changes
afterwards, so `dimensions.revisions` is `none` and an as-of reader of this panel sees no later
revision of any number.

It is the monthly sibling of [`county_realtime_panel`](../county_realtime_panel/README.md), whose
LAUS features begin at reference year 2019 because that is when the structured `LAUCN…` annual
series entered ALFRED. The alias families here (`…URN`, `…LFN`) reach back to a first vintage of
2005-06-08.

| feature | metric | unit |
| --- | --- | --- |
| `rt:laus_monthly_unemployment_rate` | county unemployment rate | percent |
| `rt:laus_monthly_labor_force` | county civilian labour force | persons |

Build it with

```sh
python3 -m worldmodel embed-panel --realtime-monthly
```

## What it does not establish

* **The early cross-section is one Federal Reserve district, not a national sample.** The 339
  county-equivalents with first releases from 2005-07-06 are every county of AR, IL, IN, KY, MS, MO
  and TN — the whole Eighth District — because FRED is the St. Louis Fed and archived its own
  district first. National coverage starts with reference month **2007-05**, published 2007-07-05,
  and every county-equivalent in the source has a first release from reference month 2008-03 on. A
  panel-wide statistic over reference months before 2007-05 is a statistic about the Eighth District.
* **Units are the source's harmonised ones, not the labels FRED printed.** FRED published county
  labour force as *Thousands of Persons* through the 2016-03-17 vintage and as *Persons* from
  2016-03-18, and the observations changed with the label. The source applies the multiplier of each
  row's own vintage, so labour force here is in **persons** in every vintage and is comparable
  across the 2016 break — but it is not the literal number that vintage printed.
* **The archive's opening snapshot is excluded.** Every series' first ALFRED vintage carries
  already-revised history back to 1990-01. Those months are not releases and are not in this panel;
  their absence is the archive's left edge, not a missing publication.
* **Hancock County, KY has two FRED aliases** with differing archives. Both map to
  `geo:US:county:21091`; where both released the same month, the earlier release is kept and the
  other dropped. They are not merged or averaged.
* **Reference months before 2005-04 are out of scope.** FRED extended `DCDIST5URN` back to 1976 in a
  2016 vintage, and a first release published forty years after its reference month is not a
  contemporaneous one.
* A first release is what the agency published then, not its best current estimate; a series that
  entered the archive late is dated later than it was truly public, never sooner.
