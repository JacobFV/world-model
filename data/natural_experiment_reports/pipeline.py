"""Reports here are published by the causal layer, not by a stage run."""

MESSAGE = ('natural_experiment_reports is written by worldmodel.causal. Commit a registration under '
           'examples/natural-experiments/registrations, then run '
           '"python3 examples/natural-experiments/run_studies.py --study <study_id>".')


def run(context):
    """Refuse to run: this dataset has no stage transformation."""
    raise ValueError(MESSAGE)
