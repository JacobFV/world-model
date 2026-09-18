# Executive summary — 2026-09-17 research push

**Status: in progress.** Six workstreams are running. This file is assembled from the workstream
logs in this directory; each section is filled in from measured results, and anything still
running says so rather than being predicted. Detail lives in the per-workstream files.

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

Two supporting measurements argued the same way:

- The sanctions↔SEC link went from **25 to 617** with no new bytes at all — by reading a
  registration-authority field GLEIF already published. A 24× gain from re-reading existing data.
- Inferred name matching measured **0.4% recall at 2.3% precision, 103,211 entities wrongly
  merged**. So a small dataset carrying CIK, LEI, GEOID or bioguide is worth more than a large one
  without an identifier.

Conclusion: buy only the data that converts directly into validity (~50 GiB does most of the
work), and spend the rest of the effort on the 35 failures that no download can fix.

## Headline numbers

| | Before | After |
| --- | --- | --- |
| Validated processes | 3 of 22 | *pending* |
| Attempts passing | 8 of 35 | *pending* |
| `interval_coverage` failures | 16 | *pending — WS-E* |
| `no_revision_leakage` failures | 8 | *pending — WS-A* |
| `minimum_test_forecasts` failures | 2 | **0** — WS-D: five new attempts, all clearing it, none passing |
| `regional_model` | untestable (no vintaged employment source) | *pending — WS-B* |
| Datasets | 125 | *pending — WS-C* |
| Acquired bytes (ledger) | 88.2 GiB of a 500 GiB pool | *pending* |
| Disk free | 1.5 TiB (after reclaiming 1,398 GiB) | *pending* |

## Workstreams

| Workstream | Goal | Status |
| --- | --- | --- |
| [WS-A](ws-a-alfred-vintages.md) | ALFRED vintages for every series a failing process touches | running |
| [WS-B](ws-b-employment-vintages.md) | vintaged employment, or a definitive negative | running |
| [WS-C](ws-c-public-employment.md) | ASPEP 1957– and FedScope: the public-sector labor graph | running |
| [WS-D](ws-d-long-panels.md) | long annual panels for the two small-n attempts | **done** — both evaluable; `population_growth_rate` now fails only `interval_coverage` (n 4 → 9 on 20 PEP vintages, → 16 on POPTHM), `deposit_rate_pass_through` now fails `beats_persistence_dm` on SNDR (n 16 → 24) and on both substitute series |
| [WS-E](ws-e-interval-coverage.md) | uncertainty calibration — the 16, with no new data | running |
| [WS-F](ws-f-fec-rebuild.md) | FEC rebuilt under the declared non-commercial purpose | running |

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

It does not make the models good. The 12 `beats_persistence_dm` failures say the models have no
demonstrated edge over "tomorrow looks like today," which is the best-replicated result in macro
forecasting and is not addressed here. It does not validate the agent layer, whose parameters
remain authored assumptions. And a fix that closes a criterion is not a finding about the world —
only a pre-registered attempt that passes on data it did not see is that.
