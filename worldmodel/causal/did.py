"""Difference-in-differences estimators for staggered adoption.

* ``att_gt``: Callaway and Sant'Anna (2021) group-time average treatment effects on the
  treated, unconditional (optionally exactly matched on a discrete stratum), with
  never-treated or not-yet-treated comparison units and a universal base period
  ``g - 1 - anticipation``. Each ATT(g, t) carries its influence function summed to clusters.
* ``event_study``: event-time aggregation, an overall post-period ATT, clustered analytic
  standard errors, a multiplier (wild cluster, Rademacher) bootstrap with uniform bands and
  a joint Wald pre-trend test.
* ``stacked_did``: Cengiz et al. (2019) stacked event study with clean controls, pooled with
  the weights the stacked two-way fixed-effects regression implies.
* ``twfe_static``: the naive two-way fixed-effects DiD coefficient, kept only as a comparison;
  it is biased under staggered timing with heterogeneous or dynamic effects.

Influence functions are "sum" normalised: an estimate's variance is ``sum_c psi_c**2`` times the
small-sample factor ``C / (C - 1)``. Weights such as treated shares are treated as fixed.
"""
import math
import random

from .stats import chi2_sf, mean, normal_ppf, normal_two_sided_p, quantile, solve


def _add(target, source, weight=1.0):
    for key, value in source.items():
        target[key] = target.get(key, 0.0) + weight * value


def _variance(psi, n_clusters):
    total = math.fsum(v * v for v in psi.values())
    factor = n_clusters / (n_clusters - 1) if n_clusters > 1 else float('nan')
    return total * factor


def _covariance(psis, n_clusters):
    factor = n_clusters / (n_clusters - 1) if n_clusters > 1 else float('nan')
    k = len(psis)
    out = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i, k):
            a, b = psis[i], psis[j]
            small, large = (a, b) if len(a) <= len(b) else (b, a)
            value = math.fsum(v * large.get(c, 0.0) for c, v in small.items()) * factor
            out[i][j] = out[j][i] = value
    return out


def att_gt(panel, *, control_group='not_yet_treated', anticipation=0, cohorts=None, min_treated=1, min_control=1):
    """Group-time ATTs with a universal base period.

    For cohort ``g`` and period ``t``, with base ``b = g - 1 - anticipation``::

        ATT(g, t) = sum_s w_s [mean_{i in g, s}(Y_it - Y_ib) - mean_{j in C(g,t), s}(Y_jt - Y_jb)]

    over strata ``s`` with treated weights ``w_s``. ``C(g, t)`` is the never-treated units, or
    (``not_yet_treated``) also units first treated after ``max(t, b) + anticipation``, never
    cohort ``g`` itself. Units need both ``Y_t`` and ``Y_b``. Cells whose treated or control
    count falls below the minimum are skipped and listed.
    """
    if control_group not in ('not_yet_treated', 'never_treated'):
        raise ValueError('control_group must be not_yet_treated or never_treated')
    if anticipation < 0:
        raise ValueError('anticipation must be >= 0')
    by_stratum = {}
    for unit in panel.outcomes:
        by_stratum.setdefault(panel.strata[unit], []).append(unit)
    for units in by_stratum.values():
        units.sort(key=str)
    all_cohorts = sorted(panel.cohort_sizes())
    selected = all_cohorts if cohorts is None else [g for g in all_cohorts if g in set(cohorts)]
    cells, skipped = [], []
    for g in selected:
        base = g - 1 - anticipation
        for t in panel.periods:
            if t == base:
                continue
            threshold = max(t, base) + anticipation
            strata_parts, total_treated, total_control = [], 0, 0
            dropped = 0
            for stratum, units in by_stratum.items():
                treated, control = [], []
                for unit in units:
                    series = panel.outcomes[unit]
                    if t not in series or base not in series:
                        continue
                    c = panel.cohorts[unit]
                    if c == g:
                        treated.append((unit, series[t] - series[base]))
                    elif c is None or (control_group == 'not_yet_treated' and c > threshold):
                        control.append((unit, series[t] - series[base]))
                if not treated:
                    continue
                if len(control) < min_control:
                    dropped += len(treated)
                    continue
                strata_parts.append((treated, control))
                total_treated += len(treated)
                total_control += len(control)
            if total_treated < min_treated or not strata_parts:
                if total_treated or dropped:
                    skipped.append({'g': g, 't': t, 'n_treated': total_treated, 'dropped_treated_no_control': dropped})
                continue
            att, psi = 0.0, {}
            for treated, control in strata_parts:
                w = len(treated) / total_treated
                mt = mean(v for _, v in treated)
                mc = mean(v for _, v in control)
                att += w * (mt - mc)
                for unit, v in treated:
                    key = panel.clusters[unit]
                    psi[key] = psi.get(key, 0.0) + (v - mt) / total_treated
                scale = w / len(control)
                for unit, v in control:
                    key = panel.clusters[unit]
                    psi[key] = psi.get(key, 0.0) - scale * (v - mc)
            cells.append({'g': g, 't': t, 'e': t - g, 'att': att, 'n_treated': total_treated,
                          'n_control': total_control, 'dropped_treated_no_control': dropped, 'psi': psi})
    return {'cells': cells, 'skipped': skipped, 'control_group': control_group, 'anticipation': anticipation,
            'base_period': 'universal: g - 1 - anticipation'}


