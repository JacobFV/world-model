"""Assay reports are published by the embedding layer, not by a stage run."""

MESSAGE = ('embedding_reports is written by the embedding layer. Run "python3 -m worldmodel embed-assay" '
           '(pre-registered attempts in worldmodel/embedding/plan.json).')


def run(context):
    """Refuse to run: this dataset has no stage transformation."""
    raise ValueError(MESSAGE)
