"""Component estimators for existing process families; declarations live in requirements.json."""
import json
import math
from functools import lru_cache
from pathlib import Path
from ..model import instant
from . import intervals
from . import linalg as la
from .data import SeriesRequirement, LeakageError, availability, period_end
from .spec import ParameterSpec, Estimate
from .regression import ols, tsls, delta_method
from .discrete import binomial_glm, poisson_glm
from .timeseries import select_ar_order
from .acceptance import default_criteria as _base_criteria

REQUIREMENTS_PATH = Path(__file__).with_name('requirements.json')
YEAR_SECONDS = 31557600
DAYS_PER_MONTH = YEAR_SECONDS / 86400 / 12
PERIOD_SECONDS = {'daily': 86400, 'weekly': 604800, 'monthly': YEAR_SECONDS / 12, 'quarterly': YEAR_SECONDS / 4, 'annual': YEAR_SECONDS}
#: How a declared conditional input's realized value is carried onto the origin's vintage.
#: A conditional forecast conditions on the *realized movement* of a driver, and a movement
#: is only a movement when both of its endpoints are read from one vintage. ``'ratio'`` and
#: ``'difference'`` convert the realized value onto the origin vintage using the two frames'
#: overlap at the anchor period; ``'none'`` (the default) hands the realized value over
#: unchanged, which is correct for a driver the design reads as a level at the target period
#: and for one that is never revised or rebased.
CONDITIONAL_REBASE = ('none', 'ratio', 'difference')


@lru_cache(maxsize=1)
def _raw_requirements():
    return REQUIREMENTS_PATH.read_text(encoding='utf-8')


def load_requirements():
    return json.loads(_raw_requirements())


def _dlog(previous, current):
    if previous is None or current is None:
        raise TypeError('unavailable value')
    if previous <= 0 or current <= 0:
        raise ValueError('log of nonpositive value')
    return math.log(current / previous)


def _logit(p):
    if not 0 < p < 1:
        raise ValueError('log-odds outside (0,1)')
    return math.log(p / (1 - p))


def _inv_logit(eta):
    return 1 / (1 + math.exp(-eta)) if eta >= 0 else math.exp(eta) / (1 + math.exp(eta))


def _cov_lookup(cov, names):
    index = {n: i for i, n in enumerate(cov['names'])}
    return [[cov['matrix'][index[a]][index[b]] for b in names] for a in names]


def _derived(func, coef, names, cov):
    values, ses, _ = delta_method(lambda p: func(dict(zip(names, p))), [coef[n] for n in names], _cov_lookup(cov, names))
    return values, ses


class ComponentEstimator:
    """Base: declarations from requirements.json, Estimate assembly and point-in-time frames."""
    component = None
    probability_target = False

    def __init__(self, overrides=None, options=None):
        document = load_requirements()
        if self.component not in document['components']:
            raise ValueError(f'Unknown estimation component {self.component}')
        self.spec = document['components'][self.component]
        self.process_id = self.spec['process_id']
        self.frequency = self.spec['frequency']
        self.method = self.spec['method']
        self.target = self.spec['target']
        self.targets = [self.target]
        self.conditional_inputs = tuple(self.spec.get('conditional_inputs', ()))
        self.conditional_rebase = dict(self.spec.get('conditional_rebase', {}))
        unknown_rebase = set(self.conditional_rebase) - set(self.conditional_inputs)
        if unknown_rebase:
            raise ValueError(f'{self.component}: conditional_rebase names non-conditional inputs {sorted(unknown_rebase)}')
        bad = {k: v for k, v in self.conditional_rebase.items() if v not in CONDITIONAL_REBASE}
        if bad:
            raise ValueError(f'{self.component}: conditional_rebase must be one of {list(CONDITIONAL_REBASE)}; got {bad}')
        self.min_observations = self.spec.get('min_observations', 12)
        self.options = dict(self.spec.get('default_options', {}), **(options or {}))
        overrides = overrides or {}
        names = {s['name'] for s in self.spec['series']}
        unknown = set(overrides) - names
        if unknown and self.spec['series']:
            raise ValueError(f'Unknown series overrides for {self.component}: {sorted(unknown)}')
        self.requirements = tuple(SeriesRequirement.from_dict({k: v for k, v in {**s, **overrides.get(s['name'], {})}.items() if v is not None})
                                  for s in self.spec['series'])
        self.parameters = tuple(ParameterSpec.from_dict(p) for p in self.spec['parameters'])

    def all_requirements(self):
        return list(self.requirements)

    def default_criteria(self):
        acceptance = self.spec.get('acceptance', {})
        criteria = _base_criteria() if acceptance.get('inherit_default', True) else []
        overrides = acceptance.get('override', {})
        for criterion in criteria:
            criterion.update(overrides.get(criterion['id'], {}))
        return criteria + [dict(c) for c in acceptance.get('add', [])]

    def frame(self, data, *, cutoff, vintage_policy='strict'):
        return data.frame(self.all_requirements(), cutoff=cutoff, vintage_policy=vintage_policy, frequency=self.frequency)

    def fit(self, data, *, cutoff, vintage_policy='strict', **options):
        frame = self.frame(data.subset(self.all_requirements()), cutoff=cutoff, vintage_policy=vintage_policy)
        return self.fit_frame(frame, cutoff=cutoff, vintage_policy=vintage_policy, **options)

    def fit_frame(self, frame, *, cutoff, vintage_policy='strict', **options):
        options = dict(self.options, **options)
        if len(frame) < self.min_observations:
            raise ValueError(f'{self.component}: {len(frame)} aligned observations available by {cutoff}; need {self.min_observations}')
        audit = frame.audit()
        core = self.estimate(frame, options)
        parameters = core['parameters']
        process_parameters = {spec.maps_to: parameters[spec.name] for spec in self.parameters
                              if spec.maps_to and parameters.get(spec.name) is not None}
        return Estimate(process_id=self.process_id, component=self.component, method=core.get('method', self.method),
                        parameters=parameters, standard_errors=core['standard_errors'], cutoff=cutoff,
                        vintage_policy=vintage_policy,
                        sample={'start': frame.times[core.get('first_index', 0)], 'end': frame.times[-1],
                                'observations': core['nobs'], 'frequency': self.frequency},
                        data_audit=audit, parameter_specs=[p.to_dict() for p in self.parameters],
                        covariance=core.get('covariance'), posterior=core.get('posterior'),
                        diagnostics=core.get('diagnostics', {}), process_parameters=process_parameters,
                        limitations=list(self.spec.get('limitations', [])) + [
                            'Reduced-form estimate; parameters map to simulator mechanisms by declared assumption (see hooks).'],
                        options=options)


