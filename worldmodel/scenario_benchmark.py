"""Pre-split synthetic policy comparisons and bounded parameter ambiguity reports."""
from copy import deepcopy
import math
from .coupled_economy import initialize_economy, step_economy
from .util import canonical, digest


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value): raise ValueError('Expected finite numeric result')
    return value


def _validate_path(path):
    if not isinstance(path, list) or not 1 <= len(path) <= 20 or any(
            not (isinstance(key, str) and key or type(key) is int and key >= 0) for key in path):
        raise ValueError('Explicit bounded path required')


def _path(value, path):
    _validate_path(path)
    for key in path:
        if isinstance(value, dict) and isinstance(key, str) and key in value: value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value): value = value[key]
        else: raise ValueError('Missing objective/output path')
    return value


def _mean(values): return _number(math.fsum(v / len(values) for v in values))


def _weights(scenarios):
    largest = max(s['weight'] for s in scenarios)
    relative = [s['weight'] / largest for s in scenarios]
    total = math.fsum(relative)
    return [w / total for w in relative]


def _pareto(scores, objectives):
    ids = sorted(scores)
    def dominates(a, b):
        pairs = [(scores[a][name], scores[b][name]) if spec['direction'] == 'maximize' else
                 (scores[b][name], scores[a][name]) for name, spec in objectives.items()]
        return all(a >= b for a, b in pairs) and any(a > b for a, b in pairs)
    return [pid for pid in ids if not any(other != pid and dominates(other, pid) for other in ids)]


