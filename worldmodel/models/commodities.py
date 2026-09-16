"""Commodity balance sheets linked to prices through demand, supply and storage elasticities.

Each period clears the balance identity with a competitive-storage reduced form::

    Q_t    = Q0 (p_{t-lag} / p0)^eps_s * supply_shock_t                (predetermined)
    C(p)   = C0 (1+g)^t (p / p0)^eps_d * demand_shock_t                (eps_d < 0)
    S*(p)  = s_bar C0 (1+g)^t (p / p0)^-eta                            (inventory demand)
    S_{t-1} + Q_t + NM_t - C(p_t) = S*(p_t)                            (solved for p_t)
    S_t    = S_{t-1} + Q_t + NM_t - C(p_t)                             (identity, exact)

Elasticities are estimated from EIA/USDA balance sheets by log-linear regressions
(ln S on ln p and trend for storage; supply by OLS; demand by OLS or 2SLS with a
supply shifter; s_bar = storage scale / fitted consumption at the reference price). OLS
demand/storage slopes are simultaneous-equation biased and labeled correlational.
"""
import math
from .base import apply_cutoff, finite, fit_result, integer, merged_parameters, ols, parameter, quantiles, requirement, rng_from, validate_family

FAMILY = validate_family({
    'id': 'commodities',
    'title': 'Commodity supply-demand balances, inventories and prices',
    'description': 'Balance-sheet identity with storage demand; prices clear availability against use and desired stocks.',
    'identification': 'correlational_unless_instrumented',
    'validated': False,
    'parameters': {
        'demand_elasticity': parameter(-0.1, 'dimensionless', 'Price elasticity of consumption.', bounds=[-5, 0]),
        'supply_elasticity': parameter(0.05, 'dimensionless', 'Elasticity of production to lagged price.', bounds=[0, 5]),
        'supply_lag': parameter(1, 'periods', 'Periods between price signal and production response.', bounds=[0, 24], source='assumed'),
        'storage_elasticity': parameter(1.5, 'dimensionless', 'Elasticity of desired stocks-to-use to price (eta).', bounds=[0, 20]),
        'target_stocks_to_use': parameter(0.15, 'ratio', 'Desired stocks-to-use at the reference price (s_bar).', bounds=[0, 10]),
        'demand_growth': parameter(0.0, 'fraction_per_period', 'Trend growth of consumption.', bounds=[-0.5, 0.5]),
        'shock_sd': parameter({'supply': 0.02, 'demand': 0.01}, 'log_units', 'Lognormal shock standard deviations for stochastic runs.'),
    },
    'requirements': [
        requirement('eia_weekly_petroleum', 'U.S. Energy Information Administration', 'Weekly Petroleum Status Report (API v2)',
                    ['PET.WCESTUS1.W', 'PET.WCRFPUS2.W', 'PET.WCRIMUS2.W', 'PET.WCREXUS2.W', 'PET.WCRRIUS2.W'], 'https://api.eia.gov/v2/petroleum/',
                    frequency='weekly', access='public_api_key', parameters=['storage_elasticity', 'target_stocks_to_use', 'supply_elasticity']),
        requirement('eia_spot_prices', 'U.S. Energy Information Administration', 'Spot prices', ['PET.RWTC.D', 'PET.RBRTE.D'],
                    'https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm', frequency='daily', parameters=['demand_elasticity', 'storage_elasticity']),
        requirement('usda_psd', 'USDA Foreign Agricultural Service', 'Production, Supply and Distribution (PSD) / WASDE',
                    ['commodity', 'market_year', 'production', 'imports', 'exports', 'domestic_consumption', 'ending_stocks'],
                    'https://apps.fas.usda.gov/psdonline/', frequency='monthly', parameters=['demand_elasticity', 'supply_elasticity', 'target_stocks_to_use']),
        requirement('nass_quickstats', 'USDA National Agricultural Statistics Service', 'Quick Stats prices received and acreage',
                    ['commodity_desc', 'statisticcat_desc=PRICE RECEIVED', 'year', 'Value'], 'https://quickstats.nass.usda.gov/api',
                    frequency='monthly', access='public_api_key', parameters=['supply_elasticity']),
        requirement('fred_wti', 'Federal Reserve Bank of St. Louis', 'FRED', ['DCOILWTICO'], 'https://fred.stlouisfed.org/series/DCOILWTICO',
                    frequency='daily', role='price_cross_check', parameters=['demand_elasticity']),
    ],
    'limitations': [
        'Single market with exogenous net imports; world price linkages and quality differentials are omitted.',
        'Reduced-form storage demand; no forward-looking expectations or stockout nonlinearity beyond the elasticity.',
        'Reported balance sheets contain statistical discrepancies; the fit reports them rather than hiding them.',
    ],
})