def aggregate_event_time(cells, e_min, e_max, *, balance=None, reference=-1):
    """Treated-count-weighted event-time ATTs from group-time cells.

    ``balance=(lo, hi)`` keeps only cohorts with a cell at every event time in ``[lo, hi]``
    other than the reference period, so that dynamics are not driven by changing cohort
    composition.
    """
    keep = None
    if balance is not None:
        lo, hi = balance
        present = {}
        for cell in cells:
            present.setdefault(cell['g'], set()).add(cell['e'])
        needed = [e for e in range(lo, hi + 1) if e != reference]
        keep = {g for g, es in present.items() if all(e in es for e in needed)}
    out = {}
    for e in range(e_min, e_max + 1):
        chosen = [c for c in cells if c['e'] == e and (keep is None or c['g'] in keep)]
        if not chosen:
            continue
        n = sum(c['n_treated'] for c in chosen)
        psi = {}
        att = 0.0
        for c in chosen:
            w = c['n_treated'] / n
            att += w * c['att']
            _add(psi, c['psi'], w)
        out[e] = {'e': e, 'att': att, 'psi': psi, 'n_treated': n, 'cohorts': sorted(c['g'] for c in chosen)}
    return out


def overall_from_event_time(event_time, post):
    """Equal-weight mean of ATT(e) over the post event times ``post`` that are present."""
    chosen = [event_time[e] for e in post if e in event_time]
    if not chosen:
        return None
    psi = {}
    for item in chosen:
        _add(psi, item['psi'], 1.0 / len(chosen))
    return {'att': mean(item['att'] for item in chosen), 'psi': psi, 'event_times': [item['e'] for item in chosen]}


def wald_test(estimates, psis, n_clusters):
    """Joint test that every estimate is zero using the clustered influence-function covariance."""
    k = len(estimates)
    if k == 0:
        return {'statistic': None, 'df': 0, 'p': None, 'note': 'no estimates to test'}
    cov = _covariance(psis, n_clusters)
    try:
        x = solve(cov, estimates)
    except ValueError:
        return {'statistic': None, 'df': k, 'p': None, 'note': 'singular covariance'}
    stat = math.fsum(a * b for a, b in zip(estimates, x))
    return {'statistic': stat, 'df': k, 'p': chi2_sf(stat, k)}


def multiplier_bootstrap(psis, *, replications=999, seed=0):
    """Rademacher multiplier (wild cluster) bootstrap draws of centred estimates.

    Returns one list of draws per parameter: ``theta*_b = sum_c v_bc psi_c`` with independent
    ``v_bc = +/-1`` shared across parameters within a cluster, so joint (uniform) inference holds.
    """
    clusters = sorted({c for psi in psis for c in psi}, key=str)
    rows = [[psi.get(c, 0.0) for psi in psis] for c in clusters]
    k = len(psis)
    rng = random.Random(seed)
    draws = [[0.0] * replications for _ in range(k)]
    for b in range(replications):
        acc = [0.0] * k
        for row in rows:
            if rng.random() < 0.5:
                for j in range(k):
                    acc[j] -= row[j]
            else:
                for j in range(k):
                    acc[j] += row[j]
        for j in range(k):
            draws[j][b] = acc[j]
    return draws


