"""Strategic multi-actor layer: goals, budgets and private observations over model environments.

A game declares actors, finite actions (config patches with resource costs),
scenarios with prior probabilities and private signals, and metric selectors on a
model family's simulation report. Private information is handled in agent-normal
form: each (actor, observed signal) pair is a player choosing an action, scored by
the actor's expected utility over scenarios consistent with that signal.

Solvers: pure-strategy Nash enumeration, best-response dynamics, fictitious play
(empirical mixed strategies with NashConv), and support enumeration for two-actor
complete-information games. ``compare_strategies`` evaluates a focal actor's
candidates against others' best responses and ranks them with
``worldmodel.strategy.rank_outcomes`` (expected, worst-case or minimax regret).

Equilibria are conditional on the declared model, actions, payoffs and priors;
nothing here is causally calibrated.
"""
from copy import deepcopy
import itertools
import math
from ..strategy import merge_overrides, rank_outcomes
from ..perception import split_observations
from .base import dig, finite, integer, solve

TOL = 1e-9


def _patch(config, overrides, appends, touched, actor):
    for path, value in _leaves(overrides):
        owner = touched.get(path)
        if owner is not None and owner != actor:
            raise ValueError(f'Actors {owner} and {actor} both set {".".join(path)}; give actors disjoint levers or use append')
        touched[path] = actor
    config = merge_overrides(config, overrides) if overrides else config
    for dotted, items in (appends or {}).items():
        if not isinstance(items, list):
            raise ValueError('Append values must be lists')
        parts = dotted.split('.')
        node = config
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault(parts[-1], [])
        if not isinstance(node[parts[-1]], list):
            raise ValueError(f'Append target {dotted} is not a list')
        node[parts[-1]] = node[parts[-1]] + deepcopy(items)
    return config


def _leaves(value, prefix=()):
    if isinstance(value, dict) and (value or not prefix):
        for key, item in value.items():
            yield from _leaves(item, prefix + (key,))
    else:
        yield prefix, value


