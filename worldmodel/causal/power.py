"""Negative controls and power for registered designs, on synthetic panels calibrated to the real ones.

A registered difference-in-differences design is re-run many times on synthetic panels that keep the
real panel's structure exactly (units, observed unit-periods, cohorts, strata and clusters) and replace
its outcomes by noise calibrated to the real panel's untreated cells:

* unit-specific idiosyncratic AR(1) noise (innovation variance per unit, shrunk to the pooled value);
* cluster-by-period shocks with the same autocorrelation, when clusters hold several units;
* no treatment effect and exactly parallel trends.

Calibration uses only untreated cells (never-treated units, and treated units before
``g - anticipation``), and first differences, so unit fixed effects never enter and no post-treatment
outcome of a treated unit is used. Each synthetic panel gives the estimator's null draw ``(att, se)``
and the pre-trend and placebo-date p-values. Because every estimator here is linear in the outcomes
and a homogeneous additive effect ``delta`` shifts every treated post-period cell, the estimate under an
effect is exactly ``att + delta`` with the same standard error, so one set of null draws gives the whole
power curve and the minimum detectable effect (MDE) at 80% power.

Standard library only.
"""
import math
import random

from .did import event_study
from .panel import Panel
from .placebo import placebo_date_test
from .stats import mean, normal_ppf, solve

SHRINK = 5.0  # pseudo-observations pulling a unit's innovation variance toward the pooled value
RHO_BOUNDS = (-0.9, 0.98)


# -- serialisation ---------------------------------------------------------------------------------------

def panel_to_dict(panel):
    return {'outcomes': {str(u): {str(t): v for t, v in s.items()} for u, s in panel.outcomes.items()},
            'cohorts': {str(u): g for u, g in panel.cohorts.items()},
            'strata': {str(u): s for u, s in panel.strata.items()},
            'clusters': {str(u): c for u, c in panel.clusters.items()}}


def panel_from_dict(d):
    return Panel(d['outcomes'], d['cohorts'], d['strata'], d['clusters'])


# -- calibration -----------------------------------------------------------------------------------------

def untreated_cells(panel, anticipation=0):
    out = {}
    for unit, series in panel.outcomes.items():
        g = panel.cohorts[unit]
        kept = {t: y for t, y in series.items() if g is None or t < g - anticipation}
        if kept:
            out[unit] = kept
    return out


def _two_way_residuals(cells, strata, *, iterations=30, tol=1e-10):
    """Remove unit and stratum-by-period means by alternating projections."""
    resid = {u: dict(s) for u, s in cells.items()}
    for _ in range(iterations):
        change = 0.0
        for u, s in resid.items():
            m = math.fsum(s.values()) / len(s)
            change = max(change, abs(m))
            for t in s:
                s[t] -= m
        sums, counts = {}, {}
        for u, s in resid.items():
            for t, v in s.items():
                key = (strata[u], t)
                sums[key] = sums.get(key, 0.0) + v
                counts[key] = counts.get(key, 0) + 1
        for u, s in resid.items():
            for t in s:
                m = sums[(strata[u], t)] / counts[(strata[u], t)]
                s[t] -= m
                change = max(change, abs(m))
        if change < tol:
            break
    return resid


