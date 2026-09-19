"""`wm identity-coverage` and `wm coverage-estimate`: what the evidence covers, measured (JSON output)."""
from pathlib import Path
import sys

COMMANDS = {'identity-coverage', 'coverage-estimate'}


def add_commands(sub):
    identity = sub.add_parser('identity-coverage', help='Share of an index\'s entities whose evidence comes from at '
                                                        'least two datasets, by dataset and domain')
    identity.add_argument('--index', type=Path, help='Index path (default <data>/world_evidence/index.sqlite)')
    identity.add_argument('--workdir', type=Path, required=True, help='Directory for the on-disk work database')
    identity.add_argument('--clusters', type=Path,
                          help='Measure a candidate clusters.jsonl (unify-resolve --no-attach) instead of the '
                               'attached resolution')
    identity.add_argument('--no-resolution', action='store_true', help='Measure shared entity IDs alone')
    identity.add_argument('--mentions', action='store_true',
                          help='Also count datasets whose records name an entity as subject or object')
    identity.add_argument('--top', type=int, default=25)
    estimate = sub.add_parser('coverage-estimate', help='For each dataset, the population it claims to cover and '
                                                        'the measured-over-declared fraction where a denominator '
                                                        'is sourced')
    estimate.add_argument('--datasets', help='Comma-separated dataset IDs (default: every declaration)')
    estimate.add_argument('--no-measure', action='store_true',
                          help='Report declared scopes and denominators without reading published outputs')


def execute(args, catalog, store, project, reference):
    if args.command == 'identity-coverage':
        from .resolution.join_coverage import join_coverage, publisher_families
        clusters = None if args.no_resolution else (args.clusters or 'index')
        return join_coverage(args.index or store.root / 'world_evidence' / 'index.sqlite', workdir=args.workdir,
                             clusters=clusters, mentions=args.mentions, families=publisher_families(catalog.root),
                             top=args.top, progress=lambda line: print(line, file=sys.stderr, flush=True))
    from .coverage_estimator import estimate_coverage
    datasets = [name.strip() for name in args.datasets.split(',') if name.strip()] if args.datasets else None
    return estimate_coverage(catalog, store, datasets=datasets, measure=not args.no_measure,
                             progress=lambda line: print(line, file=sys.stderr, flush=True))
