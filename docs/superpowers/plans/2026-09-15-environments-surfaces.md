# Environments and Surfaces Implementation Plan

Goal: generic materialization environments and composable visual surfaces.
Architecture: Environment delegates evaluation and selects actions/observations/rewards; TemporalEvaluator bridges the current materializer using explicit intervention history. Standalone HTML panels read the resulting snapshots or graph rows.
Tech: Python stdlib and embedded SVG/HTML.
Spec: ../specs/2026-09-15-environments-surfaces.md

Constraints: no downloads, no Git repository, immutable input/code lineage, bounded execution, no automatic actor visibility or calibrated prediction claims.

- [x] Temporal interventions: materialize.py and tests/test_interventions.py. Test literal input change at cadence boundary changes subsequent output only, retains deterministic prefix, rejects unknown/wrong-unit inputs and invalid times; implement scheduling and trace validation.
- [x] Surfaces: surfaces.py and tests/test_surfaces.py. render_surface(data,spec)->str standalone HTML; test hostile labels escape, scalar/table/plot/map/graph panels, map units and bounds; reject missing/invalid inputs explicitly.
- [x] Environment: environments.py and tests/test_environments.py. Environment(evaluate,spec).reset(seed), step(actions); TemporalEvaluator(store,ref,request,step_seconds). Test exact action routing, reward value/delta/units, observation filtering, termination and budget transactionality.
- [x] Integration: environment_cli.py, CLI registration, examples for multiple entities and single subject. Publish episode and surfaces via standard artifact lineage, run offline demos and full tests, inspect rendered artifact, document interface and limits.

Each implementation starts with a failing behavioral test. Independent workers own temporal and surface modules; root owns environment/CLI. Final tests/publications run with frozen package sources.
