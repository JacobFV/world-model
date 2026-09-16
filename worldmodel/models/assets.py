"""Asset price dynamics: linear factor model with GARCH(1,1) idiosyncratic volatility.

    r[i,t] = a_i + B_i . f_t + e[i,t],   e[i,t] = sqrt(h[i,t]) z[i,t]
    h[i,t] = omega_i + alpha_i e[i,t-1]^2 + beta_i h[i,t-1],  omega_i = s2_i (1 - alpha_i - beta_i)

Log returns (optionally in excess of a risk-free rate). Betas are OLS with HC1
errors; GARCH is Gaussian quasi-maximum likelihood with variance targeting,
optimized by Nelder-Mead on a persistence/share reparameterization that enforces
alpha, beta >= 0 and alpha + beta < 1. Factor returns are i.i.d. with the sample
mean and covariance (or bootstrapped from history in stochastic mode).
"""
import math
from .base import (apply_cutoff, cholesky, finite, fit_result, integer, logistic, merged_parameters, nelder_mead, normal_cdf, ols,
                   parameter, quantiles, requirement, rng_from, validate_family, UNIT_NORMAL)

FAMILY = validate_family({
    'id': 'assets',
    'title': 'Factor model with GARCH-lite volatility',
    'description': 'Daily return factor exposures, volatility clustering forecasts, and simulated price/portfolio risk.',
    'identification': 'descriptive_time_series',
    'validated': False,
    'parameters': {
        'symbols': parameter({}, 'per_symbol', 'Per-symbol alpha (log return/day), betas, GARCH omega/alpha/beta and last state.'),
        'factor_names': parameter([], 'names', 'Ordered factor names.'),
        'factor_mean': parameter({}, 'log_return_per_day', 'Mean daily factor returns.'),
        'factor_covariance': parameter([], 'log_return_squared_per_day', 'Daily factor covariance matrix.'),
        'trading_days_per_year': parameter(252, 'days', 'Annualization convention.', source='assumed'),
    },
    'requirements': [
        requirement('daily_bars', 'Exchange-licensed vendor (e.g. Nasdaq Data Link, Polygon, Tiingo) or SEC-registered source', 'Daily OHLCV adjusted bars',
                    ['symbol', 'date', 'adj_close', 'volume'], 'https://data.nasdaq.com/', frequency='daily', access='licensed_or_api_key',
                    parameters=['symbols']),
        requirement('french_factors', 'Kenneth R. French Data Library', 'Fama/French 3 or 5 Factors (Daily)', ['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA', 'RF'],
                    'https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html', frequency='daily',
                    parameters=['factor_names', 'factor_mean', 'factor_covariance', 'symbols']),
        requirement('fred_tbill', 'Federal Reserve Bank of St. Louis', 'FRED', ['DTB3'], 'https://fred.stlouisfed.org/series/DTB3', frequency='daily',
                    role='risk_free_rate', parameters=['symbols']),
    ],
    'limitations': [
        'Constant betas and Gaussian QMLE; jumps, leverage effects and regime changes are not modeled.',
        'Survivorship and corporate-action adjustments are the data provider\'s responsibility.',
        'Forecast distributions are conditional on estimated parameters without parameter uncertainty.',
    ],
})


def _returns(bars):
    by_symbol = {}
    for row in bars:
        by_symbol.setdefault(str(row['symbol']), []).append((row['date'], finite(row['close'], 'close', 1e-12)))
    returns = {}
    for symbol, rows in by_symbol.items():
        rows.sort()
        if len({d for d, _ in rows}) != len(rows):
            raise ValueError(f'Duplicate dates for {symbol}')
        returns[symbol] = {rows[k][0]: math.log(rows[k][1] / rows[k - 1][1]) for k in range(1, len(rows))}
    return returns


def garch_loglik(residuals, alpha, beta, variance):
    omega = variance * (1 - alpha - beta)
    h, total = variance, 0.0
    for e in residuals:
        if h <= 0:
            return -math.inf
        total += -0.5 * (math.log(2 * math.pi) + math.log(h) + e * e / h)
        h = omega + alpha * e * e + beta * h
    return total


