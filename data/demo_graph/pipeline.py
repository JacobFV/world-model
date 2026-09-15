'Fictional organizations, conflicting claims and aggregate observations.'
from worldmodel.source_helpers import evidence_jsonl


def run(context):
    """Apply the catalog-bound evidence_jsonl stage, preserving input evidence."""
    yield from evidence_jsonl(context)