def _series(value, t, name, default):
    if value is None:
        return default
    if isinstance(value, list):
        if not value:
            return default
        return finite(value[min(t, len(value) - 1)], name)
    return finite(value, name)


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    ref = config['reference']
    p0, c0, q0 = finite(ref['price'], 'reference price', 1e-9), finite(ref['consumption'], 'reference consumption', 1e-9), finite(ref['production'], 'reference production', 0)
    periods = integer(config.get('periods', 12), 'periods', 1, 100000)
    ed, es, eta = params['demand_elasticity'], params['supply_elasticity'], params['storage_elasticity']
    sbar, growth, lag = params['target_stocks_to_use'], params['demand_growth'], integer(params['supply_lag'], 'supply_lag', 0, 24)
    stocks = finite(config['initial']['stocks'], 'initial stocks', 0)
    price_history = [finite(config['initial']['price'], 'initial price', 1e-9)]
    if mode not in ('deterministic', 'stochastic'):
        raise ValueError('mode must be deterministic or stochastic')
    runs = integer(config.get('n_simulations', 200), 'n_simulations', 1, 100000) if mode == 'stochastic' else 1
    if runs * periods > 2_000_000:
        raise ValueError('Commodity simulation work budget exceeded')
    rng = rng_from(seed)
    paths = []
    for _ in range(runs):
        s_prev, prices = stocks, list(price_history)
        rows = []
        for t in range(periods):
            supply_shock = _series(config.get('supply_shocks'), t, 'supply shock', 1.0)
            demand_shock = _series(config.get('demand_shocks'), t, 'demand shock', 1.0)
            if mode == 'stochastic':
                supply_shock *= math.exp(rng.gauss(0, params['shock_sd']['supply']))
                demand_shock *= math.exp(rng.gauss(0, params['shock_sd']['demand']))
            signal = prices[max(0, len(prices) - lag)] if lag else prices[-1]
            production = q0 * (signal / p0) ** es * supply_shock
            net_imports = _series(config.get('net_imports'), t, 'net imports', 0.0)
            available = s_prev + production + net_imports
            if available <= 0:
                raise ValueError(f'Nonpositive availability in period {t}; balance infeasible')
            scale = c0 * (1 + growth) ** t

            def gap(log_price):
                price = math.exp(log_price)
                return available - scale * (price / p0) ** ed * demand_shock - sbar * scale * (price / p0) ** (-eta)

            lo, hi = math.log(p0) - 30, math.log(p0) + 30
            if gap(lo) > 0 or gap(hi) < 0:
                raise ValueError('Price bracket failed; elasticities must make excess supply increase with price')
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                if gap(mid) > 0:
                    hi = mid
                else:
                    lo = mid
                if hi - lo < 1e-13:
                    break
            price = math.exp(0.5 * (lo + hi))
            consumption = scale * (price / p0) ** ed * demand_shock
            ending = available - consumption
            identity = ending - s_prev - (production + net_imports - consumption)
            desired = sbar * scale * (price / p0) ** (-eta)
            rows.append({'period': t, 'price': price, 'production': production, 'consumption': consumption, 'net_imports': net_imports,
                         'beginning_stocks': s_prev, 'ending_stocks': ending, 'stocks_to_use': ending / consumption,
                         'identity_residual': identity, 'storage_market_residual': ending - desired})
            if abs(identity) > 1e-9 * max(1.0, available):
                raise ValueError('Balance identity violated')
            s_prev = ending
            prices.append(price)
        paths.append(rows)
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'quantity_unit': config.get('quantity_unit'),
              'price_unit': config.get('price_unit'), 'accounting': {'max_identity_residual': max(abs(r['identity_residual']) for p in paths for r in p)}}
    if mode == 'deterministic':
        report['periods'] = paths[0]
    else:
        report.update(n_simulations=runs, price_quantiles=[quantiles([p[t]['price'] for p in paths]) for t in range(periods)],
                      ending_stocks_quantiles=[quantiles([p[t]['ending_stocks'] for p in paths]) for t in range(periods)])
    return report


