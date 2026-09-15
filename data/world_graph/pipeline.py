'Evidence-preserving graph materialization. Replace dependencies with normalized real datasets on the workstation.'
from worldmodel.source_helpers import union


def run(context):
    """Apply the catalog-bound union stage, preserving input evidence."""
    yield from union(context)