def calibrate(panel, *, anticipation=0):
    """Noise parameters from the untreated cells' first differences.

    Returns ``rho`` (AR(1) coefficient, from the autocorrelation of within-cluster differenced residuals:
    ``corr(dx_t, dx_{t-1}) = -(1 - rho) / 2``), the pooled and per-unit innovation variances, and the
    cluster-by-period innovation variance (zero when no cluster-period holds two units).
    """
    cells = untreated_cells(panel, anticipation)
    resid = _two_way_residuals(cells, panel.strata)
    diffs = {}
    for u, s in resid.items():
        d = {t: s[t] - s[t - 1] for t in s if t - 1 in s}
        if d:
            diffs[u] = d
    by_kt = {}
    for u, d in diffs.items():
        k = panel.clusters[u]
        for t, v in d.items():
            by_kt.setdefault((k, t), []).append((u, v))
    within_ss, within_df, between = 0.0, 0, []
    unit_ss, unit_n = {}, {}
    dev = {}
    for key, members in by_kt.items():
        n = len(members)
        m = math.fsum(v for _, v in members) / n
        if n >= 2:
            between.append((m, n))
            for u, v in members:
                within_ss += (v - m) ** 2
                unit_ss[u] = unit_ss.get(u, 0.0) + (v - m) ** 2 * n / (n - 1)
                unit_n[u] = unit_n.get(u, 0) + 1
                dev.setdefault(u, {})[key[1]] = v - m
            within_df += n - 1
    if within_df > 0:
        var_within = within_ss / within_df
        var_cluster = max(0.0, mean(m * m for m, _ in between) - mean(var_within / n for _, n in between))
        series = dev
    else:  # every cluster-period holds one unit: no separate cluster component
        allv = [v for d in diffs.values() for v in d.values()]
        mu = mean(allv)
        var_within = mean((v - mu) ** 2 for v in allv)
        var_cluster = 0.0
        series = diffs
        for u, d in diffs.items():
            unit_ss[u] = math.fsum((v - mu) ** 2 for v in d.values())
            unit_n[u] = len(d)
    num = lead = lag = 0.0
    for u, d in series.items():
        for t, v in d.items():
            if t - 1 in d:
                num += v * d[t - 1]
                lead += v * v
                lag += d[t - 1] * d[t - 1]
    corr = num / math.sqrt(lead * lag) if lead > 0 and lag > 0 else 0.0
    rho = min(RHO_BOUNDS[1], max(RHO_BOUNDS[0], 1.0 + 2.0 * corr))
    scale = (1.0 + rho) / 2.0  # var(innovation) = var(dx) (1 + rho) / 2
    unit_var = {}
    for u in panel.outcomes:
        n = unit_n.get(u, 0)
        v = (unit_ss.get(u, 0.0) + SHRINK * var_within) / (n + SHRINK)
        unit_var[u] = v * scale
    return {'rho': rho, 'difference_autocorrelation': corr, 'var_diff_within': var_within,
            'var_diff_cluster': var_cluster, 'innovation_var_pooled': var_within * scale,
            'innovation_var_cluster': var_cluster * scale, 'unit_innovation_var': unit_var,
            'untreated_cells': sum(len(s) for s in cells.values()),
            'differences': sum(len(d) for d in diffs.values()), 'cluster_periods_with_two_or_more': len(between)}


def calibration_summary(params):
    return {k: v for k, v in params.items() if k not in ('unit_innovation_var', 'unit_scale')}


# -- simulation ------------------------------------------------------------------------------------------

def _ar1_path(rng, periods, rho, innovation_sd):
    lo, hi = periods[0], periods[-1]
    sd0 = innovation_sd / math.sqrt(1.0 - rho * rho)
    x = rng.gauss(0.0, sd0)
    out = {}
    for t in range(lo, hi + 1):
        if t > lo:
            x = rho * x + rng.gauss(0.0, innovation_sd)
        out[t] = x
    return out


def simulate_null_panel(panel, params, seed):
    """A panel with the real structure and calibrated noise: no effect, parallel trends by construction."""
    if params.get('model') == 'variogram':
        return simulate_variogram_panel(panel, params, seed)
    rng = random.Random(seed)
    rho = params['rho']
    periods = panel.periods
    shocks = {}
    sd_c = math.sqrt(params['innovation_var_cluster'])
    if sd_c > 0:
        for k in sorted({panel.clusters[u] for u in panel.units}, key=str):
            shocks[k] = _ar1_path(rng, periods, rho, sd_c)
    outcomes = {}
    for u in panel.units:
        series = panel.outcomes[u]
        ts = sorted(series)
        path = _ar1_path(rng, ts, rho, math.sqrt(params['unit_innovation_var'][u]))
        c = shocks.get(panel.clusters[u])
        outcomes[u] = {t: path[t] + (c[t] if c else 0.0) for t in ts}
    return panel.replace(outcomes=outcomes)


# -- amendment 1: variogram-fitted noise ------------------------------------------------------------------
#
# The registered model is a stationary AR(1) in levels, whose differences can only be negatively
# autocorrelated. Real panels here (county log employment) have positively autocorrelated growth, so the
# fitted rho hits its bound and the synthetic panels come out too smooth. This model instead fits the
# untreated cells' empirical variogram - gamma(k), half the mean squared k-period change, which is what a
# difference-in-differences standard error depends on - with three non-negative components:
#
#     gamma(k) = a (1 - rho^k)        stationary AR(1) noise (a is its variance; rho = 0 is white noise)
#              + b k                  a random walk (innovation variance 2b)
#              + c k^2                a unit-specific random trend (slope variance 2c)
#
# Fitting is a grid over rho with non-negative least squares (all subsets) on the pair-count-weighted
# empirical variogram of untreated cells. Nothing post-treatment enters.