class Game:
    def __init__(self, spec, evaluate=None):
        self.spec = deepcopy(spec)
        actors = spec['actors']
        if not isinstance(actors, list) or not 1 <= len(actors) <= 8:
            raise ValueError('Games require 1..8 actors')
        self.actor_ids = [a['id'] for a in actors]
        if len(set(self.actor_ids)) != len(self.actor_ids):
            raise ValueError('Actor IDs must be unique')
        self.actors = {a['id']: a for a in actors}
        self.infeasible = {}
        self.actions = {}
        for actor in actors:
            ids = [x['id'] for x in actor['actions']]
            if not ids or len(set(ids)) != len(ids):
                raise ValueError(f'Actor {actor["id"]} needs unique nonempty action IDs')
            budget = actor.get('budget', {})
            feasible = []
            for action in actor['actions']:
                over = {r: c for r, c in action.get('cost', {}).items() if finite(c, 'action cost', 0) > finite(budget.get(r, 0), 'budget', 0)}
                if over:
                    self.infeasible.setdefault(actor['id'], []).append({'action': action['id'], 'exceeds_budget': over})
                else:
                    feasible.append(action)
            if not feasible:
                raise ValueError(f'Actor {actor["id"]} has no action within budget')
            self.actions[actor['id']] = feasible
            for goal in actor['goals']:
                if goal.get('direction', 'maximize') not in ('maximize', 'minimize'):
                    raise ValueError('Goal direction must be maximize or minimize')
                finite(goal.get('weight', 1), 'goal weight')
        scenarios = spec.get('scenarios') or [{'id': 'baseline', 'probability': 1}]
        probabilities = [finite(s['probability'], 'scenario probability', 0, 1) for s in scenarios]
        if not math.isclose(sum(probabilities), 1, abs_tol=1e-9) or len({s['id'] for s in scenarios}) != len(scenarios):
            raise ValueError('Scenario IDs must be unique and probabilities sum to one')
        self.scenarios = scenarios
        self.metrics = spec.get('metrics', {})
        profiles = math.prod(len(v) for v in self.actions.values())
        limit = integer(spec.get('max_evaluations', 2000), 'max_evaluations', 1, 100000)
        if profiles * len(scenarios) > limit:
            raise ValueError(f'Game requires {profiles * len(scenarios)} evaluations, above max_evaluations={limit}')
        self.evaluate = evaluate or self._model_evaluate
        self._cache = {}
        self.players = []
        for actor_id in self.actor_ids:
            for key in sorted({self.observation(actor_id, s) for s in scenarios}):
                self.players.append((actor_id, key))

    # --------------------------------------------------------------- environment
    def observation(self, actor_id, scenario):
        keys = self.actors[actor_id].get('observes', [])
        signals = scenario.get('signals', {})
        view = split_observations(signals, {actor_id: list(keys)})[actor_id] if keys else {}
        return tuple((k, json_key(view[k])) for k in keys)

    def _model_evaluate(self, profile, scenario):
        from . import simulate
        environment = self.spec['environment']
        config = merge_overrides(environment['base'], scenario.get('overrides', {}))
        touched = {}
        for actor_id in self.actor_ids:
            action = next(a for a in self.actions[actor_id] if a['id'] == profile[actor_id])
            config = _patch(config, action.get('overrides', {}), action.get('append', {}), touched, actor_id)
        report = simulate(environment['family'], config, environment.get('mode', 'deterministic'), environment.get('seed', 0))
        return {name: finite(dig(report, selector['path']), name) for name, selector in self.metrics.items()}

    def outcome(self, profile, scenario):
        key = (tuple(profile[a] for a in self.actor_ids), scenario['id'])
        if key not in self._cache:
            metrics = self.evaluate(dict(profile), scenario)
            if not isinstance(metrics, dict):
                raise ValueError('Evaluator must return a metrics dictionary')
            utilities = {}
            for actor_id in self.actor_ids:
                total = 0.0
                for goal in self.actors[actor_id]['goals']:
                    sign = 1 if goal.get('direction', 'maximize') == 'maximize' else -1
                    total += sign * goal.get('weight', 1) * finite(metrics[goal['metric']], goal['metric'])
                utilities[actor_id] = total
            self._cache[key] = {'metrics': metrics, 'utilities': utilities}
        return self._cache[key]

    # --------------------------------------------------------------- utilities
    def _joint(self, strategy, scenario, override=None):
        """Yield (profile, probability) for agent-normal-form strategy in one scenario."""
        choices = []
        for actor_id in self.actor_ids:
            player = (actor_id, self.observation(actor_id, scenario))
            if override and override[0] == player:
                choices.append([(override[1], 1.0)])
            else:
                mix = strategy[player]
                choices.append([(a, p) for a, p in mix.items() if p > 0])
        for combo in itertools.product(*choices):
            yield {a: c[0] for a, c in zip(self.actor_ids, combo)}, math.prod(c[1] for c in combo)

    def player_value(self, player, strategy, action=None):
        actor_id, key = player
        total, mass = 0.0, 0.0
        for scenario in self.scenarios:
            if self.observation(actor_id, scenario) != key:
                continue
            weight = scenario['probability']
            mass += weight
            for profile, probability in self._joint(strategy, scenario, (player, action) if action is not None else None):
                total += weight * probability * self.outcome(profile, scenario)['utilities'][actor_id]
        return total / mass if mass > 0 else 0.0

    def best_response(self, player, strategy):
        values = {a['id']: self.player_value(player, strategy, a['id']) for a in self.actions[player[0]]}
        best = max(values.values())
        return next(a for a, v in values.items() if v >= best - TOL), values

    def nash_conv(self, strategy):
        gaps = {}
        for player in self.players:
            _, values = self.best_response(player, strategy)
            gaps[_player_label(player)] = max(values.values()) - self.player_value(player, strategy)
        return sum(gaps.values()), gaps

    def expected_utilities(self, strategy):
        result = {actor_id: 0.0 for actor_id in self.actor_ids}
        for scenario in self.scenarios:
            for profile, probability in self._joint(strategy, scenario):
                utilities = self.outcome(profile, scenario)['utilities']
                for actor_id in self.actor_ids:
                    result[actor_id] += scenario['probability'] * probability * utilities[actor_id]
        return result

    def pure(self, assignment):
        return {player: {assignment[player]: 1.0} for player in self.players}


def json_key(value):
    import json
    return json.dumps(value, sort_keys=True)