def benchmark_scenarios(scenarios, policies, objectives, *, selection_objective, baseline_ids,
                        constraints=None, shortlist_size=2, max_transitions=100):
    """Finite policy search: train shortlist, validation selection, frozen test report.

    Scenario config has initial_state and a shocks list defining the horizon. Policy
    actions are fixed daily policy objects, one per day. Every candidate/baseline is
    compared on final test scenarios only after selection. Test Pareto results are
    descriptive comparisons, never used to retune the selected policy.
    """
    if type(max_transitions) is not int or not 1 <= max_transitions <= 100000: raise ValueError('Invalid transition budget')
    if not isinstance(scenarios, list) or not 3 <= len(scenarios) <= 100 or not isinstance(policies, list) or not 2 <= len(policies) <= 20:
        raise ValueError('Require 3..100 scenarios and 2..20 policies')
    if not isinstance(objectives, dict) or not 1 <= len(objectives) <= 20 or selection_objective not in objectives:
        raise ValueError('Explicit named objectives and selection objective required')
    constraints = {} if constraints is None else constraints
    if not isinstance(constraints, dict) or set(constraints) - set(objectives): raise ValueError('Unknown constraint objective')
    canonical([scenarios, policies, objectives, constraints])
    for name, spec in objectives.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict) or set(spec) != {'path', 'unit', 'direction'}:
            raise ValueError('Objectives require path,unit,direction')
        if spec['direction'] not in ('maximize', 'minimize') or not isinstance(spec['unit'], str) or not spec['unit']:
            raise ValueError('Objective direction/unit invalid')
        _validate_path(spec['path'])
    for bound in constraints.values():
        if not isinstance(bound, dict) or not bound or set(bound) - {'minimum', 'maximum'}: raise ValueError('Constraint requires bounds')
        for value in bound.values(): _number(value)
        if bound.get('minimum', -math.inf) > bound.get('maximum', math.inf): raise ValueError('Constraint bounds reversed')
    ids, identities = set(), set(); split = {name: [] for name in ('train', 'validation', 'test')}
    horizon = None
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {'id', 'split', 'weight', 'config'}: raise ValueError('Scenario requires id,split,weight,config')
        sid = scenario['id']
        if not isinstance(sid, str) or not sid or sid in ids or scenario['split'] not in split: raise ValueError('Scenario identity/split invalid')
        if _number(scenario['weight']) <= 0: raise ValueError('Scenario weight must be positive')
        config = scenario['config']
        if not isinstance(config, dict) or set(config) != {'initial_state', 'shocks'} or not isinstance(config['shocks'], list) or not 1 <= len(config['shocks']) <= 1000:
            raise ValueError('Scenario config requires initial_state and bounded daily shocks')
        fingerprint = digest(config)
        if fingerprint in identities: raise ValueError('Scenario configurations must be disjoint before fitting')
        identities.add(fingerprint); ids.add(sid); split[scenario['split']].append(scenario)
        if horizon is None: horizon = len(config['shocks'])
        if len(config['shocks']) != horizon: raise ValueError('Comparison scenarios must use the same declared horizon')
    if any(not values for values in split.values()): raise ValueError('Train, validation and test scenarios all required')
    by_policy = {}
    for policy in policies:
        if not isinstance(policy, dict) or set(policy) != {'id', 'actions'} or not isinstance(policy['id'], str) or not policy['id'] or policy['id'] in by_policy:
            raise ValueError('Policies require unique ID and explicit actions')
        if not isinstance(policy['actions'], list) or len(policy['actions']) != horizon or any(not isinstance(a, dict) for a in policy['actions']):
            raise ValueError('Policy actions must align with scenario horizon')
        by_policy[policy['id']] = policy
    if not isinstance(baseline_ids, list) or not baseline_ids or any(not isinstance(pid, str) for pid in baseline_ids) or len(set(baseline_ids)) != len(baseline_ids) or set(baseline_ids) - set(by_policy):
        raise ValueError('Explicit distinct baseline IDs required')
    candidates = sorted(set(by_policy) - set(baseline_ids))
    if not candidates or type(shortlist_size) is not int or not 1 <= shortlist_size <= len(candidates): raise ValueError('Invalid training shortlist size')
    reserved = len(scenarios) * len(policies) * horizon
    if reserved > max_transitions: raise ValueError('Scenario transition budget exceeded before execution')
    def evaluate(group):
        weights = _weights(group); summaries = {}; details = []
        for pid in sorted(by_policy):
            values = []; feasible = True
            for scenario in group:
                state = initialize_economy(scenario['config']['initial_state'])
                for action, shock in zip(by_policy[pid]['actions'], scenario['config']['shocks']): state = step_economy(state, action, shock)
                scores = {name: _number(_path(state, spec['path'])) for name, spec in objectives.items()}
                violations = [name for name, bound in constraints.items() if not bound.get('minimum', -math.inf) <= scores[name] <= bound.get('maximum', math.inf)]
                feasible = feasible and not violations; values.append(scores)
                details.append({'scenario': scenario['id'], 'policy': pid, 'objectives': scores,
                                'constraint_violations': violations, 'accounting_balanced': all(r['accounting']['balanced'] for r in state['history'])})
            weighted = {name: _number(math.fsum(w * row[name] for w, row in zip(weights, values))) for name in objectives}
            worst = {name: (min if spec['direction'] == 'maximize' else max)(row[name] for row in values) for name, spec in objectives.items()}
            summaries[pid] = {'weighted': weighted, 'worst_case': worst, 'constraints_satisfied_in_all_scenarios': feasible}
        return {'scenarios': [{'id': s['id'], 'normalized_weight': w, 'config_digest': digest(s['config'])} for s, w in zip(group, weights)],
                'per_scenario': details, 'policies': summaries, 'pareto_front': _pareto({k: v['weighted'] for k, v in summaries.items()}, objectives)}
    direction = 1 if objectives[selection_objective]['direction'] == 'maximize' else -1
    def rank(result, policy_ids):
        return sorted(policy_ids, key=lambda pid: (not result['policies'][pid]['constraints_satisfied_in_all_scenarios'],
                       -direction * result['policies'][pid]['weighted'][selection_objective], pid))
    train = evaluate(split['train'])
    shortlist = rank(train, candidates)[:shortlist_size]
    validation = evaluate(split['validation'])
    selected = rank(validation, shortlist)[0]
    # The selection above is final before any test environment is constructed.
    test = evaluate(split['test'])
    return {'selected_policy': selected, 'training_shortlist': shortlist, 'selection_objective': selection_objective,
            'selection_rule': 'training shortlist then validation objective; feasible policies first, lexical ID ties',
            'baseline_ids': deepcopy(baseline_ids), 'objectives': deepcopy(objectives), 'constraints': deepcopy(constraints),
            'train': train, 'validation': validation, 'test': test, 'transitions': reserved, 'splits_disjoint': True,
            'epistemic_status': 'synthetic_scenario_generalization', 'causally_validated': False,
            'limitations': ['Finite fixed-policy search, not empirical strategy calibration.',
                           'Test comparisons and Pareto front do not change the frozen validation selection.',
                           'Objectives use explicit final-state paths; weights and constraints are declared assumptions.']}


