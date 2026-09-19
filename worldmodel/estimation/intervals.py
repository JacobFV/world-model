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

Fat-tail methods that make no Gaussian assumption (declared for the 2026-09-18
interval wave, ``real_data_plan.json`` → ``interval_wave``):

* ``student_t_mle`` (:func:`student_t_mle`) — location-zero Student-t scale and
  degrees of freedom by maximum likelihood on the residuals, rather than from
  their kurtosis.
* ``empirical_quantile`` (:func:`residual_quantile_nodes`) — the empirical
  quantiles of the raw residuals: the error distribution's shape with no
  standardization and no trailing window.
* ``conformal_rolling`` (:func:`conformal_nodes`) — rolling split-conformal
  quantiles of *out-of-sample* one-step errors known before the origin, with the
  finite-sample rank correction that makes an equal-tailed interval valid under
  exchangeability. In-sample residuals understate out-of-sample error; each of
  these errors is a genuine forecast from a fit that did not contain its target.

Nothing here tunes anything to an outcome: every quantity is computed from
information available at the forecast origin, and which method an attempt uses
is pre-registered in ``real_data_plan.json``.
"""
import math

from .distributions import norm_cdf, norm_pdf, norm_ppf, t_cdf, t_ppf

FAMILIES = ('normal', 'student_t', 'empirical')
#: Methods a :class:`~worldmodel.estimation.families.DynamicRegression` accepts.
INTERVAL_METHODS = ('gaussian_in_sample', 'gaussian_trailing', 'student_t_trailing', 'empirical_trailing',
                    'student_t_mle', 'empirical_quantile', 'conformal_rolling')
#: The three methods of the 2026-09-18 interval wave; model families accept them as
#: ``family_interval_method`` (see ``ModelFamilyEstimator``).
FAT_TAIL_METHODS = ('student_t_mle', 'empirical_quantile', 'conformal_rolling')
#: Methods whose errors are pre-origin out-of-sample one-step errors the estimator must
#: supply (``from_errors(..., out_of_sample=...)``) instead of its in-sample residuals.
OUT_OF_SAMPLE_METHODS = ('conformal_rolling',)
DEFAULT_NODES = 40
#: 45 nodes put the 0.1, 0.5 and 0.9 levels exactly on a node ((i + 0.5)/45 for i = 4, 22, 40),
#: so the scored 80% interval and the pinball quantiles of a conformal predictive are its
#: conformal order statistics rather than an interpolation between two of them.
CONFORMAL_NODES = 45
#: The fewest out-of-sample errors for which the upper 0.9 conformal rank ceil((n+1) 0.9) exists.
MIN_CONFORMAL_ERRORS = 9
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


def student_t_mle(values, *, minimum=MIN_DF, maximum=MAX_DF):
    """Maximum-likelihood ``(scale, df)`` of a location-zero Student-t fitted to ``values``.

    The location is fixed at zero because the predictive is centred on the point
    forecast. For a given ``df`` the scale solves the likelihood equation by the EM
    fixed point ``s^2 = mean(w_i e_i^2)`` with ``w_i = (df + 1)/(df + e_i^2/s^2)``;
    ``df`` then maximizes the profile likelihood by golden-section search on
    ``log df`` over ``[minimum, maximum]`` (both ends are also evaluated, so a
    Gaussian sample returns the upper bound). Deterministic: there is no starting
    value or tuning constant that could be chosen after seeing an outcome.
    """
    values = [float(v) for v in values]
    n = len(values)
    if n < 8:
        raise ValueError('Estimating Student-t degrees of freedom needs at least eight errors')
    squares = [v * v for v in values]
    base = math.fsum(squares) / n
    if base <= 0:
        raise ValueError('Student-t scale collapsed to zero')

    def profile(df):
        # The scale equation has a unique root for each df, so the iteration's start
        # affects only how quickly it converges, never where.
        s2 = base
        for _ in range(2000):
            updated = math.fsum((df + 1) * q / (df + q / s2) for q in squares) / n
            converged = abs(updated - s2) <= 1e-11 * s2
            s2 = updated
            if converged:
                break
        constant = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi) - 0.5 * math.log(s2)
        return n * constant - (df + 1) / 2 * math.fsum(math.log1p(q / (df * s2)) for q in squares), math.sqrt(s2)

    lo, hi = math.log(minimum), math.log(maximum)
    ratio = (math.sqrt(5) - 1) / 2
    a, b = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    fa, fb = profile(math.exp(a))[0], profile(math.exp(b))[0]
    while hi - lo > 1e-4:
        if fa >= fb:
            hi, b, fb = b, a, fa
            a = hi - ratio * (hi - lo)
            fa = profile(math.exp(a))[0]
        else:
            lo, a, fa = a, b, fb
            b = lo + ratio * (hi - lo)
            fb = profile(math.exp(b))[0]
    best = max((math.exp((lo + hi) / 2), minimum, maximum), key=lambda df: profile(df)[0])
    return profile(best)[1], best


def residual_quantile_nodes(values, *, nodes=DEFAULT_NODES, sign=-1.0):
    """``(scale, nodes)``: the raw residuals' empirical quantiles in units of their root mean square.

    ``sign=-1`` turns ``predicted - actual`` residuals into the ``(actual - mean)``
    orientation the predictive families use.
    """
    values = [sign * float(v) for v in values]
    if len(values) < 8:
        raise ValueError('Empirical residual quantiles need at least eight residuals')
    scale = math.sqrt(math.fsum(v * v for v in values) / len(values))
    if scale <= 0:
        raise ValueError('Residual scale collapsed to zero')
    return scale, quantile_nodes([v / scale for v in values], nodes)


def conformal_quantile(values, tau):
    """Split-conformal order statistic of ``values`` at level ``tau``.

    Upper tail (``tau >= 0.5``): the ``ceil((n+1) tau)``-th smallest value; lower tail:
    the ``floor((n+1) tau)``-th. Under exchangeability the equal-tailed interval between
    the two covers with probability at least its nominal level. A rank outside the
    sample is clipped to the extreme observation; :func:`conformal_nodes` refuses
    samples too small for the 80% interval's two ranks to exist.
    """
    ordered = sorted(values)
    n = len(ordered)
    rank = math.ceil((n + 1) * tau - 1e-9) if tau >= 0.5 else math.floor((n + 1) * tau + 1e-9)
    return ordered[min(max(rank, 1), n) - 1]


def conformal_nodes(errors, *, nodes=CONFORMAL_NODES, sign=-1.0):
    """``(scale, nodes)`` of a rolling split-conformal predictive from out-of-sample errors.

    ``errors`` are one-step forecast errors (``predicted - actual``), each made by a fit
    that did not contain its target and all known before the origin. Node ``i`` is the
    conformal order statistic at level ``(i + 0.5)/nodes``, in units of the errors' root
    mean square, so the empirical predictive family scores it unchanged.
    """
    values = [sign * float(e) for e in errors]
    if len(values) < MIN_CONFORMAL_ERRORS:
        raise ValueError(f'Split-conformal intervals need at least {MIN_CONFORMAL_ERRORS} out-of-sample errors; '
                         f'{len(values)} available')
    scale = math.sqrt(math.fsum(v * v for v in values) / len(values))
    if scale <= 0:
        raise ValueError('Out-of-sample error scale collapsed to zero')
    return scale, [conformal_quantile(values, (i + 0.5) / nodes) / scale for i in range(nodes)]


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

def from_errors(errors, *, method='gaussian_in_sample', window=None, nodes=DEFAULT_NODES, dof=0, extra=None,
                out_of_sample=None):
    """Declared predictive specification for a one-step error, in the errors' own units.

    ``errors`` are ``predicted - actual`` in the order they occurred. ``dof``
    is the number of fitted coefficients. ``gaussian_in_sample`` uses it exactly
    as it always has; ``student_t_mle`` and ``empirical_quantile`` apply the same
    ``n/(n - dof)`` variance correction when they read every residual (no window),
    because in-sample residuals understate a forecast error by that factor.
    ``extra`` names further independent standard deviations (for example
    data-revision uncertainty) added in quadrature to the residual scale.
    ``out_of_sample`` carries the pre-origin out-of-sample one-step errors that
    ``conformal_rolling`` is built from; with a declared ``window`` only the most
    recent ``window`` of them are used.
    """
    if method not in INTERVAL_METHODS:
        raise ValueError(f'Unknown interval method {method!r}; declared methods are {list(INTERVAL_METHODS)}')
    errors = list(errors)
    if not errors:
        raise ValueError('A predictive specification needs at least one residual')
    if method in ('student_t_mle', 'empirical_quantile', 'conformal_rolling'):
        return _fat_tail_spec(errors, method=method, window=window, nodes=nodes, dof=dof, extra=extra,
                              out_of_sample=out_of_sample)
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


def _fat_tail_spec(errors, *, method, window, nodes, dof, extra, out_of_sample):
    """``student_t_mle``, ``empirical_quantile`` and ``conformal_rolling`` (see :func:`from_errors`)."""
    if method == 'conformal_rolling':
        if out_of_sample is None:
            raise ValueError('conformal_rolling needs the pre-origin out-of-sample one-step errors')
        used = list(out_of_sample)
    else:
        used = list(errors)
    if window is not None:
        window = int(window)
        if window < 2:
            raise ValueError('interval_window must be at least two periods')
        used = used[-window:]
    # In-sample residuals over the whole fit understate forecast error by n/(n - dof); a
    # trailing window or genuinely out-of-sample errors carry no such bias.
    correction = 1.0 if (window is not None or method == 'conformal_rolling') else math.sqrt(len(used) / max(1, len(used) - dof))
    df = None
    if method == 'student_t_mle':
        t_scale, df = student_t_mle(used)
        residual = t_scale * math.sqrt(df / (df - 2)) * correction
    elif method == 'empirical_quantile':
        base, node_values = residual_quantile_nodes(used, nodes=nodes)
        residual = base * correction
    else:
        residual, node_values = conformal_nodes(used)
    scale, components = combine(residual=residual, **(extra or {}))
    if scale <= 0:
        raise ValueError('Predictive scale collapsed to zero')
    if method == 'student_t_mle':
        spec = student_t(scale / math.sqrt(df / (df - 2)), df)
    else:
        # Nodes are in units of the residual scale, so the spec's scale (residual plus any
        # declared extra components) keeps the shape and widens it by the extra variance.
        spec = empirical(scale, node_values)
    return dict(spec, method=method, components=components, residual_observations=len(errors),
                shape_observations=len(used), **({'interval_window': window} if window is not None else {}),
                **({'out_of_sample_errors': len(out_of_sample)} if out_of_sample is not None else {}))


def strip(spec):
    """The distribution fields only, for storage on a forecast row."""
    keep = ('family', 'scale', 'df', 'nodes')
    return {key: spec[key] for key in keep if key in spec}
