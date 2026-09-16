"""Linear regression: OLS/WLS with classical, HC0-HC3 and Newey-West (HAC) covariance; 2SLS."""
import math
from . import linalg as la
from .distributions import t_sf_two_sided, f_sf, chi2_sf
from .optimize import jacobian

COV_TYPES = ('nonrobust', 'HC0', 'HC1', 'HC2', 'HC3', 'HAC')


def newey_west_default_lags(n):
    return max(0, int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0))))


def _check(y, x):
    if not x or len(y) != len(x):
        raise ValueError('Regression requires equally many responses and design rows')
    k = len(x[0])
    if any(len(row) != k for row in x):
        raise ValueError('Ragged design matrix')
    for v in y:
        la.finite(v, 'response')
    for row in x:
        for v in row:
            la.finite(v, 'regressor')
    if len(y) <= k:
        raise ValueError(f'Regression needs more observations ({len(y)}) than parameters ({k})')
    return len(y), k


def sandwich(bread, x, u, cov_type, hac_lags=None, hat=None):
    n, k = len(x), len(x[0])
    if cov_type in ('HC0', 'HC1', 'HC2', 'HC3'):
        if cov_type in ('HC2', 'HC3'):
            if hat is None:
                hat = [la.dot(row, la.matvec(bread, row)) for row in x]
            power = 1 if cov_type == 'HC2' else 2
            weights = [ui * ui / max(1e-12, (1 - h)) ** power for ui, h in zip(u, hat)]
        else:
            weights = [ui * ui for ui in u]
        meat = la.xtwx(x, weights)
        if cov_type == 'HC1':
            meat = la.scale(meat, n / (n - k))
    elif cov_type == 'HAC':
        lags = newey_west_default_lags(n) if hac_lags is None else hac_lags
        if type(lags) is not int or lags < 0 or lags >= n:
            raise ValueError('HAC lags must be a nonnegative integer below the sample size')
        scores = [[xi * ui for xi in row] for row, ui in zip(x, u)]
        meat = la.xtwx(scores)
        for lag in range(1, lags + 1):
            weight = 1 - lag / (lags + 1)
            gamma = la.zeros(k, k)
            for t in range(lag, n):
                a, b = scores[t], scores[t - lag]
                for i in range(k):
                    for j in range(k):
                        gamma[i][j] += a[i] * b[j]
            meat = la.add(meat, la.add(gamma, la.transpose(gamma)), weight)
        meat = la.scale(meat, n / (n - k))
    else:
        raise ValueError('Unknown covariance type: ' + str(cov_type))
    return la.symmetrize(la.matmul(la.matmul(bread, meat), bread))


def _summaries(params, cov, df, names):
    se = [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(params))]
    t = [p / s if s > 0 else math.inf for p, s in zip(params, se)]
    pvalues = [t_sf_two_sided(v, df) if math.isfinite(v) else 0.0 for v in t]
    return {'params': dict(zip(names, params)), 'bse': dict(zip(names, se)),
            'tvalues': dict(zip(names, t)), 'pvalues': dict(zip(names, pvalues)),
            'cov': {'names': list(names), 'matrix': cov}}


def ols(y, x, *, names=None, weights=None, cov_type='nonrobust', hac_lags=None):
    """Least squares with optional positive weights (WLS) and robust covariance."""
    n, k = _check(y, x)
    names = list(names or [f'x{i}' for i in range(k)])
    if len(names) != k:
        raise ValueError('names must match design columns')
    if cov_type not in COV_TYPES:
        raise ValueError('cov_type must be one of ' + ', '.join(COV_TYPES))
    if weights is not None:
        if len(weights) != n or any(la.finite(w, 'weight') <= 0 for w in weights):
            raise ValueError('WLS weights must be positive and aligned with observations')
        root = [math.sqrt(w) for w in weights]
        xs = [[v * r for v in row] for row, r in zip(x, root)]
        ys = [v * r for v, r in zip(y, root)]
    else:
        xs, ys = x, y
    xtx = la.xtwx(xs)
    bread = la.inverse(xtx)
    beta = la.matvec(bread, la.xtwy(xs, ys))
    fitted = [la.dot(row, beta) for row in x]
    resid = [a - b for a, b in zip(y, fitted)]
    wresid = [a - la.dot(row, beta) for a, row in zip(ys, xs)]
    ssr = math.fsum(u * u for u in wresid)
    df = n - k
    sigma2 = ssr / df
    if cov_type == 'nonrobust':
        cov = la.scale(bread, sigma2)
    else:
        cov = sandwich(bread, xs, wresid, cov_type, hac_lags)
    ybar = (math.fsum(w * v for w, v in zip(weights, y)) / math.fsum(weights)) if weights else la.mean(y)
    tss = math.fsum((w if weights else 1.0) * (v - ybar) ** 2 for w, v in zip(weights or [1.0] * n, y))
    has_constant = any(all(abs(row[j] - x[0][j]) < 1e-12 for row in x) and x[0][j] != 0 for j in range(k))
    r2 = 1 - ssr / tss if tss > 0 else 0.0
    loglik = -n / 2 * (math.log(2 * math.pi) + math.log(max(ssr / n, 1e-300)) + 1)
    result = _summaries(beta, cov, df, names)
    result.update({'nobs': n, 'df_resid': df, 'k': k, 'resid': resid, 'fitted': fitted, 'ssr': ssr,
                   'sigma2': sigma2, 'sigma': math.sqrt(sigma2), 'r2': r2,
                   'adj_r2': 1 - (1 - r2) * (n - (1 if has_constant else 0)) / df if tss > 0 else 0.0,
                   'loglik': loglik, 'aic': -2 * loglik + 2 * k, 'bic': -2 * loglik + math.log(n) * k,
                   'cov_type': cov_type, 'hac_lags': (newey_west_default_lags(n) if hac_lags is None else hac_lags) if cov_type == 'HAC' else None,
                   'weighted': weights is not None, 'bread': bread})
    return result