def parameter_ensemble(evaluate, parameter_sets, observations, *, probes, outputs, baseline_id, tolerance=0, max_evaluations=100):
    """Finite-grid synthetic fit ambiguity and withheld response disagreement.

    evaluate(parameters,input) returns named numerical outputs. Observations must
    explicitly identify synthetic status, unit and output path. Supplied parameter
    ranges are a grid, not a posterior or an identified empirical causal model.
    """
    if not callable(evaluate) or not isinstance(parameter_sets, list) or not 2 <= len(parameter_sets) <= 100: raise ValueError('Bounded parameter candidates required')
    if not isinstance(observations, list) or not 1 <= len(observations) <= 100 or not isinstance(probes, list) or len(probes) > 100:
        raise ValueError('Bounded observations/probes required')
    if not isinstance(outputs, dict) or not 1 <= len(outputs) <= 20: raise ValueError('Explicit probe outputs required')
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 10000 or len(parameter_sets) * (len(observations) + len(probes)) > max_evaluations:
        raise ValueError('Parameter evaluation budget exceeded')
    if _number(tolerance) < 0: raise ValueError('Tolerance must be nonnegative')
    canonical([parameter_sets, observations, probes, outputs])
    candidates = {}; parameter_keys = None; fingerprints = set()
    for candidate in parameter_sets:
        if not isinstance(candidate, dict) or set(candidate) != {'id', 'parameters'} or not isinstance(candidate['id'], str) or not candidate['id'] or candidate['id'] in candidates:
            raise ValueError('Parameter candidates require unique ID and parameters')
        params = candidate['parameters']
        if not isinstance(params, dict) or not 1 <= len(params) <= 20: raise ValueError('Bounded named numeric parameters required')
        for value in params.values(): _number(value)
        if parameter_keys is None: parameter_keys = set(params)
        if set(params) != parameter_keys: raise ValueError('Parameter keys must match')
        fingerprint = digest(params)
        if fingerprint in fingerprints: raise ValueError('Duplicate parameter vector')
        fingerprints.add(fingerprint); candidates[candidate['id']] = params
    if baseline_id not in candidates: raise ValueError('Explicit baseline candidate required')
    units = {}
    for row in observations:
        if not isinstance(row, dict) or set(row) != {'input', 'path', 'value', 'unit', 'epistemic_status'} or row['epistemic_status'] != 'synthetic_scenario':
            raise ValueError('This experiment requires explicitly synthetic observations')
        _number(row['value'])
        if not isinstance(row['unit'], str) or not row['unit']: raise ValueError('Observation unit required')
        _validate_path(row['path'])
        key = canonical(row['path'])
        if key in units and units[key] != row['unit']: raise ValueError('Observation units conflict')
        units[key] = row['unit']
    for spec in outputs.values():
        if not isinstance(spec, dict) or set(spec) != {'path', 'unit'} or not isinstance(spec['unit'], str) or not spec['unit']: raise ValueError('Output path/unit required')
        _validate_path(spec['path'])
        if canonical(spec['path']) in units and units[canonical(spec['path'])] != spec['unit']: raise ValueError('Probe unit conflicts with fitted output')
    probe_ids = set()
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) != {'id', 'input'} or not isinstance(probe['id'], str) or probe['id'] in probe_ids: raise ValueError('Probes require unique IDs and inputs')
        probe_ids.add(probe['id'])
    scores = {}; calls = 0
    for pid, params in sorted(candidates.items()):
        errors = []
        for row in observations:
            predicted = _number(_path(evaluate(deepcopy(params), deepcopy(row['input'])), row['path'])); calls += 1
            errors.append(_number(abs(predicted - row['value'])))
        scores[pid] = _mean(errors)
    best = min(scores.values())
    accepted = sorted(pid for pid, score in scores.items() if score - best <= tolerance)
    reports = []
    for probe in probes:
        predictions = {}
        for pid in accepted:
            result = evaluate(deepcopy(candidates[pid]), deepcopy(probe['input'])); calls += 1
            predictions[pid] = {name: _number(_path(result, spec['path'])) for name, spec in outputs.items()}
        reports.append({'id': probe['id'], 'predictions': predictions,
                        'outputs': {name: {'minimum': min(p[name] for p in predictions.values()),
                            'maximum': max(p[name] for p in predictions.values()), 'unit': spec['unit']} for name, spec in outputs.items()}})
    return {'fit_mae': scores, 'baseline_id': baseline_id, 'baseline_mae': scores[baseline_id],
            'accepted_parameter_ids': accepted, 'accepted_parameters': {pid: deepcopy(candidates[pid]) for pid in accepted},
            'parameter_bounds': {key: {'minimum': min(p[key] for p in candidates.values()), 'maximum': max(p[key] for p in candidates.values())} for key in sorted(parameter_keys)},
            'observational_ambiguity': len(accepted) > 1, 'tolerance': tolerance, 'probes': reports,
            'evaluations': calls, 'epistemic_status': 'synthetic_parameter_experiment', 'causally_validated': False,
            'identification_status': 'multiple_equally_good_grid_candidates' if len(accepted) > 1 else 'single_best_in_supplied_grid',
            'limitations': ['Grid agreement is not empirical identification or a probability distribution.',
                           'Withheld response disagreement exposes assumptions, not validated causal effects.']}