class DynamicRegression(ComponentEstimator):
    """y_t = f(x_t) where ``design`` may use lags and declared conditional inputs at t, never the target at t.

    Predictive intervals are a *declared* choice, not a by-product. By default the
    forecast distribution is Gaussian with the in-sample residual scale
    (``interval_method='gaussian_in_sample'``). Three further options are
    available and are pre-registered per attempt in ``real_data_plan.json``:

    ``gaussian_trailing``
        Scale from the most recent ``interval_window`` one-step residuals, for
        targets whose error volatility moves between regimes so that a
        full-sample scale is an average of regimes rather than a forecast of the
        current one.
    ``student_t_trailing`` / ``empirical_trailing``
        The same trailing scale with Student-t tails (degrees of freedom from the
        residual kurtosis) or with the empirical quantiles of residuals
        standardized by their own trailing scale — for heavy-tailed or skewed
        forecast errors.

    Two independent variance components can be declared on top of any of them:
    ``interval_parameter_uncertainty`` adds ``x'Vx`` (coefficient uncertainty at
    the forecast row), and ``interval_revision`` adds the dispersion of the
    revisions the publisher has already made by the origin. The second matters
    whenever a real-time forecast is scored against the *final* vintage: the
    anchor level the forecast starts from is later revised, and that revision is
    part of the scored error but not of any in-sample residual.
    """
    link = None
    endogenous = ()
    multiplicative = False  # True: residual scale is proportional to the predicted level (log models).

    def lags(self, options):
        return 1

    def names(self, options):
        raise NotImplementedError

    def design(self, cols, t, options):
        raise NotImplementedError

    def response(self, cols, t, options):
        raise NotImplementedError

    def level(self, cols, t, fitted, options):
        return fitted

    def instruments(self, cols, t, options):
        return []

    def derive(self, coef, cov, options):
        return {}, {}

    def _columns(self, frame):
        cols = {k: list(v) for k, v in frame.columns.items()}
        cols['_time'] = list(frame.times)
        return cols

    def estimate(self, frame, options):
        cols = self._columns(frame)
        names = self.names(options)
        first = self.lags(options)
        if options.get('window'):
            first = max(first, len(frame) - int(options['window']))
        rows = []
        for t in range(first, len(frame)):
            try:
                rows.append((t, self.response(cols, t, options), self.design(cols, t, options), self.instruments(cols, t, options)))
            except (ValueError, ZeroDivisionError, OverflowError):
                continue
        if len(rows) < len(names) + 3:
            raise ValueError(f'{self.component}: only {len(rows)} usable rows for {len(names)} coefficients')
        y = [r[1] for r in rows]
        x = [r[2] for r in rows]
        cov_type, hac = options.get('cov_type', 'HAC'), options.get('hac_lags')
        if self.endogenous:
            exog_idx = [j for j, n in enumerate(names) if n not in self.endogenous]
            endog_idx = [j for j, n in enumerate(names) if n in self.endogenous]
            fit = tsls(y, [[row[j] for j in exog_idx] for row in x], [[row[j] for j in endog_idx] for row in x], [r[3] for r in rows],
                       exog_names=[names[j] for j in exog_idx], endog_names=[names[j] for j in endog_idx], cov_type=cov_type, hac_lags=hac)
        elif self.link:
            fit = binomial_glm(y, x, names=names, link=self.link, cov_type='HC0')
        else:
            fit = ols(y, x, names=names, cov_type=cov_type, hac_lags=hac)
        coef = {n: fit['params'][n] for n in names}
        errors = []
        for (t, _, row, _), in zip(rows):
            eta = math.fsum(coef[n] * v for n, v in zip(names, row))
            fitted = _inv_logit(eta) if self.link else eta
            predicted = self.level(cols, t, fitted, options)
            error = predicted - cols[self.target][t]
            errors.append(error / predicted if self.multiplicative else error)
        sigma = math.sqrt(math.fsum(e * e for e in errors) / max(1, len(errors) - len(names)))
        parameters, ses = self.derive(coef, fit['cov'], options)
        diagnostics = {'coefficients': coef, 'coefficient_se': dict(fit['bse']), 'level_sigma': sigma,
                       'sigma_is_relative': self.multiplicative,
                       'model_sigma': fit.get('sigma'), 'r2': fit.get('r2'), 'pseudo_r2': fit.get('pseudo_r2'),
                       'cov_type': 'HC0' if self.link else cov_type, 'nobs': len(rows),
                       'first_stage': fit.get('first_stage'), 'overidentification': fit.get('overidentification')}
        out_of_sample = None
        if options.get('interval_method') in intervals.OUT_OF_SAMPLE_METHODS:
            out_of_sample = self.recursive_errors(cols, rows, names, options)
        predictive = self.predictive_specification(frame, errors, len(names), options, out_of_sample=out_of_sample)
        if predictive is not None:
            diagnostics['predictive'] = predictive
        return {'parameters': parameters, 'standard_errors': ses, 'nobs': len(rows), 'first_index': rows[0][0],
                'covariance': {'names': ['coefficient:' + n for n in fit['cov']['names']], 'matrix': fit['cov']['matrix']},
                'diagnostics': diagnostics}

    def recursive_errors(self, cols, rows, names, options):
        """Pre-origin out-of-sample one-step errors for ``conformal_rolling``.

        For each of the last ``interval_window`` usable rows k (all of them when no
        window is declared), the coefficients are re-estimated on rows ``0..k-1`` only
        and row k is predicted through the same ``level`` mapping as a real forecast;
        the error is in the same units as the in-sample residuals (relative for
        multiplicative components). Every value read is in the origin's own frame, so
        nothing after the origin enters. The first recursive fit needs
        ``len(names) + 3`` rows, the estimator's own minimum. Plain least squares
        accumulates X'X and X'y row by row (the same normal equations ``ols`` solves);
        link-function and instrumented designs refit from scratch.
        """
        if options.get('window'):
            raise ValueError(f'{self.component}: conformal_rolling is not declared for rolling-window fits')
        window = options.get('interval_window')
        first = len(names) + 3
        start = first if window is None else max(first, len(rows) - int(window))
        errors = []
        simple = not self.endogenous and not self.link
        if simple:
            k = len(names)
            xtx = [[0.0] * k for _ in range(k)]
            xty = [0.0] * k
            for _, y, row, _ in rows[:start]:
                for i in range(k):
                    xty[i] += row[i] * y
                    for j in range(k):
                        xtx[i][j] += row[i] * row[j]
        for index in range(start, len(rows)):
            t, y, row, _ = rows[index]
            try:
                if simple:
                    beta = la.solve(xtx, xty)
                else:
                    prefix = rows[:index]
                    ys, xs = [r[1] for r in prefix], [r[2] for r in prefix]
                    if self.endogenous:
                        exog = [j for j, n in enumerate(names) if n not in self.endogenous]
                        endog = [j for j, n in enumerate(names) if n in self.endogenous]
                        fit = tsls(ys, [[r[j] for j in exog] for r in xs], [[r[j] for j in endog] for r in xs],
                                   [r[3] for r in prefix], exog_names=[names[j] for j in exog],
                                   endog_names=[names[j] for j in endog], cov_type='nonrobust')
                    else:
                        fit = binomial_glm(ys, xs, names=names, link=self.link, cov_type='HC0')
                    beta = [fit['params'][n] for n in names]
                eta = math.fsum(b * v for b, v in zip(beta, row))
                predicted = self.level(cols, t, _inv_logit(eta) if self.link else eta, options)
                error = predicted - cols[self.target][t]
                errors.append(error / predicted if self.multiplicative else error)
            except (ValueError, ZeroDivisionError, OverflowError):
                pass
            if simple:
                for i in range(len(names)):
                    xty[i] += row[i] * y
                    for j in range(len(names)):
                        xtx[i][j] += row[i] * row[j]
        return errors

    def predictive_specification(self, frame, errors, coefficients, options, out_of_sample=None):
        """Declared predictive distribution of a one-step error, or ``None`` for the default.

        ``None`` means "Gaussian with ``level_sigma``", which is what every
        attempt recorded before interval methods became declarable; returning it
        keeps those runs bit-identical.
        """
        method = options.get('interval_method', 'gaussian_in_sample')
        revision = options.get('interval_revision')
        if method == 'gaussian_in_sample' and not revision:
            return None
        extra = {}
        if revision:
            scale, diagnostics = self.revision_scale(frame, revision if isinstance(revision, dict) else {})
            extra['revision'] = scale
        spec = intervals.from_errors(errors, method=method, window=options.get('interval_window'),
                                     nodes=int(options.get('interval_nodes', intervals.DEFAULT_NODES)),
                                     dof=coefficients, extra=extra, out_of_sample=out_of_sample)
        if revision:
            spec['revision_diagnostics'] = diagnostics
        return spec

    def revision_scale(self, frame, declared):
        """Dispersion of the revisions the publisher has already made to the target series.

        For every period in the fit frame that is at least ``maturity`` periods
        old — old enough for most of its revisions to have been published by the
        origin — the realized revision is ``value / first_value`` (in logs for
        multiplicative components, in levels otherwise), where ``first_value`` is
        the earliest vintage available at the origin and ``value`` the latest.
        The reported scale is the standard deviation of those revisions over the
        most recent ``window`` mature periods; the mean is reported separately and
        deliberately *not* used to shift the point forecast, so a systematic
        revision bias stays visible instead of being absorbed.
        """
        window = int(declared.get('window', 120))
        maturity = int(declared.get('maturity', 12))
        if window < 8 or maturity < 0:
            raise ValueError('interval_revision needs window >= 8 and maturity >= 0')
        points = frame.series[self.target].points
        revisions = []
        for index, point in enumerate(points):
            if point.first_value is None or len(points) - 1 - index < maturity:
                continue
            if self.multiplicative:
                if point.value <= 0 or point.first_value <= 0:
                    continue
                revisions.append(math.log(point.value / point.first_value))
            else:
                revisions.append(point.value - point.first_value)
        revisions = revisions[-window:]
        if len(revisions) < 8:
            raise ValueError(f'{self.component}: only {len(revisions)} mature revisions for interval_revision')
        mean = math.fsum(revisions) / len(revisions)
        variance = math.fsum((r - mean) ** 2 for r in revisions) / (len(revisions) - 1)
        return math.sqrt(variance), {'observations': len(revisions), 'window': window, 'maturity_periods': maturity,
                                     'mean_revision': mean, 'relative': self.multiplicative,
                                     'largest_absolute_revision': max(abs(r) for r in revisions)}

    def predict_options(self, estimate):
        return dict(estimate.options)

    def _parameter_scale(self, estimate, row, options):
        """Standard deviation contributed by coefficient uncertainty at this forecast row."""
        covariance = estimate.covariance
        if not covariance:
            raise ValueError(f'{self.component}: interval_parameter_uncertainty needs a coefficient covariance')
        if self.link:
            raise ValueError(f'{self.component}: interval_parameter_uncertainty is undefined through a link function')
        names = ['coefficient:' + n for n in self.names(options)]
        index = {name: i for i, name in enumerate(covariance['names'])}
        matrix = covariance['matrix']
        variance = math.fsum(row[a] * matrix[index[names[a]]][index[names[b]]] * row[b]
                             for a in range(len(row)) for b in range(len(row)))
        return math.sqrt(max(variance, 0.0))

    def predict(self, estimate, frame, horizon=1, conditional=None, target=None):
        if target not in (None, self.target):
            raise ValueError(f'{self.component} forecasts only {self.target}')
        if self.conditional_inputs and horizon != 1:
            raise ValueError('Conditional-input estimators forecast one step')
        options = self.predict_options(estimate)
        conditional = conditional or {}
        missing = set(self.conditional_inputs) - set(conditional)
        if missing:
            raise ValueError(f'Missing conditional inputs: {sorted(missing)}')
        coef = estimate.diagnostics['coefficients']
        names = self.names(options)
        cols = self._columns(frame)
        value, design = None, None
        for step in range(horizon):
            last = instant(cols['_time'][-1]).date()
            for name in cols:
                cols[name].append(None)
            cols['_time'][-1] = period_end(last, self.frequency).isoformat()
            for name in self.conditional_inputs:
                cols[name][-1] = float(conditional[name][step])
            t = len(cols[self.target]) - 1
            try:
                row = self.design(cols, t, options)
                eta = math.fsum(coef[n] * v for n, v in zip(names, row))
                value = self.level(cols, t, _inv_logit(eta) if self.link else eta, options)
            except TypeError as error:
                raise LeakageError(f'{self.component}: forecast design read a value unavailable at the origin') from error
            cols[self.target][-1] = value
            design = row
        scale = abs(value) if self.multiplicative else 1.0
        spec = estimate.diagnostics.get('predictive')
        if spec is None:
            return {'mean': value, 'sd': estimate.diagnostics['level_sigma'] * scale * math.sqrt(horizon)}
        if options.get('interval_parameter_uncertainty'):
            total, _ = intervals.combine(predictive=intervals.standard_deviation(spec),
                                         parameters=self._parameter_scale(estimate, design, options))
            spec = intervals.rescale(spec, total / intervals.standard_deviation(spec))
        spec = intervals.rescale(spec, scale * math.sqrt(horizon))
        out = {'mean': value, 'sd': intervals.standard_deviation(spec)}
        if spec['family'] != 'normal':      # a normal predictive is fully described by ``sd``
            out['predictive'] = intervals.strip(spec)
        return out


