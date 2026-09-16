"""Shared contracts and numerical helpers for estimable political/market model families.

Pure-Python reference implementations are always available. numpy is used only
where a module explicitly offers a vectorized path and it imports cleanly.

Family convention (enforced by ``validate_family``)::

    FAMILY = {'id', 'title', 'description', 'parameters', 'requirements',
              'identification', 'validated': False, 'limitations'}
    fit(data, cutoff=None) -> {'estimate', 'diagnostics', 'evidence'}
    simulate(config, mode='deterministic'|'stochastic', seed=0) -> report
    synthetic(seed=0) -> {'data', 'truth', 'config'}
"""
from copy import deepcopy
import hashlib
import json
import math
import random
from statistics import NormalDist

try:  # Optional acceleration only; every caller has a pure-Python path.
    import numpy as _np
except ImportError:  # pragma: no cover - depends on environment
    _np = None

NUMPY = _np
UNIT_NORMAL = NormalDist()
PARAMETER_SOURCES = {'fit', 'external', 'assumed', 'data'}


def numpy_available():
    return _np is not None


# ----------------------------------------------------------------------------- validation

def finite(value, name, low=-math.inf, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite in [{low}, {high}]')
    return value


def integer(value, name, low=0, high=10**9):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low}, {high}]')
    return value


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()


def parameter(value, unit, description, *, bounds=None, source='fit', series=()):
    """Declare an estimable parameter with units and provenance of its estimate."""
    if source not in PARAMETER_SOURCES:
        raise ValueError('Unknown parameter source')
    result = {'value': deepcopy(value), 'unit': unit, 'description': description, 'source': source}
    if bounds is not None:
        result['bounds'] = list(bounds)
    if series:
        result['series'] = list(series)
    return result


def requirement(rid, publisher, dataset, series, url, *, frequency, role='estimation', parameters=(), access='public', notes=None):
    result = {'id': rid, 'publisher': publisher, 'dataset': dataset, 'series': list(series), 'url': url,
              'frequency': frequency, 'role': role, 'parameters': list(parameters), 'access': access}
    if notes:
        result['notes'] = notes
    return result


def validate_family(family):
    required = {'id', 'title', 'description', 'parameters', 'requirements', 'identification', 'validated', 'limitations'}
    missing = required - set(family)
    if missing:
        raise ValueError(f'Model family missing fields: {sorted(missing)}')
    if family['validated'] is not False:
        raise ValueError('Model families default to validated: false until the estimation layer validates them')
    if not isinstance(family['parameters'], dict) or not family['parameters']:
        raise ValueError('Model family requires parameter declarations')
    for name, spec in family['parameters'].items():
        if not isinstance(spec, dict) or not {'value', 'unit', 'description', 'source'} <= set(spec):
            raise ValueError(f'Parameter {name} requires value, unit, description and source')
    ids = set()
    for item in family['requirements']:
        if not {'id', 'publisher', 'dataset', 'series', 'url', 'frequency', 'role'} <= set(item) or item['id'] in ids:
            raise ValueError('Requirements need unique id, publisher, dataset, series, url, frequency and role')
        ids.add(item['id'])
        for name in item.get('parameters', []):
            if name not in family['parameters']:
                raise ValueError(f'Requirement {item["id"]} references undeclared parameter {name}')
    json.dumps(family, allow_nan=False)
    return family


def default_parameters(family):
    return {name: deepcopy(spec['value']) for name, spec in family['parameters'].items()}


def merged_parameters(family, supplied):
    supplied = supplied or {}
    if not isinstance(supplied, dict):
        raise ValueError('parameters must be an object')
    unknown = set(supplied) - set(family['parameters'])
    if unknown:
        raise ValueError(f'Unknown parameters for {family["id"]}: {sorted(unknown)}')
    result = default_parameters(family)
    result.update(deepcopy(supplied))
    return result


# ----------------------------------------------------------------------------- time cutoffs

def _time(value):
    from ..model import instant
    if isinstance(value, int) and not isinstance(value, bool):
        value = f'{value:04d}-12-31'
    if isinstance(value, str) and len(value) == 7:
        value = value + '-01'
    return instant(value)