def fit(data, cutoff=None):
    rows, window = apply_cutoff(data['balances'], cutoff, label='balances')
    rows = sorted(rows, key=lambda r: r['date'])
    if len(rows) < 12:
        raise ValueError('At least 12 balance-sheet periods required')
    get_nm = lambda r: finite(r['net_imports'], 'net_imports') if 'net_imports' in r else finite(r.get('imports', 0), 'imports', 0) - finite(r.get('exports', 0), 'exports', 0)
    discrepancies = [finite(rows[t]['ending_stocks'], 'ending_stocks', 0) - finite(rows[t - 1]['ending_stocks'], 'ending_stocks', 0)
                     - (finite(rows[t]['production'], 'production', 0) + get_nm(rows[t]) - finite(rows[t]['consumption'], 'consumption', 1e-12))
                     for t in range(1, len(rows))]
    p0 = math.exp(sum(math.log(finite(r['price'], 'price', 1e-12)) for r in rows) / len(rows))
    lnp = [math.log(r['price'] / p0) for r in rows]
    storage = ols([[1.0, lnp[t], t] for t in range(len(rows))], [math.log(max(r['ending_stocks'], 1e-12)) for r in rows],
                  names=['ln_stock_scale', 'neg_storage_elasticity', 'trend'])
    trend = list(range(len(rows)))
    instrument = data.get('demand_instrument')
    if instrument:
        z = [math.log(finite(r[instrument], instrument, 1e-12)) for r in rows]
        first = ols([[1.0, z[t], trend[t]] for t in range(len(rows))], lnp, names=['const', instrument, 'trend'])
        demand = ols([[1.0, first['fitted'][t], trend[t]] for t in range(len(rows))], [math.log(r['consumption']) for r in rows],
                     names=['const', 'demand_elasticity', 'trend'])
        demand_label = f'2sls_instrument_{instrument}'
        first_stage_t = first['coefficients'][instrument] / first['standard_errors'][instrument]
    else:
        demand = ols([[1.0, lnp[t], trend[t]] for t in range(len(rows))], [math.log(r['consumption']) for r in rows],
                     names=['const', 'demand_elasticity', 'trend'])
        demand_label, first_stage_t = 'ols_correlational', None
    lag = integer(data.get('supply_lag', 1), 'supply_lag', 0, 24)
    supply = ols([[1.0, lnp[t - lag], t] for t in range(lag, len(rows))], [math.log(max(rows[t]['production'], 1e-12)) for t in range(lag, len(rows))],
                 names=['const', 'supply_elasticity', 'trend'])
    estimate = {'storage_elasticity': -storage['coefficients']['neg_storage_elasticity'],
                'target_stocks_to_use': math.exp(storage['coefficients']['ln_stock_scale'] - demand['coefficients']['const']),
                'reference_consumption': math.exp(demand['coefficients']['const']),
                'demand_elasticity': demand['coefficients']['demand_elasticity'], 'supply_elasticity': supply['coefficients']['supply_elasticity'],
                'supply_lag': lag, 'demand_growth': math.expm1(demand['coefficients']['trend']), 'reference_price': p0}
    diagnostics = {'balance_discrepancy': {'mean': sum(discrepancies) / len(discrepancies), 'max_abs': max(abs(d) for d in discrepancies)},
                   'standard_errors': {'storage_elasticity': storage['standard_errors']['neg_storage_elasticity'],
                                       'demand_elasticity': demand['standard_errors']['demand_elasticity'],
                                       'supply_elasticity': supply['standard_errors']['supply_elasticity']},
                   'demand_method': demand_label, 'first_stage_t': first_stage_t,
                   'r2': {'storage': storage['r2'], 'demand': demand['r2'], 'supply': supply['r2']}}
    identification = 'instrumented_demand_assumed_exclusion' if instrument else 'correlational'
    return fit_result(estimate, diagnostics, method='log_linear_balance_sheet_regressions', identification=identification, windows=[window],
                      data=data, family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def example_config():
    return {'quantity_unit': 'million_barrels', 'price_unit': 'USD/barrel', 'periods': 12,
            'reference': {'price': 75.0, 'consumption': 620.0, 'production': 400.0},
            'initial': {'stocks': 93.0, 'price': 75.0}, 'net_imports': 220.0,
            'supply_shocks': [1.0, 1.0, 0.9, 0.9, 0.95, 1.0],
            'parameters': {'demand_elasticity': -0.08, 'supply_elasticity': 0.05, 'supply_lag': 2, 'storage_elasticity': 1.2,
                           'target_stocks_to_use': 0.15, 'demand_growth': 0.0}, 'n_simulations': 200}


def synthetic(seed=0, periods=120):
    rng = rng_from(seed)
    truth = {'demand_elasticity': -0.3, 'supply_elasticity': 0.2, 'supply_lag': 1, 'storage_elasticity': 2.0, 'target_stocks_to_use': 0.2, 'demand_growth': 0.0}
    config = {'reference': {'price': 5.0, 'consumption': 100.0, 'production': 100.0}, 'initial': {'stocks': 20.0, 'price': 5.0},
              'periods': periods, 'net_imports': 0.0, 'parameters': truth,
              'supply_shocks': [math.exp(rng.gauss(0, 0.08)) for _ in range(periods)],
              'demand_shocks': [math.exp(rng.gauss(0, 0.01)) for _ in range(periods)]}
    run = simulate(config)['periods']
    rows = [{'date': f'{1990 + t // 12}-{t % 12 + 1:02d}-28', 'price': r['price'], 'production': r['production'], 'consumption': r['consumption'],
             'ending_stocks': r['ending_stocks'], 'net_imports': r['net_imports']} for t, r in enumerate(run)]
    return {'data': {'balances': rows, 'demand_instrument': 'production', 'supply_lag': 1}, 'truth': truth, 'config': example_config()}


# ----------------------------------------------------------------------------- holdout forecaster

def _net_imports(row):
    if 'net_imports' in row:
        return finite(row['net_imports'], 'net_imports')
    return finite(row.get('imports', 0), 'imports', 0) - finite(row.get('exports', 0), 'exports', 0)


def clear_period(estimate, index, beginning_stocks, production, net_imports):
    """Price and ending stocks that clear availability against consumption and desired stocks at period ``index``."""
    ed, eta = finite(estimate['demand_elasticity'], 'demand_elasticity'), finite(estimate['storage_elasticity'], 'storage_elasticity')
    if ed > 0 or eta < 0:
        raise ValueError('Clearing requires nonpositive demand elasticity and nonnegative storage elasticity')
    log_p0 = math.log(finite(estimate['reference_price'], 'reference_price', 1e-12))
    scale = finite(estimate['reference_consumption'], 'reference_consumption', 1e-12) * (1 + estimate['demand_growth']) ** index
    sbar = finite(estimate['target_stocks_to_use'], 'target_stocks_to_use', 0)
    available = beginning_stocks + production + net_imports
    if available <= 0:
        raise ValueError('Nonpositive availability; balance infeasible')

    def use(log_price):
        return scale * math.exp(min(ed * (log_price - log_p0), 700)), sbar * scale * math.exp(min(-eta * (log_price - log_p0), 700))

    def gap(log_price):
        consumption, desired = use(log_price)
        return available - consumption - desired

    lo, hi = log_p0 - 30, log_p0 + 30
    if gap(lo) > 0 or gap(hi) < 0:
        raise ValueError('Price bracket failed at the fitted elasticities')
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if gap(mid) > 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-13:
            break
    log_price = 0.5 * (lo + hi)
    return math.exp(log_price), available - use(log_price)[0]


def _holdout_forecast(parameters, history, rows, data, calibration_periods=36):
    """Next-period price and ending stocks given realized production and net imports.

    Beginning stocks are the last pre-origin ending stocks; the fitted demand, storage and
    trend clear the period. Predictive sds are the RMSE of the same one-step clearing on the
    last ``calibration_periods`` pre-origin periods. Ending stocks are scored as the
    secondary group ``ending_stocks``.
    """
    history = sorted(history, key=lambda r: r['date'])
    n = len(history)
    errors = [[], []]
    for t in range(max(1, n - calibration_periods), n):
        price, stocks = clear_period(parameters, t, history[t - 1]['ending_stocks'], finite(history[t]['production'], 'production', 0),
                                     _net_imports(history[t]))
        errors[0].append(price - history[t]['price'])
        errors[1].append(stocks - history[t]['ending_stocks'])
    if len(errors[0]) < 3:
        raise ValueError('Commodity holdout forecasts need at least four pre-origin periods')
    sds = [math.sqrt(sum(e * e for e in values) / len(values)) for values in errors]
    out = []
    for row in rows:
        price, stocks = clear_period(parameters, n, history[-1]['ending_stocks'], finite(row['production'], 'production', 0), _net_imports(row))
        out.append({'target': 'price', 'actual': row['price'], 'mean': price, 'sd': sds[0], 'history_values': [r['price'] for r in history]})
        out.append({'target': 'ending_stocks', 'group': 'ending_stocks', 'actual': row['ending_stocks'], 'mean': stocks, 'sd': sds[1],
                    'history_values': [r['ending_stocks'] for r in history]})
    return out


holdout_forecaster = {
    'rows_key': 'balances', 'time_key': 'date', 'target': 'price', 'forecast': _holdout_forecast,
    'fit_keys': ['balances', 'demand_instrument', 'supply_lag', 'information_time', 'revisions'],
    'conditional_inputs': ['production', 'net_imports'],
    'description': 'Next-period market-clearing price given realized production and net imports (secondary group ending_stocks).',
}
