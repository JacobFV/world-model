# bea_input_output

Supply-use and input-output accounts

## Pipeline

Local implementation: [pipeline.py](pipeline.py). Stages: **normalized**; default output: `normalized`. The [dataset.json](dataset.json) declaration pins source configuration, parameters, dependencies and validation.

Dependencies: None (source input).

## Scope and evidence

Pin table vintage, valuation basis, industry crosswalks and units; coefficients are aggregate constraints. No acquired normalization path is available; the local pipeline fails explicitly instead of fabricating records.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work. Both are ignored by Git. Legacy generated paths are also ignored. Source terms and access requirements remain in `dataset.json`; repository code licensing does not grant dataset redistribution rights.
