"""Fitting whole simulator runs to target moments: simulated method of moments and rejection ABC.

Simulators are black boxes ``simulate(parameters: dict, seed: int) -> {moment: float}``.
Adapters wrap existing configuration evaluators (e.g. ``simulate_coupled_economy``,
``materialize_composition``) and :class:`worldmodel.environments.Environment` episodes.
Common random numbers (a fixed list of seeds) make the SMM objective deterministic.
"""
from copy import deepcopy
import math
import random
from . import linalg as la
from .optimize import nelder_mead, jacobian
from .distributions import chi2_sf
from .bootstrap import quantile

REDUCERS = ('mean', 'sum', 'last', 'first', 'min', 'max', 'std', 'count')


def _moment_vector(simulate, parameters, names, specs, seeds):
    values = dict(zip([s.name for s in specs], parameters))
    totals = [0.0] * len(names)
    for seed in seeds:
        moments = simulate(dict(values), seed)
        for i, name in enumerate(names):
            value = moments[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'Simulator returned nonfinite moment {name}')
            totals[i] += value
    return [v / len(seeds) for v in totals]


def smm(simulate, targets, specs, *, weighting='identity', target_cov=None, simulations=10, seed=0,
        x0=None, max_evaluations=800):
    """Minimize (m_sim(θ) - m_data)' W (m_sim(θ) - m_data).

    ``target_cov`` is the covariance of the *data moment estimator* (e.g. from a
    block bootstrap). With it, sandwich standard errors include the (1 + 1/S)
    simulation-noise factor; with ``weighting='optimal'`` W = target_cov^-1 and an
    over-identification J statistic is reported.
    """
    names = sorted(targets)
    target = [float(targets[n]) for n in names]
    if type(simulations) is not int or not 1 <= simulations <= 1000:
        raise ValueError('simulations must be an integer in 1..1000')
    rng = random.Random(seed)
    seeds = [rng.randrange(2 ** 31) for _ in range(simulations)]
    omega = target_cov['matrix'] if isinstance(target_cov, dict) else target_cov
    if weighting == 'identity':
        weight = la.identity(len(names))
    elif weighting == 'diagonal':
        if omega is None:
            scales = [max(abs(t), 1e-12) ** 2 for t in target]
            weight = [[1.0 / scales[i] if i == j else 0.0 for j in range(len(names))] for i in range(len(names))]
        else:
            weight = [[1.0 / omega[i][i] if i == j else 0.0 for j in range(len(names))] for i in range(len(names))]
    elif weighting == 'optimal':
        if omega is None:
            raise ValueError('optimal weighting requires target_cov')
        weight = la.inverse(omega)
    else:
        weight = weighting
    if len(names) < len(specs):
        raise ValueError('SMM needs at least as many moments as parameters')
    start = list(x0) if x0 is not None else []
    if not start:
        for spec in specs:
            if spec.prior and spec.prior.get('distribution') == 'normal':
                start.append(spec.prior['mean'])
            elif spec.lower is not None and spec.upper is not None:
                start.append((spec.lower + spec.upper) / 2)
            else:
                start.append(spec.lower + 1 if spec.lower is not None else 0.0)

    def moments(theta):
        return _moment_vector(simulate, theta, names, specs, seeds)

    def objective(theta):
        g = [a - b for a, b in zip(moments(theta), target)]
        return la.dot(g, la.matvec(weight, g))

    solution = nelder_mead(objective, start, bounds=[spec.bounds for spec in specs], max_evaluations=max_evaluations)
    theta = solution['x']
    simulated = moments(theta)
    gap = [a - b for a, b in zip(simulated, target)]
    result = {'method': 'simulated_method_of_moments', 'parameters': {s.name: v for s, v in zip(specs, theta)},
              'objective': solution['fun'], 'converged': solution['converged'], 'evaluations': solution['evaluations'],
              'moments': {n: {'target': t, 'simulated': s} for n, t, s in zip(names, target, simulated)},
              'simulations': simulations, 'seeds': seeds, 'weighting': weighting if isinstance(weighting, str) else 'supplied',
              'standard_errors': {s.name: None for s in specs}, 'covariance': None, 'j_test': None}
    if omega is not None:
        try:
            g_jac = jacobian(moments, theta, relative_step=1e-3)
            gw = la.matmul(la.transpose(g_jac), weight)
            bread = la.inverse(la.matmul(gw, g_jac))
            meat = la.matmul(la.matmul(gw, omega), la.transpose(gw))
            factor = 1 + 1 / simulations
            cov = la.scale(la.matmul(la.matmul(bread, meat), bread), factor)
            result['covariance'] = {'names': [s.name for s in specs], 'matrix': la.symmetrize(cov)}
            result['standard_errors'] = {s.name: math.sqrt(max(cov[i][i], 0.0)) for i, s in enumerate(specs)}
            if weighting == 'optimal' and len(names) > len(specs):
                stat = la.dot(gap, la.matvec(la.inverse(omega), gap)) / factor
                result['j_test'] = {'statistic': stat, 'df': len(names) - len(specs), 'pvalue': chi2_sf(stat, len(names) - len(specs))}
        except ValueError as error:
            result['standard_error_failure'] = str(error)
    at_bound = []
    for spec, value in zip(specs, theta):
        for bound in spec.bounds:
            if bound is not None and abs(value - bound) <= 1e-6 * max(1.0, abs(bound)):
                at_bound.append(spec.name)
    result['at_bound'] = at_bound
    return result


