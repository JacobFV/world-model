"""Monetary policy reaction (Taylor-rule family) and Nelson-Siegel yield-curve dynamics.

Policy rule with smoothing and an effective lower bound (percent, annualized)::

    i*_t = r_star + pi_t + phi_pi (pi_t - pi_target) + phi_y gap_t
    i_t  = max(elb, rho i_{t-1} + (1 - rho) i*_t + e_t)

OLS on i_t = c + rho i_{t-1} + b_pi pi_t + b_y gap_t recovers
phi_pi = b_pi / (1 - rho) - 1, phi_y = b_y / (1 - rho), r_star = c / (1 - rho) + phi_pi pi_target.
Observations at the lower bound are excluded (censoring) by default.

Nelson-Siegel (maturity tau in years, lambda per year)::

    y(tau) = b0 + b1 (1 - e^{-l tau}) / (l tau) + b2 ((1 - e^{-l tau}) / (l tau) - e^{-l tau})

Factors are OLS per date at a common lambda chosen by grid search; factor dynamics
are a VAR(1) with the policy-rate change as an exogenous regressor.
"""
import math
from .base import (apply_cutoff, cholesky, finite, fit_result, integer, merged_parameters, ols, parameter, quantiles, requirement,
                   rng_from, validate_family)

FAMILY = validate_family({
    'id': 'monetary',
    'title': 'Policy-rate reaction and yield-curve dynamics',
    'description': 'Smoothed Taylor rule with lower bound and dynamic Nelson-Siegel factors linked to policy changes.',
    'identification': 'descriptive_reaction_function',
    'validated': False,
    'parameters': {
        'rho': parameter(0.85, 'per_period', 'Interest-rate smoothing.', bounds=[0, 0.999]),
        'phi_pi': parameter(0.5, 'dimensionless', 'Response to inflation gap beyond one-for-one.', bounds=[-5, 10]),
        'phi_y': parameter(0.5, 'dimensionless', 'Response to output gap.', bounds=[-5, 10]),
        'r_star': parameter(0.5, 'percent', 'Neutral real rate.', bounds=[-5, 10]),
        'inflation_target': parameter(2.0, 'percent', 'Inflation objective.', source='external'),
        'elb': parameter(0.125, 'percent', 'Effective lower bound on the policy rate.', source='external'),
        'policy_shock_sd': parameter(0.25, 'percent', 'Residual standard deviation of the policy rule.', bounds=[0, 10]),
        'ns_lambda': parameter(0.7308, 'per_year', 'Nelson-Siegel decay (Diebold-Li 0.0609/month).', bounds=[0.01, 10]),
        'factor_intercept': parameter([0.0, 0.0, 0.0], 'percent', 'VAR intercepts for level, slope, curvature.'),
        'factor_transition': parameter([[0.99, 0, 0], [0, 0.95, 0], [0, 0, 0.9]], 'per_period', 'VAR(1) transition matrix.'),
        'policy_loading': parameter([0.3, 0.6, 0.0], 'percent_per_percent', 'Factor response to policy-rate change.'),
        'factor_residual_covariance': parameter([[0.04, 0, 0], [0, 0.04, 0], [0, 0, 0.09]], 'percent_squared', 'VAR residual covariance.'),
    },
    'requirements': [
        requirement('fred_policy', 'Federal Reserve Bank of St. Louis', 'FRED policy rate', ['FEDFUNDS', 'DFF', 'DFEDTARU'], 'https://fred.stlouisfed.org/',
                    frequency='monthly/daily', parameters=['rho', 'phi_pi', 'phi_y', 'r_star', 'policy_shock_sd']),
        requirement('fred_inflation', 'Federal Reserve Bank of St. Louis', 'FRED inflation', ['PCEPILFE', 'CPIAUCSL'], 'https://fred.stlouisfed.org/',
                    frequency='monthly', parameters=['phi_pi', 'r_star']),
        requirement('fred_output_gap', 'Federal Reserve Bank of St. Louis / CBO', 'FRED output and potential', ['GDPC1', 'GDPPOT', 'UNRATE', 'NROU'],
                    'https://fred.stlouisfed.org/', frequency='quarterly', parameters=['phi_y']),
        requirement('fred_treasury_curve', 'Federal Reserve Bank of St. Louis (H.15)', 'Constant-maturity Treasury yields',
                    ['DGS1MO', 'DGS3MO', 'DGS6MO', 'DGS1', 'DGS2', 'DGS3', 'DGS5', 'DGS7', 'DGS10', 'DGS20', 'DGS30'], 'https://fred.stlouisfed.org/',
                    frequency='daily', parameters=['ns_lambda', 'factor_intercept', 'factor_transition', 'policy_loading', 'factor_residual_covariance']),
    ],
    'limitations': [
        'Real-time data vintages (ALFRED) are required for honest backtests; revised data overstate fit.',
        'Reaction coefficients describe historical conduct, not a structural policy invariant (Lucas critique).',
        'Nelson-Siegel is not arbitrage-free; term premia are not separately identified.',
    ],
})