def economy_scenario_example():
    """Thirty-transition train/validation/test comparison across distinct economies."""
    base = {'banks': [{'id': 'a', 'reserves': 100, 'equity': 100, 'accounts': {'firm': 0}, 'loans': {'firm': 0}},
                     {'id': 'b', 'reserves': 100, 'equity': 50, 'accounts': {'worker': 50}, 'loans': {}}],
            'firms': [{'id': 'firm', 'bank': 'a', 'worker': 'worker', 'inventory': 0, 'capacity': 10, 'unit_cost': 5}],
            'households': [{'id': 'worker', 'bank': 'b', 'labor_capacity': 4}],
            'mechanisms': {'interest': {'policy_rate': .04, 'spread': .02, 'day_count': 365, 'insufficient': 'defer'}}}
    scenarios = []
    for sid, split, labor, liquidity, rate, cost in [('train_normal', 'train', 4, 100, .04, 5),
            ('train_supply', 'train', 2, 100, .08, 7), ('validation_liquidity', 'validation', 3, 8, .2, 5),
            ('test_labor', 'test', 1, 100, .08, 9), ('test_rate', 'test', 6, 15, .3, 6)]:
        initial = deepcopy(base); initial['households'][0]['labor_capacity'] = labor
        initial['banks'][0].update(reserves=liquidity, equity=liquidity)
        initial['mechanisms']['interest']['policy_rate'] = rate
        scenarios.append({'id': sid, 'split': split, 'weight': 1,
                          'config': {'initial_state': initial, 'shocks': [{}, {'unit_cost': {'firm': cost}}]}})
    policies = []
    for pid, production, purchase, repay in [('baseline', 0, 0, 0), ('cautious', 2, 1, 5), ('expansion', 4, 1, 0)]:
        action = {'firms': {'firm': {'production': production, 'price': 10, 'credit_limit': 50, 'repay': repay}},
                  'households': {'worker': {'purchases': {'firm': purchase}}}}
        policies.append({'id': pid, 'actions': [deepcopy(action), deepcopy(action)]})
    return {'scenarios': scenarios, 'policies': policies, 'objectives': {
        'inventory': {'path': ['firms', 0, 'inventory'], 'unit': 'goods', 'direction': 'maximize'},
        'debt': {'path': ['banks', 0, 'loans', 'firm'], 'unit': 'USD', 'direction': 'minimize'},
        'household_cash': {'path': ['banks', 1, 'accounts', 'worker'], 'unit': 'USD', 'direction': 'maximize'}},
        'selection_objective': 'inventory', 'baseline_ids': ['baseline'], 'constraints': {'debt': {'maximum': 40}},
        'shortlist_size': 2, 'max_transitions': 100}
