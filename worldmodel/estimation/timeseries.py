"""AR(p), ARIMA-lite (conditional sum of squares) and VAR(p) with information-criterion lag selection."""
import math
from . import linalg as la
from .regression import ols
from .optimize import nelder_mead

CRITERIA = ('aic', 'bic')


def _series(y, label='series'):
    return [la.finite(v, label) for v in y]


def difference(y, d=1):
    out = list(y)
    for _ in range(d):
        out = [b - a for a, b in zip(out, out[1:])]
    return out


def fit_ar(y, p, *, trend='c', start=None, cov_type='nonrobust', hac_lags=None):
    """Conditional least-squares AR(p). ``start`` fixes the first target index for comparable IC."""
    y = _series(y)
    if type(p) is not int or p < 0:
        raise ValueError('AR order must be a nonnegative integer')
    start = p if start is None else start
    if start < p:
        raise ValueError('start must be at least the AR order')
    rows, targets = [], []
    for t in range(start, len(y)):
        row = ([1.0] if trend == 'c' else []) + [y[t - i] for i in range(1, p + 1)]
        rows.append(row)
        targets.append(y[t])
    names = (['const'] if trend == 'c' else []) + [f'phi{i}' for i in range(1, p + 1)]
    if not names:
        n = len(targets)
        ssr = math.fsum(v * v for v in targets)
        loglik = -n / 2 * (math.log(2 * math.pi) + math.log(ssr / n) + 1)
        return {'order': 0, 'trend': trend, 'params': {}, 'bse': {}, 'sigma2': ssr / n, 'nobs': n,
                'resid': targets, 'aic': -2 * loglik, 'bic': -2 * loglik, 'loglik': loglik, 'cov': {'names': [], 'matrix': []}}
    result = ols(targets, rows, names=names, cov_type=cov_type, hac_lags=hac_lags)
    n = result['nobs']
    sigma2_ml = result['ssr'] / n
    loglik = -n / 2 * (math.log(2 * math.pi) + math.log(max(sigma2_ml, 1e-300)) + 1)
    k = len(names) + 1  # coefficients plus innovation variance
    return {'order': p, 'trend': trend, 'params': result['params'], 'bse': result['bse'], 'pvalues': result['pvalues'],
            'cov': result['cov'], 'sigma2': result['sigma2'], 'nobs': n, 'resid': result['resid'], 'loglik': loglik,
            'aic': -2 * loglik + 2 * k, 'bic': -2 * loglik + math.log(n) * k, 'r2': result['r2']}


def select_ar_order(y, max_p, *, criterion='bic', trend='c'):
    if criterion not in CRITERIA:
        raise ValueError('criterion must be aic or bic')
    if len(y) - max_p <= max_p + 2:
        raise ValueError('Series too short for requested maximum lag')
    table = []
    for p in range(0, max_p + 1):
        fit = fit_ar(y, p, trend=trend, start=max_p)
        table.append({'order': p, 'aic': fit['aic'], 'bic': fit['bic']})
    best = min(table, key=lambda row: (row[criterion], row['order']))
    return {'order': best['order'], 'criterion': criterion, 'table': table, 'common_sample_start': max_p}


def psi_weights(ar, ma, h):
    psi = [1.0]
    for j in range(1, h):
        value = ma[j - 1] if j - 1 < len(ma) else 0.0
        value += math.fsum(ar[i] * psi[j - 1 - i] for i in range(min(j, len(ar))))
        psi.append(value)
    return psi


def ar_coefficients(fit):
    return [fit['params'].get(f'phi{i}', 0.0) for i in range(1, fit['order'] + 1)]


def ar_forecast(fit, history, h=1):
    phi = ar_coefficients(fit)
    const = fit['params'].get('const', 0.0)
    path = list(history)
    if len(path) < len(phi):
        raise ValueError('History shorter than AR order')
    means = []
    for _ in range(h):
        value = const + math.fsum(c * path[-i - 1] for i, c in enumerate(phi))
        path.append(value)
        means.append(value)
    psi = psi_weights(phi, [], h)
    sds = [math.sqrt(fit['sigma2'] * math.fsum(w * w for w in psi[:j + 1])) for j in range(h)]
    return {'mean': means, 'sd': sds}


def _integrated_ar(phi, d):
    poly = [1.0] + [-c for c in phi]
    for _ in range(d):
        poly = [a - b for a, b in zip(poly + [0.0], [0.0] + poly)]
    return [-c for c in poly[1:]]


