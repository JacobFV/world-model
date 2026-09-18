"""Offline-first command line interface; JSON output is scriptable."""
import argparse
import json
import os
from pathlib import Path
import sys
from .catalog import Catalog
from .fetch import fetch
from .graph import Graph
from .pipeline import Runner
from .store import Store
from .resources import resource_roots
from .util import atomic_json, slug, read_json, hash_id

PROJECT = Path(__file__).resolve().parents[1]


def reference(value, store):
    name, separator, version = value.partition('@')
    pieces=name.split('/')
    if len(pieces)>2:raise ValueError('Reference must be dataset[/stage][@version]')
    dataset=slug(pieces[0]);stage=slug(pieces[1]) if len(pieces)==2 else None
    if separator:
        return {'dataset':dataset,**({'stage':stage} if stage else {}),'version':hash_id(version)}
    return store.latest(dataset,stage=stage) if stage else store.latest(dataset)


def parser():
    from .resources import resource_roots
    roots = resource_roots()
    p = argparse.ArgumentParser(prog='wm', description='Versioned evidence datasets and temporal graph')
    p.add_argument('--data-root', type=Path, default=roots['data'])
    p.add_argument('--catalog-root', type=Path, default=roots['catalog'])
    sub = p.add_subparsers(dest='command', required=True)
    from .strategic_cli import add_commands
    add_commands(sub)
    from .reference_cli import add_commands as add_reference
    add_reference(sub)
    from .environment_cli import add_commands as add_environment
    add_environment(sub)
    from .advanced_cli import add_commands as add_advanced
    add_advanced(sub)
    from .acquisition_cli import add_commands as add_acquisition
    add_acquisition(sub)
    from .identity_cli import add_commands as add_identity; add_identity(sub)
    from .estimation_cli import add_commands as add_estimation; add_estimation(sub)
    from .models_cli import add_commands as add_models; add_models(sub)
    from .unify_cli import add_commands as add_unify; add_unify(sub)
    from .agents_cli import add_commands as add_agents; add_agents(sub)
    from .society_cli import add_commands as add_society; add_society(sub)
    sub.add_parser('catalog', help='List declarations and readiness')
    sub.add_parser('ontology', help='Describe typed entities, relations and variables')
    sub.add_parser('processes', help='Describe process contracts and registered implementations')
    mat = sub.add_parser('materialize', help='Publish a requested temporal view or explicit forecast scenario')
    mat.add_argument('reference')
    mat.add_argument('--request', type=Path, required=True)
    view = sub.add_parser('view', help='Read a verified materialized view and execution trace')
    view.add_argument('reference')
    sub.add_parser('init', help='Create runtime directories for catalog entries')
    new = sub.add_parser('new', help='Create a source or derived dataset declaration')
    new.add_argument('dataset')
    new.add_argument('--kind', choices=['source', 'derived'], required=True)
    new.add_argument('--entrypoint', default='pipeline.py:run', help='Local pipeline.py:function to scaffold')
    new.add_argument('--depends-on', action='append', default=[])
    new.add_argument('--description', required=True)
    imp = sub.add_parser('import', help='Snapshot a local file into immutable raw storage')
    imp.add_argument('dataset')
    imp.add_argument('path', type=Path)
    imp.add_argument('--source-json', default='{}', help='Publisher, release, license, URL, etc.')
    download = sub.add_parser('fetch', help='Explicit download; never called automatically by run')
    download.add_argument('dataset')
    download.add_argument('url')
    download.add_argument('--allow-network', action='store_true')
    download.add_argument('--sha256')
    download.add_argument('--max-bytes', type=int, help='May lower the configured download budget')
    download.add_argument('--source-json', default='{}')
    download.add_argument('--user-agent', help='Publisher-required contact identity')
    plan = sub.add_parser('plan')
    plan.add_argument('dataset')
    run = sub.add_parser('run')
    run.add_argument('dataset')
    run.add_argument('--stage', help='Build a declared intermediate stage instead of the output stage')
    run.add_argument('--parameters', default='{}', help='JSON object; target only')
    run.add_argument('--input', action='append', default=[], help='Pin dependency dataset[/stage]@version')
    run.add_argument('--raw', action='append', default=[], help='Pin source dataset@artifact; repeat for shards')
    for name in ('inspect', 'lineage', 'verify'):
        command = sub.add_parser(name)
        command.add_argument('reference', help='dataset[/stage][@version]')
    sample = sub.add_parser('sample', help='Fetch bounded exploratory samples without changing full-data pointers')
    sample.add_argument('dataset', help='Dataset ID or all')
    sample.add_argument('--allow-network', action='store_true')
    explore = sub.add_parser('explore', help='Inspect a verified sample profile')
    explore.add_argument('dataset')
    sub.add_parser('demo', help='Import tiny fictional fixtures and run the full pipeline offline')
    build = sub.add_parser('graph-build')
    build.add_argument('references', nargs='+')
    build.add_argument('--index', type=Path)
    for name, argument in [('neighbors', 'entity'), ('observations', 'metric')]:
        command = sub.add_parser(name)
        command.add_argument(argument)
        command.add_argument('--index', type=Path)
        command.add_argument('--valid-at')
        command.add_argument('--known-at')
        command.add_argument('--limit', type=int, default=100)
        if name == 'neighbors':
            command.add_argument('--hops', type=int, default=1)
    return p


