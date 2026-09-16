"""Binary/grouped hazard models (logit, cloglog), Poisson GLM and PPML gravity models."""
import math
from . import linalg as la
from .distributions import t_sf_two_sided, norm_cdf

LINKS = ('logit', 'cloglog')


def _inv_link(eta, link):
    if link == 'logit':
        if eta >= 0:
            z = math.exp(-eta)
            return 1 / (1 + z)
        z = math.exp(eta)
        return z / (1 + z)
    return -math.expm1(-math.exp(min(eta, 700)))


def _dmu(eta, mu, link):
    if link == 'logit':
        return mu * (1 - mu)
    return math.exp(min(eta, 700) - math.exp(min(eta, 700)))


def _summ(beta, cov, names, df=None):
    se = [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(beta))]
    z = [b / s if s > 0 else math.inf for b, s in zip(beta, se)]
    p = [2 * (1 - norm_cdf(abs(v))) if df is None else t_sf_two_sided(v, df) for v in z]
    return {'params': dict(zip(names, beta)), 'bse': dict(zip(names, se)), 'zvalues': dict(zip(names, z)),
            'pvalues': dict(zip(names, p)), 'cov': {'names': list(names), 'matrix': cov}}


def binomial_glm(y, x, *, names=None, trials=None, link='logit', cov_type='nonrobust', max_iter=100, tol=1e-10):
    """Maximum-likelihood binary or grouped-proportion model.

    ``y`` holds 0/1 outcomes or proportions in [0,1]; ``trials`` are group sizes
    (exposures). Fractional outcomes with ``cov_type='HC0'`` give the
    Papke-Wooldridge quasi-likelihood fractional logit.
    """
    if link not in LINKS:
        raise ValueError('link must be logit or cloglog')
    n, k = len(y), len(x[0])
    names = list(names or [f'x{i}' for i in range(k)])
    trials = [1.0] * n if trials is None else [la.finite(v, 'trials') for v in trials]
    if any(t <= 0 for t in trials):
        raise ValueError('trials must be positive')
    for v in y:
        if not 0 <= la.finite(v, 'outcome') <= 1:
            raise ValueError('Outcomes must be probabilities or 0/1 indicators')
    total = math.fsum(trials)
    pbar = min(1 - 1e-6, max(1e-6, math.fsum(t * v for t, v in zip(trials, y)) / total))
    beta = [0.0] * k
    intercept = next((j for j in range(k) if all(row[j] == 1 for row in x)), None)
    if intercept is not None:
        beta[intercept] = math.log(pbar / (1 - pbar)) if link == 'logit' else math.log(-math.log(1 - pbar))
    loglik_old = -math.inf
    converged = False
    for iteration in range(max_iter):
        eta = [la.dot(row, beta) for row in x]
        mu = [min(1 - 1e-15, max(1e-15, _inv_link(e, link))) for e in eta]
        d = [max(_dmu(e, m, link), 1e-300) for e, m in zip(eta, mu)]
        w = [t * dd * dd / (m * (1 - m)) for t, dd, m in zip(trials, d, mu)]
        z = [e + (v - m) / dd for e, v, m, dd in zip(eta, y, mu, d)]
        info = la.xtwx(x, w)
        try:
            beta_new = la.solve(info, la.xtwy(x, z, w))
        except ValueError as error:
            raise ValueError('Binomial GLM information matrix is singular (perfect or quasi-complete separation, or collinear regressors)') from error
        if max(abs(b) for b in beta_new) > 60:
            raise ValueError('Binomial GLM diverged (perfect or quasi-complete separation?)')
        eta = [la.dot(row, beta_new) for row in x]
        mu = [min(1 - 1e-15, max(1e-15, _inv_link(e, link))) for e in eta]
        loglik = math.fsum(t * (v * math.log(m) + (1 - v) * math.log(1 - m)) for t, v, m in zip(trials, y, mu))
        beta = beta_new
        if abs(loglik - loglik_old) < tol * (abs(loglik) + tol):
            converged = True
            break
        loglik_old = loglik
    eta = [la.dot(row, beta) for row in x]
    mu = [min(1 - 1e-15, max(1e-15, _inv_link(e, link))) for e in eta]
    d = [max(_dmu(e, m, link), 1e-300) for e, m in zip(eta, mu)]
    w = [t * dd * dd / (m * (1 - m)) for t, dd, m in zip(trials, d, mu)]
    bread = la.inverse(la.xtwx(x, w))
    if cov_type == 'nonrobust':
        cov = bread
    elif cov_type == 'HC0':
        score = [t * (v - m) * dd / (m * (1 - m)) for t, v, m, dd in zip(trials, y, mu, d)]
        meat = la.xtwx(x, [s * s for s in score])
        cov = la.symmetrize(la.matmul(la.matmul(bread, meat), bread))
    else:
        raise ValueError('cov_type must be nonrobust or HC0')
    null = math.fsum(t * (v * math.log(pbar) + (1 - v) * math.log(1 - pbar)) for t, v in zip(trials, y))
    loglik = math.fsum(t * (v * math.log(m) + (1 - v) * math.log(1 - m)) for t, v, m in zip(trials, y, mu))
    result = _summ(beta, cov, names)
    result.update({'link': link, 'nobs': n, 'total_trials': total, 'fitted': mu, 'loglik': loglik, 'null_loglik': null,
                   'pseudo_r2': 1 - loglik / null if null else 0.0, 'aic': -2 * loglik + 2 * k,
                   'bic': -2 * loglik + math.log(total) * k, 'converged': converged, 'iterations': iteration + 1,
                   'cov_type': cov_type})
    return result


def logit(y, x, **kwargs):
    return binomial_glm(y, x, link='logit', **kwargs)


