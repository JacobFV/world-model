"""The monthly county panel is published by the embedding layer, not by a stage run."""

MESSAGE = ('county_monthly_realtime_panel is written by the embedding layer. Run '
           '"python3 -m worldmodel embed-panel --realtime-monthly" (see '
           'worldmodel/embedding/county_monthly_realtime.py and docs/world-state-embeddings.md).')


def run(context):
    """Refuse to run: this dataset has no stage transformation."""
    raise ValueError(MESSAGE)
