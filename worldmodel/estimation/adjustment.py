"""Partial-adjustment and single-equation error-correction models (e.g. policy-rate pass-through)."""
import math
from .regression import ols, delta_method


def _aligned(y, x):
    if len(y) != len(x) or len(y) < 6:
        raise ValueError('Adjustment models need aligned series with at least six observations')
    return [float(v) for v in y], [float(v) for v in x]


def fit_partial_adjustment(y, x, *, cov_type='HAC', hac_lags=None):
    """y_t = a + rho*y_{t-1} + b*x_t + u_t.

    Long-run response b/(1-rho), long-run intercept a/(1-rho) and half-life
    log(0.5)/log(rho) are reported with delta-method standard errors.
    """
    y, x = _aligned(y, x)
    rows = [[1.0, y[t - 1], x[t]] for t in range(1, len(y))]
    fit = ols(y[1:], rows, names=['const', 'rho', 'impact'], cov_type=cov_type, hac_lags=hac_lags)
    params = [fit['params'][n] for n in ('const', 'rho', 'impact')]
    rho = params[1]

    def derived(p):
        gap = 1 - p[1]
        if abs(gap) < 1e-9:
            return [math.inf, math.inf]
        return [p[2] / gap, p[0] / gap]

    long_run, long_se, _ = delta_method(derived, params, fit['cov']['matrix']) if abs(1 - rho) > 1e-6 else ([None, None], [None, None], None)
    half_life = math.log(0.5) / math.log(rho) if 0 < rho < 1 else None
    return {'model': 'partial_adjustment', 'regression': fit, 'rho': rho, 'impact': params[2],
            'long_run_response': long_run[0], 'long_run_response_se': long_se[0],
            'long_run_intercept': long_run[1], 'long_run_intercept_se': long_se[1],
            'half_life_periods': half_life, 'stable': abs(rho) < 1}


def partial_adjustment_predict(fit, y_previous, x_now):
    p = fit['regression']['params']
    return p['const'] + p['rho'] * y_previous + p['impact'] * x_now


def fit_error_correction(y, x, *, lags=0, cov_type='HAC', hac_lags=None):
    """Δy_t = c + β Δx_t + α y_{t-1} + δ x_{t-1} + Σ γ_i Δy_{t-i} + u_t.

    Long run y* = θ0 + θ1 x with θ1 = -δ/α (pass-through), θ0 = -c/α (spread);
    -α is the fraction of the gap closed per period and β the impact pass-through.
    Unit-root inference is nonstandard; standard errors are descriptive.
    """
    y, x = _aligned(y, x)
    if type(lags) is not int or lags < 0:
        raise ValueError('lags must be a nonnegative integer')
    start = 1 + lags
    rows, target = [], []
    for t in range(start, len(y)):
        row = [1.0, x[t] - x[t - 1], y[t - 1], x[t - 1]] + [y[t - i] - y[t - i - 1] for i in range(1, lags + 1)]
        rows.append(row)
        target.append(y[t] - y[t - 1])
    names = ['const', 'impact', 'alpha', 'delta'] + [f'gamma{i}' for i in range(1, lags + 1)]
    fit = ols(target, rows, names=names, cov_type=cov_type, hac_lags=hac_lags)
    base = [fit['params'][n] for n in names]
    cov = fit['cov']['matrix']

    def derived(p):
        alpha = p[2] if abs(p[2]) > 1e-12 else 1e-12
        return [-p[3] / alpha, -p[0] / alpha, -p[2]]

    (theta1, theta0, speed), (se1, se0, se_speed), _ = delta_method(derived, base, cov)
    half_life = math.log(0.5) / math.log(1 - speed) if 0 < speed < 1 else None
    return {'model': 'error_correction', 'regression': fit, 'lags': lags, 'pass_through': theta1, 'pass_through_se': se1,
            'spread': theta0, 'spread_se': se0, 'adjustment_speed': speed, 'adjustment_speed_se': se_speed,
            'impact': fit['params']['impact'], 'impact_se': fit['bse']['impact'], 'half_life_periods': half_life,
            'error_correcting': -2 < fit['params']['alpha'] < 0,
            'limitations': ['Single-equation ECM assumes weak exogeneity of x.',
                            'Coefficient t-statistics on levels are nonstandard under unit roots.']}


def error_correction_predict(fit, y_history, x_history, x_now):
    """One-step conditional prediction of y_t given y/x through t-1 and the realized x_t."""
    p = fit['regression']['params']
    lags = fit['lags']
    change = (p['const'] + p['impact'] * (x_now - x_history[-1]) + p['alpha'] * y_history[-1] + p['delta'] * x_history[-1]
              + math.fsum(p[f'gamma{i}'] * (y_history[-i] - y_history[-i - 1]) for i in range(1, lags + 1)))
    return y_history[-1] + change