def _ses(values, ses, names):
    return dict(zip(names, values)), dict(zip(names, ses))


class PopulationGrowthEstimator(DynamicRegression):
    component = 'population_growth_rate'
    multiplicative = True

    def names(self, options):
        return ['drift']

    def design(self, cols, t, options):
        return [1.0]

    def response(self, cols, t, options):
        return _dlog(cols['population'][t - 1], cols['population'][t])

    def level(self, cols, t, fitted, options):
        return cols['population'][t - 1] * math.exp(fitted)

    def derive(self, coef, cov, options):
        periods_per_year = YEAR_SECONDS / PERIOD_SECONDS[self.frequency]
        rate = coef['drift'] * periods_per_year
        se = math.sqrt(max(cov['matrix'][0][0], 0.0)) * periods_per_year
        return ({'growth_rate_per_year': rate, 'annual_growth_fraction': math.expm1(rate)},
                {'growth_rate_per_year': se, 'annual_growth_fraction': se * math.exp(rate)})

    def estimate(self, frame, options):
        core = super().estimate(frame, options)
        if options.get('growth_diagnostics'):
            from .growth import fit_exponential_growth, fit_logistic_growth, year_fraction
            years = [year_fraction(t) for t in frame.times]
            values = frame.columns['population']
            diagnostics = {'exponential_trend': {k: v for k, v in fit_exponential_growth(years, values).items() if k != 'regression'}}
            try:
                diagnostics['logistic'] = fit_logistic_growth(years, values)
            except ValueError as error:
                diagnostics['logistic'] = {'error': str(error)}
            core['diagnostics']['growth_models'] = diagnostics
        return core


