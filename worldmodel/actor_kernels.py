"""Explicit numerical actor heuristics; no fitted or causal behavior claims.

These isolated decision kernels do not settle counterparties' accounts. Use the
economy/banking kernels for closed-system financial conservation.
"""
from copy import deepcopy
import math


def _n(value, name, low=-1e100, high=1e100):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite in [{low}, {high}]')
    return value


def _bounded_json(value, depth=0, counter=None):
    counter = [0] if counter is None else counter
    counter[0] += 1
    if depth > 20 or counter[0] > 20000:
        raise ValueError('Actor state exceeds bounded JSON work budget')
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError('Actor state keys must be strings')
        for v in value.values():
            _bounded_json(v, depth + 1, counter)
    elif isinstance(value, list):
        for v in value:
            _bounded_json(v, depth + 1, counter)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _n(value, 'state value')
    elif value is not None and not isinstance(value, (str, bool)):
        raise ValueError('Actor state must be finite JSON')


def _items(values, name):
    if not isinstance(values, list) or not 1 <= len(values) <= 1000:
        raise ValueError(name + ' must contain 1..1000 items')
    ids = [v.get('id') for v in values]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError(name + ' IDs must be distinct nonempty strings')
    return values


def _human(state, mode, context):
    choices = _items(state['choices'], 'choices')
    wealth = _n(state.get('wealth', 0), 'wealth', 0)
    risk = _n(state.get('risk_aversion', 0), 'risk_aversion', 0, 10)
    loss = _n(state.get('loss_aversion', 2.25), 'loss_aversion', 0, 100)
    curvature = _n(state.get('curvature', .88), 'curvature', .01, 1)
    reference = _n(state.get('reference', 0), 'reference')
    scores = {}
    for choice in choices:
        cost = _n(choice.get('cost', 0), 'cost', 0)
        outcomes = choice['outcomes']
        if not isinstance(outcomes, list) or not 1 <= len(outcomes) <= 1000:
            raise ValueError('outcomes must contain 1..1000 items')
        score, probability = 0., 0.
        for outcome in outcomes:
            p = _n(outcome['probability'], 'probability', 0, 1)
            value = _n(outcome['value'], 'outcome') - cost
            probability += p
            if mode == 'prospect':
                delta = value - reference
                utility = delta ** curvature if delta >= 0 else -loss * (-delta) ** curvature
            else:
                total = wealth + value
                if risk and total <= 0:
                    raise ValueError('Risk-averse utility requires positive wealth in every outcome')
                utility = value if risk == 0 else math.log(total) if risk == 1 else (total ** (1 - risk) - 1) / (1 - risk)
            score += p * utility
        if not math.isclose(probability, 1, abs_tol=1e-9):
            raise ValueError('Outcome probabilities must sum to one')
        scores[choice['id']] = _n(score, 'utility')
    if mode == 'adaptive':
        learned = deepcopy(state.get('action_values', {}))
        if not isinstance(learned, dict) or set(learned) - set(scores):
            raise ValueError('action_values must refer to declared choices')
        learned = {key: _n(learned.get(key, score), 'learned action value') for key, score in scores.items()}
        alpha = _n(state.get('learning_rate', .1), 'learning_rate', 0, 1)
        feedback = state.get('feedback')
        if feedback is not None:
            if not isinstance(feedback, dict) or feedback.get('choice_id') not in scores:
                raise ValueError('Feedback must reference a declared choice')
            key = feedback['choice_id']
            learned[key] += alpha * (_n(feedback['reward'], 'reward') - learned[key])
            state['feedback'] = None
        state['action_values'] = learned
        scores = learned
    if mode == 'logit':
        temperature = _n(state.get('temperature', 1), 'temperature', 1e-9)
        maximum = max(scores.values())
        weights = {key: math.exp((value - maximum) / temperature) for key, value in scores.items()}
        total = sum(weights.values())
        probabilities = {key: weight / total for key, weight in weights.items()}
        draw, cumulative = context['rng'].random(), 0.
        decision = next(reversed(scores))
        for key, probability in probabilities.items():
            cumulative += probability
            if draw < cumulative:
                decision = key
                break
        state['choice_probabilities'] = probabilities
    else:
        decision = max(scores, key=scores.get)
    state.update(decision=decision, scores=scores)
    return state


