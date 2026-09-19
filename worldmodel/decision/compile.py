"""Compile a decision contract into an ordinary ``worldmodel.environments.Environment``.

:func:`compile_contract` validates the contract, resolves every mechanism's evidence
against the calibration reports, binds the kernel, and returns a
:class:`CompiledDecision`. Its :meth:`~CompiledDecision.environment` is the existing
materialization environment (``Environment(evaluate, spec)``): actions are routed
inputs bound to the decision-maker, observations and reward terms select that
entity's materialized outputs, and ``evaluate(history, seed)`` is a
:class:`DecisionEvaluator` that replays the kernel deterministically.

Every rollout (:meth:`~CompiledDecision.rollout`) carries the evidence status of
each mechanism it actually used, and an automatic label: anything resting on a
mechanism that is not ``validated`` says so.
"""
from copy import deepcopy
import math
import random

from .contract import action_cost, aggregate, criterion_result, validate_contract
from .evidence import DOES_NOT_ESTABLISH, recommendation_label, resolve_mechanisms
from .kernels import kernel_for

ROLLOUT_SCHEMA = 'worldmodel.decision_rollout/1'


class DecisionEvaluator:
    """``evaluate(history, seed)`` for :class:`worldmodel.environments.Environment`.

    With a fixed ``scenario`` the seed is ignored (the scenario fixes every shock).
    With ``scenario=None`` the scenario is drawn from the bound kernel's training
    source with ``random.Random(seed)``, so a seed names one resampled episode.
    Replay is deterministic; the last replayed prefix is cached, so appending one
    action costs one transition. Only the latest step's snapshots are returned (the
    environment reads the newest row per variable); :attr:`trajectory` keeps all.
    """
    bounded_output = True

    def __init__(self, compiled, scenario=None, split=None):
        self.compiled = compiled
        self.fixed = deepcopy(scenario)
        self.split = split
        self._key = None
        self.trajectory = []

    def _scenario(self, seed):
        if self.fixed is not None:
            return self.fixed
        return self.compiled.bound.training_scenario(random.Random(seed), self.split)

    def _snapshots(self, step, observation, metrics):
        entity = self.compiled.entity
        rows = [{'time': step, 'entity': entity, 'variable': name, 'value': float(value), 'unit': self.compiled.kernel.observations[name]}
                for name, value in sorted(observation.items())]
        rows += [{'time': step, 'entity': entity, 'variable': f'metric.{name}', 'value': float(value),
                  'unit': self.compiled.metric_units[name]} for name, value in sorted(metrics.items())]
        return rows

    def __call__(self, history, seed):
        bound = self.compiled.bound
        actions = [{item['port']: item['value'] for item in step['inputs']} for step in history]
        key = (seed, len(actions))
        if self._key is not None and self._key[0] == seed and self._actions == actions[:-1] and len(actions) == self._key[1] + 1:
            state, scenario = self._state, self._scenario_cache
            steps = [len(actions) - 1]
        else:
            scenario = self._scenario(seed)
            state = bound.initial(scenario)
            self.trajectory = [{'step': 0, 'observation': bound.observe(state), 'metrics': self.compiled.zero_metrics(),
                                'actions': None, 'mechanisms_used': []}]
            steps = range(len(actions))
        for t in steps:
            if t >= self.compiled.horizon:
                raise ValueError('History exceeds the contract horizon')
            state, metrics, used = bound.transition(state, actions[t], scenario, t)
            metrics = dict(metrics, action_cost=action_cost(self.compiled.contract, actions[t]))
            self.trajectory.append({'step': t + 1, 'observation': bound.observe(state), 'metrics': metrics,
                                    'actions': dict(actions[t]), 'mechanisms_used': list(used)})
        self._key, self._actions, self._state, self._scenario_cache = key, actions, state, scenario
        last = self.trajectory[-1]
        return {'snapshots': self._snapshots(last['step'], last['observation'], last['metrics']),
                'execution': {'kernel': self.compiled.kernel.id, 'steps': last['step'],
                              'scenario': scenario.get('point')}}