def discrete_hazard(events, exposures, x, *, names=None, link='cloglog', cov_type='HC0'):
    """Grouped discrete-time hazard: events out of at-risk exposures per period/group."""
    if len(events) != len(exposures):
        raise ValueError('events and exposures must align')
    proportions = []
    for e, n in zip(events, exposures):
        if n <= 0 or e < 0 or e > n:
            raise ValueError('Events must lie in [0, exposure] with positive exposure')
        proportions.append(e / n)
    result = binomial_glm(proportions, x, names=names, trials=exposures, link=link, cov_type=cov_type)
    result['model'] = 'discrete_time_hazard'
    return result


def hazard_probability(params, row, names, link):
    eta = math.fsum(params[n] * v for n, v in zip(names, row))
    return _inv_link(eta, link)


def poisson_glm(y, x, *, names=None, offset=None, cov_type='HC0', max_iter=100, tol=1e-10):
    """Poisson (pseudo-)maximum likelihood; ``cov_type='HC0'`` is the PPML sandwich."""
    n, k = len(y), len(x[0])
    names = list(names or [f'x{i}' for i in range(k)])
    offset = [0.0] * n if offset is None else [la.finite(v, 'offset') for v in offset]
    for v in y:
        if la.finite(v, 'count') < 0:
            raise ValueError('Poisson outcomes must be nonnegative')
    ybar = max(la.mean(y), 1e-8)
    beta = [0.0] * k
    intercept = next((j for j in range(k) if all(row[j] == 1 for row in x)), None)
    if intercept is not None:
        beta[intercept] = math.log(ybar) - la.mean(offset)
    old = math.inf
    converged = False
    for iteration in range(max_iter):
        eta = [la.dot(row, beta) + o for row, o in zip(x, offset)]
        mu = [math.exp(min(e, 700)) for e in eta]
        z = [e - o + (v - m) / m for e, o, v, m in zip(eta, offset, y, mu)]
        beta = la.solve(la.xtwx(x, mu), la.xtwy(x, z, mu))
        mu = [math.exp(min(la.dot(row, beta) + o, 700)) for row, o in zip(x, offset)]
        deviance = 2 * math.fsum((v * math.log(v / m) if v > 0 else 0.0) - (v - m) for v, m in zip(y, mu))
        if abs(deviance - old) < tol * (abs(deviance) + tol):
            converged = True
            break
        old = deviance
    mu = [math.exp(min(la.dot(row, beta) + o, 700)) for row, o in zip(x, offset)]
    bread = la.inverse(la.xtwx(x, mu))
    if cov_type == 'nonrobust':
        cov = bread
    elif cov_type == 'HC0':
        cov = la.symmetrize(la.matmul(la.matmul(bread, la.xtwx(x, [(v - m) ** 2 for v, m in zip(y, mu)])), bread))
    else:
        raise ValueError('cov_type must be nonrobust or HC0')
    loglik = math.fsum(v * math.log(m) - m - math.lgamma(v + 1) for v, m in zip(y, mu))
    result = _summ(beta, cov, names)
    result.update({'nobs': n, 'fitted': mu, 'deviance': 2 * math.fsum((v * math.log(v / m) if v > 0 else 0.0) - (v - m) for v, m in zip(y, mu)),
                   'loglik': loglik, 'converged': converged, 'iterations': iteration + 1, 'cov_type': cov_type})
    return result


def gravity_ppml(flows, *, origins, destinations, distance, origin_mass=None, destination_mass=None,
                 fixed_effects=False, extra=None, extra_names=None):
    """PPML gravity model: E[flow_od] = exp(b0 + b1 log M_o + b2 log M_d - b3 log dist_od + ...).

    With ``fixed_effects=True`` origin and destination indicator columns replace the
    masses (one of each dropped), the structural multilateral-resistance form.
    """
    n = len(flows)
    if not (len(origins) == len(destinations) == len(distance) == n):
        raise ValueError('Gravity inputs must align')
    if any(d <= 0 for d in distance):
        raise ValueError('Distances must be positive')
    names = ['const', 'log_distance']
    rows = [[1.0, math.log(d)] for d in distance]
    if fixed_effects:
        origin_levels = sorted(set(origins))[1:]
        destination_levels = sorted(set(destinations))[1:]
        for i in range(n):
            rows[i] += [1.0 if origins[i] == o else 0.0 for o in origin_levels]
            rows[i] += [1.0 if destinations[i] == d else 0.0 for d in destination_levels]
        names += [f'origin:{o}' for o in origin_levels] + [f'destination:{d}' for d in destination_levels]
    else:
        if origin_mass is None or destination_mass is None:
            raise ValueError('Masses are required without fixed effects')
        for i in range(n):
            rows[i] += [math.log(origin_mass[i]), math.log(destination_mass[i])]
        names += ['log_origin_mass', 'log_destination_mass']
    if extra:
        for i in range(n):
            rows[i] += list(extra[i])
        names += list(extra_names or [f'extra{j}' for j in range(len(extra[0]))])
    result = poisson_glm(flows, rows, names=names, cov_type='HC0')
    result['model'] = 'gravity_ppml'
    result['distance_elasticity'] = result['params']['log_distance']
    return result


def poisson_rate(events, exposure, confidence=0.9):
    if exposure <= 0 or events < 0:
        raise ValueError('Rate requires positive exposure and nonnegative events')
    rate = events / exposure
    from .distributions import norm_ppf
    z = norm_ppf(0.5 + confidence / 2)
    se_log = 1 / math.sqrt(events) if events > 0 else math.inf
    interval = [rate * math.exp(-z * se_log), rate * math.exp(z * se_log)] if events > 0 else [0.0, 3.0 / exposure]
    return {'rate': rate, 'se': math.sqrt(events) / exposure, 'interval': interval, 'confidence': confidence}
