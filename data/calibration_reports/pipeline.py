"""Reports here are published by the estimation layer, not by a stage run."""

MESSAGE = ('calibration_reports is written by the estimation layer. Run '
           '"python3 -m worldmodel calibrate-all" (pre-registered attempts in '
           'worldmodel/estimation/real_data_plan.json), or publish a single report with '
           '"python3 -m worldmodel validate <component> ... --dataset calibration_reports".')


def run(context):
    """Refuse to run: this dataset has no stage transformation."""
    raise ValueError(MESSAGE)
