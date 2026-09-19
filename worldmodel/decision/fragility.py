"""Adversarial scenario search: where does a supplied policy fail its success criteria worst?

Given a compiled contract and one or more candidate policies, :func:`stress_test`
searches the declared scenario box — uncertain inputs and assumed parameters within
their declared ranges, report-backed parameters within ``k`` estimated standard
errors, standardized shocks within a declared quantile bound — for scenarios where
the policy's worst success-criterion (or constraint) violation is largest.

The search is seeded, stdlib-only and budgeted in rollouts:

1. **Random phase.** One shared set of uniform draws over the box (common random
   numbers across policies), plus the nominal scenario.
2. **Local refinement.** From the ``top_k`` worst draws, a coordinate pattern search
   (step a quarter of each range, halved when a full sweep does not worsen the
   outcome) pushes toward larger violations until the budget is spent.

The output is a **fragility claim** — "this policy breaks when X and Y" — built from
the random phase only, because refinement deliberately oversamples failures: failure
rates per bin of each dimension, the dimensions whose bins differ most (the drivers),
and the two-driver cell with the highest failure rate. It is not an optimality
claim, and a failure rate is a share of a uniform sample over a declared box, not a
probability of failure in the world.
"""
import random

from .evidence import recommendation_label

SCHEMA = 'worldmodel.decision_fragility/1'
NOT_OPTIMALITY = ('This is a fragility claim, not an optimality claim: it reports where supplied policies fail inside a '
                  'declared box. It does not say any policy is best, and finding no failure is not evidence that none exists.')
NOT_PROBABILITY = ('Failure rates are shares of uniform draws over declared bounds. The bounds are not a probability '
                   'distribution, so a rate is not the chance of failure in the world.')


def _uniform(rng, dimensions):
    return {d['name']: rng.uniform(d['low'], d['high']) for d in dimensions}


def _evaluate(compiled, policy, name, point):
    record = compiled.rollout(policy, compiled.scenario(point), name=name)
    failing = [c for c in record['constraints'] + record['success_criteria'] if not c['passed']]
    return {'point': point, 'severity': record['worst_severity'], 'failed': [c['id'] for c in failing],
            'success': record['success'], 'weighted_score': record['weighted_score'],
            'observed': {c['id']: c['observed'] for c in record['constraints'] + record['success_criteria']},
            'mechanisms_used': record['mechanisms_used']}


def _refine(compiled, policy, name, start, dimensions, budget, rng):
    """Coordinate pattern search maximizing severity; returns (best, evaluations used)."""
    best, used, step = start, 0, 0.25
    order = list(dimensions)
    while used < budget and step >= 1 / 64:
        improved = False
        rng.shuffle(order)
        for d in order:
            width = d['high'] - d['low']
            if width <= 0:
                continue
            for direction in (1, -1):
                if used >= budget:
                    break
                value = min(d['high'], max(d['low'], best['point'][d['name']] + direction * step * width))
                if value == best['point'][d['name']]:
                    continue
                candidate = _evaluate(compiled, policy, name, compiled.bound.project(dict(best['point'], **{d['name']: value})))
                used += 1
                if candidate['severity'] > best['severity'] + 1e-12:
                    best, improved = candidate, True
                    break
        if not improved:
            step /= 2
    return best, used


def _bins(values, low, high, count):
    width = (high - low) / count if high > low else 1.0
    return [min(count - 1, max(0, int((v - low) / width))) if high > low else 0 for v in values]


def _edges(d, count):
    width = (d['high'] - d['low']) / count
    return [(d['low'] + i * width, d['low'] + (i + 1) * width) for i in range(count)]