def apply_cutoff(rows, cutoff, *, time_key='date', label='rows'):
    """Keep rows whose information date is at or before the cutoff; never peek later."""
    if not isinstance(rows, list):
        raise ValueError(f'{label} must be a list')
    if cutoff is None:
        kept = list(rows)
        latest = max((str(r.get(time_key)) for r in rows if r.get(time_key) is not None), default=None)
        return kept, {'label': label, 'cutoff': None, 'rows_total': len(rows), 'rows_used': len(kept),
                      'rows_excluded_after_cutoff': 0, 'latest_time_used': latest}
    limit = _time(cutoff)
    kept, excluded, latest = [], 0, None
    for row in rows:
        if not isinstance(row, dict) or time_key not in row:
            raise ValueError(f'{label} rows require {time_key} when a cutoff is supplied')
        at = _time(row[time_key])
        if at <= limit:
            kept.append(row)
            latest = at if latest is None or at > latest else latest
        else:
            excluded += 1
    return kept, {'label': label, 'cutoff': cutoff, 'rows_total': len(rows), 'rows_used': len(kept),
                  'rows_excluded_after_cutoff': excluded,
                  'latest_time_used': latest.isoformat() if latest else None}


def fit_result(estimate, diagnostics, *, method, identification, windows, data, family_id, requirements=(), extra=None):
    evidence = {'family': family_id, 'method': method, 'identification': identification, 'validated': False,
                'data_windows': windows, 'data_sha256': json_digest(data),
                'requirements': list(requirements),
                'note': 'Estimates are conditional on supplied rows and cutoff; validation belongs to worldmodel.estimation.'}
    if extra:
        evidence.update(extra)
    result = {'estimate': estimate, 'diagnostics': diagnostics, 'evidence': evidence}
    json.dumps(result, allow_nan=False)
    return result


# ----------------------------------------------------------------------------- dense linear algebra

