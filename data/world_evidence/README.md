# world_evidence

Typed evidence union of the 11 currently acquired real source samples; incomplete and nonrepresentative.

## Pipeline

Local implementation: [pipeline.py](pipeline.py). Stages: **graph**; default output: `graph`. The [dataset.json](dataset.json) declaration pins source configuration, parameters, dependencies and validation.

Dependencies: census_geography, census_population, census_business, bls_labor, classifications, eia_energy, fec, sec_gleif, transport, usaspending, usgs_resources

## Scope and evidence

Source statements retain their provenance, units and available dates. A bounded sample does not establish complete or representative coverage.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work. Both are ignored by Git. Legacy generated paths are also ignored. Source terms and access requirements remain in `dataset.json`; repository code licensing does not grant dataset redistribution rights.
