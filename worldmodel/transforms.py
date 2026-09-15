"""Reusable transformations and compatibility names for local demo pipelines."""
from .source_helpers import raw_rows, normalize, evidence_jsonl, union, run_local_pipeline


def countries(context):
    return run_local_pipeline(context, 'demo_countries', function='countries')


def happiness(context):
    return run_local_pipeline(context, 'rando_joes_happiness_index')
