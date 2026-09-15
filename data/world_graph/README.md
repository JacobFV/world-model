# world_graph

Evidence-preserving graph materialization. Replace dependencies with normalized real datasets on the workstation.

## Pipeline

Local implementation: [pipeline.py](pipeline.py). Stages: **graph**; default output: `graph`. The [dataset.json](dataset.json) declaration pins source configuration, parameters, dependencies and validation.

Dependencies: demo_graph, rando_joes_happiness_index

## Scope and evidence

Illustrative fixture; no real-world measurement or causal validation is implied.

## Local files

`artifacts/` contains generated immutable stage products; `scratch/` is temporary local work. Both are ignored by Git. Legacy generated paths are also ignored. Source terms and access requirements remain in `dataset.json`; repository code licensing does not grant dataset redistribution rights.