def solve(matrix, vector):
    """Gaussian elimination with partial pivoting. Raises on (near-)singular systems."""
    n = len(matrix)
    if any(len(row) != n for row in matrix) or len(vector) != n:
        raise ValueError('solve requires a square system')
    if _np is not None and n > 24:
        try:
            return [float(v) for v in _np.linalg.solve(_np.asarray(matrix, float), _np.asarray(vector, float))]
        except _np.linalg.LinAlgError as error:
            raise ValueError('Singular linear system') from error
    a = [list(map(float, row)) + [float(vector[i])] for i, row in enumerate(matrix)]
    scale = max((abs(v) for row in matrix for v in row), default=0.0) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) <= 1e-13 * scale:
            raise ValueError('Singular linear system')
        a[col], a[pivot] = a[pivot], a[col]
        inv = 1.0 / a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] * inv
            if factor:
                row, prow = a[r], a[col]
                for c in range(col, n + 1):
                    row[c] -= factor * prow[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def inverse(matrix):
    n = len(matrix)
    columns = [solve(matrix, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def cholesky(matrix, jitter=0.0):
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            total = matrix[i][j] + (jitter if i == j else 0.0) - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if total <= 0:
                    raise ValueError('Matrix is not positive definite')
                lower[i][j] = math.sqrt(total)
            else:
                lower[i][j] = total / lower[j][j]
    return lower


def weighted_cross(X, weights=None):
    k = len(X[0]) if X else 0
    xtx = [[0.0] * k for _ in range(k)]
    for idx, row in enumerate(X):
        w = 1.0 if weights is None else weights[idx]
        for a in range(k):
            wa = w * row[a]
            if wa:
                target = xtx[a]
                for b in range(a, k):
                    target[b] += wa * row[b]
    for a in range(k):
        for b in range(a):
            xtx[a][b] = xtx[b][a]
    return xtx


def ols(X, y, *, weights=None, ridge=0.0, names=None, robust=True, clusters=None):
    """Weighted least squares with homoskedastic, HC1 or cluster-robust (CR1) standard errors."""
    n, k = len(y), len(X[0]) if X else 0
    if n != len(X) or n <= k or k == 0:
        raise ValueError('OLS requires more observations than regressors')
    if _np is not None and n * k > 20000 and weights is None and clusters is None:
        A, b = _np.asarray(X, float), _np.asarray(y, float)
        xtx = (A.T @ A + ridge * _np.eye(k)).tolist()
        xty = (A.T @ b).tolist()
    else:
        xtx = weighted_cross(X, weights)
        for a in range(k):
            xtx[a][a] += ridge
        xty = [sum((1.0 if weights is None else weights[i]) * X[i][a] * y[i] for i in range(n)) for a in range(k)]
    coef = solve(xtx, xty)
    fitted = [sum(c * v for c, v in zip(coef, row)) for row in X]
    resid = [yi - fi for yi, fi in zip(y, fitted)]
    w = [1.0] * n if weights is None else weights
    sse = sum(wi * r * r for wi, r in zip(w, resid))
    mean = sum(wi * yi for wi, yi in zip(w, y)) / sum(w)
    sst = sum(wi * (yi - mean) ** 2 for wi, yi in zip(w, y))
    bread = inverse(xtx)
    sigma2 = sse / (n - k)
    if clusters is not None:
        groups = {}
        for i in range(n):
            score = groups.setdefault(clusters[i], [0.0] * k)
            for a in range(k):
                score[a] += w[i] * X[i][a] * resid[i]
        g = len(groups)
        if g < 2:
            raise ValueError('Cluster-robust errors require at least two clusters')
        meat = [[sum(s[a] * s[b] for s in groups.values()) for b in range(k)] for a in range(k)]
        factor = (g / (g - 1)) * ((n - 1) / (n - k))
        kind = 'cluster_robust_cr1'
    elif robust:
        meat = weighted_cross(X, [w[i] * w[i] * resid[i] * resid[i] for i in range(n)])
        factor = n / (n - k)
        kind = 'hc1'
    else:
        meat, factor, kind = None, 1.0, 'homoskedastic'
    if meat is None:
        cov = [[sigma2 * v for v in row] for row in bread]
    else:
        half = [[sum(bread[a][c] * meat[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
        cov = [[factor * sum(half[a][c] * bread[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
    se = [math.sqrt(max(cov[a][a], 0.0)) for a in range(k)]
    names = names or [f'x{a}' for a in range(k)]
    return {'coefficients': dict(zip(names, coef)), 'standard_errors': dict(zip(names, se)), 'covariance': cov,
            'names': names, 'residuals': resid, 'fitted': fitted, 'sigma': math.sqrt(sigma2),
            'r2': 1 - sse / sst if sst > 0 else None, 'n': n, 'k': k, 'se_type': kind}


def demean(columns, groups_list, weights=None, tol=1e-10, max_iter=1000):
    """Alternating projections: residualize columns on one or more sets of fixed effects."""
    n = len(groups_list[0]) if groups_list else 0
    w = [1.0] * n if weights is None else weights
    out = [list(map(float, col)) for col in columns]
    if not groups_list:
        return out, 0
    for iteration in range(1, max_iter + 1):
        change = 0.0
        for groups in groups_list:
            for col in out:
                sums, totals = {}, {}
                for i, g in enumerate(groups):
                    sums[g] = sums.get(g, 0.0) + w[i] * col[i]
                    totals[g] = totals.get(g, 0.0) + w[i]
                for i, g in enumerate(groups):
                    shift = sums[g] / totals[g]
                    col[i] -= shift
                    change = max(change, abs(shift))
        if len(groups_list) == 1 or change < tol:
            return out, iteration
    raise ValueError('Fixed-effect demeaning did not converge')


# ----------------------------------------------------------------------------- scalar helpers

def logistic(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def log1pexp(z):
    return z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))


def normal_cdf(x):
    return UNIT_NORMAL.cdf(x)


def quantiles(values, probabilities=(0.05, 0.5, 0.95)):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('Quantiles require values')
    result = {}
    for p in probabilities:
        position = p * (len(ordered) - 1)
        lo, hi = math.floor(position), math.ceil(position)
        result[f'q{round(p * 100):02d}'] = ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)
    return result


def normal_quadrature(order=64):
    """Equal-weight quantile nodes for E[f(Z)], Z~N(0,1); deterministic and bounded."""
    order = integer(order, 'quadrature order', 2, 100000)
    return [UNIT_NORMAL.inv_cdf((k + 0.5) / order) for k in range(order)], [1.0 / order] * order


def poisson_binomial(probabilities):
    """Exact distribution of the number of successes among independent Bernoulli trials."""
    dist = [1.0]
    for p in probabilities:
        p = finite(p, 'probability', 0, 1)
        nxt = [0.0] * (len(dist) + 1)
        q = 1 - p
        for k, mass in enumerate(dist):
            nxt[k] += mass * q
            nxt[k + 1] += mass * p
        dist = nxt
    return dist


# ----------------------------------------------------------------------------- random variates

def rng_from(seed):
    if isinstance(seed, random.Random):
        return seed
    return random.Random(integer(seed, 'seed', -2**63, 2**63))


def poisson(rng, lam):
    lam = finite(lam, 'Poisson mean', 0)
    if lam == 0:
        return 0
    if lam < 30:
        limit, k, product = math.exp(-lam), 0, rng.random()
        while product > limit:
            k += 1
            product *= rng.random()
        return k
    # Hormann (1993) PTRS transformed rejection; exact for lam >= 10.
    slam, loglam = math.sqrt(lam), math.log(lam)
    b = 0.931 + 2.53 * slam
    a = -0.059 + 0.02483 * b
    inv_alpha = 1.1239 + 1.1328 / (b - 3.4)
    vr = 0.9277 - 3.6224 / (b - 2)
    while True:
        u = rng.random() - 0.5
        v = rng.random()
        us = 0.5 - abs(u)
        k = math.floor((2 * a / us + b) * u + lam + 0.43)
        if us >= 0.07 and v <= vr:
            return k
        if k < 0 or (us < 0.013 and v > us):
            continue
        if math.log(v) + math.log(inv_alpha) - math.log(a / (us * us) + b) <= -lam + k * loglam - math.lgamma(k + 1):
            return k


def negative_binomial(rng, mean, alpha):
    """NB2 draw with variance mean + alpha*mean^2 (gamma-Poisson mixture)."""
    mean, alpha = finite(mean, 'mean', 0), finite(alpha, 'alpha', 0)
    if alpha == 0 or mean == 0:
        return poisson(rng, mean)
    return poisson(rng, rng.gammavariate(1 / alpha, alpha * mean))


def multinomial(rng, count, probabilities):
    remaining, mass, draws = count, 1.0, []
    for i, p in enumerate(probabilities):
        if i == len(probabilities) - 1:
            draws.append(remaining)
            break
        share = 0.0 if mass <= 0 else min(1.0, max(0.0, p / mass))
        drawn = binomial(rng, remaining, share)
        draws.append(drawn)
        remaining -= drawn
        mass -= p
    return draws


def binomial(rng, n, p):
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    if n < 64:
        return sum(1 for _ in range(n) if rng.random() < p)
    if hasattr(rng, 'binomialvariate'):
        return rng.binomialvariate(n, p)
    mean, sd = n * p, math.sqrt(n * p * (1 - p))  # pragma: no cover - Python < 3.12
    return max(0, min(n, round(rng.gauss(mean, sd))))


# ----------------------------------------------------------------------------- count regressions

def poisson_fe(y, X, fixed_effects=(), *, offset=None, names=None, tol=1e-9, max_iter=200, clusters=None):
    """Poisson pseudo-maximum likelihood (PPML) with high-dimensional fixed effects.

    Fixed effects are concentrated out with their closed-form Poisson score
    equations; slopes take Newton steps on mu-weighted FE-residualized regressors.
    Without fixed effects a constant is used. Groups whose outcomes are all zero
    are dropped (perfect separation) and reported. Standard errors are
    heteroskedasticity-robust sandwich, or cluster-robust when ``clusters`` given.
    """
    n = len(y)
    k = len(X[0]) if n and X and X[0] else 0
    offset = list(offset) if offset is not None else [0.0] * n
    groups_list = [list(g) for g in fixed_effects] or [[0] * n]
    keep = [True] * n
    for groups in groups_list:
        if len(groups) != n:
            raise ValueError('Fixed-effect vectors must match outcome length')
        totals = {}
        for i, g in enumerate(groups):
            totals[g] = totals.get(g, 0.0) + y[i]
        for i, g in enumerate(groups):
            if totals[g] <= 0:
                keep[i] = False
    idx = [i for i in range(n) if keep[i]]
    dropped = n - len(idx)
    y = [float(finite(y[i], 'count outcome', 0)) for i in idx]
    X = [[float(v) for v in X[i]] for i in idx] if k else [[] for _ in idx]
    offset = [float(offset[i]) for i in idx]
    groups_list = [[groups[i] for i in idx] for groups in groups_list]
    clusters = [clusters[i] for i in idx] if clusters is not None else None
    n = len(y)
    if n <= k + 1:
        raise ValueError('Too few informative observations for Poisson regression')
    beta = [0.0] * k
    fe = [{g: 0.0 for g in groups} for groups in groups_list]

    def eta_of():
        return [offset[i] + sum(b * v for b, v in zip(beta, X[i])) + sum(fe[d][groups_list[d][i]] for d in range(len(fe)))
                for i in range(n)]

    def sweep(eta):
        for _ in range(1000):
            biggest = 0.0
            for d, groups in enumerate(groups_list):
                ysum, musum = {}, {}
                for i, g in enumerate(groups):
                    ysum[g] = ysum.get(g, 0.0) + y[i]
                    musum[g] = musum.get(g, 0.0) + math.exp(min(eta[i], 700))
                shifts = {g: math.log(ysum[g] / musum[g]) for g in ysum}
                for g, value in shifts.items():
                    fe[d][g] += value
                    biggest = max(biggest, abs(value))
                for i, g in enumerate(groups):
                    eta[i] += shifts[g]
            if len(groups_list) == 1 or biggest < 1e-11:
                break
        return eta

    converged, iterations, step_size = False, 0, 0.0
    eta = sweep(eta_of())
    for iterations in range(1, max_iter + 1):
        mu = [math.exp(min(e, 700)) for e in eta]
        if not k:
            converged = True
            break
        columns, _ = demean([[X[i][a] for i in range(n)] for a in range(k)], groups_list, weights=mu, tol=1e-12)
        Xt = [[columns[a][i] for a in range(k)] for i in range(n)]
        gradient = [sum(Xt[i][a] * (y[i] - mu[i]) for i in range(n)) for a in range(k)]
        step = solve(weighted_cross(Xt, mu), gradient)
        step_size = max(abs(s) for s in step)
        factor = 1.0 if step_size <= 1 else 1 / step_size
        beta = [b + factor * s for b, s in zip(beta, step)]
        eta = sweep(eta_of())
        if step_size * factor < tol:
            converged = True
            break
    mu = [math.exp(min(e, 700)) for e in eta]
    loglik = sum(y[i] * eta[i] - mu[i] - math.lgamma(y[i] + 1) for i in range(n))
    cov, se = None, []
    if k:
        columns, _ = demean([[X[i][a] for i in range(n)] for a in range(k)], groups_list, weights=mu, tol=1e-12)
        Xt = [[columns[a][i] for a in range(k)] for i in range(n)]
        bread = inverse(weighted_cross(Xt, mu))
        if clusters is not None:
            scores = {}
            for i in range(n):
                row = scores.setdefault(clusters[i], [0.0] * k)
                for a in range(k):
                    row[a] += Xt[i][a] * (y[i] - mu[i])
            g = len(scores)
            if g < 2:
                raise ValueError('Cluster-robust errors require at least two clusters')
            meat = [[sum(s[a] * s[b] for s in scores.values()) for b in range(k)] for a in range(k)]
            factor, kind = g / (g - 1), 'poisson_cluster_robust'
        else:
            meat = weighted_cross(Xt, [(y[i] - mu[i]) ** 2 for i in range(n)])
            factor, kind = n / max(n - k, 1), 'poisson_sandwich'
        half = [[sum(bread[a][c] * meat[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
        cov = [[factor * sum(half[a][c] * bread[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
        se = [math.sqrt(max(cov[a][a], 0.0)) for a in range(k)]
    else:
        kind = 'none'
    names = names or [f'x{a}' for a in range(k)]
    deviance = 2 * sum((y[i] * math.log(y[i] / mu[i]) if y[i] > 0 else 0.0) - (y[i] - mu[i]) for i in range(n))
    return {'coefficients': dict(zip(names, beta)), 'standard_errors': dict(zip(names, se)), 'covariance': cov,
            'fixed_effects': [{str(g): v for g, v in values.items()} for values in fe] if fixed_effects else [],
            'intercept': fe[0][0] if not fixed_effects else None, 'fitted': mu, 'kept_index': idx,
            'loglik': loglik, 'deviance': deviance, 'iterations': iterations, 'converged': converged,
            'dropped_separated_observations': dropped, 'n': n, 'se_type': kind}


def negative_binomial_fit(y, X, *, offset=None, names=None, max_iter=100, tol=1e-9):
    """NB2 regression by IRLS for coefficients and Newton on log(alpha) for dispersion."""
    n, k = len(y), len(X[0])
    offset = offset or [0.0] * n
    names = names or [f'x{a}' for a in range(k)]
    beta = [0.0] * k
    mean = max(sum(y) / n, 1e-8)
    beta[0] = math.log(mean)  # Caller supplies an intercept column first.
    log_alpha = math.log(0.5)

    def loglik(beta, log_alpha):
        alpha = math.exp(log_alpha)
        r = 1 / alpha
        total = 0.0
        for i in range(n):
            mu = math.exp(min(offset[i] + sum(b * v for b, v in zip(beta, X[i])), 700))
            total += (math.lgamma(y[i] + r) - math.lgamma(r) - math.lgamma(y[i] + 1)
                      + r * math.log(r / (r + mu)) + y[i] * math.log(mu / (r + mu)) if mu > 0 else 0.0)
        return total

    previous = -math.inf
    converged = False
    for iteration in range(1, max_iter + 1):
        alpha = math.exp(log_alpha)
        for _ in range(5):
            eta = [offset[i] + sum(b * v for b, v in zip(beta, X[i])) for i in range(n)]
            mu = [math.exp(min(e, 700)) for e in eta]
            weights = [m / (1 + alpha * m) for m in mu]
            gradient = [sum(X[i][a] * (y[i] - mu[i]) / (1 + alpha * mu[i]) for i in range(n)) for a in range(k)]
            step = solve(weighted_cross(X, weights), gradient)
            biggest = max(abs(s) for s in step)
            factor = 1.0 if biggest <= 2 else 2 / biggest
            beta = [b + factor * s for b, s in zip(beta, step)]
            if biggest < 1e-10:
                break
        lo, hi = log_alpha - 3, log_alpha + 3
        log_alpha = golden_section(lambda la: -loglik(beta, la), max(lo, -20), min(hi, 5), tol=1e-7)
        current = loglik(beta, log_alpha)
        if abs(current - previous) < tol * (1 + abs(current)):
            converged = True
            break
        previous = current
    alpha = math.exp(log_alpha)
    eta = [offset[i] + sum(b * v for b, v in zip(beta, X[i])) for i in range(n)]
    mu = [math.exp(min(e, 700)) for e in eta]
    cov = inverse(weighted_cross(X, [m / (1 + alpha * m) for m in mu]))
    return {'coefficients': dict(zip(names, beta)),
            'standard_errors': {name: math.sqrt(max(cov[a][a], 0)) for a, name in enumerate(names)},
            'alpha': alpha, 'loglik': current, 'iterations': iteration, 'converged': converged,
            'fitted': mu, 'n': n, 'se_type': 'model_based_information'}


# ----------------------------------------------------------------------------- optimizers

def golden_section(f, lo, hi, *, tol=1e-8, max_iter=200):
    ratio = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(max_iter):
        if abs(b - a) < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = f(d)
    return (a + b) / 2


def nelder_mead(f, x0, *, step=0.1, tol=1e-9, max_iter=2000):
    n = len(x0)
    simplex = [list(map(float, x0))]
    for i in range(n):
        point = list(map(float, x0))
        point[i] += step if point[i] == 0 else step * max(1.0, abs(point[i]))
        simplex.append(point)
    values = [f(p) for p in simplex]
    iterations = 0
    for iterations in range(1, max_iter + 1):
        order = sorted(range(n + 1), key=lambda i: values[i])
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if abs(values[-1] - values[0]) <= tol * (1 + abs(values[0])):
            break
        centroid = [sum(p[j] for p in simplex[:-1]) / n for j in range(n)]
        worst = simplex[-1]
        reflected = [centroid[j] + (centroid[j] - worst[j]) for j in range(n)]
        fr = f(reflected)
        if fr < values[0]:
            expanded = [centroid[j] + 2 * (centroid[j] - worst[j]) for j in range(n)]
            fe = f(expanded)
            simplex[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = reflected, fr
        else:
            contracted = [centroid[j] + 0.5 * (worst[j] - centroid[j]) for j in range(n)]
            fc = f(contracted)
            if fc < values[-1]:
                simplex[-1], values[-1] = contracted, fc
            else:
                best = simplex[0]
                simplex = [best] + [[best[j] + 0.5 * (p[j] - best[j]) for j in range(n)] for p in simplex[1:]]
                values = [values[0]] + [f(p) for p in simplex[1:]]
    best = min(range(n + 1), key=lambda i: values[i])
    return simplex[best], values[best], iterations


def dig(value, path):
    """Select a nested JSON value by a list path (strings for keys, ints for list positions)."""
    for part in path:
        if isinstance(value, dict) and isinstance(part, str) and part in value:
            value = value[part]
        elif isinstance(value, list) and type(part) is int and -len(value) <= part < len(value):
            value = value[part]
        else:
            raise ValueError(f'Missing metric path component: {part!r}')
    return value