def _bootstrap_se(draws):
    iqr = quantile(draws, 0.75) - quantile(draws, 0.25)
    return iqr / (normal_ppf(0.75) - normal_ppf(0.25))


def event_study(panel, *, e_min, e_max, post=None, control_group='not_yet_treated', anticipation=0, balance=None,
                alpha=0.05, bootstrap=999, seed=0, strata_min_control=1):
    """Event-study leads and lags, an overall post ATT, and pre-trend tests.

    Reference period ``e = -1 - anticipation`` is zero by construction and omitted. Pre-trend
    tests use the leads ``e_min .. -2 - anticipation``. ``post`` defaults to ``0 .. e_max``.
    """
    gt = att_gt(panel, control_group=control_group, anticipation=anticipation, min_control=strata_min_control)
    reference = -1 - anticipation
    et = aggregate_event_time(gt['cells'], e_min, e_max, balance=balance, reference=reference)
    post = list(range(0, e_max + 1)) if post is None else list(post)
    overall = overall_from_event_time(et, post)
    n_clusters = panel.n_clusters()
    z = normal_ppf(1 - alpha / 2)
    keys = sorted(et)
    params = [et[e] for e in keys] + ([overall] if overall else [])
    psis = [p['psi'] for p in params]
    analytic_se = [math.sqrt(_variance(psi, n_clusters)) for psi in psis]
    boot = None
    if bootstrap:
        draws = multiplier_bootstrap(psis, replications=bootstrap, seed=seed)
        boot_se = [_bootstrap_se(d) if any(d) else 0.0 for d in draws]
        ses = [s if s > 0 else a for s, a in zip(boot_se, analytic_se)]
        event_idx = list(range(len(keys)))
        maxes = []
        for b in range(bootstrap):
            maxes.append(max((abs(draws[j][b]) / ses[j] for j in event_idx if ses[j] > 0), default=0.0))
        crit = quantile(maxes, 1 - alpha) if maxes else None
        boot = {'replications': bootstrap, 'seed': seed, 'weights': 'rademacher', 'level': 'cluster',
                'uniform_critical_value': crit}
    else:
        ses = analytic_se
        crit = None
    rows = []
    for j, e in enumerate(keys):
        item = et[e]
        se = ses[j]
        rows.append({'e': e, 'att': item['att'], 'se': se, 'analytic_se': analytic_se[j],
                     'ci_low': item['att'] - z * se, 'ci_high': item['att'] + z * se,
                     'band_low': None if crit is None else item['att'] - crit * se,
                     'band_high': None if crit is None else item['att'] + crit * se,
                     'p': normal_two_sided_p(item['att'] / se) if se > 0 else None,
                     'n_treated': item['n_treated'], 'cohorts': item['cohorts'],
                     'period': 'pre' if e < reference else 'post' if e >= 0 else 'anticipation'})
    overall_row = None
    if overall:
        se = ses[-1]
        overall_p = normal_two_sided_p(overall['att'] / se) if se > 0 else None
        if boot:
            d = draws[-1]
            boot_p = (1 + sum(1 for x in d if abs(x) >= abs(overall['att']))) / (len(d) + 1)
        else:
            boot_p = None
        overall_row = {'att': overall['att'], 'se': se, 'analytic_se': analytic_se[-1], 'ci_low': overall['att'] - z * se,
                       'ci_high': overall['att'] + z * se, 'p': overall_p, 'bootstrap_p': boot_p,
                       'event_times': overall['event_times'],
                       'definition': 'equal-weight mean of ATT(e) over the post event times listed'}
    pre_keys = [e for e in keys if e < reference]
    pre_idx = [keys.index(e) for e in pre_keys]
    wald = wald_test([et[e]['att'] for e in pre_keys], [et[e]['psi'] for e in pre_keys], n_clusters)
    sup_t = None
    if boot and pre_idx:
        observed = max(abs(et[keys[j]]['att']) / ses[j] for j in pre_idx if ses[j] > 0)
        null = [max(abs(draws[j][b]) / ses[j] for j in pre_idx if ses[j] > 0) for b in range(bootstrap)]
        sup_t = {'statistic': observed, 'p': (1 + sum(1 for x in null if x >= observed)) / (bootstrap + 1)}
    pre_trend = {'leads': pre_keys, 'wald': wald, 'sup_t': sup_t,
                 'max_abs_lead': max((abs(et[e]['att']) for e in pre_keys), default=None)}
    return {'estimator': 'callaway_santanna_unconditional' + ('_stratified' if len(set(panel.strata.values())) > 1 else ''),
            'control_group': control_group, 'anticipation': anticipation, 'reference_event_time': reference,
            'alpha': alpha, 'n_clusters': n_clusters, 'bootstrap': boot, 'event_time': rows, 'overall': overall_row,
            'pre_trend': pre_trend, 'cells': len(gt['cells']), 'skipped_cells': len(gt['skipped']),
            'dropped_treated_no_control': sum(c['dropped_treated_no_control'] for c in gt['cells']),
            'att_gt': [{k: c[k] for k in ('g', 't', 'e', 'att', 'n_treated', 'n_control')} for c in gt['cells']]}