def failure_regions(samples, dimensions, *, bins=4, min_bin_samples=8, driver_spread=0.2):
    """Per-dimension failure rates by bin, the driving dimensions and the worst two-driver cell."""
    n = len(samples)
    fails = [0 if s['success'] else 1 for s in samples]
    overall = sum(fails) / n if n else 0.0
    per_dimension = []
    for d in dimensions:
        values = [s['point'][d['name']] for s in samples]
        index = _bins(values, d['low'], d['high'], bins)
        rows = []
        for b, (low, high) in enumerate(_edges(d, bins)):
            members = [f for f, i in zip(fails, index) if i == b]
            rows.append({'range': [low, high], 'samples': len(members),
                         'failure_rate': sum(members) / len(members) if members else None})
        usable = [r['failure_rate'] for r in rows if r['samples'] >= min_bin_samples]
        spread = max(usable) - min(usable) if len(usable) >= 2 else 0.0
        mean_v = sum(values) / n
        cov = sum((v - mean_v) * (f - overall) for v, f in zip(values, fails))
        direction = 'higher' if cov > 0 else 'lower' if cov < 0 else 'none'
        per_dimension.append({'dimension': d['name'], 'kind': d['kind'], 'spread': spread, 'fails_more_when': direction, 'bins': rows})
    per_dimension.sort(key=lambda r: (-r['spread'], r['dimension']))
    drivers = [r['dimension'] for r in per_dimension if r['spread'] >= driver_spread][:3]
    cell = safest = None
    if len(drivers) >= 2 and 0 < overall < 1:
        a, b = (next(d for d in dimensions if d['name'] == name) for name in drivers[:2])
        coarse = 3
        ia = _bins([s['point'][a['name']] for s in samples], a['low'], a['high'], coarse)
        ib = _bins([s['point'][b['name']] for s in samples], b['low'], b['high'], coarse)
        cells = []
        for x, (alow, ahigh) in enumerate(_edges(a, coarse)):
            for y, (blow, bhigh) in enumerate(_edges(b, coarse)):
                members = [f for f, i, j in zip(fails, ia, ib) if i == x and j == y]
                if len(members) < min_bin_samples:
                    continue
                cells.append({'dimensions': {a['name']: [alow, ahigh], b['name']: [blow, bhigh]}, 'samples': len(members),
                              'failures': sum(members), 'failure_rate': sum(members) / len(members)})
        if cells:
            cell = max(cells, key=lambda c: (c['failure_rate'], c['samples']))
            safest = min(cells, key=lambda c: (c['failure_rate'], -c['samples']))
    directions = {r['dimension']: r['fails_more_when'] for r in per_dimension}
    by_criterion = {}
    for sample in samples:
        for criterion in sample['failed']:
            by_criterion[criterion] = by_criterion.get(criterion, 0) + 1
    return {'samples': n, 'failures': sum(fails), 'failure_rate': overall, 'failures_by_criterion': dict(sorted(by_criterion.items())),
            'drivers': drivers,
            'driver_directions': {d: directions[d] for d in drivers}, 'worst_cell': cell, 'safest_cell': safest,
            'per_dimension': per_dimension}


def _fmt(value):
    return f'{value:.3g}'


def _cell_text(cell):
    return ' and '.join(f'{dim} in [{_fmt(lo)}, {_fmt(hi)}]' for dim, (lo, hi) in cell['dimensions'].items())


def fragility_claim(name, regions, worst):
    if regions['failures'] == 0 and worst['severity'] <= 0:
        return (f'{name}: no failing scenario in {regions["samples"]} random draws or the refined search '
                f'(worst margin {_fmt(worst["severity"])} scales from failing). Not evidence that none exists.')
    text = f'{name}: fails in {regions["failures"]} of {regions["samples"]} random draws ({regions["failure_rate"]:.0%}'
    if regions.get('failures_by_criterion'):
        text += '; ' + ', '.join(f'{k} {v}' for k, v in regions['failures_by_criterion'].items())
    text += ').'
    cell, safest = regions['worst_cell'], regions.get('safest_cell')
    if cell:
        text += (f' It breaks when {_cell_text(cell)}: {cell["failures"]} of {cell["samples"]} draws there fail '
                 f'({cell["failure_rate"]:.0%}).')
        if safest and safest['failure_rate'] < cell['failure_rate']:
            text += f' It holds best when {_cell_text(safest)} ({safest["failure_rate"]:.0%} fail).'
    elif regions['drivers']:
        text += ' Failures concentrate where ' + ', '.join(
            f'{d} is {"high" if regions["driver_directions"][d] == "higher" else "low"}' for d in regions['drivers']) + '.'
    elif regions['failures']:
        text += ' No single dimension separates failing from passing draws at this sample size.'
    if worst['severity'] > 0:
        text += f' The refined worst case violates {", ".join(worst["failed"])} by {_fmt(worst["severity"])} scales.'
    return text


def _at_bounds(point, dimensions):
    out = {}
    for d in dimensions:
        width = d['high'] - d['low']
        if width <= 0:
            continue
        value = point[d['name']]
        if value >= d['high'] - 1e-9 * max(1.0, abs(d['high'])):
            out[d['name']] = 'maximum'
        elif value <= d['low'] + 1e-9 * max(1.0, abs(d['low'])):
            out[d['name']] = 'minimum'
    return out