def ns_loadings(tau, lam):
    x = lam * finite(tau, 'maturity', 1e-9)
    slope = (1 - math.exp(-x)) / x
    return [1.0, slope, slope - math.exp(-x)]


def fit_curve(maturities, yields, lam):
    if len(maturities) != len(yields) or len(maturities) < 4:
        raise ValueError('Nelson-Siegel requires at least four maturities')
    model = ols([ns_loadings(t, lam) for t in maturities], [finite(y, 'yield') for y in yields], names=['level', 'slope', 'curvature'], robust=False)
    return [model['coefficients'][k] for k in ('level', 'slope', 'curvature')], sum(e * e for e in model['residuals'])


def fit_taylor(rows, inflation_target=2.0, elb=0.125, exclude_elb=True):
    rows = sorted(rows, key=lambda r: r['date'])
    X, y, dropped = [], [], 0
    for t in range(1, len(rows)):
        current, previous = rows[t], rows[t - 1]
        i, lag = finite(current['policy_rate'], 'policy_rate'), finite(previous['policy_rate'], 'policy_rate')
        if exclude_elb and (i <= elb + 0.1 or lag <= elb + 0.1):
            dropped += 1
            continue
        X.append([1.0, lag, finite(current['inflation'], 'inflation'), finite(current['output_gap'], 'output_gap')])
        y.append(i)
    model = ols(X, y, names=['const', 'rho', 'b_pi', 'b_y'])
    c, rho, b_pi, b_y = (model['coefficients'][k] for k in ('const', 'rho', 'b_pi', 'b_y'))
    if not rho < 1:
        raise ValueError('Estimated smoothing is not below one; reaction coefficients unidentified')
    phi_pi = b_pi / (1 - rho) - 1
    return {'rho': rho, 'phi_pi': phi_pi, 'phi_y': b_y / (1 - rho), 'r_star': c / (1 - rho) + phi_pi * inflation_target,
            'policy_shock_sd': model['sigma'], 'inflation_target': inflation_target, 'elb': elb}, \
           {'reduced_form': model['coefficients'], 'standard_errors': model['standard_errors'], 'r2': model['r2'], 'n': model['n'],
            'excluded_at_lower_bound': dropped, 'long_run_inflation_response': 1 + phi_pi}