def _player_label(player):
    actor_id, key = player
    return actor_id if not key else actor_id + '|' + ','.join(f'{k}={v}' for k, v in key)


def _strategy_json(game, strategy):
    return {_player_label(p): {a: v for a, v in strategy[p].items() if v > 1e-12} for p in game.players}


def pure_nash(game, limit=100000):
    options = [[a['id'] for a in game.actions[p[0]]] for p in game.players]
    if math.prod(len(o) for o in options) > limit:
        raise ValueError('Pure-strategy enumeration exceeds limit; use fictitious_play')
    equilibria = []
    for combo in itertools.product(*options):
        strategy = game.pure(dict(zip(game.players, combo)))
        if all(max(game.best_response(p, strategy)[1].values()) <= game.player_value(p, strategy) + TOL for p in game.players):
            equilibria.append({'strategy': _strategy_json(game, strategy), 'expected_utilities': game.expected_utilities(strategy)})
    return equilibria


def best_response_dynamics(game, max_rounds=100):
    assignment = {p: game.actions[p[0]][0]['id'] for p in game.players}
    seen = []
    for round_index in range(1, max_rounds + 1):
        changed = False
        for player in game.players:
            best, values = game.best_response(player, game.pure(assignment))
            if values[best] > values[assignment[player]] + TOL:
                assignment[player] = best
                changed = True
        snapshot = tuple(assignment[p] for p in game.players)
        if not changed:
            strategy = game.pure(assignment)
            return {'converged': True, 'rounds': round_index, 'strategy': _strategy_json(game, strategy),
                    'expected_utilities': game.expected_utilities(strategy)}
        if snapshot in seen:
            return {'converged': False, 'rounds': round_index, 'cycle_detected': True, 'strategy': _strategy_json(game, game.pure(assignment))}
        seen.append(snapshot)
    return {'converged': False, 'rounds': max_rounds, 'strategy': _strategy_json(game, game.pure(assignment))}


def fictitious_play(game, iterations=500, tolerance=1e-4):
    counts = {p: {a['id']: 1.0 for a in game.actions[p[0]]} for p in game.players}
    history = []
    iteration = 0
    for iteration in range(1, iterations + 1):
        strategy = {p: {a: c / sum(counts[p].values()) for a, c in counts[p].items()} for p in game.players}
        responses = {p: game.best_response(p, strategy)[0] for p in game.players}
        for p, action in responses.items():
            counts[p][action] += 1.0
        if iteration % 10 == 0 or iteration == iterations:
            conv, _ = game.nash_conv(strategy)
            history.append({'iteration': iteration, 'nash_conv': conv})
            if conv <= tolerance:
                break
    strategy = {p: {a: c / sum(counts[p].values()) for a, c in counts[p].items()} for p in game.players}
    conv, gaps = game.nash_conv(strategy)
    return {'iterations': iteration, 'strategy': _strategy_json(game, strategy), 'nash_conv': conv, 'player_gaps': gaps,
            'expected_utilities': game.expected_utilities(strategy), 'history': history,
            'note': 'Empirical frequencies; convergence to Nash is guaranteed only for some game classes (e.g. zero-sum, potential).'}


def support_enumeration(game):
    if len(game.actor_ids) != 2 or any(game.actors[a].get('observes') for a in game.actor_ids):
        raise ValueError('Support enumeration supports two-actor complete-information games')
    row, col = game.actor_ids
    A = [a['id'] for a in game.actions[row]]
    B = [b['id'] for b in game.actions[col]]

    def payoff(actor, a, b):
        return sum(s['probability'] * game.outcome({row: a, col: b}, s)['utilities'][actor] for s in game.scenarios)

    equilibria = []
    for size in range(1, min(len(A), len(B)) + 1):
        for sa in itertools.combinations(range(len(A)), size):
            for sb in itertools.combinations(range(len(B)), size):
                # Column mix makes row indifferent over sa; row mix makes column indifferent over sb.
                q = _indifference([[payoff(row, A[i], B[j]) for j in sb] for i in sa])
                p = _indifference([[payoff(col, A[i], B[j]) for i in sa] for j in sb])
                if q is None or p is None:
                    continue
                x = {A[i]: p[k] for k, i in enumerate(sa)}
                y = {B[j]: q[k] for k, j in enumerate(sb)}
                strategy = {(row, ()): {a: x.get(a, 0.0) for a in A}, (col, ()): {b: y.get(b, 0.0) for b in B}}
                conv, _ = game.nash_conv(strategy)
                if conv <= 1e-7 and not any(_same(strategy, e['_raw']) for e in equilibria):
                    equilibria.append({'_raw': strategy, 'strategy': _strategy_json(game, strategy), 'expected_utilities': game.expected_utilities(strategy)})
    return [{k: v for k, v in e.items() if k != '_raw'} for e in equilibria]


