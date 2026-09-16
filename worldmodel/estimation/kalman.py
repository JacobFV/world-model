"""Local-level (random walk plus noise) Kalman filter, smoother and maximum likelihood."""
import math
from .optimize import nelder_mead, hessian
from . import linalg as la

DIFFUSE_VARIANCE = 1e7


def local_level_filter(y, sigma2_eps, sigma2_eta, *, a0=None, p0=None):
    """Filter y_t = mu_t + eps_t, mu_{t+1} = mu_t + eta_t. ``None`` observations are skipped.

    The log-likelihood excludes the first observed prediction error (diffuse start).
    """
    if sigma2_eps < 0 or sigma2_eta < 0:
        raise ValueError('Variances must be nonnegative')
    first = next((v for v in y if v is not None), None)
    if first is None:
        raise ValueError('Local level filter needs at least one observation')
    scale = max(1.0, abs(first))
    a = first if a0 is None else a0
    p = DIFFUSE_VARIANCE * scale * scale if p0 is None else p0
    predicted, predicted_var, filtered, filtered_var, innovations, innovation_var = [], [], [], [], [], []
    loglik, used, seen = 0.0, 0, False
    for value in y:
        predicted.append(a)
        predicted_var.append(p)
        if value is None:
            innovations.append(None)
            innovation_var.append(None)
        else:
            f = p + sigma2_eps
            if f <= 0:
                raise ValueError('Nonpositive innovation variance')
            v = value - a
            gain = p / f
            a = a + gain * v
            p = p * (1 - gain)
            innovations.append(v)
            innovation_var.append(f)
            if seen:
                loglik += -0.5 * (math.log(2 * math.pi) + math.log(f) + v * v / f)
                used += 1
            seen = True
        filtered.append(a)
        filtered_var.append(p)
        p = p + sigma2_eta
    return {'predicted': predicted, 'predicted_var': predicted_var, 'filtered': filtered, 'filtered_var': filtered_var,
            'innovations': innovations, 'innovation_var': innovation_var, 'loglik': loglik, 'loglik_count': used,
            'next_mean': a, 'next_var': p, 'sigma2_eps': sigma2_eps, 'sigma2_eta': sigma2_eta}


def local_level_smoother(result):
    """Rauch-Tung-Striebel smoother over a filter result."""
    n = len(result['filtered'])
    smoothed, smoothed_var = [0.0] * n, [0.0] * n
    smoothed[-1], smoothed_var[-1] = result['filtered'][-1], result['filtered_var'][-1]
    for t in range(n - 2, -1, -1):
        p_next = result['filtered_var'][t] + result['sigma2_eta']
        gain = result['filtered_var'][t] / p_next if p_next > 0 else 0.0
        smoothed[t] = result['filtered'][t] + gain * (smoothed[t + 1] - result['filtered'][t])
        smoothed_var[t] = result['filtered_var'][t] + gain * gain * (smoothed_var[t + 1] - p_next)
    return {'smoothed': smoothed, 'smoothed_var': smoothed_var}


def fit_local_level(y, *, max_evaluations=2000):
    observed = [v for v in y if v is not None]
    if len(observed) < 5:
        raise ValueError('Local level MLE needs at least five observations')
    diffs = [b - a for a, b in zip(observed, observed[1:])]
    base = max(la.variance(diffs) if len(diffs) > 1 else 1.0, 1e-12)
    x0 = [math.log(base / 2), math.log(base / 2)]

    def negative(z):
        return -local_level_filter(y, math.exp(z[0]), math.exp(z[1]))['loglik']

    solution = nelder_mead(negative, x0, max_evaluations=max_evaluations, step=[1.0, 1.0])
    s_eps, s_eta = (math.exp(v) for v in solution['x'])
    filtered = local_level_filter(y, s_eps, s_eta)
    se = {'sigma2_eps': None, 'sigma2_eta': None}
    try:
        cov_log = la.inverse(hessian(negative, solution['x']))
        se = {'sigma2_eps': s_eps * math.sqrt(max(cov_log[0][0], 0.0)), 'sigma2_eta': s_eta * math.sqrt(max(cov_log[1][1], 0.0))}
    except ValueError:
        pass
    k = 2
    ll = filtered['loglik']
    n = filtered['loglik_count']
    return {'model': 'local_level', 'sigma2_eps': s_eps, 'sigma2_eta': s_eta, 'signal_to_noise': s_eta / s_eps if s_eps > 0 else math.inf,
            'standard_errors': se, 'loglik': ll, 'aic': -2 * ll + 2 * k, 'bic': -2 * ll + math.log(max(n, 1)) * k,
            'filter': filtered, 'smoother': local_level_smoother(filtered), 'converged': solution['converged']}


def local_level_forecast(fit_or_filter, h=1):
    f = fit_or_filter.get('filter', fit_or_filter)
    mean = f['next_mean']
    return {'mean': [mean] * h, 'sd': [math.sqrt(f['next_var'] + j * f['sigma2_eta'] + f['sigma2_eps']) for j in range(h)]}
