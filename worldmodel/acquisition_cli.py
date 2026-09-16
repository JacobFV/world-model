"""`wm acquire` and `wm budget`: full raw acquisition under a global fair-share budget."""
import sys

COMMANDS = {'acquire', 'budget'}


def add_commands(sub):
    from .budget import DEFAULT_MAX_SHARE
    acquire = sub.add_parser('acquire', help='Download full raw data within the global fair-share budget')
    acquire.add_argument('dataset', help='Dataset ID or all (datasets with an acquisition block)')
    acquire.add_argument('--allow-network', action='store_true')
    acquire.add_argument('--budget', help='Total budget across datasets, e.g. 100GiB (default $WORLD_MODEL_DOWNLOAD_BUDGET or 100GiB)')
    acquire.add_argument('--max-share', type=float, default=DEFAULT_MAX_SHARE)
    acquire.add_argument('--dry-run', action='store_true', help='Show plan and allocation; no network')
    acquire.add_argument('--max-shards', type=int, help='Stop after N new shards (resumable)')
    acquire.add_argument('--resume', action='store_true', help='Continue an interrupted or budget-stopped acquisition')
    acquire.add_argument('--restart', action='store_true', help='Discard existing acquisition staging first')
    acquire.add_argument('--workers', type=int, default=4, help='Concurrent datasets for acquire all')
    budget = sub.add_parser('budget', help='Show or reconcile the fair-share download budget')
    budget.add_argument('action', nargs='?', choices=['show', 'reconcile'], default='show')
    budget.add_argument('--budget', help='Total budget, e.g. 100GiB')
    budget.add_argument('--max-share', type=float, default=DEFAULT_MAX_SHARE)


def execute(args, catalog, store, project, reference):
    from .budget import Ledger, budget_table
    definitions = catalog.list()
    if args.command == 'budget':
        return budget_table(definitions, store, Ledger(store.root), args.budget, args.max_share,
                            rescan=args.action == 'reconcile')
    from .acquisition import acquire
    if args.dataset == 'all':
        targets = [d['id'] for d in definitions if 'acquisition' in d]
    else:
        targets = [catalog.get(args.dataset)['id']]
    results = acquire(store, definitions, targets, total=args.budget, max_share=args.max_share,
                      allow_network=args.allow_network, dry_run=args.dry_run, max_shards=args.max_shards,
                      resume=args.resume, restart=args.restart, workers=args.workers)
    for result in results:
        print(f'{result["dataset"]}: {result["status"]}', file=sys.stderr, flush=True)
    if args.dataset != 'all' and results[0]['status'] == 'failed':
        raise ValueError(results[0]['error'])
    return results if args.dataset == 'all' else results[0]
