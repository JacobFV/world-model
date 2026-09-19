"""`wm unify` and `wm unify-scope`: build and inspect the catalog-scale unified graph."""
import json
from pathlib import Path
import sys

from .unify import (DEFAULT_PROFILE, DOMAINS, PROFILES, inventory, publish_existing_index,
                    resolve_identities, scope, unify)

COMMANDS = {'unify', 'unify-scope', 'unify-resolve', 'unify-publish'}


def _names(value):
    names = [piece.strip() for piece in value.split(',') if piece.strip()]
    if not names:
        raise ValueError('Expected a comma-separated list of dataset names')
    return names


def add_commands(sub):
    build = sub.add_parser('unify', help='Stream the catalog\'s published normalized outputs into one graph index')
    build.add_argument('--profile', choices=sorted(PROFILES), default=DEFAULT_PROFILE,
                       help='; '.join('%s: %s' % (name, PROFILES[name]['description']) for name in sorted(PROFILES)))
    build.add_argument('--all', action='store_true', help='Shorthand for --profile all (every record of every dataset)')
    build.add_argument('--datasets', type=_names, help='Comma-separated dataset IDs; overrides the profile selection')
    build.add_argument('--domain', action='append', default=[], choices=sorted(DOMAINS),
                       help='Add a docs/data dataset group; repeatable')
    build.add_argument('--exclude', type=_names, default=[], help='Comma-separated dataset IDs to drop from the scope')
    build.add_argument('--index', type=Path, help='Index path (default <data>/world_evidence/index.sqlite)')
    build.add_argument('--output-dataset', default='world_evidence', help='Dataset the pinned summary is published under')
    build.add_argument('--no-publish', action='store_true', help='Build the index without publishing a summary artifact')
    build.add_argument('--validate', action='store_true', help='Re-validate every record against the evidence model')
    build.add_argument('--no-verify', action='store_true', help='Skip input output-checksum verification (not recommended)')
    build.add_argument('--no-compress', action='store_true', help='Store record bodies as text instead of deflated blobs')
    build.add_argument('--limit', type=int, help='Stop after N indexed records per dataset (smoke tests)')
    build.add_argument('--batch-size', type=int, default=50000)
    build.add_argument('--cache-mb', type=int, default=1024, help='SQLite page cache for the load')
    build.add_argument('--progress', type=int, default=2_000_000, help='Progress line every N indexed records; 0 is silent')
    build.add_argument('--dry-run', action='store_true', help='Report the resolved scope and its cost without building')
    view = sub.add_parser('unify-scope', help='Resolve a unify scope: what is selected, what is skipped and why')
    view.add_argument('--profile', choices=sorted(PROFILES), default=DEFAULT_PROFILE)
    view.add_argument('--all', action='store_true')
    view.add_argument('--datasets', type=_names)
    view.add_argument('--domain', action='append', default=[], choices=sorted(DOMAINS))
    view.add_argument('--exclude', type=_names, default=[])
    view.add_argument('--inventory', action='store_true', help='List every catalog dataset and its published output')
    resolve = sub.add_parser('unify-resolve', help='Attach the asserted identity resolution (published same_as '
                                                   'links, shared unique identifiers and published crosswalk '
                                                   'fields) to a unified index')
    resolve.add_argument('--workdir', type=Path, required=True)
    resolve.add_argument('--profile', choices=sorted(PROFILES), default=DEFAULT_PROFILE)
    resolve.add_argument('--all', action='store_true')
    resolve.add_argument('--datasets', type=_names)
    resolve.add_argument('--domain', action='append', default=[], choices=sorted(DOMAINS))
    resolve.add_argument('--exclude', type=_names, default=[])
    resolve.add_argument('--index', type=Path)
    resolve.add_argument('--output-dataset', default='world_evidence')
    resolve.add_argument('--no-attach', action='store_true', help='Compute clusters without writing them to the index')
    resolve.add_argument('--max-cluster-size', type=int, default=5000)
    resolve.add_argument('--no-measure', action='store_true',
                         help='Skip the joined-share measurement of the scope (worldmodel.resolution.join_coverage)')
    resolve.add_argument('--no-bridges', action='store_true',
                        help='Read only identifier assertions and namespaced entity IDs, ignoring the published '
                             'crosswalk fields in worldmodel.resolution.bridges (diagnostic baseline)')
    resolve.add_argument('--progress', type=int, default=20_000_000)
    republish = sub.add_parser('unify-publish', help='Publish the pinned summary for an index that already '
                                                     'exists, recomputing per-dataset counts from it')
    republish.add_argument('--index', type=Path, help='Index path (default <data>/world_evidence/index.sqlite)')
    republish.add_argument('--output-dataset', default='world_evidence')


def execute(args, catalog, store, project, reference):
    if args.command == 'unify-publish':
        index = args.index or store.root / args.output_dataset / 'index.sqlite'
        return publish_existing_index(store, index, output_dataset=args.output_dataset)
    profile = 'all' if getattr(args, 'all', False) else args.profile
    selection = {'profile': profile, 'datasets': args.datasets, 'domains': args.domain or None,
                 'exclude': args.exclude or None}
    if args.command == 'unify-scope':
        if args.inventory:
            available, missing = inventory(catalog, store)
            return {'published': [{k: item[k] for k in ('dataset', 'stage', 'rows', 'bytes', 'domain')}
                                  for item in available],
                    'unpublished': missing,
                    'catalog_rows': sum(item['rows'] or 0 for item in available)}
        plan = scope(catalog, store, **selection)
        return {'profile': plan['profile'], 'profile_description': plan['profile_description'],
                'catalog_rows': plan['catalog_rows'], 'selected_rows': plan['selected_rows'],
                'selected': [{'dataset': i['dataset'], 'stage': i['stage'], 'version': i['ref']['version'],
                              'kinds': list(i['kinds']), 'published_rows': i['rows'], 'bytes': i['bytes']}
                             for i in plan['selected']],
                'skipped': plan['skipped']}
    if args.command == 'unify-resolve':
        return resolve_identities(catalog, store, workdir=args.workdir, index=args.index,
                                  output_dataset=args.output_dataset, attach=not args.no_attach,
                                  max_cluster_size=args.max_cluster_size, progress=args.progress or None,
                                  bridges=not args.no_bridges, measure=not args.no_measure, **selection)
    result = unify(catalog, store, project, index=args.index, output_dataset=args.output_dataset,
                   batch_size=args.batch_size, cache_mb=args.cache_mb, progress=args.progress or None,
                   publish=not args.no_publish, validate=args.validate, limit=args.limit,
                   verify=not args.no_verify, dry_run=args.dry_run, compress=not args.no_compress, **selection)
    if args.progress:
        print(json.dumps(result.get('totals', {}), sort_keys=True), file=sys.stderr, flush=True)
    return result