def twfe_static(panel, *, tol=1e-10, max_iter=5000):
    """Naive two-way fixed-effects DiD: y_it = a_i + l_t + beta D_it + e_it, D_it = 1{t >= g_i}.

    Fixed effects are removed by alternating projections (valid for unbalanced panels).
    Standard errors are cluster-robust at the panel's cluster level. Reported only as a
    comparison: under staggered timing it averages ATTs with possibly negative weights.
    """
    obs = []
    for unit, series in panel.outcomes.items():
        g = panel.cohorts[unit]
        for t, y in series.items():
            obs.append([unit, t, y, 1.0 if g is not None and t >= g else 0.0])
    if not obs:
        raise ValueError('empty panel')
    ys = [o[2] for o in obs]
    ds = [o[3] for o in obs]

    def demean(values):
        values = list(values)
        for _ in range(max_iter):
            change = 0.0
            for idx in (0, 1):
                sums, counts = {}, {}
                for o, v in zip(obs, values):
                    k = o[idx]
                    sums[k] = sums.get(k, 0.0) + v
                    counts[k] = counts.get(k, 0) + 1
                for i, o in enumerate(obs):
                    m = sums[o[idx]] / counts[o[idx]]
                    values[i] -= m
                    change = max(change, abs(m))
            if change < tol:
                break
        return values

    yt, dt = demean(ys), demean(ds)
    sdd = math.fsum(d * d for d in dt)
    if sdd <= 1e-12:
        raise ValueError('treatment has no within variation')
    beta = math.fsum(d * y for d, y in zip(dt, yt)) / sdd
    scores = {}
    for o, d, y in zip(obs, dt, yt):
        key = panel.clusters[o[0]]
        scores[key] = scores.get(key, 0.0) + d * (y - beta * d)
    c = len(scores)
    var = (c / (c - 1)) * math.fsum(s * s for s in scores.values()) / (sdd * sdd) if c > 1 else float('nan')
    se = math.sqrt(var)
    return {'estimator': 'twfe_static', 'beta': beta, 'se': se, 'p': normal_two_sided_p(beta / se) if se > 0 else None,
            'n_obs': len(obs), 'n_clusters': c,
            'warning': 'biased under staggered adoption with heterogeneous or dynamic effects; comparison only'}