VARIOGRAM_RHO_GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def _nnls_small(columns, y, weights):
    """Non-negative least squares by enumerating active sets (at most three columns)."""
    best = (float('inf'), [0.0] * len(columns))
    for mask in range(1, 1 << len(columns)):
        use = [j for j in range(len(columns)) if mask >> j & 1]
        a = [[math.fsum(w * columns[i][t] * columns[j][t] for t, w in enumerate(weights)) for j in use] for i in use]
        rhs = [math.fsum(w * columns[i][t] * y[t] for t, w in enumerate(weights)) for i in use]
        try:
            beta = solve(a, rhs)
        except ValueError:
            continue
        if any(v < 0 for v in beta):
            continue
        full = [0.0] * len(columns)
        for i, j in enumerate(use):
            full[j] = beta[i]
        sse = math.fsum(w * (y[t] - math.fsum(full[j] * columns[j][t] for j in range(len(columns)))) ** 2
                        for t, w in enumerate(weights))
        if sse < best[0]:
            best = (sse, full)
    return best[1], best[0]


def fit_variogram(gamma, weights):
    """Fit ``a (1 - rho^k) + b k + c k^2`` to an empirical variogram ``{lag: value}``."""
    lags = sorted(gamma)
    y = [gamma[k] for k in lags]
    w = [weights.get(k, 1.0) for k in lags]
    best = None
    for rho in VARIOGRAM_RHO_GRID:
        columns = [[1.0 - rho ** k for k in lags], [float(k) for k in lags], [float(k * k) for k in lags]]
        beta, sse = _nnls_small(columns, y, w)
        if best is None or sse < best[0]:
            best = (sse, rho, beta)
    sse, rho, (a, b, c) = best
    total = math.fsum(w[i] * y[i] * y[i] for i in range(len(y)))
    return {'ar1_variance': a, 'ar1_rho': rho, 'random_walk_innovation_variance': 2.0 * b,
            'trend_slope_variance': 2.0 * c, 'weighted_sse': sse,
            'weighted_r_squared': None if total <= 0 else 1.0 - sse / total,
            'lags': lags, 'fitted': [a * (1 - rho ** k) + b * k + c * k * k for k in lags], 'empirical': y}


def _variogram(series, max_lag):
    """Pair-count-weighted empirical variogram of a collection of ``{period: value}`` series."""
    total, count = {}, {}
    for s in series:
        ts = sorted(s)
        for i, t in enumerate(ts):
            for u in ts[i + 1:]:
                k = u - t
                if k > max_lag:
                    break
                total[k] = total.get(k, 0.0) + 0.5 * (s[u] - s[t]) ** 2
                count[k] = count.get(k, 0) + 1
    return {k: total[k] / count[k] for k in total}, count


def calibrate_variogram(panel, *, anticipation=0, max_lag=12):
    """Calibration by fitting the untreated cells' variogram (amendment 1; see the comment above)."""
    cells = untreated_cells(panel, anticipation)
    resid = _two_way_residuals(cells, panel.strata)
    by_kt = {}
    for u, s in resid.items():
        k = panel.clusters[u]
        for t, v in s.items():
            by_kt.setdefault((k, t), []).append((u, v))
    means, sizes, dev = {}, {}, {}
    shared = False
    for (k, t), members in by_kt.items():
        n = len(members)
        m = math.fsum(v for _, v in members) / n
        if n >= 2:
            shared = True
            means.setdefault(k, {})[t] = m
            sizes[(k, t)] = n
            scale = math.sqrt(n / (n - 1))
            for u, v in members:
                dev.setdefault(u, {})[t] = (v - m) * scale
        else:
            for u, v in members:
                dev.setdefault(u, {})[t] = v
    gamma_e, pairs_e = _variogram(dev.values(), max_lag)
    unit = fit_variogram(gamma_e, {k: float(n) for k, n in pairs_e.items()})
    cluster = None
    if shared and means:
        gamma_m, pairs_m = _variogram(means.values(), max_lag)
        share = mean(1.0 / n for n in sizes.values())
        gamma_c = {k: max(0.0, v - share * gamma_e.get(k, 0.0)) for k, v in gamma_m.items()}
        cluster = fit_variogram(gamma_c, {k: float(n) for k, n in pairs_m.items()})
    lag1 = gamma_e.get(1, 0.0)
    unit_scale = {}
    for u in panel.units:
        d = dev.get(u, {})
        ts = sorted(d)
        pairs = [(d[b] - d[a]) ** 2 for a, b in zip(ts, ts[1:]) if b - a == 1]
        s2 = (math.fsum(pairs) / 2.0 + SHRINK * lag1) / (len(pairs) + SHRINK) if lag1 > 0 else 1.0
        unit_scale[u] = s2 / lag1 if lag1 > 0 else 1.0
    return {'model': 'variogram', 'unit': unit, 'cluster': cluster, 'unit_scale': unit_scale,
            'untreated_cells': sum(len(s) for s in cells.values()), 'max_lag': max_lag,
            'cluster_periods_with_two_or_more': len(sizes)}


