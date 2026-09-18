# Executive summary — 2026-09-17 research push

**Status: complete.** All six workstreams are done. This file is assembled from the workstream
logs in this directory; every number below is a measured result, and the headline counts were
reconciled on 2026-09-17 against the 75 published validation reports in `data/calibration_reports/`
rather than against any prose summary. Detail lives in the per-workstream files.

## The decision this push acted on

The question was how much data would make this serious research. The answer, measured from this
repo's own record rather than assumed, was **much less than expected — and volume is close to the
wrong axis**.

Of 45 acceptance-criterion failures across the 35 pre-registered attempts in
`docs/calibration-status.md`, **10 are data-availability problems and 35 are not**:

| Blocking criterion | Count | Data-fixable |
| --- | ---: | --- |
| `interval_coverage` | 16 | no |
| `beats_persistence_dm` | 12 | no |
| `no_revision_leakage` | 8 | **yes** |
| `parameters_within_declared_bounds` / `bounds` | 6 | no |
| `minimum_test_forecasts` | 2 | **yes** |

Those are the counts the decision was actually taken on, kept as they stood. The reconciliation
below found them slightly low: they were read off `docs/calibration-status.md`'s Summary table,
which was missing a row for `energy_purchasing.fred_realtime_v2` (a published fourth-wave report
with no row), and "35 attempts" was the plan's 33 registered plus the two recorded `not_run`. Read
from the reports, the same moment was 48 failures across 33 registered attempts —
`interval_coverage` 17, `beats_persistence_dm` 13, `parameters_within_declared_bounds` 7,
`no_revision_leakage` 8, `minimum_test_forecasts` 2, `volatility_crps_skill` 1. The split that
drove the decision — most failures are not data-availability problems — is unchanged.

Two supporting measurements argued the same way:

- The sanctions↔SEC link went from **25 to 617** with no new bytes at all — by reading a
  registration-authority field GLEIF already published. A 24× gain from re-reading existing data.
- Inferred name matching measured **0.4% recall at 2.3% precision, 103,211 entities wrongly
  merged**. So a small dataset carrying CIK, LEI, GEOID or bioguide is worth more than a large one
  without an identifier.

Conclusion: buy only the data that converts directly into validity (~50 GiB does most of the
work), and spend the rest of the effort on the 35 failures that no download can fix.

## Headline numbers

Both columns are measured the same way, because otherwise this table cannot be read. The source is
the published validation reports in `data/calibration_reports/`, joined to
`worldmodel/estimation/real_data_plan.json` for which attempt supersedes which. The unit is the
pre-registered attempt id (the five `cash_balance` issuers are one attempt), and the criterion rows
count **current** attempts only — an attempt that has been superseded is not something the project
still fails, and counting it would make every wave look like a regression. *Before* is the
2026-09-16 state; *after* is now.

| | Before | After |
| --- | --- | --- |
| Validated processes | 3 of 22 | **4 of 22** — WS-A adds `assets_model` on a real-time FX panel |
| Attempts registered | 33 (23 current, 10 superseded) | **51** (30 current, 21 superseded) |
| Attempts passing | 8 — 6 current of 23, plus 2 superseded | **10** — 8 current of 30, plus 2 superseded |
| `beats_persistence_dm` failures | 9 | **14 ▲** — see below; this went **up** |
| `interval_coverage` failures | 10 | **7** — WS-E: coverage now passes on 10 of the 16 rows it diagnosed |
| `parameters_within_declared_bounds` failures | 6 | **7 ▲** — this went **up** |
| `no_revision_leakage` failures | 6 | **6** — flat: WS-A and WS-B closed it on `assets_model`, `monetary_model` and `regional_model`, but each closure is a *new* attempt beside the old one, and the three superseded failures were replaced by successors that inherit the same failure |
| `minimum_test_forecasts` failures | 2 | **0** — WS-D: five new attempts, all clearing it, none passing |
| `beats_year_effect_only_dm` failures | 0 | **2 ▲** — a criterion no regional attempt could reach before WS-B |
| `volatility_crps_skill` failures | 1 | **1** |
| `regional_model` | untestable (no vintaged employment source) | **testable, and tested** — passes `no_revision_leakage`; fails on skill |
| Datasets (`data/*/dataset.json`) | 121 | **125** — WS-C's `census_aspep` and `opm_fedscope` (30.8M records from 1.613 GiB raw), WS-B's `fred_state_employment_vintages`, WS-D's `fred_deposit_rates` |
| Acquired bytes (ledger) | 88.2 GiB of a 500 GiB pool | **90.0 GiB** of 500 GiB, across 126 datasets |
| Disk free | 1.5 TiB (after reclaiming 1,398 GiB) | **1.45 TiB** |

An earlier draft of this row said datasets went 125 → 127. Counted in the repository, declared
datasets went **121 → 125**: the four above, with `fred_state_industry_vintages` declared and then
withdrawn in favour of the employment panel. The +4 is right on either measure; the level was not.

### Two failure counts went up, and that is the most honest thing here

`beats_persistence_dm` went 9 → 14 and `parameters_within_declared_bounds` went 6 → 7. Neither is a
regression in the code. **A criterion that never got evaluated is not a criterion that passed.** Ten
attempts registered this push cleared the data or scoring problem that had been blocking them, which
is exactly what the workstreams were for, and were then graded on a skill test the earlier failure
had made untestable — and failed it:

- three `deposit_rate_pass_through` attempts (WS-D) reached enough forecasts to score and lost to
  persistence on the declared SNDR series and on both substitutes;