def _indifference(matrix):
    """Probabilities over columns making every row's payoff equal; None if infeasible."""
    n = len(matrix)
    if n == 1:
        return [1.0]
    system = [[matrix[i][j] - matrix[0][j] for j in range(n)] for i in range(1, n)] + [[1.0] * n]
    try:
        weights = solve(system, [0.0] * (n - 1) + [1.0])
    except ValueError:
        return None
    if any(w < -1e-10 for w in weights):
        return None
    return [max(0.0, w) for w in weights]


def _same(left, right):
    return all(abs(left[p].get(a, 0) - right[p].get(a, 0)) < 1e-7 for p in left for a in left[p])


def solve_game(spec, evaluate=None):
    game = Game(spec, evaluate)
    solver = spec.get('solver', {'method': 'pure_nash'})
    method = solver.get('method', 'pure_nash')
    if method == 'pure_nash':
        result = {'equilibria': pure_nash(game)}
    elif method == 'best_response_dynamics':
        result = best_response_dynamics(game, solver.get('max_rounds', 100))
    elif method == 'fictitious_play':
        result = fictitious_play(game, solver.get('iterations', 500), solver.get('tolerance', 1e-4))
    elif method == 'support_enumeration':
        result = {'equilibria': support_enumeration(game)}
    else:
        raise ValueError('solver method must be pure_nash, best_response_dynamics, fictitious_play or support_enumeration')
    table = [{'profile': dict(zip(game.actor_ids, key[0])), 'scenario': key[1], **value} for key, value in sorted(game._cache.items())]
    return {'status': 'game_solution', 'method': method, **result, 'players': [_player_label(p) for p in game.players],
            'infeasible_actions': game.infeasible, 'evaluations': len(game._cache), 'payoff_table': table, 'causally_calibrated': False,
            'limitations': ['Equilibria are conditional on declared actions, priors, payoffs and an unvalidated model.',
                            'Private information is modeled only through declared scenario signals (agent-normal form).',
                            'Budgets are hard per-action feasibility constraints, not intertemporal allocation.']}


def compare_strategies(spec, focal_actor, *, response='best_response', ranking='expected', constraints=(), evaluate=None):
    """Rank a focal actor's actions when other actors best-respond (or hold their first action)."""
    game = Game(spec, evaluate)
    if focal_actor not in game.actors:
        raise ValueError('Unknown focal actor')
    if response not in ('best_response', 'fixed'):
        raise ValueError('response must be best_response or fixed')
    operators = {'<=': lambda x, y: x <= y, '>=': lambda x, y: x >= y}
    outcomes, responses = [], {}
    for action in game.actions[focal_actor]:
        assignment = {p: (action['id'] if p[0] == focal_actor else game.actions[p[0]][0]['id']) for p in game.players}
        if response == 'best_response':
            for _ in range(100):
                changed = False
                for player in game.players:
                    if player[0] == focal_actor:
                        continue
                    best, values = game.best_response(player, game.pure(assignment))
                    if values[best] > values[assignment[player]] + TOL:
                        assignment[player] = best
                        changed = True
                if not changed:
                    break
            else:
                raise ValueError('Responders did not reach a pure best-response profile')
        responses[action['id']] = _strategy_json(game, game.pure(assignment))
        for scenario in game.scenarios:
            profile = {a: assignment[(a, game.observation(a, scenario))] for a in game.actor_ids}
            result = game.outcome(profile, scenario)
            violations = []
            for constraint in constraints:
                actual = result['metrics'][constraint['metric']]
                if not operators[constraint['op']](actual, constraint['value']):
                    violations.append({**constraint, 'actual': actual})
            outcomes.append({'policy': action['id'], 'scenario': scenario['id'], 'probability': scenario['probability'],
                             'value': result['utilities'][focal_actor], 'metrics': result['metrics'], 'profile': profile, 'violations': violations})
    ranked = rank_outcomes([a['id'] for a in game.actions[focal_actor]], game.scenarios, outcomes, 1, ranking)
    return {'status': 'scenario_comparison', 'focal_actor': focal_actor, 'response': response, 'ranking_mode': ranking, 'ranking': ranked,
            'recommended_policy': next((r['policy'] for r in ranked if r['feasible']), None), 'responses': responses, 'outcomes': outcomes,
            'evaluations': len(game._cache), 'causally_calibrated': False,
            'limitations': ['Value is the focal actor\'s declared utility; responders best-respond in pure strategies.',
                            'Ranking reuses worldmodel.strategy.rank_outcomes and is conditional on supplied scenarios.']}


