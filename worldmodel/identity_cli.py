"""Units, crosswalks, entity resolution, beliefs and resolved-graph queries (JSON output)."""
import json
from pathlib import Path

COMMANDS = {'units-convert', 'units-describe', 'country-code', 'county-lookup', 'crosswalk-apportion', 'reference-declarations',
            'link-identifiers', 'resolve-entities', 'beliefs', 'graph-neighborhood', 'graph-paths', 'graph-centrality',
            'graph-flow', 'graph-attach-resolution'}


def add_commands(sub):
    convert = sub.add_parser('units-convert', help='Convert a value with explicit factors and provenance')
    convert.add_argument('value', type=float)
    convert.add_argument('from_unit')
    convert.add_argument('to_unit')
    convert.add_argument('--commodity')
    convert.add_argument('--day-count', choices=['julian', 'act365', 'act360', 'gregorian_mean'])
    convert.add_argument('--allow-approximate', action='store_true')
    describe = sub.add_parser('units-describe', help='Parse a unit string, or list the registry when omitted')
    describe.add_argument('unit', nargs='?')
    country = sub.add_parser('country-code', help='Translate dated country codes (ISO, M49, World Bank, COW, GW)')
    country.add_argument('value')
    country.add_argument('--from', dest='from_scheme', required=True)
    country.add_argument('--to', dest='to_scheme', required=True)
    country.add_argument('--at')
    county = sub.add_parser('county-lookup', help='County FIPS record and change history')
    county.add_argument('geoid')
    county.add_argument('--at')
    apportion = sub.add_parser('crosswalk-apportion', help='Apportion extensive totals through a crosswalk, conserving totals')
    apportion.add_argument('--crosswalk', required=True,
                           choices=['naics_2012_2017', 'naics_2017_2022', 'naics_2012_2022', 'us_county', 'ct_cousub', 'zcta_county'])
    apportion.add_argument('--values', type=Path, required=True, help='JSON object {source_code: amount}')
    apportion.add_argument('--weights', type=Path, help='JSON {source: {target: weight}} for split rows')
    apportion.add_argument('--unweighted', choices=['error', 'equal'], default='error')
    apportion.add_argument('--unmapped', choices=['error', 'report'], default='error')
    apportion.add_argument('--start', help='us_county: source vintage date')
    apportion.add_argument('--end', help='us_county: target vintage date')
    apportion.add_argument('--relationship-file', type=Path, help='zcta_county: acquired Census relationship file')
    sub.add_parser('reference-declarations', help='Acquisition declarations for large crosswalk/reference datasets')
    link = sub.add_parser('link-identifiers', help='Deterministic links from a published mapping (JSONL rows)')
    link.add_argument('spec')
    link.add_argument('--rows', type=Path, required=True)
    link.add_argument('--observed-at', required=True)
    link.add_argument('--evidence', required=True, help='JSON evidence list for the mapping publication')
    link.add_argument('--output', type=Path)
    resolve = sub.add_parser('resolve-entities', help='Blocked Fellegi-Sunter resolution with auditable match assertions')
    source = resolve.add_mutually_exclusive_group(required=True)
    source.add_argument('--graph', help='dataset[/stage][@version] whose entity records are resolved and published')
    source.add_argument('--input', type=Path, help='Local JSONL resolution inputs (outputs stay in --workdir)')
    resolve.add_argument('--workdir', type=Path, required=True)
    resolve.add_argument('--kind', choices=['organization', 'person'], default='organization')
    resolve.add_argument('--entity-type', action='append')
    resolve.add_argument('--threshold', type=float, default=0.95)
    resolve.add_argument('--lower', type=float, default=0.5)
    resolve.add_argument('--max-block-size', type=int, default=1000)
    resolve.add_argument('--max-cluster-size', type=int, default=50)
    resolve.add_argument('--link-mode', choices=['dedupe', 'link'], default='dedupe')
    resolve.add_argument('--training', choices=['auto', 'em', 'identifier'], default='auto')
    resolve.add_argument('--workers', type=int, default=1)
    resolve.add_argument('--reviews', type=Path, help='JSON list of {a,b,status,reviewer,reason}')
    resolve.add_argument('--observed-at', required=True)
    resolve.add_argument('--dataset', default='entity_resolution')
    beliefs = sub.add_parser('beliefs', help='Materialize current beliefs and conflicts under an explicit policy')
    beliefs.add_argument('--graph', required=True)
    beliefs.add_argument('--known-at', required=True)
    beliefs.add_argument('--at')
    beliefs.add_argument('--policy', type=Path)
    beliefs.add_argument('--subject', action='append')
    beliefs.add_argument('--variable', action='append')
    beliefs.add_argument('--dataset', default='belief_state')
    for name in ('graph-neighborhood', 'graph-paths', 'graph-centrality', 'graph-flow'):
        command = sub.add_parser(name)
        if name == 'graph-neighborhood':
            command.add_argument('entity')
            command.add_argument('--hops', type=int, default=2)
        if name == 'graph-paths':
            command.add_argument('source')
            command.add_argument('target')
            command.add_argument('--max-hops', type=int, default=4)
        if name == 'graph-flow':
            command.add_argument('predicate')
            command.add_argument('--group-by', choices=['subject', 'object', 'pair'], default='subject')
        if name == 'graph-centrality':
            command.add_argument('--pagerank', action='store_true')
            command.add_argument('--weighted', action='store_true')
        if name != 'graph-flow':
            command.add_argument('--predicate', action='append')
        if name in ('graph-neighborhood', 'graph-paths'):
            command.add_argument('--direction', choices=['in', 'out', 'both'], default='both')
            command.add_argument('--min-weight', type=float)
        command.add_argument('--index', type=Path)
        command.add_argument('--valid-at')
        command.add_argument('--known-at')
        command.add_argument('--include-unknown-publication', action='store_true',
                             help='With --known-at: also include rows whose publication date is unknown, dated by '
                                  'when they were ingested. The result discloses it; it is not point-in-time.')
        command.add_argument('--limit', type=int, default=100)
        command.add_argument('--resolved', action='store_true')
    attach = sub.add_parser('graph-attach-resolution', help='Attach a resolve-entities result to a graph index')
    attach.add_argument('--workdir', type=Path, required=True)
    attach.add_argument('--index', type=Path)