class InventoryBalanceEstimator(DynamicRegression):
    component = 'inventory_balance'

    def names(self, options):
        return ['unmeasured_change', 'flow_scale']

    def design(self, cols, t, options):
        days = PERIOD_SECONDS[self.frequency] / 86400
        net = cols['production'][t] + cols['imports'][t] - cols['exports'][t] - cols['refinery_input'][t]
        return [1.0, days * net]

    def response(self, cols, t, options):
        return cols['crude_stocks'][t] - cols['crude_stocks'][t - 1]

    def level(self, cols, t, fitted, options):
        return cols['crude_stocks'][t - 1] + fitted

    def derive(self, coef, cov, options):
        scale = 1000 / PERIOD_SECONDS[self.frequency]  # thousand barrels per period -> barrels per second
        se = {n: math.sqrt(max(cov['matrix'][i][i], 0.0)) for i, n in enumerate(cov['names'])}
        return ({'flow_scale': coef['flow_scale'], 'unmeasured_net_flow': coef['unmeasured_change'] * scale},
                {'flow_scale': se['flow_scale'], 'unmeasured_net_flow': se['unmeasured_change'] * scale})


class CashBalanceEstimator(DynamicRegression):
    component = 'cash_balance'

    def names(self, options):
        return ['unmeasured_change', 'cash_conversion']

    def design(self, cols, t, options):
        return [1.0, cols['revenue'][t] - cols['operating_costs'][t] - cols['capital_expenditure'][t]]

    def response(self, cols, t, options):
        return cols['cash'][t] - cols['cash'][t - 1]

    def level(self, cols, t, fitted, options):
        return cols['cash'][t - 1] + fitted

    def derive(self, coef, cov, options):
        seconds = PERIOD_SECONDS[self.frequency]
        se = {n: math.sqrt(max(cov['matrix'][i][i], 0.0)) for i, n in enumerate(cov['names'])}
        return ({'cash_conversion': coef['cash_conversion'], 'unmeasured_net_cash_flow': coef['unmeasured_change'] / seconds},
                {'cash_conversion': se['cash_conversion'], 'unmeasured_net_cash_flow': se['unmeasured_change'] / seconds})


class InterestPassThroughEstimator(DynamicRegression):
    """Δy_t = c + β Δx_t + α y_{t-1} + δ x_{t-1} + Σγ Δy_{t-i}; long run y = -c/α - (δ/α) x."""
    component = 'interest_pass_through'
    response_name, driver_name = 'loan_rate', 'policy_rate'

    def lags(self, options):
        return 1 + int(options.get('ecm_lags', 1))

    def names(self, options):
        return ['const', 'impact', 'alpha', 'delta'] + [f'gamma{i}' for i in range(1, int(options.get('ecm_lags', 1)) + 1)]

    def design(self, cols, t, options):
        y, x = cols[self.response_name], cols[self.driver_name]
        return [1.0, x[t] - x[t - 1], y[t - 1], x[t - 1]] + [y[t - i] - y[t - i - 1] for i in range(1, int(options.get('ecm_lags', 1)) + 1)]

    def response(self, cols, t, options):
        return cols[self.response_name][t] - cols[self.response_name][t - 1]

    def level(self, cols, t, fitted, options):
        return cols[self.response_name][t - 1] + fitted

    def derive(self, coef, cov, options):
        names = ['const', 'impact', 'alpha', 'delta']

        def transform(p):
            alpha = p['alpha'] if abs(p['alpha']) > 1e-12 else -1e-12
            speed = -p['alpha']
            daily = 1 - (1 - speed) ** (1 / DAYS_PER_MONTH) if 0 < speed < 1 else speed / DAYS_PER_MONTH
            return [-p['const'] / alpha / 100, -p['delta'] / alpha, speed, daily, p['impact']]

        values, ses = _derived(transform, coef, names, cov)
        return _ses(values, ses, ['spread', 'pass_through', 'adjustment_speed_per_month', 'adjustment_speed', 'impact_pass_through'])


class DepositRatePassThroughEstimator(InterestPassThroughEstimator):
    component = 'deposit_rate_pass_through'
    response_name, driver_name = 'deposit_rate', 'policy_rate'