def fit_curves(rows, policy=None, grid=None):
    rows = sorted(rows, key=lambda r: r['date'])
    grid = grid or [round(0.1 * k, 4) for k in range(2, 31)]
    panels = [([float(m) for m in r['yields']], list(r['yields'].values())) for r in rows]
    best = min(grid, key=lambda lam: sum(fit_curve(m, y, lam)[1] for m, y in panels))
    factors = [fit_curve(m, y, best)[0] for m, y in panels]
    sse = sum(fit_curve(m, y, best)[1] for m, y in panels)
    count = sum(len(m) for m, _ in panels)
    policy = policy or {}
    has_policy = all(r['date'] in policy for r in rows) and bool(policy)
    intercept, transition, loading, residuals = [], [], [], []
    for k in range(3):
        X, y = [], []
        for t in range(1, len(rows)):
            row = [1.0] + factors[t - 1]
            if has_policy:
                row.append(policy[rows[t]['date']] - policy[rows[t - 1]['date']])
            X.append(row)
            y.append(factors[t][k])
        model = ols(X, y, names=['const', 'level', 'slope', 'curvature'] + (['d_policy'] if has_policy else []), robust=False)
        intercept.append(model['coefficients']['const'])
        transition.append([model['coefficients'][n] for n in ('level', 'slope', 'curvature')])
        loading.append(model['coefficients'].get('d_policy', 0.0))
        residuals.append(model['residuals'])
    n = len(residuals[0])
    covariance = [[sum(residuals[a][t] * residuals[b][t] for t in range(n)) / max(n - 5, 1) for b in range(3)] for a in range(3)]
    return {'ns_lambda': best, 'factor_intercept': intercept, 'factor_transition': transition, 'policy_loading': loading,
            'factor_residual_covariance': covariance, 'last_factors': factors[-1]}, \
           {'curve_rmse': math.sqrt(sse / count), 'dates': len(rows), 'policy_linked': has_policy}


def fit(data, cutoff=None):
    estimate, diagnostics, windows = {}, {}, []
    if data.get('observations'):
        rows, window = apply_cutoff(data['observations'], cutoff, label='policy_observations')
        windows.append(window)
        taylor, diag = fit_taylor(rows, data.get('inflation_target', 2.0), data.get('elb', 0.125), data.get('exclude_elb', True))
        estimate.update(taylor)
        diagnostics['taylor'] = diag
    if data.get('curves'):
        rows, window = apply_cutoff(data['curves'], cutoff, label='yield_curves')
        windows.append(window)
        policy = {r['date']: r['policy_rate'] for r in data.get('observations', [])}
        curve, diag = fit_curves(rows, policy)
        estimate.update(curve)
        diagnostics['nelson_siegel'] = diag
    if not estimate:
        raise ValueError('Supply observations and/or curves')
    return fit_result(estimate, diagnostics, method='ols_smoothed_taylor_rule_and_dynamic_nelson_siegel', identification=FAMILY['identification'],
                      windows=windows, data=data, family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def policy_target(params, inflation, gap):
    return params['r_star'] + inflation + params['phi_pi'] * (inflation - params['inflation_target']) + params['phi_y'] * gap


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    horizon = integer(config.get('horizon', 12), 'horizon', 1, 10000)
    paths = config.get('paths', {})
    maturities = [finite(m, 'maturity', 1e-6) for m in config.get('maturities', [0.25, 1, 2, 5, 10, 30])]
    if mode not in ('deterministic', 'stochastic'):
        raise ValueError('mode must be deterministic or stochastic')
    runs = integer(config.get('n_simulations', 500), 'n_simulations', 1, 100000) if mode == 'stochastic' else 1
    rng = rng_from(seed)
    lower = cholesky(params['factor_residual_covariance'], jitter=1e-12) if mode == 'stochastic' else None
    results = []
    for _ in range(runs):
        rate = finite(config['initial']['policy_rate'], 'initial policy rate')
        factors = list(config['initial'].get('factors', [rate + 1.0, -1.0, 0.0]))
        rows = []
        for t in range(horizon):
            inflation = finite((paths.get('inflation') or [params['inflation_target']])[min(t, len(paths.get('inflation') or [0]) - 1)], 'inflation')
            gap = finite((paths.get('output_gap') or [0.0])[min(t, len(paths.get('output_gap') or [0]) - 1)], 'output_gap')
            desired = policy_target(params, inflation, gap)
            shock = rng.gauss(0, params['policy_shock_sd']) if mode == 'stochastic' else 0.0
            new_rate = max(params['elb'], params['rho'] * rate + (1 - params['rho']) * desired + shock)
            change = new_rate - rate
            u = [0.0, 0.0, 0.0]
            if mode == 'stochastic':
                z = [rng.gauss(0, 1) for _ in range(3)]
                u = [sum(lower[a][b] * z[b] for b in range(3)) for a in range(3)]
            factors = [params['factor_intercept'][k] + sum(params['factor_transition'][k][m] * factors[m] for m in range(3))
                       + params['policy_loading'][k] * change + u[k] for k in range(3)]
            curve = {str(m): sum(f * l for f, l in zip(factors, ns_loadings(m, params['ns_lambda']))) for m in maturities}
            rows.append({'period': t, 'policy_rate': new_rate, 'desired_rate': desired, 'at_lower_bound': new_rate == params['elb'],
                         'factors': factors, 'yields': curve})
            rate = new_rate
        results.append(rows)
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'units': {'rates': 'percent_annualized', 'maturities': 'years'}}
    if mode == 'deterministic':
        report['path'] = results[0]
    else:
        report.update(n_simulations=runs, policy_rate_quantiles=[quantiles([r[t]['policy_rate'] for r in results]) for t in range(horizon)],
                      yield_quantiles_final={str(m): quantiles([r[-1]['yields'][str(m)] for r in results]) for m in maturities},
                      lower_bound_frequency=[sum(r[t]['at_lower_bound'] for r in results) / runs for t in range(horizon)])
    return report


