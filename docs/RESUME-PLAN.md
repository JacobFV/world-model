# Resume plan, paused 2026-09-20

Everything below is committed and pushed to `origin/main`. Nothing is half-applied, the working
tree is clean apart from the data root, and the full suite is green at **1,729 tests**.

## The one command that is queued and not run

```sh
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/home/brandonin/Documents/world-model/data \
  python3 examples/natural-experiments/run_studies.py --study fema_monthly_dose_county_employment
```

The wave-3 design is **registered** (`examples/natural-experiments/registrations/fema_monthly_dose_county_employment.json`),
so its acceptance criteria are now fixed and the runner refuses a registration whose working copy
differs from the committed one. No treated-versus-control contrast has been computed on the real
monthly panel — no ATT, no leads, no pre-trend Wald, no placebo test — and the runner was tested on
fixtures with planted effects alone. Whatever the run returns is the result.

Expect hours, dominated by the 100-replication placebo-unit test over a 2,078-unit monthly panel,
across three outcomes. Run it on `gb10-direct` from an immutable code export under
`~/wm-code/<commit>/` with `PYTHONPATH` set, under
`systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0`, not on the shared machine.

Two things must survive contact with the result: 12 and 24 months are **bounding only** (the test
rejects a true null 11.5% of the time there), and the primary estimand is the mean ATT over event
months 0..6.

## Outstanding, in the order I would take them

1. **Rebuild the index.** It is graph schema 3 and was built before three things that belong in it:
   the OpenFIGI bridge, the monthly LAUS vintages and the monthly county panel. A schema-4 rebuild
   also turns on the publication dates merged in `593aa22` — until then `--known-at` is refused
   against the live index rather than answered from ingest time, which is the correct failure.
   Then re-run `unify-resolve`, `identity-coverage`, `products-index` and the six graph queries, and
   update `identity-coverage.md` and `unified-graph.md` with the new numbers. ~86 min to build,
   ~48 min to resolve, on `gb10-direct`.
2. **One control pass for the OpenFIGI bridge.** It was still running when work paused. It adds a
   confirmation to `docs/openfigi-bridge.md`; it does not change a published number.
3. **Sanctions wave 3.** The follow-up names it: dated Federal Register listings by sector rather
   than a year-level wave, with a monthly outcome. Power first, as with FEMA.
4. **`realtime_panel.first_releases` for the annual callers.** The period scheme is in; nothing else
   needs it yet.

## What not to do

- Do not re-run the wave-3 power measurement to "check" it against the study result. The power was
  measured before registration, from untreated cells; recomputing it after seeing an estimate is
  how a pre-registration stops meaning anything.
- Do not weaken a criterion because a design failed one. Three waves of results in
  `docs/natural-experiments.md` are failures recorded as results, and that is what makes the two
  identified nulls worth reading.
