# bls_labor

QCEW, CES, OEWS, CPS and JOLTS

## Pipeline

Local implementation: [pipeline.py](pipeline.py). Stages: **normalized**; default output: `normalized`. The [dataset.json](dataset.json) declaration pins source configuration, parameters, dependencies and validation.

Dependencies: None (source input).

## Scope and evidence

Pin series, occupation/industry revisions, seasonal adjustment and survey universe.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work. Both are ignored by Git. Legacy generated paths are also ignored. Source terms and access requirements remain in `dataset.json`; repository code licensing does not grant dataset redistribution rights.