def _variogram_path(rng, periods, fit, scale):
    lo, hi = periods[0], periods[-1]
    ar_sd = math.sqrt(fit['ar1_variance'] * scale)
    rw_sd = math.sqrt(fit['random_walk_innovation_variance'] * scale)
    slope = rng.gauss(0.0, math.sqrt(fit['trend_slope_variance'] * scale))
    rho = fit['ar1_rho']
    x = rng.gauss(0.0, ar_sd)
    walk = 0.0
    out = {}
    for t in range(lo, hi + 1):
        if t > lo:
            x = (rho * x + rng.gauss(0.0, ar_sd * math.sqrt(1 - rho * rho))) if rho else rng.gauss(0.0, ar_sd)
            walk += rng.gauss(0.0, rw_sd)
        out[t] = x + walk + slope * (t - lo)
    return out


def simulate_variogram_panel(panel, params, seed):
    """Synthetic null panel under the amended model."""
    rng = random.Random(seed)
    shocks = {}
    if params.get('cluster'):
        for k in sorted({panel.clusters[u] for u in panel.units}, key=str):
            shocks[k] = _variogram_path(rng, panel.periods, params['cluster'], 1.0)
    outcomes = {}
    for u in panel.units:
        ts = sorted(panel.outcomes[u])
        path = _variogram_path(rng, ts, params['unit'], params['unit_scale'].get(u, 1.0))
        c = shocks.get(panel.clusters[u])
        outcomes[u] = {t: path[t] + (c[t] if c else 0.0) for t in ts}
    return panel.replace(outcomes=outcomes)


def null_draw(panel, spec, params, seed):
    """One synthetic replication of a registered design's primary estimator and placebo-date test."""
    fake = simulate_null_panel(panel, params, seed)
    common = dict(control_group=spec['control_group'], anticipation=spec.get('anticipation', 0), alpha=spec.get('alpha', 0.05))
    cohorts = spec.get('cohorts')
    res = event_study(fake, e_min=spec['e_min'], e_max=spec['e_max'], post=spec.get('post'), bootstrap=0,
                      cohorts=cohorts, **common)
    overall = res['overall']
    out = {'seed': seed, 'att': overall and overall['att'], 'se': overall and overall['se'],
           'pre_trend_p': res['pre_trend']['wald']['p']}
    if spec.get('placebo_shift'):
        pd = placebo_date_test(fake, shift=spec['placebo_shift'], cohorts=cohorts, **common)
        out['placebo_date_p'] = pd['p']
    return out


def _draw_worker(args):
    panel_dict, spec, params, seed = args
    return null_draw(panel_from_dict(panel_dict), spec, params, seed)


def null_draws(panel, spec, params, *, replications, seed, workers=1):
    seeds = [seed * 100003 + r for r in range(replications)]
    if workers <= 1:
        return [null_draw(panel, spec, params, s) for s in seeds]
    import multiprocessing
    payload = panel_to_dict(panel)
    with multiprocessing.get_context('fork').Pool(workers) as pool:
        return pool.map(_draw_worker, [(payload, spec, params, s) for s in seeds], chunksize=1)


# -- power and minimum detectable effect ------------------------------------------------------------------

def power_at(draws, delta, alpha=0.05):
    z = normal_ppf(1 - alpha / 2)
    usable = [d for d in draws if d.get('att') is not None and d.get('se')]
    if not usable:
        return None
    return sum(1 for d in usable if abs(d['att'] + delta) / d['se'] > z) / len(usable)


