'Influence panel stages: thin wrappers over worldmodel.panels.influence, which holds the logic and its tests.'
from worldmodel.panels import influence


def _source(context):
    return influence.RecordSource(context.store, context.inputs)


def bills(context):
    """One row per measure: sponsors, committees, roll calls and LDA citations of its number."""
    yield from influence.build_bills(_source(context), context.parameters)


def panel(context):
    """One row per (bioguide, congress, chamber), reading the bills stage for sponsorship and lobbying."""
    yield from influence.build_panel(_source(context), context.stage_records('bills'), context.parameters,
                                     bills_ref=context.stage_ref('bills'))
