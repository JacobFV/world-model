"""``wm dossier``, ``wm screen``, ``wm place-brief``, ``wm serve`` and ``wm products-index``."""
from pathlib import Path
import sys

COMMANDS = {'dossier', 'screen', 'place-brief', 'serve', 'products-index'}


def add_commands(sub):
    def common(command):
        command.add_argument('--index', type=Path, help='Unified index (default $WORLD_MODEL_INDEX or '
                                                        '<data>/world_evidence/index.sqlite)')
        command.add_argument('--products-index', type=Path, help='Companion index from products-index '
                                                                 '(default products.sqlite beside the index)')

    dossier = sub.add_parser('dossier', help='Everything the unified index holds about one entity, by asserted identity')
    dossier.add_argument('entity', help='A namespaced entity ID (lei:..., ofac:party:..., sec:cik:...) or a name')
    dossier.add_argument('--pick', type=int, default=0, help='For a name: which candidate (0 = best match)')
    dossier.add_argument('--counterparties', type=int, default=40, help='Hop-1 and hop-2 counterparties to show')
    dossier.add_argument('--html', type=Path, help='Also write a standalone HTML report here')
    common(dossier)
    screen = sub.add_parser('screen', help='Sanctions and ownership proximity for a list of names or entity IDs')
    screen.add_argument('file', type=Path, help='One name or entity ID per line; # starts a comment')
    screen.add_argument('--hops', type=int, default=3, help='Maximum path length (1..4)')
    screen.add_argument('--groups', default='ownership,control',
                        help='Predicate groups to traverse: ownership, control, holdings, associates, lineage')
    screen.add_argument('--predicates', default='', help='Extra comma-separated predicates to traverse')
    screen.add_argument('--limit', type=int, default=20000, help='Edge budget per input (1..100000)')
    screen.add_argument('--candidates', type=int, default=3, help='Clusters screened per name input')
    common(screen)
    place = sub.add_parser('place-brief', help='One US county: economy, population, hazard, weather, storms, assistance')
    place.add_argument('place', help='5-digit county FIPS, geo:US:county:<FIPS>, or "Name, ST"')
    place.add_argument('--html', type=Path, help='Also write a standalone HTML report here')
    common(place)
    serve = sub.add_parser('serve', help='Serve the products and graph queries as a read-only local JSON API')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--host', default='127.0.0.1', help='Bind address (default loopback only)')
    common(serve)
    build = sub.add_parser('products-index', help='Build the companion index for name search, aliases and '
                                                  'county-keyed storm and disaster events')
    build.add_argument('--parts', default='labels,aliases,places')
    common(build)


def execute(args, catalog, store, project, reference):
    from .base import default_index, default_products_index
    index = args.index or default_index(args.data_root)
    products_index = args.products_index or default_products_index(index)
    options = {'index': index, 'products_index': products_index, 'data_root': args.data_root,
               'catalog_root': args.catalog_root}
    if args.command == 'products-index':
        from .search import build, main_progress
        return build(index, products_index, progress=main_progress,
                     parts=tuple(p.strip() for p in args.parts.split(',') if p.strip()))
    if args.command == 'dossier':
        from .dossier import dossier
        result = dossier(args.entity, pick=args.pick, counterparties=args.counterparties,
                         second_hop=args.counterparties, **options)
        if args.html:
            from .html import render_dossier
            _write(args.html, render_dossier(result))
            result['html_report'] = str(args.html)
        return result
    if args.command == 'screen':
        from .screen import read_entries, screen
        groups = tuple(g.strip() for g in args.groups.split(',') if g.strip())
        extra = [p.strip() for p in args.predicates.split(',') if p.strip()]
        return screen(read_entries(args.file), hops=args.hops, groups=groups, predicates=extra, limit=args.limit,
                      candidates=args.candidates, **options)
    if args.command == 'place-brief':
        from .place import place_brief
        result = place_brief(args.place, **options)
        if args.html:
            from .html import render_place
            _write(args.html, render_place(result))
            result['html_report'] = str(args.html)
        return result
    from .server import serve
    return serve(index, host=args.host, port=args.port, products_index=products_index, data_root=args.data_root,
                 catalog_root=args.catalog_root)


def _write(path, html):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding='utf-8')
    print('wrote ' + str(path), file=sys.stderr)