def synthetic(seed=0, periods=240):
    rng = rng_from(seed)
    truth = {'rho': 0.8, 'phi_pi': 0.6, 'phi_y': 0.4, 'r_star': 1.0, 'inflation_target': 2.0, 'elb': 0.125, 'ns_lambda': 0.6}
    rate, inflation, gap = 4.0, 2.5, 0.0
    observations, curves = [], []
    level, slope, curvature = 5.0, -1.5, 0.5
    maturities = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]
    for t in range(periods):
        inflation = 2.0 + 0.9 * (inflation - 2.0) + rng.gauss(0, 0.3)
        gap = 0.85 * gap + rng.gauss(0, 0.5)
        desired = truth['r_star'] + inflation + truth['phi_pi'] * (inflation - 2.0) + truth['phi_y'] * gap
        new_rate = max(truth['elb'], truth['rho'] * rate + (1 - truth['rho']) * desired + rng.gauss(0, 0.1))
        date = f'{1990 + t // 12}-{t % 12 + 1:02d}-01'
        observations.append({'date': date, 'policy_rate': new_rate, 'inflation': inflation, 'output_gap': gap})
        change = new_rate - rate
        level = 0.2 + 0.96 * level + 0.3 * change + rng.gauss(0, 0.05)
        slope = -0.05 + 0.95 * slope + 0.6 * change + rng.gauss(0, 0.05)
        curvature = 0.9 * curvature + rng.gauss(0, 0.05)
        curves.append({'date': date, 'yields': {str(m): sum(f * l for f, l in zip((level, slope, curvature), ns_loadings(m, truth['ns_lambda']))) for m in maturities}})
        rate = new_rate
    config = {'horizon': 8, 'initial': {'policy_rate': 5.25, 'factors': [4.5, -0.5, 0.2]},
              'paths': {'inflation': [3.0, 2.8, 2.6, 2.4, 2.2, 2.1, 2.0, 2.0], 'output_gap': [-0.5, -1.0, -1.5, -1.5, -1.0, -0.5, 0.0, 0.0]},
              'maturities': [0.25, 2, 10, 30], 'n_simulations': 300}
    return {'data': {'observations': observations, 'curves': curves}, 'truth': truth, 'config': config}