class MultiActorEnvironment:
    """Split one environment's actions/observations among actors with private views, budgets and goals.

    Each actor controls a disjoint subset of action names, observes an allowlisted
    subset of observations, pays declared per-unit costs against a finite budget
    and receives a reward from its own goal terms (delta of observed values).
    Privileged observations are never returned to actors.
    """
    def __init__(self, env, actors):
        spec = env.spec
        self.env = env
        self.actors = {a['id']: deepcopy(a) for a in actors}
        controlled = [name for a in actors for name in a['controls']]
        if len(controlled) != len(set(controlled)) or set(controlled) != set(spec['actions']):
            raise ValueError('Actors must partition the environment actions exactly')
        for actor in actors:
            unknown = set(actor.get('observes', [])) - set(spec['observations'])
            if unknown:
                raise ValueError(f'Actor {actor["id"]} observes undeclared outputs {sorted(unknown)}')
            for goal in actor.get('goals', []):
                if goal['observation'] not in spec['observations']:
                    raise ValueError('Goal observations must be declared outputs')
        self.remaining = {a['id']: finite(a.get('budget', {}).get('amount', math.inf) if a.get('budget') else math.inf, 'budget', 0)
                          if a.get('budget') else math.inf for a in actors}
        self._full = None

    def _views(self, observation):
        return split_observations(observation, {a: list(spec.get('observes', [])) for a, spec in self.actors.items()})

    def reset(self, seed=0):
        observation, info = self.env.reset(seed=seed)
        self._full = observation
        self.remaining = {a: (spec['budget']['amount'] if spec.get('budget') else math.inf) for a, spec in self.actors.items()}
        return self._views(observation), {a: {'step': info.get('step', 0), 'budget_remaining': self.remaining[a]} for a in self.actors}

    def step(self, joint):
        if self._full is None:
            raise ValueError('Reset required before stepping')
        if set(joint) != set(self.actors):
            raise ValueError('Joint action must include every actor')
        actions, costs = {}, {}
        for actor_id, spec in self.actors.items():
            if set(joint[actor_id]) != set(spec['controls']):
                raise ValueError(f'Actor {actor_id} must set exactly its controlled actions')
            cost = 0.0
            for name, value in joint[actor_id].items():
                rate = spec.get('budget', {}).get('costs', {}).get(name, 0.0)
                if rate:
                    cost += finite(rate, 'cost rate', 0) * abs(finite(value, name))
            if cost > self.remaining[actor_id] + 1e-12:
                raise ValueError(f'Actor {actor_id} exceeds remaining budget')
            costs[actor_id] = cost
            actions.update(joint[actor_id])
        before = self._full
        observation, _, terminated, truncated, info = self.env.step(actions)
        rewards = {}
        for actor_id, spec in self.actors.items():
            self.remaining[actor_id] -= costs[actor_id]
            total = 0.0
            for goal in spec.get('goals', []):
                sign = 1 if goal.get('direction', 'maximize') == 'maximize' else -1
                total += sign * goal.get('weight', 1) * (finite(observation[goal['observation']], 'goal observation') - finite(before[goal['observation']], 'goal observation'))
            rewards[actor_id] = total
        self._full = observation
        infos = {a: {'step': info.get('step'), 'budget_remaining': self.remaining[a], 'cost': costs[a]} for a in self.actors}
        return self._views(observation), rewards, terminated, truncated, infos
