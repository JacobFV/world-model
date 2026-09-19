'Firm panel stages: thin wrappers over worldmodel.panels.firm, which holds the logic and its tests.'
import tempfile

from worldmodel.panels import firm


def _source(context):
    return firm.LineSource(context.store, context.inputs)


def links(context):
    """One row per CIK tied to an LEI (and that LEI's CUSIPs) by published identifiers."""
    yield from firm.build_links(_source(context), context.parameters)


def ownership(context):
    """One row per issuer CIK x 13F report quarter, through the links stage's CUSIPs only."""
    yield from firm.build_ownership(_source(context), context.stage_records('links'), context.parameters)


def panel(context):
    """One row per (CIK, fiscal period end) with every filed vintage, the LEI and the 13F aggregate."""
    with tempfile.TemporaryDirectory(dir=context.store.scratch_dir('firm_panel')) as work:
        yield from firm.build_panel(_source(context), list(context.stage_records('links')),
                                    list(context.stage_records('ownership')), context.parameters, workdir=work,
                                    links_ref=context.stage_ref('links'), ownership_ref=context.stage_ref('ownership'))
