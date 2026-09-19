"""Policy optimization that runs only on validated dynamics.

:func:`optimization_gate` refuses unless

* the evidence was read from the ``calibration_reports`` store (an
  :class:`~worldmodel.decision.evidence.EvidenceIndex` built by ``from_store``),
* every mechanism the compiled environment runs resolves to ``validated`` there —
  after binding coverage, so a mechanism with any parameter its report does not set
  is refused too — and
* training scenarios come from an observed source (resampled published data), not
  from a declared range, which would be an assumed driver process under another name.

:func:`optimize` then trains a bounded tabular Q-learner with the existing
:func:`worldmodel.rl.train_tabular` over the compiled ``Environment``, freezes the
greedy policy, and compares it with supplied rule baselines on held-out seeds (each
seed names one resampled scenario, shared by every policy).

Passing the gate means forecast skill was validated. It does not mean the response
to anyone's action was: validated forecast skill is not validated counterfactual
response, so even a gated policy is conditional on the fitted dynamics.
"""
import json
import math

from .compile import Policy
from .evidence import VALIDATED_BUT, recommendation_label

SCHEMA = 'worldmodel.decision_optimization/1'
CONDITIONAL = ('Validated forecast skill is not validated counterfactual response. This policy is optimal, at best, '
               'for the fitted dynamics and the resampled scenarios; it is conditional on both.')


class RefusedToOptimize(ValueError):
    """Raised by :func:`optimize` when the gate fails; ``report`` says why."""

    def __init__(self, report):
        self.report = report
        super().__init__('Refused to optimize: ' + '; '.join(report['reasons']))


def optimization_gate(compiled):
    reasons = []
    index = compiled.index
    if index is None or index.source != 'store':
        reasons.append('evidence was not read from the calibration_reports store, so no mechanism can count as validated')
    mechanisms = {}
    for name, resolution in sorted(compiled.resolutions.items()):
        mechanisms[name] = {'status': resolution['status'], 'declared_status': resolution['declared_status'],
                            'report_id': resolution['report_id'], 'reasons': list(resolution['reasons'])}
        if resolution['status'] != 'validated':
            reasons.append(f'mechanism {name} is {resolution["status"]}: ' + '; '.join(resolution['reasons'][-1:]))
    source = compiled.bound.training_source()
    if not source.get('observed'):
        reasons.append('training scenarios are not drawn from observed data (' + source.get('reason', 'no source') +
                       '); an invented driver distribution is an assumed mechanism')
    return {'allowed': not reasons, 'reasons': reasons, 'mechanisms': mechanisms, 'training_source': source,
            'evidence_index': index.summary() if index is not None else None}


def bin_encoder(bins):
    """Encoder from ``{observation: [edges...]}``: each observation becomes its bin index."""
    if not isinstance(bins, dict) or not bins:
        raise ValueError('Encoder bins must map observation names to ascending edge lists')
    for name, edges in bins.items():
        if not isinstance(edges, list) or any(type(e) not in (int, float) for e in edges) or edges != sorted(edges):
            raise ValueError(f'Encoder edges for {name} must be an ascending numeric list')

    def encode(observation):
        return [sum(observation[name] >= edge for edge in edges) for name, edges in sorted(bins.items())]
    return encode


class GreedyPolicy:
    def __init__(self, q_table, actions, encoder, fallback, name='learned'):
        self.q, self.actions, self.encoder, self.fallback, self.name = q_table, actions, encoder, fallback, name
        self.unseen = 0

    def act(self, observation, t):
        key = json.dumps(self.encoder(observation), sort_keys=True, allow_nan=False, separators=(',', ':'))
        values = self.q.get(key)
        if values is None:
            self.unseen += 1
            return dict(self.actions[self.fallback])
        return dict(self.actions[max(range(len(values)), key=lambda i: values[i])])


def _summary(runs, metrics):
    n = len(runs)
    out = {'episodes': n, 'success_rate': sum(r['success'] for r in runs) / n,
           'feasible_rate': sum(r['feasible'] for r in runs) / n,
           'mean_weighted_score': math.fsum(r['weighted_score'] for r in runs) / n,
           'mean_environment_return': math.fsum(r['environment_return'] for r in runs) / n}
    for metric in metrics:
        values = [next(o['value'] for o in r['objectives'] if o['id'] == metric) for r in runs]
        out[f'mean_{metric}'] = math.fsum(values) / n
    return out


def _paired(a, b):
    diffs = [x['weighted_score'] - y['weighted_score'] for x, y in zip(a, b)]
    n = len(diffs)
    mean = math.fsum(diffs) / n
    sd = math.sqrt(math.fsum((d - mean) ** 2 for d in diffs) / (n - 1)) if n > 1 else float('nan')
    se = sd / math.sqrt(n) if n > 1 else float('nan')
    return {'mean_difference': mean, 'standard_error': se, 'interval_95': [mean - 1.96 * se, mean + 1.96 * se],
            'learned_better_share': sum(d > 0 for d in diffs) / n}


