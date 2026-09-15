# Reference Backbone Implementation Plan

Goal: source-grounded temporal identities and independently inspectable data/model coverage.
Spec: docs/superpowers/specs/2026-09-15-reference-backbone.md
Constraints: existing bounded sampling and immutable provenance; stdlib; no name-based merges; no Git operations.

- [x] A identity.py + tests/test_identity.py + examples/identity.json; temporal lookup, explicit equivalence, ambiguous identifiers, aliases.
- [x] B market_sources.py + new data configs + tests/test_market_sources.py; verified source endpoints, typed normalizers and explicit blockers.
- [x] C people_sources.py + new data configs + tests/test_people_sources.py; public roles/identifiers with source and temporal evidence.
- [x] D coverage.py + exposure.py + tests; separate universe/evidence/validation maps and bounded financial exposure stress.
- [x] E reference_build.py, reference_cli.py, ontology/CLI integration; acquire bounded samples, publish joined evidence/coverage, run examples and tests, document actual scopes.

Independent workers own A/B/C disjoint modules; root owns D/E/shared files. Source-hash integrity requires final publications after source files are stable. Review concrete issues, verify relevant tests, and persist final artifact hashes/counts in docs.
