# Research log

Append-only record of the 2026-09-17 research push. Each workstream writes one file here and
updates it as it goes, so the executive summary can be assembled from these rather than
reconstructed from a transcript.

## The thesis this push is testing

Measured from `docs/calibration-status.md` on 2026-09-17: of 45 acceptance-criterion failures
across 35 attempts, **10 are data-availability problems and 35 are not**.

| Blocking criterion | Count | Data-fixable |
| --- | ---: | --- |
| `interval_coverage` | 16 | no — miscalibrated uncertainty or mis-scoring |
| `beats_persistence_dm` | 12 | no — no demonstrated edge over persistence |
| `no_revision_leakage` | 8 | **yes** — needs point-in-time vintages |
| `parameters_within_declared_bounds` / `bounds` | 6 | no — theory contradicts the fit |
| `minimum_test_forecasts` | 2 | **yes** — needs longer panels |

So this push spends most of its effort on the 35, not the 10, and buys only the data that
converts directly into validity. The bet is that *validated processes / total processes* (3 of 22
at the start) is the number that matters, not bytes acquired.

**Outcome: 4 of 22.** The counts above are the ones the push was planned on and are kept as they
stood; they were read off the `docs/calibration-status.md` Summary table, which was then missing a
row, so the reports say 48 failures across 33 registered attempts rather than 45 across 35. The
reconciled before/after, measured from the published validation reports, is in
[EXECUTIVE-SUMMARY.md](EXECUTIVE-SUMMARY.md) — including the two criterion counts that went **up**.

## Workstreams

| File | Workstream | Converts |
| --- | --- | --- |
| [ws-a-alfred-vintages.md](ws-a-alfred-vintages.md) | ALFRED vintage coverage for every series a process touches | the 8 `no_revision_leakage` failures |
| [ws-b-employment-vintages.md](ws-b-employment-vintages.md) | vintaged employment from archived releases | `regional_model`, currently untestable |
| [ws-c-public-employment.md](ws-c-public-employment.md) | ASPEP 1957– and FedScope: the public-sector labor graph | a new question, not an old failure |
| [ws-d-long-panels.md](ws-d-long-panels.md) | long annual series | the 2 `minimum_test_forecasts` failures |
| [ws-e-interval-coverage.md](ws-e-interval-coverage.md) | uncertainty calibration, no new data | the 16 `interval_coverage` failures |
| [ws-f-fec-rebuild.md](ws-f-fec-rebuild.md) | rebuild FEC under the declared non-commercial purpose | contributor-level rows |

## Ground rules for every workstream

1. **Record failures as prominently as successes.** An attempt that fails after a fix is a
   result; a fix that is not tested is not.
2. **Never report a pass without saying what the acceptance criteria were.** "Improved" is not a
   verdict.
3. **Separate "our extraction was incomplete" from "the world is like this."** The
   sanctions↔SEC precedent: the link went 25 → 617 by reading a field GLEIF already published,
   but of 1,928 sanctions LEIs, genuinely zero carry SEC registration. Different claims.
4. **State units.** GiB throughout, not GB.
5. **No estimate presented as a measurement.** Extrapolations say so and show the basis.

## Budget

Disk: 1.5 TiB free at the start; the floor is **100 GB left clear**. The acquisition ledger is
raised to 500 GiB (`WORLD_MODEL_DOWNLOAD_BUDGET`), fair-share cap 5% = 25 GiB per dataset, so a
large source is split into shards as the catalog already does for FEC cycles and OSM regions
rather than being given a larger share.
