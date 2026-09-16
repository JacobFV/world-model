"""Agent-based price formation with fundamentalist, chartist and noise traders.

Each agent chooses a target fraction theta of wealth held in the risky asset:

    fundamentalist: theta = clip(0.5 + k_f * ln(F_t / p))
    chartist:       theta = clip(0.5 + k_c * mean of last `window` log returns)
    noise:          theta = clip(0.5 + sigma_n * z)

Desired shares theta * (cash + shares * p) / p never imply shorting or borrowing.
Clearing is either Walrasian (bisection on the monotone aggregate excess demand, with
price-dependent allocations re-evaluated at each candidate price and
integer allocation by largest remainder) or a continuous double auction with
price-time priority and day orders. Cash is integer cents and shares integers; both
totals are checked after every step. Fundamental value follows a log random walk.
"""
from copy import deepcopy
import math
from .base import apply_cutoff, finite, fit_result, integer, merged_parameters, parameter, requirement, rng_from, validate_family

FAMILY = validate_family({
    'id': 'market_abm',
    'title': 'Agent-based order flow and price formation',
    'description': 'Heterogeneous-expectation traders with Walrasian or limit-order-book clearing and exact cash/share conservation.',
    'identification': 'simulated_method_of_moments',
    'validated': False,
    'parameters': {
        'fundamentalist_strength': parameter(2.0, 'wealth_share_per_log_mispricing', 'Sensitivity of fundamentalist allocation to ln(F/p).', bounds=[0, 100]),
        'chartist_strength': parameter(20.0, 'wealth_share_per_log_return', 'Sensitivity of chartist allocation to recent mean return.', bounds=[0, 1000]),
        'chartist_window': parameter(5, 'steps', 'Trend lookback.', bounds=[1, 250]),
        'noise_sd': parameter(0.1, 'wealth_share', 'Standard deviation of noise-trader allocation shocks.', bounds=[0, 1]),
        'fundamental_volatility': parameter(0.01, 'log_value_per_step', 'Random-walk volatility of fundamental value.', bounds=[0, 1]),
        'aggressiveness': parameter(0.005, 'fraction_of_price', 'Order-book limit price offset from last price.', bounds=[0, 0.5]),
    },
    'requirements': [
        requirement('daily_bars_moments', 'Exchange-licensed vendor or Nasdaq Data Link', 'Daily adjusted closes for target moments', ['date', 'adj_close'],
                    'https://data.nasdaq.com/', frequency='daily', access='licensed_or_api_key',
                    parameters=['fundamentalist_strength', 'chartist_strength', 'noise_sd', 'fundamental_volatility']),
        requirement('lobster_or_taq', 'LOBSTER / NYSE TAQ', 'Limit order book messages (optional microstructure moments)', ['time', 'type', 'order_id', 'size', 'price', 'direction'],
                    'https://lobsterdata.com/', frequency='event', access='licensed', parameters=['aggressiveness']),
    ],
    'limitations': [
        'Stylized; agent populations and rules are assumptions. SMM fit matches moments, not microfoundations.',
        'No leverage, short selling, dividends, interest or inter-asset substitution.',
        'Moment matching with common random numbers is conditional on the seed used for simulation.',
    ],
})


def _agents(config, params):
    agents = []
    for spec in config['agents']:
        count = integer(spec.get('count', 1), 'agent count', 1, 100000)
        for k in range(count):
            agent = {'id': spec.get('id', spec['type']) + (f'_{k}' if count > 1 else ''), 'type': spec['type'],
                     'cash': integer(spec['cash_cents'], 'cash_cents', 0, 10**15), 'shares': integer(spec['shares'], 'shares', 0, 10**12)}
            if agent['type'] not in ('fundamentalist', 'chartist', 'noise'):
                raise ValueError('Agent type must be fundamentalist, chartist or noise')
            agent['strength'] = finite(spec.get('strength', params['fundamentalist_strength'] if agent['type'] == 'fundamentalist'
                                            else params['chartist_strength'] if agent['type'] == 'chartist' else params['noise_sd']), 'strength', 0)
            agent['window'] = integer(spec.get('window', params['chartist_window']), 'window', 1, 1000)
            agents.append(agent)
    if len(agents) > 5000:
        raise ValueError('At most 5000 agents')
    ids = [a['id'] for a in agents]
    if len(set(ids)) != len(ids):
        raise ValueError('Agent IDs must be unique')
    return agents


