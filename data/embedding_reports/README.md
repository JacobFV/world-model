# embedding_reports

Validation reports of the world-state encoder (`worldmodel.embedding`), one per target of a
pre-registered attempt in [`worldmodel/embedding/plan.json`](../../worldmodel/embedding/plan.json).
Each report pins the `county_panel` version it read, records the frozen selection between
candidates on the validation origins, the holdout forecasts against persistence, drift and a
gradient-boosted model fed the same subgraph, pooled and year-clustered Diebold-Mariano tests,
the leakage audit and the acceptance verdict.

```sh
python3 -m worldmodel embed-assay                          # list the plan
python3 -m worldmodel embed-assay places.county_root_readout_v1
```

`validated: true` means only that the attempt met its own declared criteria on an untouched
holdout. Encoder checkpoints are written to `scratch/checkpoints/` (not published) for
`wm embed-query`.
