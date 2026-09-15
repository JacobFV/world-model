'Typed evidence union of the 11 currently acquired real source samples; incomplete and nonrepresentative.'
from worldmodel.source_helpers import union


def run(context):
    """Apply the catalog-bound union stage, preserving input evidence."""
    yield from union(context)
