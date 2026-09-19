# natural_experiment_reports

Immutable results of pre-registered natural-experiment studies produced by `worldmodel.causal`.

Each artifact is a content-addressed report published through
`worldmodel.artifacts.publish_report`. It pins every input the registration names
(`event_library@version` and the outcome datasets), the code snapshot and the inherited rights
of those inputs. The report body (`worldmodel.natural_experiment_report/1`) holds the
registration's path, SHA-256 and committing Git commit, and one result record
(`worldmodel.causal_result/1`) per registered outcome: the identification label, the stated
assumptions, the estimates, the pre-trend and placebo diagnostics, the acceptance results and a
plain verdict.

A result labelled `quasi_experimental_did` identifies an average effect on the treated only under
the assumptions listed in it. `did_failed_diagnostics`, `not_estimable` and
`predictive_association` results are equally valid recorded outcomes and are kept.

## How it is produced

```sh
WORLD_MODEL_RAW_VERIFY=size WORLD_MODEL_DATA=/path/to/data \
  python3 examples/natural-experiments/run_studies.py --study fema_disasters_county_employment
```

The runner refuses a registration that is not committed or differs from its commit. It also writes
the same report to `examples/natural-experiments/results/<study_id>.json` for review in Git (aggregate
estimates only, no source rows). Outcome extraction caches live in this dataset's `scratch/panels/`,
named by input version and a digest of the extraction spec.

This dataset has no stage pipeline; `pipeline.py` refuses to run. See
[docs/natural-experiments.md](../../docs/natural-experiments.md).