def _business(state, mode):
    inputs = _items(state['inputs'], 'inputs')
    cash = _n(state['cash'], 'cash', 0)
    capacity = _n(state['capacity'], 'capacity', 0)
    demand = _n(state['base_demand'], 'base_demand', 0)
    base_price = _n(state['base_price'], 'base_price', 1e-9)
    labor = _n(state['labor_cost_per_unit'], 'labor_cost_per_unit', 0)
    elasticity = _n(state['elasticity'], 'elasticity', 0, 20)
    cost_per_unit = labor
    for item in inputs:
        quantity = _n(item['inventory'], 'inventory', 0)
        required = _n(item['required_per_unit'], 'required_per_unit', 1e-9)
        cost_per_unit += required * _n(item['unit_cost'], 'unit_cost', 0)
        capacity = min(capacity, quantity / required)
    if labor:
        capacity = min(capacity, cash / labor)
    if mode == 'cost_plus':
        markup = _n(state.get('markup', .2), 'markup', 0, 100)
        prices = [max(1e-9, cost_per_unit * (1 + markup))]
    else:
        prices = state['candidate_prices']
        if not isinstance(prices, list) or not 1 <= len(prices) <= 100:
            raise ValueError('candidate_prices must contain 1..100 prices')
    evaluations = []
    for candidate in prices:
        price = _n(candidate, 'candidate price', 1e-9)
        desired = _n(demand * (price / base_price) ** (-elasticity), 'derived demand', 0)
        output = min(capacity, desired) if price >= cost_per_unit else 0.
        evaluations.append({'price': price, 'demand': desired, 'production': output,
                            'profit': (price - cost_per_unit) * output})
    best = max(evaluations, key=lambda e: e['profit'])
    output = best['production']
    for item in inputs:
        item['inventory'] = max(0., item['inventory'] - item['required_per_unit'] * output)
    state.update(price=best['price'], production=output, sales=output, revenue=output * best['price'],
                 operating_profit=best['profit'], cash=cash + output * (best['price'] - labor),
                 unmet_demand=max(0., best['demand'] - output), price_evaluations=evaluations,
                 unit_cost=cost_per_unit)
    return state


def _government(state, mode):
    if mode == 'monetary':
        inflation = _n(state['inflation'], 'inflation', -1, 10)
        target = _n(state['inflation_target'], 'inflation_target', -1, 10)
        gap = _n(state['output_gap'], 'output_gap', -1, 10)
        neutral = _n(state['neutral_rate'], 'neutral_rate', -1, 10)
        inflation_weight = _n(state.get('inflation_weight', .5), 'inflation_weight', 0, 100)
        output_weight = _n(state.get('output_weight', .5), 'output_weight', 0, 100)
        smoothing = _n(state.get('smoothing', .5), 'smoothing', 0, 1)
        previous = _n(state['policy_rate'], 'policy_rate', -1, 10)
        lower = _n(state.get('rate_floor', 0), 'rate_floor', -1, 10)
        upper = _n(state.get('rate_ceiling', 1), 'rate_ceiling', lower, 10)
        desired = neutral + inflation + inflation_weight * (inflation - target) + output_weight * gap
        state.update(desired_policy_rate=desired,
                     policy_rate=max(lower, min(upper, smoothing * previous + (1 - smoothing) * desired)))
    else:
        budget = _n(state['budget'], 'budget', 0)
        programs = _items(state['programs'], 'programs')
        for program in programs:
            _n(program['requested'], 'requested', 0)
            _n(program['benefit_per_dollar'], 'benefit_per_dollar', 0)
        remaining = budget
        allocations = {}
        for program in sorted(programs, key=lambda p: -p['benefit_per_dollar']):
            allocated = min(program['requested'], remaining)
            allocations[program['id']] = allocated
            remaining = max(0., remaining - allocated)
        state.update(allocations=allocations, unallocated_budget=remaining,
                     estimated_benefit=sum(allocations[p['id']] * p['benefit_per_dollar'] for p in programs))
    return state