def wald_test(result, restrictions, values=None):
    """Joint Wald test R b = r using the result's covariance; restrictions are name->coefficient dicts."""
    names = result['cov']['names']
    r_mat = [[row.get(name, 0.0) for name in names] for row in restrictions]
    b = [result['params'][name] for name in names]
    r = values or [0.0] * len(restrictions)
    diff = [a - c for a, c in zip(la.matvec(r_mat, b), r)]
    middle = la.inverse(la.matmul(la.matmul(r_mat, result['cov']['matrix']), la.transpose(r_mat)))
    stat = la.dot(diff, la.matvec(middle, diff))
    q = len(restrictions)
    return {'statistic': stat, 'df': q, 'chi2_pvalue': chi2_sf(stat, q),
            'f_statistic': stat / q, 'f_pvalue': f_sf(stat / q, q, result['df_resid'])}


def delta_method(func, params, cov_matrix):
    """Standard errors of g(params) with a central-difference Jacobian."""
    values = func(list(params))
    jac = jacobian(func, list(params))
    cov = la.matmul(la.matmul(jac, cov_matrix), la.transpose(jac))
    return values, [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(values))], cov


def tsls(y, exog, endog, instruments, *, exog_names=None, endog_names=None, cov_type='nonrobust', hac_lags=None):
    """Two-stage least squares. ``exog`` includes any constant; ``instruments`` are excluded instruments."""
    n = len(y)
    if not (len(exog) == len(endog) == len(instruments) == n):
        raise ValueError('2SLS inputs must align')
    kx, ke, kz = len(exog[0]), len(endog[0]), len(instruments[0])
    if kz < ke:
        raise ValueError('2SLS requires at least as many excluded instruments as endogenous regressors')
    exog_names = list(exog_names or [f'x{i}' for i in range(kx)])
    endog_names = list(endog_names or [f'endog{i}' for i in range(ke)])
    z = [a + b for a, b in zip(exog, instruments)]
    x = [a + b for a, b in zip(exog, endog)]
    _check(y, x)
    _check(y, z)
    ztz_inv = la.inverse(la.xtwx(z))
    first_stage = []
    xhat_endog_columns = []
    for j in range(ke):
        target = [row[j] for row in endog]
        full = ols(target, z)
        restricted_ssr = ols(target, exog)['ssr'] if kx else math.fsum(v * v for v in target)
        f_stat = ((restricted_ssr - full['ssr']) / kz) / (full['ssr'] / full['df_resid'])
        first_stage.append({'endogenous': endog_names[j], 'partial_f': f_stat, 'f_pvalue': f_sf(f_stat, kz, full['df_resid']),
                            'r2': full['r2'], 'weak_instrument_warning': f_stat < 10})
        xhat_endog_columns.append(full['fitted'])
    xhat = [exog[i] + [xhat_endog_columns[j][i] for j in range(ke)] for i in range(n)]
    bread = la.inverse(la.xtwx(xhat))
    beta = la.matvec(bread, la.xtwy(xhat, y))
    resid = [yi - la.dot(row, beta) for yi, row in zip(y, x)]
    k = kx + ke
    df = n - k
    sigma2 = math.fsum(u * u for u in resid) / df
    cov = la.scale(bread, sigma2) if cov_type == 'nonrobust' else sandwich(bread, xhat, resid, cov_type, hac_lags)
    names = exog_names + endog_names
    result = _summaries(beta, cov, df, names)
    overid = None
    if kz > ke:
        aux = ols(resid, z)
        stat = n * aux['r2']
        overid = {'test': 'sargan', 'statistic': stat, 'df': kz - ke, 'pvalue': chi2_sf(stat, kz - ke)}
    del ztz_inv
    result.update({'nobs': n, 'df_resid': df, 'k': k, 'resid': resid, 'sigma2': sigma2, 'sigma': math.sqrt(sigma2),
                   'first_stage': first_stage, 'overidentification': overid, 'cov_type': cov_type, 'method': '2sls'})
    return result


def add_constant(rows):
    return [[1.0] + list(row) for row in rows]