def fit_garch(residuals, start=(0.05, 0.9)):
    if len(residuals) < 100:
        raise ValueError('GARCH estimation requires at least 100 residuals')
    variance = sum(e * e for e in residuals) / len(residuals)
    if variance <= 0:
        raise ValueError('Residual variance must be positive')

    def unpack(u):
        persistence = 0.9999 * logistic(u[0])
        share = logistic(u[1])
        return persistence * share, persistence * (1 - share)

    persistence0 = sum(start)
    share0 = start[0] / persistence0
    u0 = [math.log(persistence0 / 0.9999 / (1 - persistence0 / 0.9999)), math.log(share0 / (1 - share0))]
    best, value, iterations = nelder_mead(lambda u: -garch_loglik(residuals, *unpack(u), variance), u0, step=0.5, tol=1e-10, max_iter=3000)
    alpha, beta = unpack(best)
    omega = variance * (1 - alpha - beta)
    h = variance
    standardized = []
    for e in residuals:
        standardized.append(e / math.sqrt(h))
        last_h = h
        h = omega + alpha * e * e + beta * h
    squares = [z * z for z in standardized]
    mean_sq = sum(squares) / len(squares)
    denom = sum((s - mean_sq) ** 2 for s in squares)
    q = 0.0
    n = len(squares)
    for lag in range(1, 11):
        rho = sum((squares[t] - mean_sq) * (squares[t - lag] - mean_sq) for t in range(lag, n)) / denom if denom else 0.0
        q += rho * rho / (n - lag)
    q *= n * (n + 2)
    return {'omega': omega, 'alpha': alpha, 'beta': beta, 'unconditional_variance': variance, 'next_variance': h,
            'last_residual': residuals[-1], 'loglik': -value, 'iterations': iterations, 'persistence': alpha + beta,
            'half_life_days': math.log(0.5) / math.log(alpha + beta) if 0 < alpha + beta < 1 else None,
            'ljung_box_q10_squared_standardized': q}