def _json_file(path, kind):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, kind):
        raise ValueError(f'{path} must contain a JSON {kind.__name__}')
    return value


def _default_index(store):
    for name in ('reference_evidence', 'strategic_evidence', 'world_evidence', 'world_graph'):
        path = store.root / name / 'index.sqlite'
        if path.exists():
            return path
    return store.root / 'world_graph/index.sqlite'


def resolve_graph(records, *, ref, kind, entity_types, observed_at, workdir, threshold, lower, max_block_size,
                  max_cluster_size, link_mode, training, workers, reviews):
    """Resolve entity records from a pinned graph artifact; returns (view, match assertions)."""
    from .resolution.engine import ResolutionEngine, inputs_from_records
    records = list(records)
    record_ids = {}
    for record in records:
        if record['kind'] == 'entity':
            record_ids.setdefault(record.get('entity_id', record['id']), record['id'])
    workdir.mkdir(parents=True, exist_ok=True)
    engine = ResolutionEngine(workdir / 'resolution.sqlite', kind=kind, max_block_size=max_block_size, link_mode=link_mode, fresh=True)
    try:
        engine.add_records(inputs_from_records(records, entity_types=set(entity_types) if entity_types else None))
        engine.candidate_pairs()
        engine.compare(workers=workers)
        engine.estimate(training=training)
        if reviews:
            engine.add_reviews(reviews)
        engine.cluster(threshold=threshold, lower=lower, max_cluster_size=max_cluster_size)
        view = engine.resolved_view()
        assertions = []
        for assertion in engine.match_assertions(observed_at=observed_at, lower=lower, threshold=threshold,
                                                 evidence=[{'input': ref, 'record_id': 'pending'}]):
            assertion['evidence'] = [{'input': ref, 'record_id': record_ids[e]} for e in (assertion['subject'], assertion['object'])]
            assertions.append(assertion)
        (workdir / 'view.json').write_text(json.dumps(view, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        with (workdir / 'clusters.jsonl').open('w', encoding='utf-8') as stream:
            for cluster in engine.clusters(min_size=2):
                stream.write(json.dumps(cluster, sort_keys=True) + '\n')
        return {**view, 'model': engine.stats['model'], 'timings': engine.timings}, assertions
    finally:
        engine.close()


def execute(args, catalog, store, project, reference):
    command = args.command
    if command == 'units-convert':
        from .units import convert
        return convert(args.value, args.from_unit, args.to_unit, commodity=args.commodity, day_count=args.day_count,
                       allow_approximate=args.allow_approximate)
    if command == 'units-describe':
        from .units import describe_registry, parse_unit
        return parse_unit(args.unit).describe() if args.unit else describe_registry()
    if command == 'country-code':
        from .crosswalks import CountryCodes
        return CountryCodes().convert(args.value, args.from_scheme, args.to_scheme, at=args.at)
    if command == 'county-lookup':
        from .crosswalks import UsGeography
        return UsGeography().county(args.geoid, at=args.at)
    if command == 'reference-declarations':
        from .crosswalks import acquisition_declarations
        return acquisition_declarations()
    if command == 'crosswalk-apportion':
        from . import crosswalks
        values = _json_file(args.values, dict)
        weights = _json_file(args.weights, dict) if args.weights else None
        name = args.crosswalk
        if name.startswith('naics_'):
            _, a, b = name.split('_')
            walk = crosswalks.naics_concordance(int(a), int(b))
        elif name == 'us_county':
            if not args.start or not args.end:
                raise ValueError('us_county needs --start and --end')
            walk = crosswalks.UsGeography().county_crosswalk(args.start, args.end)
        elif name == 'ct_cousub':
            walk = crosswalks.UsGeography().ct_cousub_crosswalk()
        else:
            if not args.relationship_file:
                raise ValueError('zcta_county needs --relationship-file (see reference-declarations)')
            walk = crosswalks.zcta_county_crosswalk(args.relationship_file)
        return {'crosswalk': walk.describe(), **walk.apportion(values, weights=weights, unweighted=args.unweighted,
                                                                unmapped=args.unmapped)}
    if command == 'link-identifiers':
        from .resolution.deterministic import link_mapping
        rows = [json.loads(line) for line in args.rows.read_text(encoding='utf-8').splitlines() if line.strip()]
        result = link_mapping(args.spec, rows, observed_at=args.observed_at, evidence=json.loads(args.evidence))
        if args.output:
            with args.output.open('w', encoding='utf-8') as stream:
                for assertion in result['assertions']:
                    stream.write(json.dumps(assertion, sort_keys=True) + '\n')
            result = {**result, 'assertions': len(result['assertions']), 'output': str(args.output)}
        return result
    if command == 'resolve-entities':
        reviews = _json_file(args.reviews, list) if args.reviews else ()
        options = dict(kind=args.kind, entity_types=args.entity_type, observed_at=args.observed_at, workdir=args.workdir,
                       threshold=args.threshold, lower=args.lower, max_block_size=args.max_block_size,
                       max_cluster_size=args.max_cluster_size, link_mode=args.link_mode, training=args.training,
                       workers=args.workers, reviews=reviews)
        if args.input:
            from .resolution.engine import ResolutionEngine
            items = [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
            args.workdir.mkdir(parents=True, exist_ok=True)
            engine = ResolutionEngine(args.workdir / 'resolution.sqlite', kind=args.kind, max_block_size=args.max_block_size,
                                      link_mode=args.link_mode, fresh=True)
            try:
                engine.add_records(items)
                engine.candidate_pairs()
                engine.compare(workers=args.workers)
                engine.estimate(training=args.training)
                if reviews:
                    engine.add_reviews(reviews)
                engine.cluster(threshold=args.threshold, lower=args.lower, max_cluster_size=args.max_cluster_size)
                view = engine.resolved_view()
                evidence = [{'input': {'dataset': 'local_resolution_input'}, 'locator': str(args.input)}]
                with (args.workdir / 'matches.jsonl').open('w', encoding='utf-8') as stream:
                    for assertion in engine.match_assertions(observed_at=args.observed_at, evidence=evidence,
                                                             lower=args.lower, threshold=args.threshold):
                        stream.write(json.dumps(assertion, sort_keys=True) + '\n')
                (args.workdir / 'view.json').write_text(json.dumps(view, indent=1, sort_keys=True) + '\n', encoding='utf-8')
                with (args.workdir / 'clusters.jsonl').open('w', encoding='utf-8') as stream:
                    for cluster in engine.clusters(min_size=2):
                        stream.write(json.dumps(cluster, sort_keys=True) + '\n')
                return {**view, 'timings': engine.timings, 'outputs': str(args.workdir),
                        'published': False, 'note': 'Local input: evidence locators are not store artifacts; import the input to publish.'}
            finally:
                engine.close()
        from .artifacts import publish_report
        ref = reference(args.graph, store)
        view, assertions = resolve_graph(store.records(ref), ref=ref, **options)
        output = publish_report(store, args.dataset, view, {k: v for k, v in options.items() if k != 'workdir' and k != 'reviews'}
                                | {'reviews': list(reviews), 'workdir': str(args.workdir)},
                                inputs=[ref], records=assertions, entrypoint='worldmodel.identity_cli:resolve_graph')
        return {'artifact': output, **{k: view[k] for k in ('counts', 'rejected_merges', 'view_digest', 'input_digest', 'model_digest')},
                'match_assertions': len(assertions)}
    if command == 'beliefs':
        from .artifacts import publish_report
        from .reconciliation import materialize_beliefs
        ref = reference(args.graph, store)
        policy = _json_file(args.policy, dict) if args.policy else None
        result = materialize_beliefs(store.records(ref), known_at=args.known_at, at=args.at, policy=policy,
                                     subjects=set(args.subject) if args.subject else None,
                                     variables=set(args.variable) if args.variable else None)
        output = publish_report(store, args.dataset, result, {'policy': result['policy'], 'known_at': args.known_at, 'at': args.at},
                                inputs=[ref], entrypoint='worldmodel.reconciliation:materialize_beliefs')
        return {'artifact': output, 'counts': result['counts'], 'stale': result['stale'], 'conflicts': result['conflicts'][:100],
                'digest': result['digest']}
    from .graph import Graph
    graph = Graph(getattr(args, 'index', None) or _default_index(store))
    if command == 'graph-attach-resolution':
        view = _json_file(args.workdir / 'view.json', dict)
        clusters = (json.loads(line) for line in (args.workdir / 'clusters.jsonl').read_text(encoding='utf-8').splitlines() if line.strip())
        return graph.attach_resolution(clusters, view=view)
    filters = {'valid_at': args.valid_at, 'known_at': args.known_at, 'resolved': args.resolved,
               'include_unknown_publication': args.include_unknown_publication}
    if command == 'graph-neighborhood':
        return graph.neighborhood(args.entity, hops=args.hops, limit=args.limit, predicates=args.predicate,
                                  direction=args.direction, min_weight=args.min_weight, **filters)
    if command == 'graph-paths':
        return graph.paths(args.source, args.target, max_hops=args.max_hops, limit=args.limit, predicates=args.predicate,
                           direction=args.direction, min_weight=args.min_weight, **filters)
    if command == 'graph-centrality':
        if args.pagerank:
            return graph.pagerank(predicates=args.predicate, limit=args.limit, weighted=args.weighted, **filters)
        return graph.degree_centrality(predicates=args.predicate, weighted=args.weighted, limit=args.limit, **filters)
    return graph.flow_aggregate(args.predicate, group_by=args.group_by, limit=args.limit, **filters)