def _handler(actor, mode):
    port = actor + '_state'
    def predict(inputs, parameters, context):
        if context['dt_seconds'] != 86400:
            raise ValueError('Actor kernels require exactly one day')
        state = deepcopy(inputs[port]['value'])
        _bounded_json(state)
        status = state.get('status')
        if status not in {'alive', 'active', 'dead', 'dissolved'}:
            raise ValueError('Explicit status must be alive, active, dead or dissolved')
        inactive = status in {'dead', 'dissolved'}
        events = []
        diagnostics = {'illustrative': True, 'validated': False, 'inactive': inactive,
            'assumptions': 'Declared numerical heuristic; no fitted causal response. Isolated actor, no counterparty accounting.'}
        if not inactive:
            try:
                if mode == 'agent':
                    request = {'process_id': actor + '_behavior', 'inputs': inputs, 'parameters': parameters,
                               'entity_id': context.get('entity_id'), 'state': state,
                               'dt_seconds': 86400, 'time': context.get('time'), 'memory': context.get('memory', {}),
                               'outputs': {port: {'type': 'object', 'unit': None}}}
                    response = context['agent_backend'].predict(deepcopy(request))
                    _bounded_json(response)
                    pressures = response.get('pressures', [])
                    if len(pressures) != 1 or pressures[0].get('port') != port or pressures[0].get('mode') != 'set':
                        raise ValueError('Actor backend must return one object-state set pressure')
                    state = pressures[0]['value']
                    if not isinstance(state, dict) or state.get('status') != status:
                        raise ValueError('Actor backend cannot change lifecycle status')
                    diagnostics['agent_audit'] = {'request': request, 'response': response}
                elif actor == 'human' and mode in {'aging', 'mortality'}:
                    age = _n(state['age_years'], 'age_years', 0)
                    state['age_years'] = age + 1 / 365.25
                    maximum = _n(state['max_age_years'], 'max_age_years', 0) if mode == 'aging' else None
                    probability = 0.
                    if mode == 'mortality':
                        hazard = _n(state['mortality_hazard_per_year'], 'mortality_hazard_per_year', 0)
                        probability = -math.expm1(-hazard / 365.25)
                    dies = state['age_years'] >= maximum if mode == 'aging' else context['rng'].random() < probability
                    diagnostics.update(year_days=365.25, death_probability=probability if mode == 'mortality' else int(dies))
                    if dies:
                        state['status'] = 'dead'
                        event = {'event_type': 'death', 'entity_id': context.get('entity_id'),
                                 'epistemic_status': 'synthetic_scenario', 'offset_seconds': 86400,
                                 'cause_model': mode, 'synthetic': True}
                        if context.get('time') is not None:
                            from datetime import timedelta
                            from .model import instant
                            event['occurred_at'] = (instant(context['time']) + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
                        events.append(event)
                elif actor == 'human':
                    state = _human(state, mode, context)
                elif actor == 'business':
                    state = _business(state, mode)
                else:
                    state = _government(state, mode)
            except (OverflowError, ZeroDivisionError) as exc:
                raise ValueError('Actor computation exceeds finite numeric range') from exc
        _bounded_json(state)
        return {'pressures': [{'port': port, 'mode': 'set', 'value': state, 'unit': None,
                              'strength': 1, 'confidence': 1}], 'events': events, 'memory': {}, 'diagnostics': diagnostics}
    return predict


def register_actor_kernels(registry):
    variants = {'human': [('utility', 'deterministic'), ('prospect', 'deterministic'), ('logit', 'stochastic'),
                          ('adaptive', 'deterministic'), ('aging', 'deterministic'), ('mortality', 'stochastic')],
                'business': [('cost_plus', 'deterministic'), ('demand_pricing', 'deterministic')],
                'government': [('monetary', 'deterministic'), ('fiscal', 'deterministic')]}
    for actor, implementations in variants.items():
        port = {actor + '_state': {'type': 'object', 'unit': None}}
        registry.register_process({'id': actor + '_behavior', 'inputs': port, 'outputs': port,
            'topology': 'Explicit actor state; numerical decisions and resource constraints; inactive statuses preserved.',
            'description': f'Illustrative {actor} numerical behavior; select implementation explicitly.', 'validated': False})
        for mode, fidelity in implementations + [('agent', 'agent')]:
            registry.register_implementation({'id': actor + '_behavior.' + mode, 'process_id': actor + '_behavior',
                'fidelity': fidelity, 'min_step_seconds': 86400, 'max_step_seconds': 86400,
                'cost_per_call': 10 if mode == 'agent' else 1, 'output_timing': 'end_of_step',
                'requires_backend': mode == 'agent',
                'description': f'{mode} heuristic; bounded 20000 JSON nodes, 1000 choices/items, 100 prices. Uncalibrated.'},
                _handler(actor, mode))
    return registry


def schema():
    return {'entity_types': {}, 'relations': {}, 'variables': {
        actor + '_state': {'type': 'object', 'unit': None, 'domain': domain}
        for actor, domain in [('human', 'person'), ('business', 'business'), ('government', 'government_agency')]}}


def example_actors():
    return {'human_state': {'status': 'alive', 'wealth': 100, 'risk_aversion': 0, 'temperature': 5,
        'choices': [{'id': 'work', 'cost': 2, 'outcomes': [{'value': 10, 'probability': 1}]},
                    {'id': 'venture', 'outcomes': [{'value': 30, 'probability': .5}, {'value': -10, 'probability': .5}]}]},
        'business_state': {'status': 'active', 'cash': 1000, 'capacity': 100, 'base_demand': 80,
            'base_price': 10, 'elasticity': 1.5, 'labor_cost_per_unit': 2, 'markup': .2,
            'inputs': [{'id': 'energy', 'inventory': 100, 'required_per_unit': 2, 'unit_cost': 2}],
            'candidate_prices': [7, 9, 11, 15]},
        'government_state': {'status': 'active', 'inflation': .06, 'inflation_target': .02,
            'output_gap': .02, 'neutral_rate': .01, 'policy_rate': .04, 'smoothing': 0,
            'budget': 100, 'programs': [{'id': 'infrastructure', 'requested': 80, 'benefit_per_dollar': 2},
                                       {'id': 'education', 'requested': 80, 'benefit_per_dollar': 3}]}}