def fit(data, cutoff=None):
    bars, window = apply_cutoff(data['bars'], cutoff, label='daily_bars')
    windows = [window]
    returns = _returns(bars)
    rf = {}
    if data.get('risk_free'):
        rows, rwindow = apply_cutoff(data['risk_free'], cutoff, label='risk_free')
        windows.append(rwindow)
        rf = {r['date']: finite(r['rf'], 'rf') for r in rows}
    names = list(data.get('factor_names', []))
    factors = {}
    if names:
        rows, fwindow = apply_cutoff(data['factors'], cutoff, label='factors')
        windows.append(fwindow)
        factors = {r['date']: [finite(r[n], n) for n in names] for r in rows}
        factor_label = 'supplied'
    else:
        common = set.intersection(*(set(v) for v in returns.values()))
        factors = {d: [sum(returns[s][d] for s in returns) / len(returns)] for d in common}
        names = ['equal_weight_market']
        factor_label = 'constructed_equal_weight_market_includes_assets'
    symbols = {}
    for symbol, series in sorted(returns.items()):
        dates = sorted(d for d in series if d in factors and (not data.get('risk_free') or d in rf))
        if len(dates) < 120:
            raise ValueError(f'{symbol} has fewer than 120 aligned returns')
        y = [series[d] - rf.get(d, 0.0) for d in dates]
        X = [[1.0] + factors[d] for d in dates]
        model = ols(X, y, names=['alpha'] + names)
        garch = fit_garch(model['residuals'])
        symbols[symbol] = {'alpha': model['coefficients']['alpha'], 'betas': {n: model['coefficients'][n] for n in names},
                           'beta_standard_errors': {n: model['standard_errors'][n] for n in names}, 'r2': model['r2'],
                           'garch': {k: garch[k] for k in ('omega', 'alpha', 'beta', 'unconditional_variance', 'next_variance', 'last_residual')},
                           'diagnostics': {k: garch[k] for k in ('loglik', 'iterations', 'persistence', 'half_life_days', 'ljung_box_q10_squared_standardized')},
                           'observations': len(dates)}
    fdates = sorted(factors)
    fmean = [sum(factors[d][k] for d in fdates) / len(fdates) for k in range(len(names))]
    fcov = [[sum((factors[d][a] - fmean[a]) * (factors[d][b] - fmean[b]) for d in fdates) / (len(fdates) - 1) for b in range(len(names))]
            for a in range(len(names))]
    estimate = {'symbols': symbols, 'factor_names': names, 'factor_mean': dict(zip(names, fmean)), 'factor_covariance': fcov}
    diagnostics = {'factor_source': factor_label, 'symbols': len(symbols),
                   'annualized_idiosyncratic_vol': {s: math.sqrt(252 * v['garch']['unconditional_variance']) for s, v in symbols.items()}}
    return fit_result(estimate, diagnostics, method='ols_factor_betas_plus_variance_targeted_garch11_qmle', identification=FAMILY['identification'],
                      windows=windows, data=data, family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, {k: config[k] for k in ('symbols', 'factor_names', 'factor_mean', 'factor_covariance') if k in config})
    names, symbols = params['factor_names'], params['symbols']
    horizon = integer(config.get('horizon_days', 20), 'horizon_days', 1, 5000)
    weights = config.get('weights') or {s: 1 / len(symbols) for s in symbols}
    if set(weights) - set(symbols):
        raise ValueError('Portfolio weights reference unknown symbols')
    fmean = [params['factor_mean'][n] for n in names]
    fcov = params['factor_covariance']
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'horizon_days': horizon}

    def variance_path(g):
        path, h = [], g['next_variance']
        for _ in range(horizon):
            path.append(h)
            h = g['omega'] + (g['alpha'] + g['beta']) * h
        return path

    if mode == 'deterministic':
        expected, variances = {}, {}
        for s, spec in symbols.items():
            mu = spec['alpha'] + sum(spec['betas'][n] * m for n, m in zip(names, fmean))
            systematic = sum(spec['betas'][a] * fcov[i][j] * spec['betas'][b] for i, a in enumerate(names) for j, b in enumerate(names))
            path = variance_path(spec['garch'])
            expected[s] = mu * horizon
            variances[s] = {'daily_idiosyncratic_variance_path': path, 'horizon_total_variance': sum(path) + systematic * horizon}
        exposure = [sum(w * symbols[s]['betas'][n] for s, w in weights.items()) for n in names]
        systematic = sum(exposure[i] * fcov[i][j] * exposure[j] for i in range(len(names)) for j in range(len(names))) * horizon
        idiosyncratic = sum(w * w * sum(variance_path(symbols[s]['garch'])) for s, w in weights.items())
        mean = sum(w * expected[s] for s, w in weights.items())
        sd = math.sqrt(systematic + idiosyncratic)
        report.update(expected_log_return=expected, variance=variances,
                      portfolio={'expected_log_return': mean, 'standard_deviation': sd,
                                 'value_at_risk_95': -(mean + UNIT_NORMAL.inv_cdf(0.05) * sd),
                                 'expected_shortfall_95': -(mean - sd * math.exp(-0.5 * UNIT_NORMAL.inv_cdf(0.05) ** 2) / (math.sqrt(2 * math.pi) * 0.05))},
                      approximation=['Normal horizon approximation; GARCH term structure for expected variance.'])
        return report
    if mode != 'stochastic':
        raise ValueError('mode must be deterministic or stochastic')
    rng = rng_from(seed)
    runs = integer(config.get('n_simulations', 1000), 'n_simulations', 1, 100000)
    if runs * horizon * max(1, len(symbols)) > 5_000_000:
        raise ValueError('Asset simulation work budget exceeded')
    lower = cholesky(fcov, jitter=1e-18) if names else []
    df = config.get('student_t_df')
    prices = config.get('initial_prices', {})

    def shock():
        z = rng.gauss(0, 1)
        if df is None:
            return z
        chi = rng.gammavariate(df / 2, 2)
        return z / math.sqrt(chi / df) * math.sqrt((df - 2) / df)

    portfolio, finals = [], {s: [] for s in symbols}
    for _ in range(runs):
        state = {s: (spec['garch']['next_variance'], None) for s, spec in symbols.items()}
        cumulative = {s: 0.0 for s in symbols}
        for _ in range(horizon):
            z = [rng.gauss(0, 1) for _ in names]
            f = [fmean[a] + sum(lower[a][b] * z[b] for b in range(len(names))) for a in range(len(names))]
            for s, spec in symbols.items():
                h, _ = state[s]
                e = math.sqrt(h) * shock()
                cumulative[s] += spec['alpha'] + sum(spec['betas'][n] * v for n, v in zip(names, f)) + e
                g = spec['garch']
                state[s] = (g['omega'] + g['alpha'] * e * e + g['beta'] * h, e)
        for s in symbols:
            finals[s].append(cumulative[s])
        portfolio.append(math.log(sum(w * math.exp(cumulative[s]) for s, w in weights.items()) / sum(weights.values())))
    ordered = sorted(portfolio)
    cut = max(1, int(0.05 * runs))
    report.update(n_simulations=runs, log_return_quantiles={s: quantiles(v) for s, v in finals.items()},
                  portfolio_log_return_quantiles=quantiles(portfolio), value_at_risk_95=-ordered[cut - 1],
                  expected_shortfall_95=-sum(ordered[:cut]) / cut,
                  terminal_price_quantiles={s: {k: prices[s] * math.exp(v) for k, v in quantiles(finals[s]).items()} for s in symbols if s in prices},
                  shocks='normal' if df is None else f'student_t_{df}')
    return report