def abc_rejection(simulate, targets, specs, *, draws=2000, accept_fraction=0.05, scales=None, seed=0):
    """Rejection ABC: prior draws whose simulated moments fall nearest the targets."""
    if type(draws) is not int or not 20 <= draws <= 200000 or not 0 < accept_fraction <= 1:
        raise ValueError('ABC needs 20..200000 draws and an acceptance fraction in (0,1]')
    names = sorted(targets)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        theta = {spec.name: spec.sample_prior(rng) for spec in specs}
        run_seed = rng.randrange(2 ** 31)
        try:
            moments = simulate(dict(theta), run_seed)
            vector = [float(moments[n]) for n in names]
        except (ValueError, KeyError, ZeroDivisionError, OverflowError):
            continue
        if all(math.isfinite(v) for v in vector):
            samples.append((theta, vector))
    if len(samples) < 10:
        raise ValueError('Too few successful ABC simulations')
    if scales is None:
        scales = []
        for i in range(len(names)):
            column = sorted(s[1][i] for s in samples)
            median = quantile(column, 0.5)
            mad = quantile(sorted(abs(v - median) for v in column), 0.5)
            scales.append(mad if mad > 0 else 1.0)
    else:
        scales = [float(scales[n]) for n in names]
    target = [float(targets[n]) for n in names]
    scored = sorted(((math.sqrt(math.fsum(((v - t) / s) ** 2 for v, t, s in zip(vector, target, scales))), i, theta)
                     for i, (theta, vector) in enumerate(samples)), key=lambda row: (row[0], row[1]))
    kept = scored[:max(2, int(round(accept_fraction * len(scored))))]
    posterior = {}
    for spec in specs:
        values = sorted(row[2][spec.name] for row in kept)
        mean = math.fsum(values) / len(values)
        posterior[spec.name] = {'mean': mean, 'sd': math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1)),
                                'quantiles': {q: quantile(values, float(q)) for q in ('0.05', '0.5', '0.95')}}
    return {'method': 'abc_rejection', 'posterior': posterior, 'accepted': len(kept), 'simulated': len(samples),
            'epsilon': kept[-1][0], 'scales': dict(zip(names, scales)), 'seed': seed,
            'draws': [{**row[2], '_distance': row[0]} for row in kept[:1000]],
            'limitations': ['Rejection ABC approximates the posterior given the chosen summaries and tolerance only.']}


def _json_path(value, path):
    if not isinstance(path, list) or not path:
        raise ValueError('Moment path must be a nonempty list')
    for position, key in enumerate(path):
        if key == '*':
            if not isinstance(value, list):
                raise ValueError('Wildcard moment path requires a list')
            return [_json_path(item, path[position + 1:]) if path[position + 1:] else item for item in value]
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and -len(value) <= key < len(value):
            value = value[key]
        else:
            raise ValueError(f'Missing moment path element {key!r}')
    return value


def _flatten(value):
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_flatten(item))
        return out
    return [value]


def reduce_moment(value, reducer):
    values = [float(v) for v in _flatten(value) if not isinstance(v, bool)]
    if reducer not in REDUCERS:
        raise ValueError('Unknown moment reducer')
    if not values:
        raise ValueError('Empty moment selection')
    if reducer == 'mean':
        return math.fsum(values) / len(values)
    if reducer == 'sum':
        return math.fsum(values)
    if reducer == 'last':
        return values[-1]
    if reducer == 'first':
        return values[0]
    if reducer == 'min':
        return min(values)
    if reducer == 'max':
        return max(values)
    if reducer == 'count':
        return float(len(values))
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1))


def config_simulator(evaluate, baseline, parameter_paths, moments, *, seed_path=None, transforms=None):
    """Adapt a configuration evaluator into ``simulate(parameters, seed) -> moments``.

    ``parameter_paths`` maps parameter names to one or more JSON paths set in a
    deep copy of ``baseline``; ``moments`` maps names to ``{'path': [...], 'reduce': ...}``
    (paths may contain ``'*'``). ``transforms`` optionally maps parameter names to
    callables producing the value written (e.g. integer rounding for count fields).
    """
    transforms = transforms or {}

    def simulate(parameters, seed):
        config = deepcopy(baseline)
        for name, paths in parameter_paths.items():
            value = transforms[name](parameters[name]) if name in transforms else parameters[name]
            for path in (paths if isinstance(paths[0], list) else [paths]):
                parent = _json_path(config, path[:-1]) if len(path) > 1 else config
                parent[path[-1]] = value
        if seed_path is not None:
            parent = _json_path(config, seed_path[:-1]) if len(seed_path) > 1 else config
            parent[seed_path[-1]] = seed
        result = evaluate(config)
        return {name: reduce_moment(_json_path(result, spec['path']), spec.get('reduce', 'last')) for name, spec in moments.items()}

    return simulate


def environment_simulator(make_environment, policy, summarize, *, max_steps):
    """Adapt :class:`worldmodel.environments.Environment` episodes into a moment simulator.

    ``make_environment(parameters)`` builds an environment, ``policy(parameters, observation, step)``
    returns declared actions, and ``summarize(trajectory)`` maps the list of
    ``{observation, reward, info}`` records to moments.
    """
    def simulate(parameters, seed):
        environment = make_environment(dict(parameters))
        observation, info = environment.reset(seed=seed)
        trajectory = [{'observation': observation, 'reward': 0.0, 'info': info}]
        for step in range(max_steps):
            observation, reward, terminated, truncated, info = environment.step(policy(parameters, observation, step))
            trajectory.append({'observation': observation, 'reward': reward, 'info': info})
            if terminated or truncated:
                break
        return summarize(trajectory)

    return simulate
