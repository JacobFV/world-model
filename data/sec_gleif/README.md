# sec_gleif

Company identities, filings, XBRL facts and LEI relationships

## Pipeline

Local implementation: [pipeline.py](pipeline.py). Stages: **normalized**; default output: `normalized`. The [dataset.json](dataset.json) declaration pins source configuration, parameters, dependencies and validation.

Dependencies: None (source input).

## Scope and evidence

Parse filing-specific contexts, accession IDs, units and periods. Match identifiers only with explicit evidence.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work. Both are ignored by Git. Legacy generated paths are also ignored. Source terms and access requirements remain in `dataset.json`; repository code licensing does not grant dataset redistribution rights.