class DefaultHazardEstimator(DynamicRegression):
    """Fractional logit: rate_t = Λ(b0 + ρ logit(rate_{t-1}) + b_u u_t + b_i i_{t-1})."""
    component = 'default_hazard'
    link = 'logit'

    def names(self, options):
        return ['hazard_intercept', 'persistence', 'unemployment_sensitivity', 'rate_sensitivity']

    def design(self, cols, t, options):
        return [1.0, _logit(cols['delinquency_rate'][t - 1] / 100), cols['unemployment_rate'][t] / 100, cols['policy_rate'][t - 1] / 100]

    def response(self, cols, t, options):
        value = cols['delinquency_rate'][t] / 100
        if not 0 <= value <= 1:
            raise ValueError('rate outside [0,1]')
        return value

    def level(self, cols, t, fitted, options):
        return fitted * 100

    def derive(self, coef, cov, options):
        se = {n: math.sqrt(max(cov['matrix'][i][i], 0.0)) for i, n in enumerate(cov['names'])}
        return dict(coef), se


class _LogGrowthADL(DynamicRegression):
    level_name = None
    multiplicative = True

    def response(self, cols, t, options):
        return _dlog(cols[self.level_name][t - 1], cols[self.level_name][t])

    def level(self, cols, t, fitted, options):
        return cols[self.level_name][t - 1] * math.exp(fitted)

    def lags(self, options):
        return 2


class DepositGrowthEstimator(_LogGrowthADL):
    component = 'deposit_growth'
    level_name = 'deposits'

    def names(self, options):
        return ['const', 'persistence', 'rate_change']

    def design(self, cols, t, options):
        d = cols['deposits']
        return [1.0, _dlog(d[t - 2], d[t - 1]), (cols['policy_rate'][t] - cols['policy_rate'][t - 1]) / 100]

    def derive(self, coef, cov, options):
        names = ['const', 'persistence', 'rate_change']
        values, ses = _derived(lambda p: [p['const'] / (1 - p['persistence']), p['persistence'], p['rate_change']], coef, names, cov)
        return _ses(values, ses, ['mean_growth_per_month', 'persistence', 'rate_semi_elasticity'])


class EnergyPurchasingEstimator(_LogGrowthADL):
    component = 'energy_purchasing'
    level_name = 'real_sales'

    def names(self, options):
        return ['const', 'persistence', 'energy_price_change', 'rate_change']

    def design(self, cols, t, options):
        s = cols['real_sales']
        return [1.0, _dlog(s[t - 2], s[t - 1]), _dlog(cols['crude_price'][t - 1], cols['crude_price'][t]),
                (cols['policy_rate'][t] - cols['policy_rate'][t - 1]) / 100]

    def derive(self, coef, cov, options):
        names = ['energy_price_change', 'rate_change', 'persistence']
        values, ses = _derived(lambda p: [-p['energy_price_change'], -p['rate_change'], p['persistence']], coef, names, cov)
        return _ses(values, ses, ['energy_response', 'rate_response', 'persistence'])


class LaborDemandEstimator(_LogGrowthADL):
    component = 'labor_demand'
    level_name = 'employment'

    def names(self, options):
        return ['const', 'persistence', 'output_growth']

    def design(self, cols, t, options):
        n = cols['employment']
        return [1.0, _dlog(n[t - 2], n[t - 1]), _dlog(cols['output'][t - 1], cols['output'][t])]

    def derive(self, coef, cov, options):
        names = ['persistence', 'output_growth']
        values, ses = _derived(lambda p: [p['output_growth'] / (1 - p['persistence']), p['output_growth'], p['persistence']], coef, names, cov)
        return _ses(values, ses, ['employment_output_elasticity', 'impact_elasticity', 'persistence'])


class CreditGrowthEstimator(_LogGrowthADL):
    """AR(p) on monthly log growth with p selected by BIC on a common sample."""
    component = 'credit_growth'
    level_name = 'consumer_credit'

    def lags(self, options):
        return int(options.get('max_order', 6)) + 1

    def names(self, options):
        return ['const'] + [f'phi{i}' for i in range(1, int(options['order']) + 1)]

    def design(self, cols, t, options):
        c = cols['consumer_credit']
        return [1.0] + [_dlog(c[t - i - 1], c[t - i]) for i in range(1, int(options['order']) + 1)]

    def estimate(self, frame, options):
        levels = frame.columns['consumer_credit']
        growth = [_dlog(a, b) for a, b in zip(levels, levels[1:])]
        selection = select_ar_order(growth, int(options.get('max_order', 6)), criterion=options.get('criterion', 'bic'))
        core = super().estimate(frame, dict(options, order=selection['order']))
        core['diagnostics']['order'] = selection['order']
        core['diagnostics']['order_selection'] = selection
        return core

    def predict_options(self, estimate):
        return dict(estimate.options, order=estimate.diagnostics['order'])

    def derive(self, coef, cov, options):
        names = self.names(options)
        phis = names[1:]
        values, ses = _derived(lambda p: [p['const'] / (1 - math.fsum(p[n] for n in phis)), math.fsum(p[n] for n in phis)], coef, names, cov)
        return _ses(values, ses, ['mean_growth_per_month', 'persistence_sum'])


class DemandElasticityEstimator(DynamicRegression):
    """log q_t = a + ρ log q_{t-1} + Fourier(month) + ε log p_t; log p_t instrumented by log crude price."""
    component = 'demand_price_elasticity'
    multiplicative = True
    endogenous = ('log_price',)

    def names(self, options):
        return ['const', 'lagged_log_quantity', 'sin1', 'cos1', 'sin2', 'cos2', 'log_price']

    def design(self, cols, t, options):
        month = instant(cols['_time'][t]).month
        angle = 2 * math.pi * month / 12
        if cols['quantity'][t - 1] <= 0 or cols['retail_price'][t] <= 0:
            raise ValueError('log of nonpositive value')
        return [1.0, math.log(cols['quantity'][t - 1]), math.sin(angle), math.cos(angle), math.sin(2 * angle), math.cos(2 * angle),
                math.log(cols['retail_price'][t])]

    def instruments(self, cols, t, options):
        if cols['crude_price'][t] <= 0:
            raise ValueError('log of nonpositive value')
        return [math.log(cols['crude_price'][t])]

    def response(self, cols, t, options):
        if cols['quantity'][t] <= 0:
            raise ValueError('log of nonpositive value')
        return math.log(cols['quantity'][t])

    def level(self, cols, t, fitted, options):
        return math.exp(fitted)

    def derive(self, coef, cov, options):
        names = ['lagged_log_quantity', 'log_price']
        values, ses = _derived(lambda p: [-p['log_price'] / (1 - p['lagged_log_quantity']), p['log_price'], p['lagged_log_quantity']], coef, names, cov)
        return _ses(values, ses, ['elasticity', 'short_run_elasticity', 'persistence'])