class CompiledDecision:
    def __init__(self, contract, kernel, bound, index):
        self.contract = contract
        self.kernel = kernel
        self.bound = bound
        self.index = index
        self.resolutions = bound.resolutions
        self.entity = contract['decision_maker']['id']
        self.horizon = contract['horizon']['steps']
        self.metric_units = dict(kernel.metrics, action_cost=contract.get('budget', {}).get('unit', 'cost_units'))

    def zero_metrics(self):
        return {name: 0.0 for name in self.metric_units}

    def environment_spec(self):
        actions = {name: {'binding': self.entity, 'port': name, 'type': 'number', 'unit': spec['unit'],
                          'minimum': spec['minimum'], 'maximum': spec['maximum']} for name, spec in self.contract['actions'].items()}
        observations = {name: {'entity': self.entity, 'variable': name, 'unit': unit} for name, unit in self.kernel.observations.items()}
        terms, approximations = [], []
        for objective in self.contract['objectives']:
            if objective['aggregate'] not in ('sum', 'mean'):
                approximations.append(f'objective {objective["id"]} aggregates by {objective["aggregate"]}, which a per-step '
                                      'reward cannot express; it is scored on the finished rollout only')
                continue
            sign = -1.0 if objective['direction'] == 'minimize' else 1.0
            divisor = self.horizon if objective['aggregate'] == 'mean' else 1
            terms.append({'selector': {'entity': self.entity, 'variable': f'metric.{objective["metric"]}',
                                       'unit': self.metric_units[objective['metric']]},
                          'mode': 'value', 'weight': sign * objective['weight'], 'scale': objective['scale'] * divisor})
        if not terms:
            raise ValueError('At least one objective must aggregate by sum or mean to define a per-step reward')
        return {'actions': actions, 'observations': observations, 'reward': {'terms': terms}, 'max_steps': self.horizon,
                'reward_approximations': approximations}

    def environment(self, scenario=None, split=None):
        """An :class:`~worldmodel.environments.Environment` over this contract.

        ``scenario`` is a kernel scenario (see :meth:`scenario`); ``None`` draws one
        from the training source per reset seed; ``split`` (``'train'`` or ``'evaluate'``)
        selects that side of a declared history split.
        """
        from ..environments import Environment
        spec = self.environment_spec()
        spec.pop('reward_approximations')
        evaluator = DecisionEvaluator(self, scenario, split)
        environment = Environment(evaluator, spec)
        environment.decision_evaluator = evaluator
        return environment

    def scenario(self, point=None):
        return self.bound.scenario(point or {})

    def mechanisms_used(self, trajectory):
        counts = {}
        for step in trajectory:
            for name in step['mechanisms_used']:
                counts[name] = counts.get(name, 0) + 1
        return {name: dict(self.resolutions[name], steps_used=count) for name, count in sorted(counts.items())}

    def score(self, trajectory):
        """Objectives, constraints, budget and success criteria of a finished trajectory."""
        steps = trajectory[1:]
        series = {name: [s['metrics'][name] for s in steps] for name in self.metric_units}
        objectives, total = [], 0.0
        for objective in self.contract['objectives']:
            value = aggregate(series[objective['metric']], objective['aggregate'])
            sign = -1.0 if objective['direction'] == 'minimize' else 1.0
            contribution = sign * objective['weight'] * value / objective['scale']
            total += contribution
            objectives.append({'id': objective['id'], 'metric': objective['metric'], 'aggregate': objective['aggregate'],
                               'direction': objective['direction'], 'value': value, 'weight': objective['weight'],
                               'contribution': contribution})
        constraints = [criterion_result(c, aggregate(series[c['metric']], c['aggregate'])) for c in self.contract['constraints']]
        if 'budget' in self.contract:
            limit = self.contract['budget']['limit']
            constraints.append(criterion_result({'id': 'budget', 'metric': 'action_cost', 'aggregate': 'sum', 'operator': 'lte',
                                                 'value': limit, 'scale': max(limit, 1.0)}, math.fsum(series['action_cost'])))
        criteria = [criterion_result(c, aggregate(series[c['metric']], c['aggregate'])) for c in self.contract['success_criteria']]
        checks = constraints + criteria
        return {'objectives': objectives, 'weighted_score': total, 'constraints': constraints, 'success_criteria': criteria,
                'feasible': all(c['passed'] for c in constraints), 'success': all(c['passed'] for c in checks),
                'worst_severity': max(c['severity'] for c in checks),
                'failed': sorted(c['id'] for c in checks if not c['passed'])}

    def rollout(self, policy, scenario=None, *, seed=0, name=None, keep_trajectory=False, split=None):
        """Run ``policy`` through the compiled environment; returns a labelled rollout record."""
        environment = self.environment(scenario, split)
        observation, info = environment.reset(seed=seed)
        rewards, done, t = [], False, 0
        while not done:
            actions = policy.act(observation, t) if hasattr(policy, 'act') else policy(observation, t)
            actions = {k: _clip_action(self.contract['actions'][k], float(v)) for k, v in actions.items()}
            observation, reward, terminated, truncated, info = environment.step(actions)
            rewards.append(reward)
            done, t = terminated or truncated, t + 1
        trajectory = environment.decision_evaluator.trajectory
        used = self.mechanisms_used(trajectory)
        record = {'schema': ROLLOUT_SCHEMA, 'contract': self.contract['id'], 'decision_maker': self.entity,
                  'policy': name or getattr(policy, 'name', None), 'seed': seed,
                  'scenario': trajectory and environment.materialization['execution']['scenario'],
                  'steps': len(trajectory) - 1, 'environment_return': math.fsum(rewards), **self.score(trajectory),
                  'mechanisms_used': used, 'identities_enforced': list(self.kernel.identities),
                  'label': recommendation_label(used), 'does_not_establish': self.does_not_establish()}
        if keep_trajectory:
            record['trajectory'] = deepcopy(trajectory)
        return record

    def does_not_establish(self):
        return list(DOES_NOT_ESTABLISH) + list(self.contract['does_not_establish'])

    def evidence(self):
        return {'index': self.index.summary() if self.index is not None else None, 'mechanisms': deepcopy(self.resolutions),
                'identities_enforced': list(self.kernel.identities)}