def optimize(compiled, *, actions, encoder_bins, training_seeds, evaluation_seeds, baselines=None, alpha=0.1, gamma=0.0,
             epsilon=0.2, seed=0, fallback_action=0):
    """Gate, train, freeze, and compare with baselines on held-out seeds.

    ``actions`` is an explicit finite list of action dictionaries inside the
    contract's bounds; ``encoder_bins`` maps observations to bin edges; ``baselines``
    maps names to :class:`~worldmodel.decision.compile.Policy` declarations or policy
    objects. Raises :class:`RefusedToOptimize` when the gate fails.
    """
    gate = optimization_gate(compiled)
    if not gate['allowed']:
        raise RefusedToOptimize(gate)
    from ..rl import train_tabular
    for action in actions:
        for name, value in action.items():
            spec = compiled.contract['actions'][name]
            if not spec['minimum'] <= value <= spec['maximum']:
                raise ValueError(f'action {action} lies outside the contract bounds')
    encode = bin_encoder(encoder_bins)
    trained = train_tabular(lambda: compiled.environment(None, 'train'), actions, encode, training_seeds=list(training_seeds),
                            evaluation_seeds=list(evaluation_seeds), max_steps=compiled.horizon, alpha=alpha, gamma=gamma,
                            epsilon=epsilon, seed=seed, baseline_action=fallback_action)
    learned = GreedyPolicy(trained['q_table'], actions, encode, fallback_action)
    policies = {'learned': learned}
    for name, declaration in (baselines or {}).items():
        if isinstance(declaration, dict) and declaration.get('kind') == 'fitted_rule_quantile':
            if not hasattr(compiled.bound, 'fitted_rule_declaration'):
                raise ValueError(f'baseline {name}: kernel {compiled.kernel.id} has no fitted rule to take a quantile of')
            declaration = compiled.bound.fitted_rule_declaration(declaration['quantile'])
        policies[name] = declaration if hasattr(declaration, 'act') or callable(declaration) else Policy(name, declaration, compiled.contract)
    objectives = [o['id'] for o in compiled.contract['objectives']]
    split = (compiled.bound.training_source() or {}).get('split')
    if isinstance(split, dict):
        plans = [('held_out_history', 'evaluate', 'held-out seeds on blocks of history the learner never trained on'),
                 ('training_history', 'train', 'held-out seeds on the blocks the learner trained on (fresh shocks only)')]
    else:
        plans = [('held_out_seeds', None, 'held-out seeds; training and evaluation resample the same history')]
    evaluations, used = {}, None
    for label_name, which, description in plans:
        learned.unseen = 0
        runs = {name: [compiled.rollout(policy, None, seed=s, name=name, split=which) for s in evaluation_seeds]
                for name, policy in policies.items()}
        used = used or runs['learned'][0]['mechanisms_used']
        comparison = {name: _summary(rows, objectives) for name, rows in runs.items()}
        best = max(comparison, key=lambda name: comparison[name]['mean_weighted_score'])
        evaluations[label_name] = {'description': description, 'comparison': comparison,
                                   'learned_minus_baseline': {name: _paired(runs['learned'], rows) for name, rows in runs.items()
                                                              if name != 'learned'},
                                   'best_mean_weighted_score': best, 'unseen_state_visits': learned.unseen}
    table = {}
    for key, values in trained['q_table'].items():
        best = max(range(len(values)), key=lambda i: values[i])
        table[key] = {'action': actions[best], 'q': values}
    label = recommendation_label(used)
    return {'schema': SCHEMA, 'contract': compiled.contract['id'], 'decision_maker': compiled.entity, 'gate': gate,
            'algorithm': {'method': 'tabular Q-learning (worldmodel.rl.train_tabular), epsilon-greedy, frozen greedy policy',
                          'alpha': alpha, 'gamma': gamma, 'epsilon': epsilon, 'seed': seed, 'actions': actions,
                          'encoder_bins': encoder_bins, 'training_episodes': len(training_seeds),
                          'evaluation_episodes': len(evaluation_seeds), 'transitions': trained['transitions'],
                          'states_visited': len(trained['q_table']), 'fallback_action': actions[fallback_action]},
            'baselines': {name: getattr(policy, 'declaration', None) for name, policy in policies.items() if name != 'learned'},
            'learned_policy': table, 'evaluations': evaluations,
            'mechanisms': used, 'label': label, 'conditional_on_fitted_dynamics': CONDITIONAL,
            'validated_is_not_counterfactual': VALIDATED_BUT,
            'does_not_establish': compiled.does_not_establish() + [CONDITIONAL]}
