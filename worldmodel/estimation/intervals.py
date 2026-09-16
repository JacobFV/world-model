"""Declared predictive distributions for holdout forecasts.

Scoring a forecast needs a *distribution*, not only a point. The default
everywhere in this package is a Gaussian centred on the forecast with the
estimator's in-sample residual scale. That default is an assumption, and when it
is wrong the model fails ``interval_coverage`` even though its point forecasts
have skill. This module makes the choice explicit: a forecast may carry a
``predictive`` description, and :mod:`worldmodel.estimation.validation` scores
coverage, pinball loss, CRPS and the log score from it instead of assuming a
Gaussian.

Three declared families::

    {'family': 'normal',    'scale': s}
    {'family': 'student_t', 'scale': s, 'df': d}       # sd = s * sqrt(d/(d-2))
    {'family': 'empirical', 'scale': s, 'nodes': [u1 .. uN]}

``nodes`` are equally spaced quantiles of the *standardized forecast error*
``u = (actual - mean) / s``, so an empirical predictive reproduces whatever
skew and kurtosis the residuals showed and needs no distributional assumption.
Every family reports the standard deviation it implies, so ``sd`` stays
comparable across methods.

Scale estimators (used by an estimator at fit time, never by the scorer):

* :func:`trailing_scale` — root mean square of the most recent ``window``
  one-step errors. A full-sample residual scale is an average over regimes; a
  trailing window tracks the current one.
* :func:`standardized_nodes` — the empirical quantile nodes of errors
  standardized by their own trailing scale, which separates the shape of the
  error distribution from its (time-varying) level.
* :func:`moment_df` — Student-t degrees of freedom from the sample kurtosis.
* :func:`combine` — independent variance components (residual, parameter,
  revision) added in quadrature.

Nothing here tunes anything to an outcome: every quantity is computed from
information available at the forecast origin, and which method an attempt uses
is pre-registered in ``real_data_plan.json``.
"""
import math

from .distributions import norm_cdf, norm_pdf, norm_ppf, t_cdf, t_ppf

FAMILIES = ('normal', 'student_t', 'empirical')
#: Methods a :class:`~worldmodel.estimation.families.DynamicRegression` accepts.
INTERVAL_METHODS = ('gaussian_in_sample', 'gaussian_trailing', 'student_t_trailing', 'empirical_trailing')
DEFAULT_NODES = 40
MIN_DF, MAX_DF = 2.5, 100.0


# ----------------------------------------------------------------------------- constructors

def normal(scale):
    return {'family': 'normal', 'scale': float(scale)}


def student_t(scale, df):
    if not df > 2:
        raise ValueError('Student-t predictive distributions need df > 2 for a finite variance')
    return {'family': 'student_t', 'scale': float(scale), 'df': float(df)}


def empirical(scale, nodes):
    nodes = [float(u) for u in nodes]
    if len(nodes) < 8:
        raise ValueError('An empirical predictive distribution needs at least eight quantile nodes')
    return {'family': 'empirical', 'scale': float(scale), 'nodes': sorted(nodes)}


def rescale(spec, factor):
    """The same predictive shape at ``factor`` times the scale (level or horizon scaling)."""
    out = dict(spec)
    out['scale'] = float(spec['scale']) * float(factor)
    return out


# ----------------------------------------------------------------------------- distribution interface

def standard_deviation(spec):
    family, scale = spec['family'], float(spec['scale'])
    if family == 'normal':
        return scale
    if family == 'student_t':
        df = float(spec['df'])
        return scale * math.sqrt(df / (df - 2))
    if family == 'empirical':
        nodes = spec['nodes']
        mean = math.fsum(nodes) / len(nodes)
        return scale * math.sqrt(math.fsum((u - mean) ** 2 for u in nodes) / len(nodes))
    raise ValueError(f'Unknown predictive family {family!r}')


def standardized_quantile(spec, tau):
    """Quantile of the standardized error ``(actual - mean) / scale``."""
    if not 0 < tau < 1:
        raise ValueError('Quantile level must be in (0,1)')
    family = spec['family']
    if family == 'normal':
        return norm_ppf(tau)
    if family == 'student_t':
        return t_ppf(tau, float(spec['df']))
    if family == 'empirical':
        nodes = spec['nodes']
        n = len(nodes)
        position = tau * n - 0.5          # nodes are the (i+0.5)/n quantiles
        if position <= 0:
            return nodes[0]
        if position >= n - 1:
            return nodes[-1]
        low = int(math.floor(position))
        weight = position - low
        return nodes[low] * (1 - weight) + nodes[low + 1] * weight
    raise ValueError(f'Unknown predictive family {family!r}')