def minimum_detectable_effect(draws, *, alpha=0.05, power=0.8, sign=1.0):
    """Smallest |delta| (in the direction ``sign``) whose rejection rate reaches ``power``."""
    usable = [d for d in draws if d.get('att') is not None and d.get('se')]
    if not usable:
        return None
    hi = 20.0 * max(d['se'] for d in usable) + max(abs(d['att']) for d in usable)
    if power_at(usable, sign * hi, alpha) < power:
        return None
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if power_at(usable, sign * mid, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi


def summarize_draws(draws, *, alpha=0.05, power=0.8, real_se=None, plausible=None):
    usable = [d for d in draws if d.get('att') is not None and d.get('se')]
    pre = [d['pre_trend_p'] for d in draws if d.get('pre_trend_p') is not None]
    plc = [d['placebo_date_p'] for d in draws if d.get('placebo_date_p') is not None]
    both = [d for d in draws if d.get('pre_trend_p') is not None and d.get('placebo_date_p') is not None]
    up = minimum_detectable_effect(usable, alpha=alpha, power=power, sign=1.0)
    down = minimum_detectable_effect(usable, alpha=alpha, power=power, sign=-1.0)
    mde_sim = None if up is None or down is None else max(up, down)
    z = normal_ppf(1 - alpha / 2) + normal_ppf(power)
    mean_se = mean(d['se'] for d in usable) if usable else None
    mde_analytic = z * real_se if real_se else None
    ratio = mean_se / real_se if (mean_se and real_se) else None
    calibrated = ratio is not None and 0.67 <= ratio <= 1.5
    mde = mde_sim if calibrated else (mde_analytic if mde_analytic is not None else mde_sim)
    out = {'replications': len(draws), 'usable': len(usable),
           'null_rejection_rate': power_at(usable, 0.0, alpha) if usable else None,
           'null_att_mean': mean(d['att'] for d in usable) if usable else None,
           'null_att_sd': (math.sqrt(mean((d['att'] - mean(x['att'] for x in usable)) ** 2 for d in usable))
                           if len(usable) > 1 else None),
           'null_se_mean': mean_se,
           'pre_trend_pass_rate': sum(1 for p in pre if p >= alpha) / len(pre) if pre else None,
           'placebo_date_pass_rate': sum(1 for p in plc if p >= alpha) / len(plc) if plc else None,
           'both_diagnostics_pass_rate': (sum(1 for d in both if d['pre_trend_p'] >= alpha and d['placebo_date_p'] >= alpha)
                                          / len(both)) if both else None,
           'mde_80_simulated': mde_sim, 'mde_80_simulated_increase': up, 'mde_80_simulated_decrease': down,
           'real_se': real_se, 'mde_80_analytic_from_real_se': mde_analytic,
           'se_ratio_simulated_to_real': ratio, 'calibration_ok': calibrated,
           'mde_80': mde, 'mde_source': 'simulated' if (calibrated or mde_analytic is None) else 'analytic (calibration check failed)'}
    if plausible is not None and mde is not None:
        out['plausible_effect'] = plausible
        out['mde_over_plausible'] = mde / plausible
        out['power_at_plausible'] = min(power_at(usable, plausible, alpha), power_at(usable, -plausible, alpha))
    return out


# -- ranking test power ----------------------------------------------------------------------------------

def sign_flip_p_normal(differences):
    """One-sided sign-flip p-value by its normal approximation: z = sum(d) / sqrt(sum(d^2))."""
    ss = math.fsum(x * x for x in differences)
    if ss <= 0:
        return None
    return 0.5 * math.erfc(math.fsum(differences) / math.sqrt(ss) / math.sqrt(2.0))


def ranking_test_power(differences, *, plausible, alpha=0.05, power=0.8, draws=2000, seed=0):
    """Power of the one-sided paired sign-flip test on per-event score differences.

    Synthetic studies resample the real events' centred differences with replacement (so the null holds)
    and add a shift ``delta``; the test is the sign-flip test in its normal approximation. The MDE is the
    smallest ``delta`` whose rejection rate reaches ``power`` (bisection; the rate is monotone in delta).
    """
    d = [x for x in differences if x is not None]
    n = len(d)
    if n == 0:
        return {'events': 0, 'mde_80': None}
    centred = [x - mean(d) for x in d]
    rng = random.Random(seed)
    samples = [[rng.choice(centred) for _ in range(n)] for _ in range(draws)]

    def rate(delta):
        hits = 0
        for sample in samples:
            p = sign_flip_p_normal([x + delta for x in sample])
            hits += p is not None and p <= alpha
        return hits / draws

    lo, hi = 0.0, 1.0
    mde = None
    if rate(hi) >= power:
        for _ in range(40):
            mid = (lo + hi) / 2
            if rate(mid) >= power:
                hi = mid
            else:
                lo = mid
        mde = hi
    sd = math.sqrt(mean(x * x for x in centred))
    return {'events': n, 'difference_sd': sd, 'observed_mean_difference': mean(d), 'draws': draws,
            'test': 'one-sided paired sign-flip, normal approximation', 'null_rejection_rate': rate(0.0),
            'mde_80': mde, 'power_at_plausible': rate(plausible), 'plausible_effect': plausible,
            'mde_over_plausible': None if mde is None else mde / plausible,
            'analytic_mde_80': (normal_ppf(1 - alpha) + normal_ppf(power)) * sd / math.sqrt(n)}