def _css_residuals(w, const, phi, theta):
    p, q = len(phi), len(theta)
    e = [0.0] * len(w)
    for t in range(p, len(w)):
        value = w[t] - const - math.fsum(phi[i] * w[t - i - 1] for i in range(p))
        value -= math.fsum(theta[j] * e[t - j - 1] for j in range(q) if t - j - 1 >= p)
        e[t] = value
    return e[p:]


def fit_arima(y, order, *, trend='c', max_evaluations=4000):
    """ARIMA(p,d,q) by conditional sum of squares; q>0 uses bounded Nelder-Mead from the AR solution.

    MA coefficients are individually bounded in (-0.98, 0.98); joint invertibility
    for q>1 is not enforced. Constant refers to the differenced series.
    """
    p, d, q = order
    y = _series(y)
    w = difference(y, d)
    if len(w) <= p + q + 3:
        raise ValueError('Series too short for ARIMA order')
    base = fit_ar(w, p, trend=trend)
    if q == 0:
        return {**base, 'order': (p, d, 0), 'ar': ar_coefficients(base), 'ma': [], 'const': base['params'].get('const', 0.0),
                'method': 'conditional_least_squares', 'differenced_resid': base['resid']}
    x0 = ([base['params'].get('const', 0.0)] if trend == 'c' else []) + ar_coefficients(base) + [0.0] * q
    bounds = ([None] if trend == 'c' else []) + [(-2.0, 2.0)] * p + [(-0.98, 0.98)] * q

    def unpack(x):
        offset = 1 if trend == 'c' else 0
        return (x[0] if trend == 'c' else 0.0), x[offset:offset + p], x[offset + p:]

    def css(x):
        const, phi, theta = unpack(x)
        return math.fsum(e * e for e in _css_residuals(w, const, phi, theta))

    solution = nelder_mead(css, x0, bounds=bounds, max_evaluations=max_evaluations)
    const, phi, theta = unpack(solution['x'])
    resid = _css_residuals(w, const, phi, theta)
    n = len(resid)
    sigma2 = solution['fun'] / n
    k = len(x0) + 1
    loglik = -n / 2 * (math.log(2 * math.pi) + math.log(max(sigma2, 1e-300)) + 1)
    names = (['const'] if trend == 'c' else []) + [f'phi{i}' for i in range(1, p + 1)] + [f'theta{j}' for j in range(1, q + 1)]
    return {'order': (p, d, q), 'trend': trend, 'params': dict(zip(names, solution['x'])), 'ar': list(phi), 'ma': list(theta),
            'const': const, 'sigma2': sigma2, 'nobs': n, 'resid': resid, 'differenced_resid': resid, 'loglik': loglik,
            'aic': -2 * loglik + 2 * k, 'bic': -2 * loglik + math.log(n) * k, 'method': 'conditional_sum_of_squares',
            'converged': solution['converged']}


def arima_forecast(fit, history, h=1):
    p, d, q = fit['order']
    phi, theta, const = fit['ar'], fit['ma'], fit['const']
    y = list(history)
    w = difference(y, d)
    e = _css_residuals(w, const, phi, theta) if q else []
    e_full = [0.0] * (len(w) - len(e)) + list(e)
    w_path, e_path = list(w), list(e_full)
    w_means = []
    for _ in range(h):
        value = const + math.fsum(phi[i] * w_path[-i - 1] for i in range(p))
        value += math.fsum(theta[j] * e_path[-j - 1] for j in range(q) if j < len(e_path))
        w_path.append(value)
        e_path.append(0.0)
        w_means.append(value)
    means = w_means
    for level in range(d, 0, -1):
        base = difference(y, level - 1)[-1]
        integrated = []
        for value in means:
            base += value
            integrated.append(base)
        means = integrated
    psi = psi_weights(_integrated_ar(phi, d), theta, h)
    sds = [math.sqrt(fit['sigma2'] * math.fsum(v * v for v in psi[:j + 1])) for j in range(h)]
    return {'mean': means, 'sd': sds}


def select_arima(y, *, d=0, max_p=3, max_q=1, criterion='bic', trend='c'):
    table = []
    for p in range(max_p + 1):
        for q in range(max_q + 1):
            try:
                fit = fit_arima(y[max_p - p:] if q == 0 else y, (p, d, q), trend=trend)
            except ValueError:
                continue
            table.append({'order': [p, d, q], 'aic': fit['aic'], 'bic': fit['bic']})
    if not table:
        raise ValueError('No ARIMA order could be fitted')
    best = min(table, key=lambda row: (row[criterion], row['order']))
    return {'order': tuple(best['order']), 'criterion': criterion, 'table': table,
            'note': 'CSS likelihoods for different (p,q) use slightly different effective samples.'}