def object_json(value):
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object')
    return result


def execute(args):
    catalog, store = Catalog(args.catalog_root), Store(args.data_root)
    runner = Runner(catalog, store, PROJECT)
    command = args.command
    from .strategic_cli import COMMANDS, execute as strategic_execute
    if command in COMMANDS:
        return strategic_execute(args, catalog, store, PROJECT, reference)
    from .reference_cli import COMMANDS as REFERENCE_COMMANDS, execute as reference_execute
    if command in REFERENCE_COMMANDS:
        return reference_execute(args, catalog, store, PROJECT, reference)
    from .environment_cli import COMMANDS as ENV_COMMANDS, execute as environment_execute
    if command in ENV_COMMANDS:
        return environment_execute(args, catalog, store, PROJECT, reference)
    from .advanced_cli import COMMANDS as ADVANCED_COMMANDS, execute as advanced_execute
    if command in ADVANCED_COMMANDS:
        return advanced_execute(args, catalog, store, PROJECT, reference)
    from .acquisition_cli import COMMANDS as ACQUISITION_COMMANDS, execute as acquisition_execute
    if command in ACQUISITION_COMMANDS:
        return acquisition_execute(args, catalog, store, PROJECT, reference)
    from .estimation_cli import COMMANDS as ESTIMATION_COMMANDS, execute as estimation_execute
    if command in ESTIMATION_COMMANDS: return estimation_execute(args, catalog, store, PROJECT, reference)
    from .models_cli import COMMANDS as MODEL_COMMANDS, execute as models_execute
    if command in MODEL_COMMANDS: return models_execute(args, catalog, store, PROJECT, reference)
    from .identity_cli import COMMANDS as IDENTITY_COMMANDS, execute as identity_execute
    if command in IDENTITY_COMMANDS: return identity_execute(args, catalog, store, PROJECT, reference)
    if command == 'ontology':
        from .ontology import describe
        return describe()
    if command == 'processes':
        from .process_library import default_registry
        return default_registry().describe()
    from .unify_cli import COMMANDS as UNIFY_COMMANDS, execute as unify_execute
    if command in UNIFY_COMMANDS: return unify_execute(args, catalog, store, PROJECT, reference)
    from .agents_cli import COMMANDS as AGENT_COMMANDS, execute as agents_execute
    if command in AGENT_COMMANDS: return agents_execute(args, catalog, store, PROJECT, reference)
    from .society_cli import COMMANDS as SOCIETY_COMMANDS, execute as society_execute
    if command in SOCIETY_COMMANDS: return society_execute(args, catalog, store, PROJECT, reference)
    if command in ('materialize', 'view'):
        from .materialize import materialize, load_view
        ref = reference(args.reference, store)
        if command == 'view':
            return load_view(store, ref)
        result = materialize(store, ref, read_json(args.request))
        return {key: result[key] for key in ('artifact', 'plan', 'execution', 'limitations')}
    if command == 'sample':
        from .sampling import sample_dataset
        definitions = catalog.list() if args.dataset == 'all' else [catalog.get(args.dataset)]
        results = []
        for definition in definitions:
            result = sample_dataset(store, definition, allow_network=args.allow_network)
            results.append(result)
            print(f'{definition["id"]}: {result["status"]}', file=sys.stderr, flush=True)
        return results
    if command == 'explore':
        from .sampling import explore
        return explore(store, args.dataset)
    if command == 'catalog':
        return [{k: d.get(k) for k in ('id', 'kind', 'status', 'description', 'dependencies')}
                for d in catalog.list()]
    if command == 'init':
        names = [d['id'] for d in catalog.list()]
        for name in names:
            store.initialize(name)
        return {'data_root': str(store.root), 'datasets': names}
    if command == 'new':
        slug(args.dataset)
        for dependency in args.depends_on:
            catalog.get(dependency)
        path = catalog.root / args.dataset / 'dataset.json'
        if path.exists():
            raise ValueError('Dataset already exists')
        from .dataset_scaffold import create_dataset
        definition=create_dataset(catalog,args.dataset,kind=args.kind,description=args.description,
                                  dependencies=args.depends_on,entrypoint=args.entrypoint)
        store.initialize(args.dataset)
        return definition
    if command in ('import', 'fetch'):
        definition = catalog.get(args.dataset)
        if definition['kind'] != 'source':
            raise ValueError('Raw acquisition belongs to source datasets')
        metadata = {**definition.get('source', {}), **object_json(args.source_json)}
        if command == 'import':
            return store.import_file(args.dataset, args.path, metadata)
        headers = {'User-Agent': args.user_agent} if args.user_agent else None
        budget = definition.get('sampling', {}).get('max_download_bytes', 1024 ** 2)
        if args.max_bytes is not None:
            budget = min(budget, args.max_bytes)
        return fetch(store, args.dataset, args.url, metadata, allow_network=args.allow_network,
                     expected_sha256=args.sha256, max_bytes=budget, headers=headers)
    if command == 'plan':
        return [{'dataset': name, 'status': catalog.get(name).get('status'),
                 'entrypoint': catalog.get(name).get('entrypoint'),
                 'output_stage':catalog.get(name).get('output_stage'),
                 'stages':catalog.get(name).get('stages',[])} for name in catalog.plan(args.dataset)]
    if command == 'run':
        inputs, raws = {}, {}
        for value in args.input:
            if '@' not in value:
                raise ValueError('--input requires dataset[/stage]@version')
            ref = reference(value, store)
            if ref['dataset'] in inputs:
                raise ValueError('Duplicate input pin')
            inputs[ref['dataset']] = ref
        for value in args.raw:
            if '@' not in value:
                raise ValueError('--raw requires dataset@artifact')
            dataset, artifact = value.split('@', 1)
            raws.setdefault(dataset, []).append({'dataset': dataset, 'artifact': artifact})
        return runner.run(args.dataset, parameters=object_json(args.parameters), raw_refs=raws, input_refs=inputs, stage=args.stage)
    if command in ('inspect', 'lineage', 'verify'):
        ref = reference(args.reference, store)
        if command == 'inspect':
            return store.manifest(ref)
        if command == 'lineage':
            return store.lineage(ref)
        return {'reference': ref, 'verified': store.verify(ref)}
    default_index = store.root / 'world_evidence/index.sqlite'
    if (store.root/'strategic_evidence/index.sqlite').exists():
        default_index = store.root/'strategic_evidence/index.sqlite'
    if (store.root/'reference_evidence/index.sqlite').exists():
        default_index = store.root/'reference_evidence/index.sqlite'
    if command == 'demo' or not default_index.exists():
        default_index = store.root / 'world_graph/index.sqlite'
    graph = Graph(getattr(args, 'index', None) or default_index)
    if command == 'demo':
        for dataset, filename in [('demo_countries', 'countries.csv'), ('demo_graph', 'graph.jsonl')]:
            store.import_file(dataset, resource_roots()['fixtures'] / filename,
                              catalog.get(dataset)['source'])
        ref = runner.run('world_graph')
        return {'output': ref, 'graph': graph.build(store, [ref]), 'fictional_data': True}
    if command == 'graph-build':
        return graph.build(store, [reference(value, store) for value in args.references])
    filters = {'limit': args.limit, 'valid_at': args.valid_at, 'known_at': args.known_at}
    if command == 'neighbors':
        return graph.neighbors(args.entity, hops=args.hops, **filters)
    return graph.observations(args.metric, **filters)


def main(argv=None):
    if argv is None:  # Real command-line startup: load <project>/.env and $WORLD_MODEL_ENV_FILE.
        from .env import load_project_env
        load_project_env()
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 1
