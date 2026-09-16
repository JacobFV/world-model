"""Exponential and logistic growth models for population-like stocks."""
import math
from datetime import datetime, timezone
from . import linalg as la
from .regression import ols
from .optimize import nelder_mead, jacobian
from ..model import instant

SECONDS_PER_YEAR = 31557600


def year_fraction(value):
    moment = instant(value) if isinstance(value, str) else value
    start = datetime(moment.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
    return moment.year + (moment - start).total_seconds() / (end - start).total_seconds()


def fit_exponential_growth(years, values, *, cov_type='HAC', hac_lags=None):
    """log y = a + r t (t in years). r is the continuous growth rate per year."""
    if len(years) != len(values) or len(years) < 3:
        raise ValueError('Exponential growth needs at least three aligned observations')
    if any(v <= 0 for v in values):
        raise ValueError('Exponential growth requires positive values')
    origin = years[0]
    rows = [[1.0, t - origin] for t in years]
    fit = ols([math.log(v) for v in values], rows, names=['log_level', 'rate_per_year'], cov_type=cov_type, hac_lags=hac_lags)
    rate = fit['params']['rate_per_year']
    return {'model': 'exponential_growth', 'rate_per_year': rate, 'rate_per_year_se': fit['bse']['rate_per_year'],
            'annual_growth_fraction': math.expm1(rate), 'rate_per_second': rate / SECONDS_PER_YEAR,
            'origin_year': origin, 'log_level': fit['params']['log_level'], 'log_sigma': fit['sigma'],
            'regression': fit}


def logistic_value(t, capacity, rate, midpoint):
    z = -rate * (t - midpoint)
    return capacity / (1 + math.exp(max(-700, min(700, z))))


def fit_logistic_growth(years, values, *, max_evaluations=6000):
    """Nonlinear least squares on log levels for P(t) = K / (1 + exp(-r (t - t0)))."""
    if len(years) < 5:
        raise ValueError('Logistic growth needs at least five observations')
    if any(v <= 0 for v in values):
        raise ValueError('Logistic growth requires positive values')
    exponential = fit_exponential_growth(years, values, cov_type='nonrobust')
    peak = max(values)
    x0 = [peak * 2.0, max(abs(exponential['rate_per_year']) * 2, 1e-3), years[len(years) // 2]]
    bounds = [(peak * 1.0001, peak * 1000), (1e-6, 10.0), (years[0] - 500, years[-1] + 500)]

    def predictions(p):
        return [math.log(logistic_value(t, *p)) for t in years]

    logs = [math.log(v) for v in values]

    def sse(p):
        return math.fsum((a - b) ** 2 for a, b in zip(logs, predictions(p)))

    solution = nelder_mead(sse, x0, bounds=bounds, max_evaluations=max_evaluations)
    params = solution['x']
    n, k = len(years), 3
    sigma2 = solution['fun'] / max(n - k, 1)
    try:
        jac = jacobian(predictions, params)
        cov = la.scale(la.inverse(la.xtwx(jac)), sigma2)
        se = [math.sqrt(max(cov[i][i], 0.0)) for i in range(k)]
    except ValueError:
        cov, se = None, [None] * k
    at_bound = any(abs(v - b[0]) < 1e-6 * max(1, abs(b[0])) or abs(v - b[1]) < 1e-6 * max(1, abs(b[1])) for v, b in zip(params, bounds))
    return {'model': 'logistic_growth', 'capacity': params[0], 'rate_per_year': params[1], 'midpoint_year': params[2],
            'standard_errors': dict(zip(['capacity', 'rate_per_year', 'midpoint_year'], se)),
            'cov': {'names': ['capacity', 'rate_per_year', 'midpoint_year'], 'matrix': cov} if cov else None,
            'sigma_log': math.sqrt(sigma2), 'converged': solution['converged'], 'at_search_bound': at_bound,
            'weakly_identified': at_bound or (se[0] is not None and se[0] > params[0])}