def _theta(agent, price, fundamental, returns, noise):
    if agent['type'] == 'fundamentalist':
        value = 0.5 + agent['strength'] * math.log(fundamental / price)
    elif agent['type'] == 'chartist':
        recent = returns[-agent['window']:]
        value = 0.5 + agent['strength'] * (sum(recent) / len(recent) if recent else 0.0)
    else:
        value = 0.5 + agent['strength'] * noise.get(agent['id'], 0.0)
    return min(1.0, max(0.0, value))


def _desired(agent, theta, price_cents):
    wealth = agent['cash'] + agent['shares'] * price_cents
    return theta * wealth / price_cents


def _allocate(total, weights):
    """Integer largest-remainder allocation of `total` proportional to integer weights (all <= weight)."""
    mass = sum(weights)
    if total <= 0 or mass <= 0:
        return [0] * len(weights)
    raw = [total * w / mass for w in weights]
    base = [min(w, math.floor(r)) for r, w in zip(raw, weights)]
    remaining = total - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order:
        if remaining == 0:
            break
        if base[i] < weights[i]:
            base[i] += 1
            remaining -= 1
    return base


def _walrasian(agents, theta_at, last_price):
    def excess(price):
        return sum(t * (a['cash'] + a['shares'] * price) / price - a['shares'] for a, t in zip(agents, theta_at(price)))

    lo, hi = math.log(max(last_price / 20, 1)), math.log(last_price * 20)
    if excess(math.exp(hi)) > 0:
        price = math.exp(hi)
    elif excess(math.exp(lo)) < 0:
        price = math.exp(lo)
    else:
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            if excess(math.exp(mid)) > 0:
                lo = mid
            else:
                hi = mid
        price = math.exp(0.5 * (lo + hi))
    cents = max(1, round(price))
    buys, sells = [], []
    for a, t in zip(agents, theta_at(cents)):
        gap = _desired(a, t, cents) - a['shares']
        buys.append(max(0, min(math.floor(gap), a['cash'] // cents)) if gap > 0 else 0)
        sells.append(max(0, min(math.floor(-gap), a['shares'])) if gap < 0 else 0)
    volume = min(sum(buys), sum(sells))
    bought, sold = _allocate(volume, buys), _allocate(volume, sells)
    for a, b, s in zip(agents, bought, sold):
        a['shares'] += b - s
        a['cash'] -= (b - s) * cents
    return cents, volume


def _order_book(agents, thetas, last_price, aggressiveness, order):
    bids, asks = [], []  # (price, sequence, agent index, quantity)
    reserved_cash = [0] * len(agents)
    reserved_shares = [0] * len(agents)
    trades, volume, sequence, last_trade = [], 0, 0, None
    for idx in order:
        a, t = agents[idx], thetas[idx]
        gap = _desired(a, t, last_price) - a['shares']
        if abs(gap) < 1:
            continue
        sequence += 1
        if gap > 0:
            limit = max(1, round(last_price * (1 + aggressiveness)))
            quantity = min(math.floor(gap), (a['cash'] - reserved_cash[idx]) // limit)
            while quantity > 0 and asks and asks[0][0] <= limit:
                price, seq, seller, available = asks[0]
                fill = min(quantity, available)
                agents[seller]['shares'] -= fill
                reserved_shares[seller] -= fill
                agents[seller]['cash'] += fill * price
                a['shares'] += fill
                a['cash'] -= fill * price
                quantity -= fill
                volume += fill
                last_trade = price
                if fill == available:
                    asks.pop(0)
                else:
                    asks[0] = (price, seq, seller, available - fill)
            if quantity > 0:
                reserved_cash[idx] += quantity * limit
                bids.append((limit, sequence, idx, quantity))
                bids.sort(key=lambda o: (-o[0], o[1]))
        else:
            limit = max(1, round(last_price * (1 - aggressiveness)))
            quantity = min(math.floor(-gap), a['shares'] - reserved_shares[idx])
            while quantity > 0 and bids and bids[0][0] >= limit:
                price, seq, buyer, wanted = bids[0]
                fill = min(quantity, wanted)
                agents[buyer]['shares'] += fill
                agents[buyer]['cash'] -= fill * price
                reserved_cash[buyer] -= fill * price
                a['shares'] -= fill
                a['cash'] += fill * price
                quantity -= fill
                volume += fill
                last_trade = price
                if fill == wanted:
                    bids.pop(0)
                else:
                    bids[0] = (price, seq, buyer, wanted - fill)
            if quantity > 0:
                reserved_shares[idx] += quantity
                asks.append((limit, sequence, idx, quantity))
                asks.sort(key=lambda o: (o[0], o[1]))
    if any(a['cash'] < 0 or a['shares'] < 0 for a in agents):
        raise ValueError('Order book produced a negative balance')
    return (last_trade if last_trade is not None else last_price), volume


def _moments(returns):
    n = len(returns)
    if n < 10:
        raise ValueError('At least 10 returns required for moments')
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    sd = math.sqrt(var)
    if var == 0:
        return {'sd': 0.0, 'excess_kurtosis': 0.0, 'autocorr_1': 0.0, 'abs_autocorr_1': 0.0}
    kurt = sum((r - mean) ** 4 for r in returns) / n / var ** 2 - 3
    ac = sum((returns[t] - mean) * (returns[t - 1] - mean) for t in range(1, n)) / n / var
    absr = [abs(r) for r in returns]
    am = sum(absr) / n
    av = sum((v - am) ** 2 for v in absr) / n
    aac = sum((absr[t] - am) * (absr[t - 1] - am) for t in range(1, n)) / n / av if av else 0.0
    return {'sd': sd, 'excess_kurtosis': kurt, 'autocorr_1': ac, 'abs_autocorr_1': aac}


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    agents = _agents(config, params)
    steps = integer(config.get('steps', 250), 'steps', 1, 100000)
    clearing = config.get('clearing', 'walrasian')
    if clearing not in ('walrasian', 'order_book'):
        raise ValueError('clearing must be walrasian or order_book')
    if steps * len(agents) > 2_000_000:
        raise ValueError('Market simulation work budget exceeded')
    rng = rng_from(seed)
    stochastic = mode == 'stochastic'
    if mode not in ('deterministic', 'stochastic'):
        raise ValueError('mode must be deterministic or stochastic')
    price = integer(config['initial_price_cents'], 'initial_price_cents', 1, 10**12)
    fundamental = float(config.get('fundamental', {}).get('initial_cents', price))
    path = config.get('fundamental', {}).get('path')
    total_cash, total_shares = sum(a['cash'] for a in agents), sum(a['shares'] for a in agents)
    prices, fundamentals, volumes, returns = [price], [fundamental], [], []
    initial_wealth = {t: sum(a['cash'] + a['shares'] * price for a in agents if a['type'] == t) for t in ('fundamentalist', 'chartist', 'noise')}
    for step in range(steps):
        if path is not None:
            fundamental = finite(path[min(step, len(path) - 1)], 'fundamental path', 1e-9)
        elif stochastic:
            fundamental *= math.exp(rng.gauss(0, params['fundamental_volatility']))
        noise = {a['id']: rng.gauss(0, 1) for a in agents if a['type'] == 'noise'} if stochastic else {}
        if clearing == 'walrasian':
            new_price, volume = _walrasian(agents, lambda p: [_theta(a, p, fundamental, returns, noise) for a in agents], price)
        else:
            thetas = [_theta(a, price, fundamental, returns, noise) for a in agents]
            order = list(range(len(agents)))
            if stochastic:
                rng.shuffle(order)
            new_price, volume = _order_book(agents, thetas, price, params['aggressiveness'], order)
        if sum(a['cash'] for a in agents) != total_cash or sum(a['shares'] for a in agents) != total_shares:
            raise ValueError('Market clearing violated cash or share conservation')
        returns.append(math.log(new_price / price))
        price = new_price
        prices.append(price)
        fundamentals.append(fundamental)
        volumes.append(volume)
    wealth = {t: sum(a['cash'] + a['shares'] * price for a in agents if a['type'] == t) for t in ('fundamentalist', 'chartist', 'noise')}
    return {'family': FAMILY['id'], 'mode': mode, 'clearing': clearing, 'validated': False, 'prices_cents': prices,
            'fundamental_cents': fundamentals, 'volume_shares': volumes, 'moments': _moments(returns) if len(returns) >= 10 else None,
            'mean_abs_log_mispricing': sum(abs(math.log(p / f)) for p, f in zip(prices, fundamentals)) / len(prices),
            'wealth_by_type_cents': {'initial': initial_wealth, 'final': wealth},
            'conservation': {'cash_cents': total_cash, 'shares': total_shares, 'checked_every_step': True},
            'agents_final': [{k: a[k] for k in ('id', 'type', 'cash', 'shares')} for a in agents[:200]]}


def fit(data, cutoff=None):
    bars, window = apply_cutoff(data['bars'], cutoff, label='daily_bars')
    closes = [finite(r['close'], 'close', 1e-12) for r in sorted(bars, key=lambda r: r['date'])]
    target = _moments([math.log(closes[t] / closes[t - 1]) for t in range(1, len(closes))])
    grid = data['grid']
    names = sorted(grid)
    combos = [{}]
    for name in names:
        if name not in FAMILY['parameters']:
            raise ValueError(f'Unknown grid parameter {name}')
        combos = [dict(c, **{name: v}) for c in combos for v in grid[name]]
    if not 1 <= len(combos) <= 500:
        raise ValueError('SMM grid must contain 1..500 combinations')
    scale = data.get('moment_scale', {'sd': target['sd'] or 1e-3, 'excess_kurtosis': 1.0, 'autocorr_1': 0.1, 'abs_autocorr_1': 0.1})
    base = deepcopy(data['base_config'])
    base.setdefault('steps', len(closes) - 1)
    burn_in = integer(data.get('burn_in', 0), 'burn_in', 0, 100000)
    horizon = integer(data.get('simulation_horizon_steps', 0), 'simulation_horizon_steps', 0, 100000)
    seed = data.get('seed', 0)
    table = []
    for combo in combos:
        config = deepcopy(base)
        config['parameters'] = {**base.get('parameters', {}), **combo}
        # The sample starts ``burn_in`` steps after the declared initial state; paths are prefix-consistent.
        prices = simulated_prices(config, seed, burn_in + base['steps'], horizon)
        moments = _moments(_log_returns(prices)[burn_in:])
        distance = sum(((moments[k] - target[k]) / scale[k]) ** 2 for k in scale)
        table.append({'parameters': combo, 'moments': moments, 'distance': distance})
    table.sort(key=lambda row: (row['distance'], sorted(row['parameters'].items())))
    return fit_result({**table[0]['parameters']}, {'target_moments': target, 'best_distance': table[0]['distance'], 'grid': table[:50],
                                                     'common_random_numbers_seed': seed, 'burn_in_steps': burn_in},
                      method='grid_simulated_method_of_moments', identification=FAMILY['identification'], windows=[window], data=data,
                      family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def example_config():
    return {'initial_price_cents': 10000, 'fundamental': {'initial_cents': 10000}, 'steps': 200, 'clearing': 'walrasian',
            'agents': [{'type': 'fundamentalist', 'count': 20, 'cash_cents': 5_000_000, 'shares': 500},
                       {'type': 'chartist', 'count': 20, 'cash_cents': 5_000_000, 'shares': 500},
                       {'type': 'noise', 'count': 20, 'cash_cents': 5_000_000, 'shares': 500}],
            'parameters': {'fundamentalist_strength': 2.0, 'chartist_strength': 20.0, 'noise_sd': 0.1, 'fundamental_volatility': 0.01}}


def moment_windows(bars, length):
    """Consecutive non-overlapping windows of ``length`` returns: rows {date: window end, start: base bar date}."""
    ordered = sorted(bars, key=lambda r: r['date'])
    length = integer(length, 'window length', 10, 100000)
    return [{'date': ordered[end]['date'], 'start': ordered[end - length]['date']} for end in range(length, len(ordered), length)]


def synthetic(seed=0, steps=150, agents_per_type=None, window=None, burn_in=0, truth=None, grid=None):
    """Bars generated with chartist_strength 40 (or ``truth``) from the declared initial state.

    ``burn_in`` discards generated steps before the first bar (recorded so fits and holdouts
    discard the same steps). With ``window`` (holdout fixtures) the SMM simulates the sample
    length, ``windows`` rows are added, and the fit and holdout random numbers (seeds
    ``seed + 1000`` and ``seed + 2000 + run``) are decoupled from the generating path.
    """
    config = example_config()
    config['steps'] = steps + burn_in
    if agents_per_type is not None:
        config['agents'] = [dict(a, count=agents_per_type) for a in config['agents']]
    truth = dict(truth or {'chartist_strength': 40.0})
    generated = simulate({**config, 'parameters': {**config['parameters'], **truth}}, 'stochastic', seed)
    from datetime import date, timedelta
    bars = [{'date': (date(2000, 1, 3) + timedelta(days=t)).isoformat(), 'close': p / 100}
            for t, p in enumerate(generated['prices_cents'][burn_in:])]
    config['steps'] = steps
    data = {'bars': bars, 'grid': grid or {'chartist_strength': [0.0, 20.0, 40.0, 80.0]}, 'base_config': config, 'seed': seed}
    if burn_in:
        data['burn_in'] = burn_in
    if window:
        data.update(base_config={k: v for k, v in config.items() if k != 'steps'}, windows=moment_windows(bars, window),
                    seed=seed + 1000, holdout_seed=seed + 2000, holdout_simulation_runs=10, simulation_horizon_steps=burn_in + steps)
    return {'data': data, 'truth': truth, 'config': config}


# ----------------------------------------------------------------------------- simulation cache and holdout forecaster

_PRICE_PATHS = {}


def simulated_prices(config, seed, steps, horizon=0):
    """Stochastic price path (cents) of ``steps`` steps, memoized.

    Draws are made step by step, so a longer run with the same configuration and seed has
    the shorter run as its prefix; a cached run is reused for any shorter request, and
    ``horizon`` lets callers simulate the longest length they will need once.
    """
    import json
    body = {k: v for k, v in config.items() if k != 'steps'}
    key = json.dumps([body, seed], sort_keys=True)
    cached = _PRICE_PATHS.get(key)
    if cached is None or len(cached) - 1 < steps:
        if len(_PRICE_PATHS) >= 512:
            _PRICE_PATHS.clear()
        cached = simulate(dict(body, steps=max(steps, horizon)), 'stochastic', seed)['prices_cents']
        _PRICE_PATHS[key] = cached
    return cached[:steps + 1]


WINDOW_MOMENTS = (('sd', 'window_return_sd'), ('abs_autocorr_1', 'window_abs_autocorr_1'), ('excess_kurtosis', 'window_excess_kurtosis'))


def _log_returns(closes):
    return [math.log(closes[t] / closes[t - 1]) for t in range(1, len(closes))]


def _holdout_forecast(parameters, history, rows, data):
    """Moments of the next non-overlapping return window from the ABM at the SMM-fitted parameters.

    Consistent with the fit (the sample starts ``burn_in`` steps after the declared initial
    state), the predictive distribution of a window moment is its mean and spread across
    ``holdout_simulation_runs`` seeded runs (seeds ``holdout_seed + run``) at the same elapsed
    steps, so slow wealth-share dynamics are matched by position. Primary target: window
    return sd; absolute-return autocorrelation and excess kurtosis are secondary groups.
    Baselines use earlier windows.
    """
    bars = sorted(data['bars'], key=lambda r: r['date'])
    position = {r['date']: k for k, r in enumerate(bars)}
    closes = [finite(r['close'], 'close', 1e-12) for r in bars]

    def moments(row):
        return _moments(_log_returns(closes[position[row['start']]:position[row['date']] + 1]))

    config = {k: v for k, v in deepcopy(data['base_config']).items() if k != 'steps'}
    config['parameters'] = {**config.get('parameters', {}), **{name: parameters[name] for name in sorted(data['grid']) if name in parameters}}
    runs = integer(data.get('holdout_simulation_runs', 10), 'holdout_simulation_runs', 2, 1000)
    seed = data.get('holdout_seed', data.get('seed', 0) + 1)
    burn_in = integer(data.get('burn_in', 0), 'burn_in', 0, 100000)
    horizon = integer(data.get('simulation_horizon_steps', 0), 'simulation_horizon_steps', 0, 100000)
    past = [moments(r) for r in sorted(history, key=lambda r: r['date'])]
    out = []
    for row in rows:
        start, end = position[row['start']], position[row['date']]
        actual = moments(row)
        simulated = [_moments(_log_returns(simulated_prices(config, seed + run, burn_in + end, horizon))[burn_in + start:burn_in + end])
                     for run in range(runs)]
        for key, group in WINDOW_MOMENTS:
            values = [m[key] for m in simulated]
            mean = sum(values) / runs
            spread = math.sqrt(sum((v - mean) ** 2 for v in values) / (runs - 1) * (1 + 1 / runs))
            out.append({'target': group, 'group': group, 'actual': actual[key], 'mean': mean, 'sd': spread,
                        'history_values': [m[key] for m in past]})
    return out


holdout_forecaster = {
    'rows_key': 'windows', 'time_key': 'date', 'target': 'window_return_sd', 'forecast': _holdout_forecast,
    'fit_keys': ['bars', 'grid', 'base_config', 'seed', 'burn_in', 'simulation_horizon_steps', 'moment_scale', 'information_time', 'revisions'],
    'conditional_inputs': [],
    'description': 'Return sd of the next non-overlapping window versus its simulated distribution across seeded runs at the same '
                   'elapsed steps and the SMM-fitted parameters (secondary groups: absolute-return autocorrelation, excess kurtosis).',
}