def _clip_action(spec, value):
    if not math.isfinite(value):
        raise ValueError('Policy returned a non-finite action')
    return min(spec['maximum'], max(spec['minimum'], value))


def compile_contract(contract, index=None, *, history=None):
    """Validate, resolve evidence against ``index`` and bind the kernel.

    ``index`` is an :class:`~worldmodel.decision.evidence.EvidenceIndex` (normally
    ``EvidenceIndex.from_store(store)``). ``history`` supplies observed driver rows for
    kernels configured to resample them.
    """
    contract = validate_contract(contract)
    kernel = kernel_for(contract['environment']['kernel'])
    resolutions = resolve_mechanisms(contract, index)
    bound = kernel.bind(contract, resolutions, index, history=history)
    steps, unit = contract['horizon']['steps'], contract['horizon']['step']
    for resolution in bound.resolutions.values():
        scope = resolution.get('scope') or {}
        if scope.get('horizon') and steps > scope['horizon']:
            resolution['caveats'].append(f'scored at horizon {scope["horizon"]} ({scope.get("frequency") or "native"} steps); '
                                         f'rollouts iterate it for {steps} {unit}s, and multi-step skill was never tested')
        if scope.get('conditional_inputs'):
            resolution['caveats'].append(f'scored conditional on realized {", ".join(scope["conditional_inputs"])}; here '
                                         'they are scenario inputs, and forecasting them was never tested')
        if scope.get('validation_end'):
            resolution['caveats'].append(f'holdout after {scope["validation_end"]} through cutoff {scope.get("cutoff")}; '
                                         'nothing says the fitted conduct persists outside that window')
    return CompiledDecision(contract, kernel, bound, index)