def stacked_did(panel, *, e_min, e_max, post=None, anticipation=0, alpha=0.05, require_balanced=True):
    """Stacked event study with clean controls.

    For each cohort ``g`` a sub-experiment holds cohort ``g`` and the units not treated before
    ``g + e_max + anticipation + 1`` (never-treated or later cohorts), over event times
    ``e_min .. e_max``. Within a stack the event-time effect is the DiD of means relative to
    ``e = -1 - anticipation``; stacks are pooled with the weights ``n_T n_C / (n_T + n_C)``
    that the stacked regression with stack-by-unit and stack-by-period fixed effects implies.
    Influence functions add across stacks within a cluster, so a unit reused as a control in
    several stacks is not treated as independent.
    """
    reference = -1 - anticipation
    event_times = [e for e in range(e_min, e_max + 1) if e != reference]
    post = list(range(0, e_max + 1)) if post is None else list(post)
    stacks = []
    for g in sorted(panel.cohort_sizes()):
        base = g + reference
        needed = [g + e for e in range(e_min, e_max + 1)]
        horizon = g + e_max + anticipation
        def usable(series):
            if require_balanced:
                return all(p in series for p in needed)
            return base in series
        treated = [u for u in panel.units if panel.cohorts[u] == g and usable(panel.outcomes[u])]
        control = [u for u in panel.units if (panel.cohorts[u] is None or panel.cohorts[u] > horizon)
                   and usable(panel.outcomes[u])]
        if not treated or not control:
            continue
        by_e = {}
        for e in event_times:
            t = g + e
            tv = [(u, panel.outcomes[u][t] - panel.outcomes[u][base]) for u in treated
                  if t in panel.outcomes[u] and base in panel.outcomes[u]]
            cv = [(u, panel.outcomes[u][t] - panel.outcomes[u][base]) for u in control
                  if t in panel.outcomes[u] and base in panel.outcomes[u]]
            if not tv or not cv:
                continue
            mt, mc = mean(v for _, v in tv), mean(v for _, v in cv)
            psi_t = {}
            for u, v in tv:
                psi_t[panel.clusters[u]] = psi_t.get(panel.clusters[u], 0.0) + (v - mt) / len(tv)
            for u, v in cv:
                psi_t[panel.clusters[u]] = psi_t.get(panel.clusters[u], 0.0) - (v - mc) / len(cv)
            by_e[e] = (mt - mc, psi_t)
        weight = len(treated) * len(control) / (len(treated) + len(control))
        stacks.append({'g': g, 'n_treated': len(treated), 'n_control': len(control), 'weight': weight, 'effects': by_e})
    n_clusters = panel.n_clusters()
    z = normal_ppf(1 - alpha / 2)
    rows, table = [], {}
    for e in event_times:
        members = [s for s in stacks if e in s['effects']]
        if not members:
            continue
        total = sum(s['weight'] for s in members)
        att, psi = 0.0, {}
        for s in members:
            w = s['weight'] / total
            att += w * s['effects'][e][0]
            _add(psi, s['effects'][e][1], w)
        se = math.sqrt(_variance(psi, n_clusters))
        table[e] = (att, psi)
        rows.append({'e': e, 'att': att, 'se': se, 'ci_low': att - z * se, 'ci_high': att + z * se,
                     'p': normal_two_sided_p(att / se) if se > 0 else None, 'stacks': len(members)})
    chosen = [table[e] for e in post if e in table]
    overall = None
    if chosen:
        psi = {}
        for _, p in chosen:
            _add(psi, p, 1.0 / len(chosen))
        att = mean(a for a, _ in chosen)
        se = math.sqrt(_variance(psi, n_clusters))
        overall = {'att': att, 'se': se, 'ci_low': att - z * se, 'ci_high': att + z * se,
                   'p': normal_two_sided_p(att / se) if se > 0 else None,
                   'definition': 'equal-weight mean of stacked ATT(e) over post event times'}
    pre = [e for e in table if e < reference]
    wald = wald_test([table[e][0] for e in pre], [table[e][1] for e in pre], n_clusters)
    return {'estimator': 'stacked_did_clean_controls', 'stacks': [{k: s[k] for k in ('g', 'n_treated', 'n_control', 'weight')}
                                                                   for s in stacks],
            'event_time': rows, 'overall': overall, 'pre_trend': {'leads': pre, 'wald': wald}, 'n_clusters': n_clusters}