def stress_test(compiled, policies, *, evaluations=400, refine_share=0.5, top_k=4, seed=0, bins=4, min_bin_samples=8,
                driver_spread=0.2):
    """Search for each policy's failure regions within the contract's declared scenario box."""
    dimensions = compiled.bound.dimensions
    if not dimensions:
        raise ValueError('The compiled contract declares no uncertain dimension to search')
    if type(evaluations) is not int or not 10 <= evaluations <= 1000000:
        raise ValueError('evaluations must be an integer in 10..1000000 rollouts per policy')
    if not 0 <= refine_share < 1:
        raise ValueError('refine_share must be in [0, 1)')
    refine_budget = int(evaluations * refine_share)
    random_count = evaluations - refine_budget - 1
    rng = random.Random(seed)
    nominal = compiled.bound.nominal_point()
    draws = [compiled.bound.project(_uniform(rng, dimensions)) for _ in range(random_count)]
    report = {'schema': SCHEMA, 'contract': compiled.contract['id'], 'decision_maker': compiled.entity,
              'kernel': compiled.kernel.id, 'claim_type': 'fragility', 'not_an_optimality_claim': NOT_OPTIMALITY,
              'failure_rates_are_not_probabilities': NOT_PROBABILITY,
              'dimensions': dimensions, 'parameter_uncertainty': getattr(compiled.bound, 'uncertainty', None),
              'search': {'evaluations_per_policy': evaluations, 'random_draws': random_count, 'refinement_budget': refine_budget,
                         'refinement_starts': top_k, 'seed': seed, 'bins': bins, 'min_bin_samples': min_bin_samples,
                         'method': 'uniform random search plus coordinate pattern search from the worst draws'},
              'policies': {}}
    used = {}
    for name, policy in policies.items():
        nominal_result = _evaluate(compiled, policy, name, dict(nominal))
        samples = [_evaluate(compiled, policy, name, point) for point in draws]
        regions = failure_regions(samples, dimensions, bins=bins, min_bin_samples=min_bin_samples, driver_spread=driver_spread)
        starts = sorted(samples + [nominal_result], key=lambda s: -s['severity'])[:top_k]
        policy_rng = random.Random(f'{seed}:{name}')
        refined, spent = [], 0
        for i, start in enumerate(starts):
            share = (refine_budget - spent) // (len(starts) - i)
            best, cost = _refine(compiled, policy, name, start, dimensions, share, policy_rng)
            spent += cost
            refined.append(best)
        refined.sort(key=lambda s: -s['severity'])
        worst_cases, seen = [], set()
        for case in refined:
            key = tuple(round((case['point'][d['name']] - d['low']) / ((d['high'] - d['low']) or 1), 3) for d in dimensions)
            if key in seen:
                continue
            seen.add(key)
            worst_cases.append({'severity': case['severity'], 'failed': case['failed'], 'observed': case['observed'],
                                'point': case['point'], 'at_bounds': _at_bounds(case['point'], dimensions)})
        worst = refined[0] if refined else nominal_result
        for mechanism, resolution in nominal_result['mechanisms_used'].items():
            used[mechanism] = resolution
        report['policies'][name] = {
            'nominal': {'success': nominal_result['success'], 'severity': nominal_result['severity'],
                        'failed': nominal_result['failed'], 'observed': nominal_result['observed']},
            'random': {'draws': len(samples), 'failures': regions['failures'], 'failure_rate': regions['failure_rate']},
            'refinement': {'evaluations': spent, 'worst_severity': worst['severity'],
                           'found_failure': worst['severity'] > 0},
            'regions': regions, 'worst_cases': worst_cases,
            'claim': fragility_claim(name, regions, worst)}
    report['comparison'] = sorted(({'policy': n, 'nominal_success': r['nominal']['success'],
                                    'random_failure_rate': r['random']['failure_rate'],
                                    'worst_severity': r['refinement']['worst_severity'],
                                    'drivers': r['regions']['drivers']} for n, r in report['policies'].items()),
                                  key=lambda row: (row['random_failure_rate'], row['worst_severity'], row['policy']))
    report['mechanisms'] = used
    report['label'] = recommendation_label(used)
    report['does_not_establish'] = compiled.does_not_establish() + [NOT_OPTIMALITY, NOT_PROBABILITY]
    return report
