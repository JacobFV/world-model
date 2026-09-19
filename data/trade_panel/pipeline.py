'Trade panel stages: thin wrappers over worldmodel.panels.trade, which holds the logic and its tests.'
from worldmodel.panels import trade


def _source(context):
    return trade.LineSource(context.store, context.inputs)


def concordance(context):
    """One row per HS2022 or HS2017 source code: its targets in the UNSD correlation table."""
    yield from trade.build_concordance(_source(context), context.parameters)


def tariffs(context):
    """MFN applied rates per reporter x year in BACI's HS1992 and HS2017 codes, exact translations only."""
    yield from trade.build_tariffs(_source(context), context.stage_records('concordance'), context.parameters)


def panel(context):
    """One record per (nomenclature, exporter, importer, year) with its HS6 table, tariff and gravity."""
    yield from trade.build_panel(_source(context), context.stage_records('tariffs'), context.parameters,
                                 tariffs_ref=context.stage_ref('tariffs'))
