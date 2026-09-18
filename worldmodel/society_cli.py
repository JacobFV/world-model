"""``wm society-run`` and ``wm society-report``: run a society of grounded agents, and read it back.

``society-run`` builds the population its config names out of the unified-graph index, runs it for
a number of ticks on one shared clock, and prints (or publishes) the observatory's report.
``society-report`` renders a published report.

Both are read-only with respect to the catalog unless ``--publish`` is given, in which case the
report goes out through the ordinary :func:`worldmodel.artifacts.publish_report` path and is
content-addressed like any other derived dataset.

The cognitive substrate is an optional extra; without it these commands fail with the install hint
and nothing else in ``wm`` is affected.
"""
from pathlib import Path

from .agents import INSTALL_HINT, tensacode_available

COMMANDS = {'society-run', 'society-report'}
DEFAULT_DATASET = 'agent_society_report'
DEFAULT_MEMORY_GB = 8


def add_commands(sub):
    run = sub.add_parser('society-run',
                         help='Run a society of grounded agents and report what the observatory measured')
    run.add_argument('--config', type=Path, required=True,
                     help='Society config JSON (see examples/society-congress.json)')
    run.add_argument('--ticks', type=int, required=True, help='How many ticks to run')
    run.add_argument('--seed', type=int, help='Override the config seed; fixes each agent perceptual axes')
    run.add_argument('--index', type=Path, help='Graph index path (default <data>/world_evidence/index.sqlite)')
    run.add_argument('--cache-mb', type=int, default=64, help='SQLite page cache for the read-only connection')
    run.add_argument('--memory-gb', type=float, default=DEFAULT_MEMORY_GB,
                     help='Memory budget the focal ceiling is computed against')
    run.add_argument('--measure-resident', action='store_true',
                     help='Measure resident bytes per claim with tracemalloc (slower, more honest)')
    run.add_argument('--no-series', action='store_true',
                     help='Omit the per-agent divergence and belief-provenance series for a smaller report')
    run.add_argument('--summary', action='store_true', help='Print the rendered summary instead of JSON')
    run.add_argument('--publish', action='store_true', help='Publish the report as an immutable artifact')
    run.add_argument('--dataset', default=DEFAULT_DATASET, help='Report dataset name when publishing')
    report = sub.add_parser('society-report', help='Render a published society report')
    report.add_argument('artifact', help='dataset[/stage][@version] of a published society report')
    report.add_argument('--json', action='store_true', help='Print the stored report instead of the summary')
    report.add_argument('--section', choices=['belief_divergence', 'propagation', 'trajectories',
                                              'decision_structure', 'tiering', 'ticks'],
                        help='Print one section of the stored report as JSON')


def execute(args, catalog, store, project, reference):
    if not tensacode_available():
        raise RuntimeError(INSTALL_HINT)
    if args.command == 'society-report':
        from .artifacts import load_report
        from .agents.observatory import render
        report = load_report(store, reference(args.artifact, store))
        if args.section:
            return report.get(args.section)
        if args.json:
            return report
        return render(report)

    from .agents.observatory import Observatory, render
    from .agents.society import Society, load_config, open_index

    config = load_config(args.config)
    index = open_index(args.index, data_root=store.root, cache_mb=args.cache_mb)
    try:
        society = Society.from_config(config, index=index, catalog=store, seed=args.seed)
        resident = _resident(society) if args.measure_resident else None
        society.run(max(0, int(args.ticks)))
        observatory = Observatory(society)
        payload = observatory.report(memory_budget_bytes=int(args.memory_gb * 1024 ** 3),
                                     resident=resident, include_series=not args.no_series)
    finally:
        index.close()
    if args.publish:
        artifact = observatory.publish(store, dataset=args.dataset, report=payload,
                                       parameters={'config': str(args.config), 'ticks': int(args.ticks),
                                                   'seed': society.seed})
        return {'artifact': artifact, 'summary': render(payload)}
    return render(payload) if args.summary else payload


def _resident(society):
    """Resident bytes per claim, measured by re-seeding one focal agent under ``tracemalloc``.

    It re-seeds rather than measuring a live agent because ``tracemalloc`` reports allocation
    deltas, not the size of an object already in memory. The agent it builds is thrown away.
    """
    from .agents.society import measure_resident_bytes
    focal = [m for m in society.members.values() if m.focal and m.kind == 'person']
    if not focal:
        return None
    member = sorted(focal, key=lambda m: m.entity_id)[0]

    def build():
        from .agents.society import SocialPerson
        from .agents.grounding import LEGISLATOR
        agent = SocialPerson.ground(society.index, member.entity_id, horizon=LEGISLATOR, seed=society.seed)
        return agent, agent.store

    bytes_per_claim, _agent = measure_resident_bytes(build)
    return bytes_per_claim
