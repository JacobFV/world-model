"""``wm decision-validate``, ``wm decision-rollout``, ``wm decision-stress`` and ``wm decision-optimize``.

Every command reads the published calibration reports (``calibration_reports`` in the
data root) to resolve evidence, unless ``--no-store`` is given, in which case no
mechanism can resolve to ``validated``.
"""
import json
from pathlib import Path

COMMANDS = {'decision-validate', 'decision-rollout', 'decision-stress', 'decision-optimize'}


def add_commands(sub):
    def common(command):
        command.add_argument('contract', type=Path, help='Decision contract JSON (worldmodel.decision_contract/1)')
        command.add_argument('--no-store', action='store_true',
                             help='Do not read calibration reports; every report-backed claim is then unconfirmed')
        command.add_argument('--out', type=Path, help='Also write the full JSON result here')

    validate = sub.add_parser('decision-validate', help='Validate a decision contract and resolve each mechanism\'s evidence')
    common(validate)
    rollout = sub.add_parser('decision-rollout', help='Run candidate policies on the nominal scenario; labelled by evidence')
    common(rollout)
    rollout.add_argument('--policies', type=Path, required=True, help='JSON object of named policy declarations')
    rollout.add_argument('--point', type=Path, help='JSON object overriding scenario dimensions (default nominal)')
    stress = sub.add_parser('decision-stress', help='Search declared scenario bounds for where candidate policies fail')
    common(stress)
    stress.add_argument('--policies', type=Path, required=True, help='JSON object of named policy declarations')
    stress.add_argument('--evaluations', type=int, default=400, help='Rollouts per policy (random plus refinement)')
    stress.add_argument('--refine-share', type=float, default=0.5, help='Share of the budget spent on local refinement')
    stress.add_argument('--seed', type=int, default=0)
    optimize = sub.add_parser('decision-optimize',
                              help='Tabular policy optimization, refused unless every mechanism is validated in the store')
    common(optimize)
    optimize.add_argument('--request', type=Path, required=True,
                          help='JSON: actions, encoder_bins, training_seeds, evaluation_seeds, baselines, alpha, gamma, epsilon, seed')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _seeds(value, name):
    if isinstance(value, dict):
        start, count = value.get('start'), value.get('count')
        if type(start) is not int or type(count) is not int or not 1 <= count <= 1000000:
            raise ValueError(f'{name} needs integer start and count in 1..1000000')
        return list(range(start, start + count))
    if isinstance(value, list) and value and all(type(v) is int for v in value):
        return value
    raise ValueError(f'{name} must be {{"start", "count"}} or a list of integer seeds')


def _write(path, result):
    if path is not None:
        Path(path).write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def execute(args, catalog, store, project, reference):
    from .compile import compile_contract, compile_from_store, policies_from, recommend
    from .contract import load_contract
    contract = load_contract(args.contract)
    if args.command == 'decision-validate' and args.no_store:
        from .evidence import recommendation_label, resolve_mechanisms
        from .kernels import kernel_for
        resolutions = resolve_mechanisms(contract, None)
        result = {'contract': contract['id'], 'valid': True, 'kernel': kernel_for(contract['environment']['kernel']).describe(),
                  'evidence': {'index': None, 'mechanisms': resolutions}, 'label': recommendation_label(resolutions),
                  'note': 'Structure checked; no calibration reports were read, so nothing was bound or confirmed.'}
        _write(args.out, result)
        return result
    compiled = compile_contract(contract, None) if args.no_store else compile_from_store(contract, store)
    if args.command == 'decision-validate':
        spec = compiled.environment_spec()
        result = {'contract': compiled.contract['id'], 'valid': True, 'kernel': compiled.kernel.describe(),
                  'evidence': compiled.evidence(), 'environment_spec': spec,
                  'scenario_dimensions': compiled.bound.dimensions,
                  'parameter_uncertainty': getattr(compiled.bound, 'uncertainty', None),
                  'training_source': compiled.bound.training_source(),
                  'label': _label(compiled), 'does_not_establish': compiled.does_not_establish()}
    elif args.command == 'decision-rollout':
        policies = policies_from(_read(args.policies), compiled.contract)
        point = _read(args.point) if args.point else {}
        scenario = compiled.scenario(point)
        runs = {name: compiled.rollout(policy, scenario, name=name) for name, policy in policies.items()}
        result = {'contract': compiled.contract['id'], 'scenario': scenario.get('point'), 'rollouts': runs,
                  'recommendation': recommend(compiled, policies, [scenario])}
    elif args.command == 'decision-stress':
        from .fragility import stress_test
        policies = policies_from(_read(args.policies), compiled.contract)
        result = stress_test(compiled, policies, evaluations=args.evaluations, refine_share=args.refine_share, seed=args.seed)
    else:
        from .gated_rl import RefusedToOptimize, optimize
        request = _read(args.request)
        try:
            result = optimize(compiled, actions=request['actions'], encoder_bins=request['encoder_bins'],
                              training_seeds=_seeds(request['training_seeds'], 'training_seeds'),
                              evaluation_seeds=_seeds(request['evaluation_seeds'], 'evaluation_seeds'),
                              baselines=request.get('baselines'), alpha=request.get('alpha', 0.1),
                              gamma=request.get('gamma', 0.0), epsilon=request.get('epsilon', 0.2),
                              seed=request.get('seed', 0), fallback_action=request.get('fallback_action', 0))
        except RefusedToOptimize as refusal:
            result = {'contract': compiled.contract['id'], 'refused': True, 'gate': refusal.report,
                      'label': _label(compiled),
                      'statement': 'No policy was optimized. Optimizing against these mechanisms would produce a policy '
                                   'that is optimal only for their assumptions.'}
    _write(args.out, result)
    return result


def _label(compiled):
    from .evidence import recommendation_label
    return recommendation_label(compiled.resolutions)