class PriceAdjustmentEstimator(DynamicRegression):
    """Δlog p_t = drift + a·gap_{t-1} + b·Δlog crude_t, gap = (trailing mean inventory - inventory)/trailing mean."""
    component = 'price_adjustment'
    multiplicative = True

    def lags(self, options):
        return int(options.get('target_window', 12)) + 1

    def names(self, options):
        return ['drift', 'inventory_gap', 'crude_cost_change']

    def design(self, cols, t, options):
        window = int(options.get('target_window', 12))
        inventory = cols['inventory']
        target = math.fsum(inventory[t - 1 - window:t - 1]) / window
        if target <= 0:
            raise ValueError('nonpositive inventory target')
        return [1.0, (target - inventory[t - 1]) / target, _dlog(cols['crude_price'][t - 1], cols['crude_price'][t])]

    def response(self, cols, t, options):
        return _dlog(cols['retail_price'][t - 1], cols['retail_price'][t])

    def level(self, cols, t, fitted, options):
        return cols['retail_price'][t - 1] * math.exp(fitted)

    def derive(self, coef, cov, options):
        per_period_days = PERIOD_SECONDS[self.frequency] / 86400
        names = ['drift', 'inventory_gap', 'crude_cost_change']
        values, ses = _derived(lambda p: [p['inventory_gap'], p['inventory_gap'] / per_period_days, p['crude_cost_change'], p['drift']], coef, names, cov)
        return _ses(values, ses, ['adjustment_per_month', 'adjustment', 'cost_pass_through', 'drift_per_month'])


class PolicyRuleEstimator(DynamicRegression):
    """i_t = c + ρ i_{t-1} + b π_t + d gap_t; responses b/(1-ρ), d/(1-ρ)."""
    component = 'policy_rule'

    def lags(self, options):
        return 4

    def names(self, options):
        return ['const', 'smoothing', 'inflation', 'output_gap']

    def design(self, cols, t, options):
        prices = cols['price_index']
        if prices[t - 4] <= 0 or cols['potential_gdp'][t] <= 0:
            raise ValueError('nonpositive level')
        inflation = 100 * (prices[t] / prices[t - 4] - 1)
        gap = 100 * (cols['real_gdp'][t] - cols['potential_gdp'][t]) / cols['potential_gdp'][t]
        return [1.0, cols['policy_rate'][t - 1], inflation, gap]

    def response(self, cols, t, options):
        return cols['policy_rate'][t]

    def derive(self, coef, cov, options):
        target = float(options.get('inflation_target', 0.02))
        names = ['const', 'smoothing', 'inflation', 'output_gap']

        def transform(p):
            gap = 1 - p['smoothing']
            phi_pi, phi_y = p['inflation'] / gap, p['output_gap'] / gap
            return [p['smoothing'], phi_pi, phi_y, (p['const'] / gap + phi_pi * target * 100) / 100]

        values, ses = _derived(transform, coef, names, cov)
        parameters, errors = _ses(values, ses, ['smoothing', 'inflation_response', 'output_response', 'reference_rate'])
        parameters['inflation_target'] = target
        errors['inflation_target'] = None
        return parameters, errors


class FieldDiffusionEstimator(ComponentEstimator):
    """Pooled explicit-Euler regression of cell amount changes on diffusion, transport, decay and source terms.

    Requires ``options['topology'] = {'cells': [{'id', 'measure'}], 'edges': [{'source', 'target'}]}``
    matching :class:`worldmodel.fields.FieldWorld` semantics: conductance acts on
    concentration differences across each edge; transport_rate moves a fraction of
    the source amount toward the target.
    """
    component = 'field_diffusion_transport'

    def __init__(self, overrides=None, options=None):
        super().__init__(overrides=None, options=options)
        topology = self.options.get('topology')
        if not isinstance(topology, dict) or not topology.get('cells'):
            raise ValueError('field_diffusion_transport requires options.topology with cells and edges')
        self.cells = {c['id']: float(c['measure']) for c in topology['cells']}
        if any(m <= 0 for m in self.cells.values()):
            raise ValueError('Cell measures must be positive')
        self.edges = [(e['source'], e['target']) for e in topology.get('edges', [])]
        if any(a not in self.cells or b not in self.cells for a, b in self.edges):
            raise ValueError('Edges must reference declared cells')
        self.frequency = self.options.get('frequency', self.frequency)
        template = dict(self.spec['series_template'], frequency=self.frequency)
        template.update((overrides or {}).get('cell', {}))
        self.requirements = tuple(SeriesRequirement.from_dict({**template, 'name': cell, 'subject': template['subject'].replace('{cell}', cell)})
                                  for cell in self.cells)
        self.targets = list(self.options.get('targets') or self.cells)
        self.target = self.targets[0]

    def _names(self, options):
        return ['conductance', 'transport_rate'] + (['decay_rate'] if options.get('include_decay') else []) + (['source_rate'] if options.get('include_source') else [])

    def _terms(self, concentrations, options):
        amounts = {c: concentrations[c] * (self.cells[c] if options.get('kind') == 'intensive' else 1.0) for c in self.cells}
        conc = {c: amounts[c] / self.cells[c] for c in self.cells}
        terms = {c: {'conductance': 0.0, 'transport_rate': 0.0, 'decay_rate': -amounts[c], 'source_rate': self.cells[c]} for c in self.cells}
        for a, b in self.edges:
            terms[a]['conductance'] += conc[b] - conc[a]
            terms[b]['conductance'] += conc[a] - conc[b]
            terms[a]['transport_rate'] -= amounts[a]
            terms[b]['transport_rate'] += amounts[a]
        return amounts, terms

    def estimate(self, frame, options):
        dt = PERIOD_SECONDS[self.frequency]
        names = self._names(options)
        y, x = [], []
        for t in range(1, len(frame)):
            before = {c: frame.columns[c][t - 1] for c in self.cells}
            after = {c: frame.columns[c][t] for c in self.cells}
            amounts, terms = self._terms(before, options)
            next_amounts, _ = self._terms(after, options)
            for cell in self.cells:
                y.append((next_amounts[cell] - amounts[cell]) / dt)
                x.append([terms[cell][n] for n in names])
        fit = ols(y, x, names=names, cov_type=options.get('cov_type', 'HC1'))
        coef = dict(fit['params'])
        degree = {c: 0 for c in self.cells}
        out_degree = {c: 0 for c in self.cells}
        for a, b in self.edges:
            degree[a] += 1
            degree[b] += 1
            out_degree[a] += 1
        stability = max(dt * (max(coef['conductance'], 0) * degree[c] / self.cells[c] + max(coef['transport_rate'], 0) * out_degree[c]) for c in self.cells)
        errors = []
        for t in range(1, len(frame)):
            predicted = self._step({c: frame.columns[c][t - 1] for c in self.cells}, coef, options)
            errors.extend(predicted[c] - frame.columns[c][t] for c in self.targets)
        sigma = math.sqrt(math.fsum(e * e for e in errors) / max(1, len(errors) - len(names)))
        parameters = {n: coef.get(n, 0.0) for n in ['conductance', 'transport_rate', 'decay_rate', 'source_rate']}
        return {'parameters': parameters, 'standard_errors': {n: fit['bse'].get(n) for n in parameters}, 'nobs': len(y), 'first_index': 0,
                'covariance': fit['cov'], 'diagnostics': {'coefficients': coef, 'level_sigma': sigma, 'r2': fit['r2'],
                                                          'euler_stability_number': stability, 'euler_stable': stability < 1,
                                                          'cells': len(self.cells), 'edges': len(self.edges), 'dt_seconds': dt}}

    def _step(self, concentrations, coef, options):
        dt = PERIOD_SECONDS[self.frequency]
        amounts, terms = self._terms(concentrations, options)
        out = {}
        for cell in self.cells:
            change = math.fsum(coef.get(n, 0.0) * terms[cell][n] for n in self._names(options))
            amount = amounts[cell] + dt * change
            out[cell] = amount / self.cells[cell] if options.get('kind') == 'intensive' else amount
        return out

    def predict(self, estimate, frame, horizon=1, conditional=None, target=None):
        target = target or self.target
        state = {c: frame.columns[c][-1] for c in self.cells}
        for _ in range(horizon):
            state = self._step(state, estimate.diagnostics['coefficients'], estimate.options)
        return {'mean': state[target], 'sd': estimate.diagnostics['level_sigma'] * math.sqrt(horizon)}