def fit_var(data, p, *, names=None, trend='c', start=None):
    """VAR(p) by equation-wise OLS. ``data`` is a list of observation vectors."""
    if type(p) is not int or p < 1:
        raise ValueError('VAR order must be a positive integer')
    k = len(data[0])
    names = list(names or [f'y{i}' for i in range(k)])
    start = p if start is None else start
    rows, targets = [], []
    for t in range(start, len(data)):
        row = [1.0] if trend == 'c' else []
        for lag in range(1, p + 1):
            row.extend(data[t - lag])
        rows.append(row)
        targets.append(list(data[t]))
    T = len(rows)
    m = len(rows[0])
    if T <= m:
        raise ValueError('VAR sample too short for the number of coefficients')
    bread = la.inverse(la.xtwx(rows))
    coefficients, resid_columns, bse = [], [], []
    for i in range(k):
        yi = [row[i] for row in targets]
        beta = la.matvec(bread, la.xtwy(rows, yi))
        coefficients.append(beta)
        resid_columns.append([a - la.dot(r, beta) for a, r in zip(yi, rows)])
    resid = [[resid_columns[i][t] for i in range(k)] for t in range(T)]
    sigma_ml = la.scale(la.xtwx(resid), 1.0 / T)
    sigma = la.scale(la.xtwx(resid), 1.0 / (T - m))
    for i in range(k):
        bse.append([math.sqrt(max(sigma[i][i] * bread[j][j], 0.0)) for j in range(m)])
    logdet = la.log_det_spd(sigma_ml)
    free = k * m
    const = [c[0] for c in coefficients] if trend == 'c' else [0.0] * k
    offset = 1 if trend == 'c' else 0
    lags = [[[coefficients[i][offset + (lag * k) + j] for j in range(k)] for i in range(k)] for lag in range(p)]
    loglik = -T * k / 2 * math.log(2 * math.pi) - T / 2 * logdet - T * k / 2
    return {'order': p, 'names': names, 'const': const, 'lags': lags, 'sigma': sigma, 'sigma_ml': sigma_ml,
            'nobs': T, 'resid': resid, 'loglik': loglik, 'bse': bse,
            'aic': logdet + 2 * free / T, 'bic': logdet + math.log(T) * free / T, 'trend': trend,
            'stable': var_is_stable(lags)}


def select_var_order(data, max_p, *, criterion='bic', trend='c'):
    table = []
    for p in range(1, max_p + 1):
        fit = fit_var(data, p, trend=trend, start=max_p)
        table.append({'order': p, 'aic': fit['aic'], 'bic': fit['bic']})
    best = min(table, key=lambda row: (row[criterion], row['order']))
    return {'order': best['order'], 'criterion': criterion, 'table': table, 'common_sample_start': max_p}


def _companion_power_norm(lags, power=200):
    k, p = len(lags[0]), len(lags)
    size = k * p
    companion = la.zeros(size, size)
    for lag in range(p):
        for i in range(k):
            for j in range(k):
                companion[i][lag * k + j] = lags[lag][i][j]
    for i in range(k * (p - 1)):
        companion[k + i][i] = 1.0
    vector = [1.0] * size
    growth = 0.0
    for _ in range(power):
        vector = la.matvec(companion, vector)
        norm = math.sqrt(la.dot(vector, vector))
        if norm == 0 or not math.isfinite(norm):
            return 0.0 if norm == 0 else math.inf
        vector = [v / norm for v in vector]
        growth = norm
    return growth


def var_is_stable(lags):
    """Power-iteration estimate of the companion spectral radius (< 1 means stable)."""
    return _companion_power_norm(lags) < 1.0


def var_forecast(fit, history, h=1):
    k, p = len(fit['names']), fit['order']
    path = [list(v) for v in history]
    if len(path) < p:
        raise ValueError('History shorter than VAR order')
    means = []
    for _ in range(h):
        value = list(fit['const'])
        for lag in range(p):
            previous = path[-lag - 1]
            for i in range(k):
                value[i] += la.dot(fit['lags'][lag][i], previous)
        path.append(value)
        means.append(value)
    phi = [la.identity(k)]
    for j in range(1, h):
        total = la.zeros(k, k)
        for lag in range(1, min(j, p) + 1):
            total = la.add(total, la.matmul(fit['lags'][lag - 1], phi[j - lag]))
        phi.append(total)
    covariances, running = [], la.zeros(k, k)
    for j in range(h):
        running = la.add(running, la.matmul(la.matmul(phi[j], fit['sigma']), la.transpose(phi[j])))
        covariances.append(running)
    return {'mean': means, 'cov': covariances, 'sd': [[math.sqrt(c[i][i]) for i in range(k)] for c in covariances]}