def synthetic(seed=0, days=2500):
    rng = rng_from(seed)
    names = ['MKT', 'SMB']
    truth = {'A': {'alpha': 0.0001, 'betas': {'MKT': 1.2, 'SMB': 0.3}, 'garch': (0.08, 0.9)},
             'B': {'alpha': 0.0, 'betas': {'MKT': 0.7, 'SMB': -0.2}, 'garch': (0.05, 0.93)}}
    price = {s: 100.0 for s in truth}
    h = {s: 1e-4 for s in truth}
    bars, factors = [], []
    from datetime import date, timedelta
    start = date(2010, 1, 4)
    for s in truth:
        bars.append({'symbol': s, 'date': start.isoformat(), 'close': price[s]})
    for t in range(1, days + 1):
        day = (start + timedelta(days=t)).isoformat()
        f = {'MKT': rng.gauss(0.0003, 0.01), 'SMB': rng.gauss(0.0, 0.005)}
        factors.append({'date': day, **f})
        for s, spec in truth.items():
            a, b = spec['garch']
            omega = 1e-4 * (1 - a - b)
            e = math.sqrt(h[s]) * rng.gauss(0, 1)
            h[s] = omega + a * e * e + b * h[s]
            price[s] *= math.exp(spec['alpha'] + sum(spec['betas'][n] * f[n] for n in names) + e)
            bars.append({'symbol': s, 'date': day, 'close': price[s]})
    config = {'horizon_days': 10, 'weights': {'A': 0.5, 'B': 0.5}, 'initial_prices': {'A': 100.0, 'B': 50.0}, 'n_simulations': 400,
              'factor_names': names, 'factor_mean': {'MKT': 0.0003, 'SMB': 0.0}, 'factor_covariance': [[1e-4, 0.0], [0.0, 2.5e-5]],
              'symbols': {s: {'alpha': spec['alpha'], 'betas': spec['betas'],
                              'garch': {'omega': 1e-4 * (1 - sum(spec['garch'])), 'alpha': spec['garch'][0], 'beta': spec['garch'][1],
                                        'unconditional_variance': 1e-4, 'next_variance': 2e-4, 'last_residual': 0.0}} for s, spec in truth.items()}}
    return {'data': {'bars': bars, 'factors': factors, 'factor_names': names}, 'truth': truth, 'config': config}


# ----------------------------------------------------------------------------- holdout forecaster

CONSTANT_VOLATILITY = 'constant_volatility'


