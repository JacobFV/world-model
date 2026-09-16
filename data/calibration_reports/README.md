# calibration_reports

Immutable estimation artifacts: validation reports and final estimates produced by
`worldmodel.estimation` from the normalized datasets published in this catalog.

## What is in here

Each artifact is a content-addressed report published through
`worldmodel.artifacts.publish_report`, so every version pins its inputs
(`dataset@version` of every dataset the estimate read), the code snapshot, the
entrypoint and the inherited rights of those inputs.

| Report | Schema | Contents |
| --- | --- | --- |
| validation report | `worldmodel.validation_report/1` | frozen selection, holdout forecasts with baselines and Diebold-Mariano tests, the final estimate, the declared acceptance criteria and the pass/fail verdict |
| estimate | `worldmodel.estimate/1` | parameters, standard errors, process-parameter paths, sample window and the point-in-time data audit |

`parameters.attempt` names the pre-registered attempt in
[`worldmodel/estimation/real_data_plan.json`](../../worldmodel/estimation/real_data_plan.json),
and `parameters.evidence` carries the loader's dataset inputs and the digest of the
source record ids behind the inputs.

Nothing here is authoritative about the world. `validated: true` means only that the
attempt passed its own declared acceptance criteria on an untouched holdout; a
report with `validated: false` is an equally valid recorded result and is kept.

## How it is produced

```sh
python3 -m worldmodel calibrate-all                       # every pre-registered attempt
python3 -m worldmodel calibrate-all --attempt inventory_balance.eia_weekly
python3 -m worldmodel calibration-status calibration_reports@<version>
```

This dataset has no acquisition and no ordinary stage pipeline: its records are
written by the estimation layer's publish path, not by `wm run`. `pipeline.py`
therefore refuses to run and points at the command above. The current results are
summarized in [docs/calibration-status.md](../../docs/calibration-status.md).

## Inputs

Whatever the pre-registered attempts read, currently `eia_energy`,
`fred_oil_price`, `fred_policy_rate`, `sec_company_assets`, `census_population`,
`ucdp_conflicts`, `vdem`, `alpaca_daily_bars` and `census_business`. Each report
names the exact versions it used; source terms are inherited from those datasets
(see [DATA_RIGHTS.md](../../DATA_RIGHTS.md)). Alpaca bars in particular are
personal/internal use only, so reports derived from them stay local.

`artifacts/` and `scratch/` are ignored by Git; `manifests/` keeps the compact
metadata for review.
