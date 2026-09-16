"""Finite policy search under explicit stress scenarios and objective constraints."""
from copy import deepcopy
import math
from .util import canonical


def merge_overrides(base, patch):
    if not isinstance(patch, dict) or not isinstance(base, dict):
        raise ValueError('Overrides and base must be objects')
    result = deepcopy(base)
    for key, value in patch.items():
        if key == 'businesses' and key in result:
            if not isinstance(value, list):
                raise ValueError('Business overrides must be a list')
            by_id = {row['id']: row for row in result[key]}
            seen = set()
            for row in value:
                identity = row['id']
                if identity in seen or identity not in by_id:
                    raise ValueError('Unknown or duplicate business in override')
                seen.add(identity)
                by_id[identity] = merge_overrides(by_id[identity], row)
            result[key] = [by_id[row['id']] for row in result[key]]
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_overrides(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _finite(value, name):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite numeric')
    return value


RANKING_MODES=('expected','worst_case','minimax_regret')


def rank_outcomes(policy_ids, scenarios, outcomes, sign, ranking_mode='expected'):
    """Rank candidates from outcome rows {policy, scenario, probability, value, violations}.

    Shared by economy strategy search and the multi-actor game layer. Regret is
    measured against the best supplied candidate in each scenario, including
    constraint-violating candidates; infeasible candidates sort last.
    """
    if ranking_mode not in RANKING_MODES:
        raise ValueError('Unknown ranking mode')
    if sign not in (1,-1):
        raise ValueError('sign must be 1 (maximize) or -1 (minimize)')
    for row in outcomes:
        _finite(row['value'],'outcome value')
    best={scenario['id']:max(sign*row['value'] for row in outcomes if row['scenario']==scenario['id']) for scenario in scenarios}
    ranking=[]
    for policy in policy_ids:
        rows=[row for row in outcomes if row['policy']==policy]
        if {row['scenario'] for row in rows}!={scenario['id'] for scenario in scenarios}:
            raise ValueError('Every candidate requires one outcome per scenario')
        expected=sum(row['value']*row['probability'] for row in rows)
        worst=min(rows,key=lambda row:sign*row['value'])['value']
        regrets=[best[row['scenario']]-sign*row['value'] for row in rows]
        feasible=not any(row.get('violations') for row in rows)
        score={'expected':sign*expected,'worst_case':sign*worst,'minimax_regret':-max(regrets)}[ranking_mode]
        ranking.append({'policy':policy,'feasible':feasible,'expected_value':expected,'worst_case_value':worst,
                        'max_regret':max(regrets),'score':score})
    ranking.sort(key=lambda row:(not row['feasible'],-row['score'],row['policy']))
    return ranking


def evaluate_strategies(config):
    from .economy import simulate_economy
    canonical(config)
    policies, scenarios = config['policies'], config['scenarios']
    maximum = config.get('max_runs',100)
    if type(maximum) is not int or not 1 <= maximum <= 1000:
        raise ValueError('max_runs budget must be in 1..1000')
    if not policies or not scenarios or len(policies)*len(scenarios)>maximum:
        raise ValueError('Nonempty policies/scenarios must fit the run budget')
    for entries in (policies,scenarios):
        ids = [entry['id'] for entry in entries]
        if any(not isinstance(x,str) or not x for x in ids) or len(ids)!=len(set(ids)):
            raise ValueError('Policy and scenario IDs must be unique nonempty strings')
    for policy in policies:
        patch = policy.get('overrides', {})
        if set(patch) - {'businesses'}:
            raise ValueError('Policy controls are limited to business purchasing policies; environment is fixed by scenario')
        for business in patch.get('businesses', []):
            if set(business) - {'id', 'policy'} or set(business.get('policy', {})) - {'target_days', 'anticipation', 'expected_price_change'}:
                raise ValueError('Policy cannot change initial endowments, credit limits or the economic environment')
    probabilities = [_finite(row['probability'],'probability') for row in scenarios]
    if any(p<0 for p in probabilities) or not math.isclose(sum(probabilities),1,rel_tol=0,abs_tol=1e-9):
        raise ValueError('Scenario probabilities must be nonnegative and sum to one')
    objective = config['objective']
    direction, metric = objective['direction'], objective['metric']
    if direction not in ('maximize','minimize'):
        raise ValueError('Objective direction must be maximize or minimize')
    sign = 1 if direction=='maximize' else -1
    ranking_mode = config.get('ranking','expected')
    if ranking_mode not in RANKING_MODES:
        raise ValueError('Unknown ranking mode')
    constraints = config.get('constraints',[])
    operators = {'<=':lambda x,y:x<=y, '>=':lambda x,y:x>=y}
    for constraint in constraints:
        if constraint['op'] not in operators:
            raise ValueError('Constraint operator must be <= or >=')
        _finite(constraint['value'],'constraint')
    # Bound the full requested step workload, not just the number of runs.
    jobs=[]
    total_steps=0
    for policy in policies:
        for scenario in scenarios:
            parameters=merge_overrides(merge_overrides(config['base'],scenario.get('overrides',{})),policy.get('overrides',{}))
            days=parameters['days']
            if type(days) is not int or days<1:
                raise ValueError('Each simulation requires positive integer days')
            total_steps+=days*len(parameters['businesses'])
            jobs.append((policy,scenario,parameters))
    step_budget=config.get('max_steps',100000)
    if type(step_budget) is not int or not 1<=step_budget<=1000000 or total_steps>step_budget:
        raise ValueError('Strategy step budget exceeded')
    outcomes=[]
    for policy,scenario,parameters in jobs:
        result=simulate_economy(parameters)
        metrics=result['metrics']
        value=_finite(metrics[metric],metric)
        violations=[]
        for constraint in constraints:
            actual=_finite(metrics[constraint['metric']],constraint['metric'])
            if not operators[constraint['op']](actual,constraint['value']):
                violations.append({**constraint,'actual':actual})
        outcomes.append({'policy':policy['id'],'scenario':scenario['id'],'probability':scenario['probability'],
                         'value':value,'metrics':metrics,'violations':violations,
                         'parameters':parameters,'simulation':result})
    ranking=rank_outcomes([policy['id'] for policy in policies],scenarios,outcomes,sign,ranking_mode)
    result={'status':'scenario_comparison','causally_calibrated':False,'objective':objective,'ranking_mode':ranking_mode,
            'runs':len(jobs),'steps':total_steps,'work_unit':'firm_days','ranking':ranking,'outcomes':outcomes,
            'recommended_policy':next((row['policy'] for row in ranking if row['feasible']),None),
            'limitations':['Ranking is conditional on supplied model, candidate policies, probabilities and constraints.',
                           'Regret benchmark includes every supplied candidate, including constraint-violating candidates.',
                           'All scenarios including zero-probability stress cases must satisfy hard constraints.']}
    canonical(result)
    return result
