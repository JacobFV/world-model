"""Command line access to political, geopolitical and market model families and strategic games."""
import json
from pathlib import Path
from .util import read_json

COMMANDS = {'models'}
ACTIONS = ('list', 'describe', 'requirements', 'hooks', 'processes', 'synthetic', 'fit', 'simulate', 'game', 'compare')


def add_commands(sub):
    command = sub.add_parser('models', help='Estimable political/geopolitical/market model families, simulations and games')
    command.add_argument('action', choices=ACTIONS)
    command.add_argument('family', nargs='?', help='Model family id (see `models list`)')
    command.add_argument('--request', type=Path, help='simulate: {family, mode, seed, config}; game/compare: game specification')
    command.add_argument('--data', type=Path, help='fit: JSON data following the family fit contract')
    command.add_argument('--synthetic', action='store_true', help='fit/simulate: use the family synthetic fixture with known truth')
    command.add_argument('--cutoff', help='fit: information cutoff (ISO date); later rows are excluded')
    command.add_argument('--mode', choices=['deterministic', 'stochastic'])
    command.add_argument('--seed', type=int)
    command.add_argument('--focal-actor', help='compare: actor whose candidate actions are ranked')
    command.add_argument('--publish', action='store_true', help='Publish the result as an immutable report dataset')
    command.add_argument('--dataset', help='Report dataset name when publishing')


def _family(args):
    if not args.family:
        raise ValueError(f'models {args.action} requires a family id')
    return args.family


def execute(args, catalog, store, project, reference):
    from . import models
    action = args.action
    if action == 'list':
        return [{k: f[k] for k in ('id', 'title', 'identification', 'validated')} | {'parameters': sorted(f['parameters']),
                 'requirements': [r['id'] for r in f['requirements']]} for f in models.families()]
    if action == 'describe':
        return models.describe(_family(args))
    if action == 'requirements':
        return models.requirements(args.family)
    if action == 'hooks':
        return models.parameter_hooks(_family(args))
    if action == 'processes':
        from .processes import ProcessRegistry
        return models.register_model_processes(ProcessRegistry()).describe()
    raw, entrypoint, parameters = [], None, {}
    if action == 'synthetic':
        fixture = models.synthetic(_family(args), args.seed or 0)
        return {'family': args.family, 'truth': fixture['truth'], 'config': fixture['config'],
                'data_summary': {k: (len(v) if isinstance(v, list) else type(v).__name__) for k, v in fixture['data'].items()},
                'epistemic_status': 'synthetic_fixture'}
    if action == 'fit':
        family = _family(args)
        if args.synthetic:
            fixture = models.synthetic(family, args.seed or 0)
            data, truth = fixture['data'], fixture['truth']
        elif args.data:
            data, truth = read_json(args.data), None
            if args.publish:
                raw.append(store.import_file(f'{family}_fit_inputs', args.data, {'publisher': 'user-provided', 'role': 'model fit data'}, update_latest=False))
        else:
            raise ValueError('models fit requires --data or --synthetic')
        result = models.fit(family, data, args.cutoff)
        if truth is not None:
            result['synthetic_truth'] = truth
        entrypoint, parameters = f'worldmodel.models.{family}:fit', {'family': family, 'cutoff': args.cutoff, 'synthetic': args.synthetic}
    elif action == 'simulate':
        if args.request:
            request = _request(args, store, 'model scenario')
            raw.extend(request.pop('_raw', []))
            family = request.get('family') or _family(args)
            config, mode, seed = request['config'], request.get('mode', 'deterministic'), request.get('seed', 0)
        elif args.synthetic:
            family = _family(args)
            config, mode, seed = models.synthetic(family, 0)['config'], 'deterministic', 0
        else:
            raise ValueError('models simulate requires --request or --synthetic')
        mode = args.mode or mode
        seed = args.seed if args.seed is not None else seed
        result = models.simulate(family, config, mode, seed)
        entrypoint, parameters = f'worldmodel.models.{family}:simulate', {'family': family, 'mode': mode, 'seed': seed}
    elif action in ('game', 'compare'):
        from .models.games import solve_game, compare_strategies
        if not args.request:
            raise ValueError(f'models {action} requires --request')
        spec = _request(args, store, 'strategic game specification')
        raw.extend(spec.pop('_raw', []))
        if action == 'game':
            result, entrypoint = solve_game(spec), 'worldmodel.models.games:solve_game'
        else:
            options = spec.get('compare', {})
            focal = args.focal_actor or options.get('focal_actor')
            if not focal:
                raise ValueError('compare requires --focal-actor or compare.focal_actor')
            result = compare_strategies(spec, focal, response=options.get('response', 'best_response'), ranking=options.get('ranking', 'expected'),
                                        constraints=options.get('constraints', []))
            entrypoint = 'worldmodel.models.games:compare_strategies'
        parameters = {'action': action}
    if not args.publish:
        return result
    from .artifacts import publish_report
    dataset = args.dataset or f'models_{action}_report'
    ref = publish_report(store, dataset, result, parameters, raw_inputs=raw, entrypoint=entrypoint)
    return {'artifact': ref, **result}


def _request(args, store, label):
    if args.publish:
        from .strategic_cli import _local_input
        ref, payload = _local_input(store, args.dataset or 'models_request', args.request, label)
        payload['_raw'] = [ref]
        return payload
    return json.loads(Path(args.request).read_text())