def quantile(spec, mean, tau):
    return mean + float(spec['scale']) * standardized_quantile(spec, tau)


def interval(spec, mean, level):
    if not 0 < level < 1:
        raise ValueError('Interval level must be in (0,1)')
    return [quantile(spec, mean, (1 - level) / 2), quantile(spec, mean, 0.5 + level / 2)]


def covers(spec, mean, actual, level):
    low, high = interval(spec, mean, level)
    return low <= actual <= high


def crps(spec, mean, actual):
    """Continuous ranked probability score of the predictive distribution.

    Closed form for the normal and Student-t families; the empirical family uses
    the energy form over its quantile nodes, which is the standard ensemble CRPS
    and agrees with the closed form to better than one percent at the default
    node count (tested).
    """
    family, scale = spec['family'], float(spec['scale'])
    if scale <= 0:
        return abs(actual - mean)
    z = (actual - mean) / scale
    if family == 'normal':
        return scale * (z * (2 * norm_cdf(z) - 1) + 2 * norm_pdf(z) - 1 / math.sqrt(math.pi))
    if family == 'student_t':
        df = float(spec['df'])
        # Jordan, Krueger and Lerch (2019), Student-t CRPS:
        #   y(2F-1) + 2f (nu + y^2)/(nu - 1) - 2 sqrt(nu) B(1/2, nu-1/2) / ((nu-1) B(1/2, nu/2)^2)
        pdf = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi) * (1 + z * z / df) ** (-(df + 1) / 2)
        beta_ratio = math.exp(math.lgamma(df - 0.5) + 2 * math.lgamma((df + 1) / 2)
                              - math.lgamma(0.5) - math.lgamma(df) - 2 * math.lgamma(df / 2))
        term = z * (2 * t_cdf(z, df) - 1) + 2 * pdf * (df + z * z) / (df - 1)
        return scale * (term - 2 * math.sqrt(df) * beta_ratio / (df - 1))
    if family == 'empirical':
        nodes = spec['nodes']
        n = len(nodes)
        first = math.fsum(abs(mean + scale * u - actual) for u in nodes) / n
        second = math.fsum((2 * (i + 1) - n - 1) * (mean + scale * u) for i, u in enumerate(nodes)) / (n * n)
        return first - second
    raise ValueError(f'Unknown predictive family {family!r}')


def log_score(spec, mean, actual):
    """Negative log predictive density; ``None`` where the family has no density."""
    family, scale = spec['family'], float(spec['scale'])
    if scale <= 0:
        return None
    z = (actual - mean) / scale
    if family == 'normal':
        return math.log(scale) + 0.5 * math.log(2 * math.pi) + z * z / 2
    if family == 'student_t':
        df = float(spec['df'])
        return (math.log(scale) + 0.5 * math.log(df * math.pi) + math.lgamma(df / 2) - math.lgamma((df + 1) / 2)
                + (df + 1) / 2 * math.log1p(z * z / df))
    return None


# ----------------------------------------------------------------------------- scale estimators

def trailing_scale(errors, window=None):
    """Root mean square of the last ``window`` errors (all of them when ``window`` is None)."""
    values = list(errors)[-window:] if window else list(errors)
    if not values:
        raise ValueError('A trailing scale needs at least one error')
    return math.sqrt(math.fsum(e * e for e in values) / len(values))


def _kurtosis(values):
    n = len(values)
    mean = math.fsum(values) / n
    variance = math.fsum((v - mean) ** 2 for v in values) / n
    if variance <= 0:
        return 3.0
    return math.fsum((v - mean) ** 4 for v in values) / n / (variance * variance)


def moment_df(values, *, minimum=MIN_DF, maximum=MAX_DF):
    """Student-t degrees of freedom matching the sample kurtosis: ``df = 4 + 6/(k-3)``."""
    if len(values) < 8:
        raise ValueError('Estimating tail degrees of freedom needs at least eight errors')
    excess = _kurtosis(values) - 3.0
    df = maximum if excess <= 0 else 4.0 + 6.0 / excess
    return min(maximum, max(minimum, df))


