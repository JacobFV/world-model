# fdic_bank_financials

100 largest FDIC-insured banks by reported assets at2024-12-31; monetary source values in thousands USD

## Pipeline

Local implementation: [pipeline.py](pipeline.py). Stages: **normalized**; default output: `normalized`. The [dataset.json](dataset.json) declaration pins source configuration, parameters, dependencies and validation.

Dependencies: None (source input).

## Scope and evidence

Source statements retain their provenance, units and available dates. A bounded sample does not establish complete or representative coverage.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work. Both are ignored by Git. Legacy generated paths are also ignored. Source terms and access requirements remain in `dataset.json`; repository code licensing does not grant dataset redistribution rights.
