"""``wm embed-panel``, ``wm embed-assay`` and ``wm embed-query``: the world-state embedding layer.

``embed-panel`` needs only the standard library. The other two need the optional ``embed`` extra
(numpy, torch, lightgbm); without it they fail with the install hint and nothing else in ``wm``
is affected.
"""
from pathlib import Path

COMMANDS = {'embed-panel', 'embed-assay', 'embed-query', 'embed-publish'}
INSTALL_HINT = 'The embedding assay needs the optional extra: pip install "worldmodel-substrate[embed]"'


def add_commands(sub):
    panel = sub.add_parser('embed-panel', help='Build and publish the dated county panel and county graph')
    panel.add_argument('--processes', type=int, default=6, help='Parallel collector processes')
    panel.add_argument('--cache', type=Path, help='Pickle of collected inputs, reused if present')
    panel.add_argument('--dry-run', action='store_true', help='Collect and summarize without publishing')
    assay = sub.add_parser('embed-assay', help='Run a pre-registered world-state encoder attempt (worldmodel/embedding/plan.json)')
    assay.add_argument('attempt', nargs='?', help='Attempt id; omit to list the plan')
    assay.add_argument('--no-publish', action='store_true', help='Score without publishing reports')
    assay.add_argument('--save', type=Path, help='Write the unpublished reports to this JSON file (implies --no-publish), '
                                                 'for runs on a compute host that publish elsewhere with embed-publish')
    assay.add_argument('--device', help='torch device (default cuda when available)')
    publish = sub.add_parser('embed-publish', help='Publish reports saved by embed-assay --save (verifies each report id)')
    publish.add_argument('reports', type=Path)
    query = sub.add_parser('embed-query', help='Places whose embedded state, as known then, is nearest to a county as known now')
    query.add_argument('county', help='County id, e.g. geo:US:county:48453')
    query.add_argument('--as-of', type=int, required=True, help='Origin year of the query county (state as of YYYY-12-31)')
    query.add_argument('--across', choices=('time', 'space'), default='time',
                       help='time: search every county at every earlier origin; space: every county at the same origin')
    query.add_argument('--checkpoint', type=Path, required=True, help='Encoder checkpoint written by embed-assay')
    query.add_argument('--limit', type=int, default=10)


def execute(args, catalog, store, project, reference):
    if args.command == 'embed-panel':
        from .embedding.county_panel import build
        ref, report = build(store.root, processes=args.processes, publish=not args.dry_run,
                            cache=str(args.cache) if args.cache else None)
        return {'ref': ref, 'counties': report['counties'], 'edges': report['edges'],
                'features': {k: {'rows': v['rows'], 'years': [v['first_year'], v['last_year']]}
                             for k, v in report['features'].items()},
                'does_not_establish': report['does_not_establish']}
    try:
        import numpy  # noqa: F401
        import torch  # noqa: F401
    except ImportError as error:
        raise RuntimeError(INSTALL_HINT) from error
    if args.command == 'embed-publish':
        from .embedding.assay import publish_saved
        return publish_saved(store, args.reports)
    if args.command == 'embed-assay':
        from .embedding.assay import load_plan, run_attempt, save_reports
        if not args.attempt:
            return {'plan': [{'id': a['id'], 'targets': a['targets'], 'protocol': a['protocol']} for a in load_plan()['attempts']]}
        import sys
        if args.attempt.startswith('actors.'):
            from .embedding.actors_assay import run_attempt
        publish = not (args.no_publish or args.save)
        reports = run_attempt(store, args.attempt, publish=publish, device=args.device,
                              log=lambda message: print(message, file=sys.stderr, flush=True))
        if args.save:
            save_reports(args.save, reports)
        return [{'target': r['target'], 'ref': r['ref'], 'validated': r['report']['validated'],
                 'acceptance': [{k: x[k] for k in ('id', 'passed', 'observed')} for x in r['report']['acceptance']['results']],
                 'year_clustered_dm': {b: v.get('pvalue') for b, v in r['report']['test']['year_clustered_dm'].items()}}
                for r in reports]
    from .embedding.query import nearest
    return nearest(store, args.county, args.as_of, checkpoint=args.checkpoint, across=args.across, limit=args.limit)