def _holdout_forecast(parameters, history, rows, data):
    """Next-day log return per symbol with GARCH(1,1) variance filtered through the origin.

    Primary (conditional on realized target-day factor returns and risk-free rate): mean
    rf + alpha + beta . f and variance h, where h runs the fitted GARCH recursion over
    pre-origin residuals, so the density tests betas and the volatility forecast directly.
    Secondary group ``unconditional_log_return``: mean rf_last + alpha + beta . factor_mean
    and variance beta' Sigma beta + h. ``constant_volatility`` keeps each mean and replaces h
    by the unconditional idiosyncratic variance. Supplied factor series are required: the
    constructed equal-weight market would contain the target returns.
    """
    if not data.get('factor_names'):
        raise ValueError('The assets holdout forecaster requires supplied factor returns (factor_names and factors)')
    names, symbols = parameters['factor_names'], parameters['symbols']
    closes = {}
    for r in sorted(history, key=lambda r: r['date']):
        closes.setdefault(str(r['symbol']), []).append((r['date'], finite(r['close'], 'close', 1e-12)))
    returns = {s: {rows_[k][0]: math.log(rows_[k][1] / rows_[k - 1][1]) for k in range(1, len(rows_))} for s, rows_ in closes.items()}
    target_dates = {r['date'] for r in rows}
    dates = {r['date'] for r in history}
    rf_all = {r['date']: finite(r['rf'], 'rf') for r in data.get('risk_free') or []}
    rf = {d: v for d, v in rf_all.items() if d in dates}
    factors = {r['date']: [finite(r[n], n) for n in names] for r in data['factors'] if r['date'] in dates or r['date'] in target_dates}
    fmean = [parameters['factor_mean'][n] for n in names]
    fcov = parameters['factor_covariance']
    last_rf = rf[max(rf)] if rf else 0.0
    out = []
    for row in sorted(rows, key=lambda r: str(r['symbol'])):
        symbol, day = str(row['symbol']), row['date']
        spec = symbols.get(symbol)
        if spec is None or symbol not in closes or day not in factors or (data.get('risk_free') and day not in rf_all):
            continue
        betas = [spec['betas'][n] for n in names]
        g = spec['garch']
        h = g['unconditional_variance']
        for past_day in sorted(returns[symbol]):
            if past_day in factors and (not rf or past_day in rf):
                e = returns[symbol][past_day] - rf.get(past_day, 0.0) - spec['alpha'] - sum(b * f for b, f in zip(betas, factors[past_day]))
                h = g['omega'] + g['alpha'] * e * e + g['beta'] * h
        systematic = sum(betas[a] * fcov[a][c] * betas[c] for a in range(len(names)) for c in range(len(names)))
        actual = math.log(finite(row['close'], 'close', 1e-12) / closes[symbol][-1][1])
        past = [returns[symbol][d] for d in sorted(returns[symbol])]
        conditional = rf_all.get(day, 0.0) + spec['alpha'] + sum(b * f for b, f in zip(betas, factors[day]))
        unconditional = last_rf + spec['alpha'] + sum(b * m for b, m in zip(betas, fmean))
        out.append({'target': f'log_return:{symbol}', 'actual': actual, 'mean': conditional, 'sd': math.sqrt(h), 'history_values': past,
                    'baselines': {CONSTANT_VOLATILITY: {'mean': conditional, 'sd': math.sqrt(g['unconditional_variance'])}}})
        out.append({'target': f'unconditional_log_return:{symbol}', 'group': 'unconditional_log_return', 'actual': actual,
                    'mean': unconditional, 'sd': math.sqrt(systematic + h), 'history_values': past,
                    'baselines': {CONSTANT_VOLATILITY: {'mean': unconditional, 'sd': math.sqrt(systematic + g['unconditional_variance'])}}})
    return out


holdout_forecaster = {
    'rows_key': 'bars', 'time_key': 'date', 'target': 'log_return', 'forecast': _holdout_forecast,
    'fit_keys': ['bars', 'factors', 'factor_names', 'risk_free', 'information_time', 'revisions'],
    'conditional_inputs': ['factor_returns_at_target_day', 'risk_free_rate_at_target_day'], 'baselines': [CONSTANT_VOLATILITY],
    'description': 'Next-day log-return distribution given realized factor returns (GARCH variance); CRPS, pinball and coverage '
                   'against the same mean with constant volatility (secondary group: unconditional return distribution).',
}
