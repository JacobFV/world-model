"""The county panel is published by the embedding layer, not by a stage run."""

MESSAGE = ('county_panel is written by the embedding layer. Run "python3 -m worldmodel embed-panel" '
           '(see worldmodel/embedding/county_panel.py and docs/world-state-embeddings.md).')


def run(context):
    """Refuse to run: this dataset has no stage transformation."""
    raise ValueError(MESSAGE)
