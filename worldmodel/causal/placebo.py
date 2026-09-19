"""Falsification tests: placebo dates and placebo units."""
import random

from .did import event_study


def placebo_date_test(panel, *, shift, control_group='not_yet_treated', anticipation=0, alpha=0.05, e_min=None,
                      cohorts=None):
    """Pretend every treated unit was treated ``shift`` periods early, using only pre-treatment data.

    Treated units keep observations strictly before ``g - anticipation``; their placebo cohort is
    ``g - shift``. The overall placebo ATT averages placebo event times ``0 .. shift - 1 - anticipation``,
    all of which precede real treatment. A design whose parallel-trends assumption holds should
    find no effect. ``cohorts`` restricts the estimated (real) cohorts; units of other cohorts
    keep serving as not-yet-treated comparisons at their shifted dates.
    """
    if shift < 1 + anticipation:
        raise ValueError('shift must leave at least one placebo post period before real treatment')
    outcomes, shifted = {}, {}
    for unit, series in panel.outcomes.items():
        g = panel.cohorts[unit]
        if g is None:
            outcomes[unit], shifted[unit] = series, None
            continue
        kept = {t: v for t, v in series.items() if t < g - anticipation}
        if kept:
            outcomes[unit], shifted[unit] = kept, g - shift
    placebo = panel.replace(outcomes=outcomes, cohorts=shifted)
    last = shift - 1 - anticipation
    e_min = -shift if e_min is None else e_min
    fake = None if cohorts is None else [g - shift for g in cohorts]
    result = event_study(placebo, e_min=e_min, e_max=last, post=range(0, last + 1), control_group=control_group,
                         anticipation=anticipation, alpha=alpha, bootstrap=0, cohorts=fake)
    overall = result['overall']
    return {'test': 'placebo_date', 'shift': shift, 'overall': overall,
            'event_time': [r for r in result['event_time'] if r['e'] >= 0],
            'p': None if overall is None else overall['p'],
            'passed': None if overall is None or overall['p'] is None else overall['p'] >= alpha,
            'treated_units_with_pre_data': sum(1 for u in placebo.outcomes if placebo.cohorts[u] is not None)}


def placebo_unit_test(panel, *, replications=100, seed=0, e_min, e_max, post=None, control_group='not_yet_treated',
                      anticipation=0, alpha=0.05, observed=None, min_never_treated=20, cohorts=None):
    """Assign fake cohorts to never-treated units and re-estimate.

    Real treated units are removed. In each replication a share of never-treated units equal to
    the real treated share (within stratum) receives cohorts drawn from the real cohort
    distribution of that stratum. The rejection rate at ``alpha`` estimates the test's size under
    the null; ``observed`` (the real overall ATT) is ranked against the placebo distribution.
    """
    never = panel.never_treated_units()
    if len(never) < min_never_treated:
        return {'test': 'placebo_unit', 'ran': False, 'reason': f'only {len(never)} never-treated units (< {min_never_treated})',
                'passed': None}
    by_stratum_cohorts, by_stratum_units, shares = {}, {}, {}
    for unit in panel.units:
        s = panel.strata[unit]
        if panel.cohorts[unit] is None:
            by_stratum_units.setdefault(s, []).append(unit)
        elif cohorts is None or panel.cohorts[unit] in set(cohorts):
            by_stratum_cohorts.setdefault(s, []).append(panel.cohorts[unit])
    for s, units in by_stratum_units.items():
        treated = len(by_stratum_cohorts.get(s, []))
        shares[s] = treated / (treated + len(units))
    rng = random.Random(seed)
    estimates, rejections, ran = [], 0, 0
    base = panel.subset(never)
    for _ in range(replications):
        assigned = {u: None for u in never}
        for s, units in by_stratum_units.items():
            pool = by_stratum_cohorts.get(s)
            if not pool:
                continue
            k = max(1, round(shares[s] * len(units))) if len(units) > 1 else 0
            k = min(k, len(units) - 1)
            for unit in rng.sample(units, k):
                assigned[unit] = rng.choice(pool)
        fake = base.replace(cohorts=assigned)
        result = event_study(fake, e_min=e_min, e_max=e_max, post=post, control_group=control_group,
                             anticipation=anticipation, alpha=alpha, bootstrap=0)
        overall = result['overall']
        if overall is None or overall['p'] is None:
            continue
        ran += 1
        estimates.append(overall['att'])
        rejections += overall['p'] < alpha
    rate = rejections / ran if ran else None
    out = {'test': 'placebo_unit', 'ran': True, 'replications': ran, 'seed': seed, 'rejection_rate': rate,
           'nominal_alpha': alpha, 'placebo_mean': sum(estimates) / ran if ran else None,
           'placebo_sd': (sum((x - sum(estimates) / ran) ** 2 for x in estimates) / (ran - 1)) ** 0.5 if ran > 1 else None,
           'never_treated_units': len(never)}
    if observed is not None and ran:
        out['observed'] = observed
        out['permutation_p'] = (1 + sum(1 for x in estimates if abs(x) >= abs(observed))) / (ran + 1)
    return out