class GravityEstimator(ComponentEstimator):
    """PPML gravity with origin/destination fixed effects on annual bilateral flows; cross-sectional holdout by year."""
    component = 'bilateral_flow_gravity'

    def _pairs(self, data, cutoff, vintage_policy):
        cut = instant(cutoff)
        flow_req, distance_req = self.requirements
        flows, distances, audit = {}, {}, {'records': 0, 'max_available_at': None, 'vintage_modes': set(), 'evidence': []}
        for record in data.records:
            for requirement, store in ((flow_req, flows), (distance_req, distances)):
                if not requirement.matches(record):
                    continue
                dims = record.get('dimensions') or {}
                if not dims.get('origin') or not dims.get('destination') or record.get('value') is None:
                    continue
                available, vintage = availability(record, requirement, vintage_policy)
                if available > cut or instant(record['valid_from']) > cut:
                    continue
                key = (dims['origin'], dims['destination']) + ((instant(record['valid_from']).year,) if store is flows else ())
                if key not in store or available > store[key][0]:
                    store[key] = (available, float(record['value']))
                audit['records'] += 1
                audit['vintage_modes'].add(vintage['mode'])
                if audit['max_available_at'] is None or available > audit['max_available_at']:
                    audit['max_available_at'] = available
                if record.get('_input') and len(audit['evidence']) < 2000:
                    audit['evidence'].append({'input': record['_input'], 'record_id': record.get('id')})
        rows = [(o, d, year, value) for (o, d, year), (_, value) in sorted(flows.items()) if (o, d) in distances]
        if audit['max_available_at'] is not None and audit['max_available_at'] > cut:
            raise LeakageError('Gravity record after cutoff')
        revisions = {r.name: r.revisions for r in self.requirements}
        audit = {'series': {'flow': {'count': len(rows), 'vintage_modes': sorted(audit['vintage_modes']), 'revisions': revisions['flow'],
                                     'revision_leakage_possible': any(m != 'real_time' for m in audit['vintage_modes']) and revisions['flow'] != 'none',
                                     'max_available_at': audit['max_available_at'].isoformat() if audit['max_available_at'] else None,
                                     'evidence': {'records': audit['records'], 'sample': audit['evidence']}}},
                 'cutoff': cutoff, 'vintage_policy': vintage_policy}
        return rows, {(o, d): v for (o, d), (_, v) in distances.items()}, audit

    def _design(self, rows, distances, levels, years):
        origin_levels, destination_levels = levels
        x = []
        for o, d, year, _ in rows:
            row = [1.0, math.log(distances[(o, d)])]
            row += [1.0 if o == level else 0.0 for level in origin_levels[1:]]
            row += [1.0 if d == level else 0.0 for level in destination_levels[1:]]
            row += [1.0 if year == level else 0.0 for level in years[1:]]
            x.append(row)
        names = ['const', 'log_distance'] + [f'origin:{v}' for v in origin_levels[1:]] + [f'destination:{v}' for v in destination_levels[1:]] + [f'year:{v}' for v in years[1:]]
        return x, names

    def fit(self, data, *, cutoff, vintage_policy='strict', **options):
        rows, distances, audit = self._pairs(data.subset(self.all_requirements()), cutoff, vintage_policy)
        if len(rows) < self.min_observations:
            raise ValueError(f'bilateral_flow_gravity: {len(rows)} flows available by {cutoff}; need {self.min_observations}')
        origins = sorted({r[0] for r in rows})
        destinations = sorted({r[1] for r in rows})
        years = sorted({r[2] for r in rows})
        x, names = self._design(rows, distances, (origins, destinations), years)
        fit = poisson_glm([r[3] for r in rows], x, names=names, cov_type='HC0')
        k = len(names)
        pearson = math.fsum((r[3] - m) ** 2 / m for r, m in zip(rows, fit['fitted']) if m > 0) / max(1, len(rows) - k)
        parameters = {'distance_elasticity': fit['params']['log_distance']}
        return Estimate(process_id=self.process_id, component=self.component, method=self.method, parameters=parameters,
                        standard_errors={'distance_elasticity': fit['bse']['log_distance']}, cutoff=cutoff, vintage_policy=vintage_policy,
                        sample={'start': str(years[0]), 'end': str(years[-1]), 'observations': len(rows), 'frequency': 'annual'},
                        data_audit=audit, parameter_specs=[p.to_dict() for p in self.parameters],
                        covariance=None, diagnostics={'coefficients': dict(fit['params']), 'pearson_dispersion': pearson,
                                                      'levels': {'origins': origins, 'destinations': destinations, 'years': years},
                                                      'converged': fit['converged'], 'deviance': fit['deviance']},
                        process_parameters={'routes.distance_elasticity': parameters['distance_elasticity']}, options=dict(self.options, **options),
                        limitations=['Cross-sectional PPML; distance elasticity is descriptive, not a causal trade-cost response.'])

    def backtest(self, data, *, start, end, evaluation_cutoff, vintage_policy='strict', interval_level=0.8, baselines=('persistence', 'historical_mean'), **_):
        from .validation import score_forecasts
        data = data.subset(self.all_requirements())
        truth, distances, _ = self._pairs(data, evaluation_cutoff, vintage_policy)
        lo, hi = instant(start).year, instant(end).year
        forecasts, skipped, checked = [], [], 0
        target_years = sorted({r[2] for r in truth if lo < r[2] <= hi and instant(f'{r[2]}-01-01') > instant(start)})
        lag = self.requirements[0].publication_lag_days
        for year in target_years:
            origin_cutoff = (instant(f'{year - 1}-12-31') + __import__('datetime').timedelta(days=lag + 1)).isoformat()
            if instant(origin_cutoff) > instant(evaluation_cutoff):
                skipped.append({'time': str(year), 'reason': 'origin_after_evaluation_cutoff'})
                continue
            try:
                estimate = self.fit(data, cutoff=origin_cutoff, vintage_policy=vintage_policy)
            except ValueError as error:
                skipped.append({'time': str(year), 'reason': 'fit_failed', 'error': str(error)})
                continue
            checked += 1
            history, _, _ = self._pairs(data, origin_cutoff, vintage_policy)
            if any(r[2] >= year for r in history):
                raise LeakageError('Target-year flows visible at gravity origin')
            by_pair = {}
            for o, d, y, v in history:
                by_pair.setdefault((o, d), []).append((y, v))
            changes = [b[1] - a[1] for series in by_pair.values() for a, b in zip(sorted(series), sorted(series)[1:])]
            change_sd = math.sqrt(math.fsum(c * c for c in changes) / len(changes)) if changes else 0.0
            coef, levels = estimate.diagnostics['coefficients'], estimate.diagnostics['levels']
            for o, d, y, actual in truth:
                if y != year or (o, d) not in by_pair or o not in levels['origins'] or d not in levels['destinations']:
                    continue
                eta = coef['const'] + coef['log_distance'] * math.log(distances[(o, d)])
                eta += coef.get(f'origin:{o}', 0.0) + coef.get(f'destination:{d}', 0.0) + coef.get(f'year:{levels["years"][-1]}', 0.0)
                mean = math.exp(eta)
                series = sorted(by_pair[(o, d)])
                values = [v for _, v in series]
                historical = math.fsum(values) / len(values)
                spread = math.sqrt(math.fsum((v - historical) ** 2 for v in values) / max(1, len(values) - 1))
                forecasts.append({'time': str(year), 'target': f'{o}->{d}', 'origin_cutoff': origin_cutoff, 'actual': actual, 'mean': mean,
                                  'sd': math.sqrt(max(mean, 0.0) * max(estimate.diagnostics['pearson_dispersion'], 1e-12)),
                                  'baselines': {'persistence': {'mean': values[-1], 'sd': change_sd},
                                                'historical_mean': {'mean': historical, 'sd': spread}}})
        scored = score_forecasts(forecasts, baselines=[b for b in baselines if b in ('persistence', 'historical_mean')], interval_level=interval_level)
        return {'forecasts': forecasts, 'skipped': skipped, **scored, 'mase_scale': None, 'fits': checked,
                'leakage_audit': {'origins_checked': checked, 'violations': 0, 'truncated_future_rows': 0,
                                  'vintage_modes': sorted({m for r in [self._pairs(data, evaluation_cutoff, vintage_policy)[2]] for m in r['series']['flow']['vintage_modes']}),
                                  'conditional_inputs': ['distance'], 'forecast_type': 'cross_sectional_next_year', 'evaluation_cutoff': evaluation_cutoff}}