- `labor_demand_v4`, `policy_rule_v3`, `energy_purchasing_v3` and `conflict_model_v2` (WS-E) had
  their coverage failure removed and stood revealed as having no edge over a random walk;
- `monetary_model.okun_unrate_realtime_v2` (WS-A) closed `no_revision_leakage` and then failed both
  the skill test and its declared bounds, with `r_star` moving from −1.99 to −5.14 once the
  published unemployment rate was read in real time;
- the two real-time `regional_model.ces_sae` attempts (WS-B) became scorable at all and failed on
  skill, plus the new `beats_year_effect_only_dm`.

Five older skill failures retired with their superseded attempts, so the net is +5. The direction is
the finding: **clearing a data problem converts an untested attempt into a tested failure far more
often than into a pass**, and this push produced exactly two new current passes
(`interest_pass_through.fred_realtime_v2`, `assets_model.fred_fx_realtime`) against ten new
skill failures. Volume was not the binding constraint; neither, on this evidence, was uncertainty
calibration.

## Workstreams

| Workstream | Goal | Status |
| --- | --- | --- |
| [WS-A](ws-a-alfred-vintages.md) | ALFRED vintages for every series a failing process touches | **done** — and it acquired **0 new bytes**: the binding constraint is what the publisher archives, not the download budget. Of the eight `no_revision_leakage` failures, three are closable from vintages already on disk, three belong to WS-B, and two (UCDP, EIA petroleum *supply*) cannot be closed from any vintage archive at any price. `assets_model.fred_fx_realtime` passes **all seven criteria** and makes `assets_model` the fourth validated process; `monetary_model.okun_unrate_realtime_v2` closes the revision criterion and then fails on skill and on bounds |
| [WS-B](ws-b-employment-vintages.md) | vintaged employment, or a definitive negative | **done** — the negative was wrong. ALFRED carries the BLS CES State and Area state-by-supersector series with 229 vintages from 2007-06-19; `fred_state_employment_vintages` (0.085 GiB, 949,207 records) publishes them, and `regional_model.ces_sae_realtime` **passes `no_revision_leakage` and `interval_coverage`** on 306 real-time forecasts. It still fails on skill (DM p = 1.000 vs the mechanism-off baseline), because the Bartik elasticity is −0.58 (SE 1.70) until 2020 is in the sample |
| [WS-C](ws-c-public-employment.md) | ASPEP and FedScope: the public-sector labor graph | **done** — ASPEP 1993-2024 (31 of 32 years; 1996 has no unit file) and FedScope 1998-2024, both verified; 102,269 of 102,270 government units join to `geo:US:county`/`state`, reaching `census_population` at 0.9999 and `usaspending` at 0.9467; FedScope's 51 state keys reach five targets at 1.0000 and `usaspending` at 0.0000, which is a finding rather than a gap. Machine-readable ASPEP microdata starts in 1993, not 1957 |
| [WS-D](ws-d-long-panels.md) | long annual panels for the two small-n attempts | **done** — both evaluable; `population_growth_rate` now fails only `interval_coverage` (n 4 → 9 on 20 PEP vintages, → 16 on POPTHM), `deposit_rate_pass_through` now fails `beats_persistence_dm` on SNDR (n 16 → 24) and on both substitute series |
| [WS-E](ws-e-interval-coverage.md) | uncertainty calibration — the 16, with no new data | **done**, with no new data at all — `interval_coverage` now passes on **10 of the 16** diagnosed rows, and **one** became a full pass (`interest_pass_through.fred_realtime_v2`). Four more had the coverage failure removed and now fail on skill or bounds, which is the useful part: it moves them from "the uncertainty is wrong" to "there is no demonstrated edge over a random walk". Two real code defects found and fixed (a mixed-vintage conditional input that put a fabricated 22% collapse in industrial production into every `labor_demand` design row; conflict counts scored Poisson against a negative-binomial simulator). No threshold relaxed — the declared selection rule twice rejected the wider interval that would have passed |
| [WS-F](ws-f-fec-rebuild.md) | FEC rebuilt under the declared non-commercial purpose | **done** — all three cycles rebuilt under `WM_COMMERCIAL_USE=0`, emitting **153,362,743** contributor rows for **+7.3225 GiB** (10.9519 → 18.2745 GiB across the three dataset directories), and every one of the 5,891,914 aggregate rows is bit-identical by digest before and after. The mechanism adds data without moving a single existing number |

## Already landed this session

- **1,398 GiB reclaimed.** The HuggingFace cache held 95 model repos; 61 of them (598 GiB) had
  symlinks munged by an `rsync --munge-links` copy and were unusable as they stood. Deleted at the
  owner's instruction, auth token preserved. Disk went 109 GB → 1.5 TiB free, 97% → 57% used.
- **A declared-purpose rights mechanism** (`WM_COMMERCIAL_USE` + per-dataset
  `source.person_level_records`), so a restriction that binds only commercial users stops
  discarding data this catalog is entitled to keep. Unset means commercial, so a fresh clone keeps
  the conservative path. See [use-policy.md](../use-policy.md).
- **Acquisition pool raised** 100 → 500 GiB, fair-share cap 25 GiB per dataset.

## What this push does not claim

It does not make the models good. It leaves **14** current `beats_persistence_dm` failures where it
found 9, and they say the models have no demonstrated edge over "tomorrow looks like today," which
is the best-replicated result in macro forecasting and is not addressed here. It does not validate
`coupled_economy`, which needs nine components and has three passing, one of them only on a
substituted series. It does not validate the agent layer, whose parameters
remain authored assumptions. And a fix that closes a criterion is not a finding about the world —
only a pre-registered attempt that passes on data it did not see is that.