def standardized_errors(errors, window):
    """``e_t / trailing_scale(e_{t-window..t-1})`` for every error with a full trailing window."""
    values = list(errors)
    if window is None or window < 2:
        raise ValueError('Standardizing errors needs a trailing window of at least two')
    out = []
    for t in range(window, len(values)):
        scale = trailing_scale(values[t - window:t])
        if scale > 0:
            out.append(values[t] / scale)
    return out


def quantile_nodes(values, nodes=DEFAULT_NODES):
    """The ``(i+0.5)/nodes`` empirical quantiles of ``values`` (linear interpolation)."""
    ordered = sorted(values)
    n = len(ordered)
    if n < 8:
        raise ValueError('Empirical quantile nodes need at least eight values')
    out = []
    for i in range(nodes):
        position = ((i + 0.5) / nodes) * n - 0.5
        low = min(max(int(math.floor(position)), 0), n - 1)
        high = min(low + 1, n - 1)
        weight = min(max(position - low, 0.0), 1.0)
        out.append(ordered[low] * (1 - weight) + ordered[high] * weight)
    return out


def standardized_nodes(errors, window, *, nodes=DEFAULT_NODES, sign=-1.0):
    """Quantile nodes of the standardized *error*, oriented as ``(actual - mean)/scale``.

    Estimators define their residuals as ``predicted - actual``, so ``sign=-1``
    turns them into the orientation the predictive families use.
    """
    standardized = standardized_errors(errors, window)
    if len(standardized) < 8:
        raise ValueError(f'Only {len(standardized)} standardized errors for an empirical predictive distribution')
    return quantile_nodes([sign * u for u in standardized], nodes), len(standardized)


def combine(**components):
    """Independent variance components added in quadrature, keeping each one visible."""
    kept = {name: float(value) for name, value in components.items() if value}
    return math.sqrt(math.fsum(value * value for value in kept.values())), kept


# ----------------------------------------------------------------------------- residual-based specification

def from_errors(errors, *, method='gaussian_in_sample', window=None, nodes=DEFAULT_NODES, dof=0, extra=None):
    """Declared predictive specification for a one-step error, in the errors' own units.

    ``errors`` are ``predicted - actual`` in the order they occurred. ``dof``
    is the number of fitted coefficients, used only by the in-sample method,
    which keeps the historical behaviour exactly. ``extra`` names further
    independent standard deviations (for example data-revision uncertainty)
    added in quadrature to the residual scale.
    """
    if method not in INTERVAL_METHODS:
        raise ValueError(f'Unknown interval method {method!r}; declared methods are {list(INTERVAL_METHODS)}')
    errors = list(errors)
    if not errors:
        raise ValueError('A predictive specification needs at least one residual')
    if method == 'gaussian_in_sample':
        residual = math.sqrt(math.fsum(e * e for e in errors) / max(1, len(errors) - dof))
        window_used, shape_observations, df = None, None, None
    else:
        if window is None:
            raise ValueError(f'{method} requires a declared interval_window')
        window = int(window)
        if window < 2:
            raise ValueError('interval_window must be at least two periods')
        if len(errors) < window:
            raise ValueError(f'{method} needs at least {window} residuals; {len(errors)} available')
        residual = trailing_scale(errors, window)
        window_used, shape_observations, df = window, None, None
    scale, components = combine(residual=residual, **(extra or {}))
    if scale <= 0:
        raise ValueError('Predictive scale collapsed to zero')
    if method in ('gaussian_in_sample', 'gaussian_trailing'):
        spec = normal(scale)
    elif method == 'student_t_trailing':
        df = moment_df(standardized_errors(errors, window))
        spec = student_t(scale / math.sqrt(df / (df - 2)), df)
    else:
        node_values, shape_observations = standardized_nodes(errors, window, nodes=nodes)
        spec = empirical(scale, node_values)
    return dict(spec, method=method, components=components, residual_observations=len(errors),
                **({'interval_window': window_used} if window_used else {}),
                **({'shape_observations': shape_observations} if shape_observations else {}))


def strip(spec):
    """The distribution fields only, for storage on a forecast row."""
    keep = ('family', 'scale', 'df', 'nodes')
    return {key: spec[key] for key in keep if key in spec}