ESTIMATORS = {cls.component: cls for cls in (
    PopulationGrowthEstimator, InventoryBalanceEstimator, CashBalanceEstimator, InterestPassThroughEstimator,
    DepositRatePassThroughEstimator, DefaultHazardEstimator, DepositGrowthEstimator, CreditGrowthEstimator,
    DemandElasticityEstimator, PriceAdjustmentEstimator, EnergyPurchasingEstimator, LaborDemandEstimator,
    PolicyRuleEstimator, FieldDiffusionEstimator, GravityEstimator)}


def estimator_for(component, *, overrides=None, options=None):
    from .model_families import ModelFamilyEstimator, family_components
    families = family_components()
    if component in families:
        if overrides:
            raise ValueError('Model family components take native data; series overrides do not apply')
        return ModelFamilyEstimator(families[component], options=options)
    if component not in ESTIMATORS:
        raise ValueError(f'Unknown estimation component: {component}')
    return ESTIMATORS[component](overrides=overrides, options=options)


def components_for(process_id, *, include_optional=False):
    process = load_requirements()['processes'].get(process_id)
    if process is None:
        known = sorted(load_requirements()['processes'])
        raise ValueError(f'No estimation requirements for process {process_id!r}; known: {known}')
    return list(process.get('required_components', [])) + (list(process.get('optional_components', [])) if include_optional else [])


def fit_component(component, data, *, cutoff, vintage_policy='strict', overrides=None, options=None):
    return estimator_for(component, overrides=overrides, options=options).fit(data, cutoff=cutoff, vintage_policy=vintage_policy)


def requirements_summary(process_id=None):
    document = load_requirements()
    processes = [process_id] if process_id else sorted(document['processes'])
    out = {}
    for pid in processes:
        entries = []
        for component in components_for(pid, include_optional=True):
            spec = document['components'][component]
            if spec['estimator'] == 'ModelFamilyEstimator':
                entries.append({'component': component, 'family': spec['family'], 'identification': spec['identification'],
                                'holdout_forecaster': spec['holdout_forecaster'],
                                'requirements': [{k: r.get(k) for k in ('id', 'publisher', 'dataset', 'series', 'frequency', 'url')}
                                                 for r in spec['family_requirements']],
                                'parameters': {p['name']: p.get('maps_to') for p in spec['parameters']}, 'hooks': spec.get('hooks', [])})
                continue
            series = spec['series'] or [spec.get('series_template', {})]
            entries.append({'component': component, 'method': spec['method'], 'frequency': spec['frequency'],
                            'series': [{'name': s.get('name', '{cell}'), 'metric': s['metric'], 'unit': s['unit'],
                                        'source_series': s.get('source_series'), 'revisions': s.get('revisions'),
                                        'sources': [src.get('series_id') for src in s.get('sources', [])]} for s in series],
                            'parameters': {p['name']: p.get('maps_to') for p in spec['parameters']}, 'hooks': spec.get('hooks', [])})
        out[pid] = {'required_components': document['processes'][pid].get('required_components', []), 'components': entries,
                    **({'note': document['processes'][pid]['note']} if 'note' in document['processes'][pid] else {})}
    return out
