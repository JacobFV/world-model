# Remaining concerns

This repository is an experimental evidence and simulation substrate. Passing tests
establish implemented behavior; they do not establish world coverage or predictive validity.

## Highest-priority implementation work

1. **Couple economic mechanisms.** The behavioral economy uses a cash-funded lender;
   commercial-bank deposit creation runs in a separate accounting kernel. Production,
   contracting, credit decisions and settlement need one consistent transaction path.
   Its replay-derived economy state also needs explicit dynamic policy inputs before
   it can support closed-loop economic policy training.
2. **Replace episode replay with checkpoints.** Current temporal environments replay
   each prefix. Calls grow quadratically with episode length, and process step sizes
   must align. Add transactional checkpoints for graph state, RNG, memory and schedule.
   Live LLM kernels are deliberately unsupported by this replay adapter.
3. **Add RL integration and evaluation.** The environment exposes reset/step tuples;
   Gymnasium spaces/wrappers, vectorization, training algorithms and policy evaluation
   are absent. Output selection does not implement noisy sensors, reporting delays,
   permissions or an actor's beliefs. Define these explicitly for first-person tasks.
4. **Complete evolving topology.** Lifecycle reconstruction and eligibility checks work,
   but births, deaths and mergers do not automatically mutate arbitrary coupled runs.
   Field supports remain finite and in memory; distributed storage, adaptive meshes,
   vector fields and geodetic geometry are not implemented.

## Evidence and validation

5. **Acquire missing economic structure.** Samples are bounded and nonrepresentative.
   Real bilateral obligations, complete ownership, supplier dependencies, adjusted
   security prices, corporate actions and index constituents remain missing. SEC sources
   need a real contact header; some feeds require access arrangements. Roads are local
   subsets, and airport references are not flights or passenger movements.
6. **Validate mechanisms independently.** Government/actor kernels are illustrative;
   there is no integrated, calibrated geopolitical simulator. A prior descriptive WTI
   holdout did not beat persistence. Sensitivity analysis, out-of-time evaluation,
   accounting reconciliation and external benchmarks must precede claims about strategic
   prediction. A policy can optimize a misspecified simulator.
7. **Improve temporal identity and reconciliation.** Undated ticker assignments report
   uncertainty, and explicit equivalence avoids name-based merging. Coverage remains
   sparse; richer dated crosswalks, instrument continuity and explicit reconciliation
   of conflicting sources are still needed. Entity counts include source-scoped IDs.

## Distribution and usability

8. **Use a source checkout.** The CLI finds dataset definitions, fixtures and code for
   provenance relative to the source tree. The current wheel configuration packages
   Python modules only; a standalone installed wheel does not provide the full catalog
   workflow. Package resources/configuration need an explicit distribution design.
9. **Keep local artifacts distinct from repository contents.** Downloaded data, runtime
   versions and generated source-backed dashboards are excluded from Git. Committed
   verification reports describe prior local runs; their artifact hashes are not
   downloadable releases. A fresh clone can run tests and the fictional demo offline;
   real-evidence commands need acquisition/normalization first. Six integration tests
   explicitly skip without the acquired samples; fixture-based coverage should expand.
10. **Resolve licensing before redistribution.** There is no project license selected
    yet. Public visibility alone does not grant general reuse rights. Dataset-specific
    terms are separate; source configs record known publishers and access/terms status.
    A GitHub source release does not grant rights to redistribute acquired datasets.
11. **Extend visual interaction.** Current HTML surfaces are static and bounded. Maps
    display explicit coordinates without terrain/basemaps; large graphs truncate with
    counts. Interactive layers, linked selection, animation and richer diagram editing
    remain future work.

See [environments and surfaces](environments-and-surfaces.md),
[reference backbone](reference-backbone.md), and [strategic systems](strategic-systems.md)
for exact implemented contracts and runnable examples.