def compile_from_store(contract, store, *, index=None):
    """Read the calibration reports, load any history the kernel asks for, and compile.

    A kernel that needs observed data (``history_report``) gets the data its cited
    report's attempt was fitted on, through the plan's loader pinned to the report's
    input versions — never a newer or different panel.
    """
    from .evidence import EvidenceIndex, load_attempt_data
    contract = validate_contract(contract)
    index = index if index is not None else EvidenceIndex.from_store(store)
    kernel = kernel_for(contract['environment']['kernel'])
    history = None
    report_id = kernel.history_report(contract) if hasattr(kernel, 'history_report') else None
    if report_id is not None and index.get(report_id) is not None and index.get(report_id).get('attempt'):
        loaded = load_attempt_data(store, index, report_id)
        history = {'rows': kernel.history_rows(loaded['data']), 'evidence': loaded['evidence'], 'attempt': loaded['attempt'],
                   'versions': loaded['versions']}
    return compile_contract(contract, index, history=history)


# ----------------------------------------------------------------------------- declarative policies

class Policy:
    """A named policy built from a JSON declaration (no code is loaded from files).

    Kinds:

    * ``constant``: ``{"actions": {name: value}}`` every step;
    * ``schedule``: ``{"actions": [{name: value}, ...]}``, the last entry repeating;
    * ``target_path``: ``{"action", "observation", "path": [...]}``: the action is
      ``path[t] - observation`` (for example a reserve margin that follows a planned
      rate path), the last path value repeating;
    * ``linear_rule``: ``{"action", "intercept", "coefficients": {observation: c}}``.

    Every action is clipped to the contract's bounds when it is applied.
    """

    def __init__(self, name, declaration, contract):
        self.name = name
        self.declaration = deepcopy(declaration)
        kind = declaration.get('kind')
        actions = set(contract['actions'])
        if kind == 'constant':
            if set(declaration['actions']) != actions:
                raise ValueError(f'policy {name}: constant actions must name exactly {sorted(actions)}')
        elif kind == 'schedule':
            if not declaration['actions'] or any(set(a) != actions for a in declaration['actions']):
                raise ValueError(f'policy {name}: every scheduled step must name exactly {sorted(actions)}')
        elif kind in ('target_path', 'linear_rule'):
            if actions != {declaration.get('action')}:
                raise ValueError(f'policy {name}: {kind} sets the single action {sorted(actions)}')
            if kind == 'target_path' and not declaration.get('path'):
                raise ValueError(f'policy {name}: target_path needs a nonempty path')
        else:
            raise ValueError(f'policy {name}: unknown kind {kind!r}')

    def act(self, observation, t):
        d = self.declaration
        if d['kind'] == 'constant':
            return dict(d['actions'])
        if d['kind'] == 'schedule':
            return dict(d['actions'][min(t, len(d['actions']) - 1)])
        if d['kind'] == 'target_path':
            return {d['action']: d['path'][min(t, len(d['path']) - 1)] - observation[d['observation']]}
        return {d['action']: d.get('intercept', 0.0) + math.fsum(c * observation[k] for k, c in d.get('coefficients', {}).items())}


def policies_from(declarations, contract):
    if not isinstance(declarations, dict) or not declarations:
        raise ValueError('Policies must be a nonempty object of named declarations')
    return {name: Policy(name, spec, contract) for name, spec in sorted(declarations.items())}


def recommend(compiled, policies, scenarios):
    """Rank candidate policies over supplied scenarios; the pick carries the evidence label.

    Feasibility across every scenario ranks first, then the mean weighted score. This
    is a comparison of supplied candidates under supplied scenarios, not a search for
    an optimum.
    """
    rows = []
    for name, policy in policies.items():
        runs = [compiled.rollout(policy, scenario, name=name) for scenario in scenarios]
        rows.append({'policy': name, 'scenarios': len(runs), 'success_rate': sum(r['success'] for r in runs) / len(runs),
                     'feasible_everywhere': all(r['feasible'] for r in runs),
                     'mean_weighted_score': math.fsum(r['weighted_score'] for r in runs) / len(runs),
                     'worst_severity': max(r['worst_severity'] for r in runs)})
        used = runs[-1]['mechanisms_used']
    rows.sort(key=lambda r: (not r['feasible_everywhere'], -r['mean_weighted_score'], r['policy']))
    return {'ranking': rows, 'recommended': rows[0]['policy'], 'label': recommendation_label(used),
            'not_an_optimality_claim': 'Ranks the supplied candidates only; a better policy may exist outside them.',
            'does_not_establish': compiled.does_not_establish()}
