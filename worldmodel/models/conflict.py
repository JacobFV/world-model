"""Conflict and instability: country-month event counts with self-excitation and cross-border diffusion.

Discrete-time multivariate Hawkes (Poisson autoregression) with geometric kernel::

    lambda[c,t] = exp(x[c,t] . gamma) + eta_self * S[c,t] + eta_neighbor * sum_c' W[c,c'] S[c',t]
    S[c,t]      = (1 - beta) * N[c,t-1] + beta * S[c,t-1]

W is the row-normalized contiguity matrix. gamma, eta are estimated by EM on the
branching structure (background vs. triggered events); beta by golden-section
profile likelihood. A negative-binomial (NB2) regression with lagged own and
neighbor counts is provided as a dispersion-robust alternative.
The branching ratio (spectral radius of eta_self I + eta_neighbor W) must be < 1
for stationarity. Coefficients are predictive associations, not causal effects.
"""
import math
from .base import (apply_cutoff, finite, fit_result, integer, merged_parameters, negative_binomial, negative_binomial_fit, parameter,
                   poisson, quantiles, requirement, rng_from, solve, validate_family, golden_section, inverse)

FAMILY = validate_family({
    'id': 'conflict',
    'title': 'Conflict event hazards with escalation and diffusion',
    'description': 'Self-exciting country-month event counts with neighbor diffusion and regime/economic covariates.',
    'identification': 'predictive_association',
    'validated': False,
    'parameters': {
        'background_coefficients': parameter({'const': 0.0}, 'log_events_per_month', 'Background log-intensity coefficients.'),
        'self_excitation': parameter(0.3, 'events_per_event', 'Expected own-country offspring per event.', bounds=[0, 1]),
        'neighbor_excitation': parameter(0.1, 'events_per_event', 'Expected offspring in neighbors per event (row-normalized).', bounds=[0, 1]),
        'decay': parameter(0.5, 'monthly_retention', 'Geometric kernel retention beta (mean lag 1/(1-beta) months).', bounds=[0, 0.999]),
        'dispersion': parameter(0.0, 'nb2_alpha', 'Negative-binomial overdispersion for stochastic draws (0 = Poisson).', bounds=[0, 50]),
    },
    'requirements': [
        requirement('ucdp_ged', 'Uppsala Conflict Data Program', 'UCDP Georeferenced Event Dataset (GED)', ['id', 'country_id', 'date_start', 'type_of_violence', 'best'],
                    'https://ucdp.uu.se/downloads/', frequency='annual release + monthly candidate', parameters=['background_coefficients', 'self_excitation', 'neighbor_excitation', 'decay']),
        requirement('gdelt_events', 'GDELT Project', 'GDELT 2.0 Events', ['SQLDATE', 'ActionGeo_CountryCode', 'EventRootCode', 'QuadClass', 'NumMentions'],
                    'https://www.gdeltproject.org/data.html', frequency='15 minutes', parameters=['self_excitation'],
                    notes='Media-coded; heavy duplication and coverage drift require deduplication and normalization.'),
        requirement('vdem', 'V-Dem Institute', 'V-Dem Country-Year Core', ['country_text_id', 'year', 'v2x_polyarchy', 'v2x_libdem'],
                    'https://www.v-dem.net/data/the-v-dem-dataset/', frequency='annual', parameters=['background_coefficients']),
        requirement('wdi_growth', 'World Bank', 'World Development Indicators', ['NY.GDP.MKTP.KD.ZG', 'SP.POP.TOTL'],
                    'https://data.worldbank.org/', frequency='annual', parameters=['background_coefficients']),
        requirement('cshapes_contiguity', 'ETH Zurich ICR', 'CShapes 2.0 (distance/contiguity)', ['gwcode', 'start', 'end', 'geometry'],
                    'https://icr.ethz.ch/data/cshapes/', frequency='as borders change', role='neighbor_graph', parameters=['neighbor_excitation']),
    ],
    'limitations': [
        'Reporting intensity changes (media attention) are confounded with event intensity.',
        'Annual covariates repeated monthly understate uncertainty; covariates must be lagged to be known at forecast time.',
        'Neighbor diffusion is contiguity-based and cannot distinguish contagion from common shocks.',
    ],
})


def _panel(rows, covariates, neighbors):
    countries = sorted({r['country'] for r in rows})
    months = sorted({r['month'] for r in rows})
    index = {(r['country'], r['month']): r for r in rows}
    if len(index) != len(rows):
        raise ValueError('Duplicate country-month rows')
    missing = [(c, m) for c in countries for m in months if (c, m) not in index]
    if missing:
        raise ValueError(f'Panel must be balanced with explicit zero counts; missing {missing[:3]}')
    N = [[integer(index[(c, m)]['count'], 'event count', 0, 10**9) for m in months] for c in countries]
    X = [[[1.0] + [finite(index[(c, m)][name], name) for name in covariates] for m in months] for c in countries]
    W = [[0.0] * len(countries) for _ in countries]
    position = {c: i for i, c in enumerate(countries)}
    for c, adjacent in (neighbors or {}).items():
        if c not in position:
            continue
        valid = [a for a in adjacent if a in position and a != c]
        for a in valid:
            W[position[c]][position[a]] = 1.0 / len(valid)
    return countries, months, N, X, W


def _excitation(N, W, beta):
    C, T = len(N), len(N[0])
    S = [[0.0] * T for _ in range(C)]
    for c in range(C):
        for t in range(1, T):
            S[c][t] = (1 - beta) * N[c][t - 1] + beta * S[c][t - 1]
    Snb = [[sum(W[c][d] * S[d][t] for d in range(C) if W[c][d]) for t in range(T)] for c in range(C)]
    return S, Snb


def _loglik(N, X, S, Snb, gamma, es, en, start=1):
    total = 0.0
    for c in range(len(N)):
        for t in range(start, len(N[0])):
            lam = math.exp(sum(g * v for g, v in zip(gamma, X[c][t]))) + es * S[c][t] + en * Snb[c][t]
            total += (N[c][t] * math.log(lam) if N[c][t] else 0.0) - lam - math.lgamma(N[c][t] + 1)
    return total


def _em(N, X, S, Snb, gamma, es, en, max_iter=1000, tol=1e-9):
    """Generalized EM: exact updates for excitation weights, one Newton step for background coefficients."""
    obs = [(X[c][t], N[c][t], S[c][t], Snb[c][t]) for c in range(len(N)) for t in range(1, len(N[0]))]
    k = len(gamma)
    constant = sum(math.lgamma(n + 1) for _, n, _, _ in obs)
    total_s = sum(o[2] for o in obs)
    total_n = sum(o[3] for o in obs)
    previous = -math.inf
    for iteration in range(1, max_iter + 1):
        ll, rs, rn = -constant, 0.0, 0.0
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for x, n, s, sn in obs:
            mu = math.exp(sum(g * v for g, v in zip(gamma, x)))
            lam = mu + es * s + en * sn
            ll += (n * math.log(lam) if n else 0.0) - lam
            residual = -mu
            if n:
                rs += n * es * s / lam
                rn += n * en * sn / lam
                residual += n * mu / lam
            for a in range(k):
                grad[a] += residual * x[a]
                wa = mu * x[a]
                row = hess[a]
                for b in range(a, k):
                    row[b] += wa * x[b]
        if abs(ll - previous) <= tol * (1 + abs(ll)):
            return gamma, es, en, ll, iteration, True
        previous = ll
        for a in range(k):
            for b in range(a):
                hess[a][b] = hess[b][a]
        es = rs / total_s if total_s > 0 else 0.0
        en = rn / total_n if total_n > 0 else 0.0
        step = solve(hess, grad)
        biggest = max(abs(v) for v in step)
        factor = 1.0 if biggest <= 1 else 1 / biggest
        gamma = [g + factor * v for g, v in zip(gamma, step)]
    return gamma, es, en, ll, max_iter, False


def spectral_radius(es, en, W, iterations=500):
    C = len(W)
    v = [1.0] * C
    value = 0.0
    for _ in range(iterations):
        nxt = [es * v[c] + en * sum(W[c][d] * v[d] for d in range(C)) for c in range(C)]
        norm = max(abs(x) for x in nxt) or 1.0
        new_value = norm / (max(abs(x) for x in v) or 1.0)
        v = [x / norm for x in nxt]
        if abs(new_value - value) < 1e-12:
            return new_value
        value = new_value
    return value


def fit_hawkes(rows, covariates=(), neighbors=None, cutoff=None, decay_bounds=(0.01, 0.98)):
    rows, window = apply_cutoff(rows, cutoff, time_key='month', label='country_months')
    countries, months, N, X, W = _panel(rows, list(covariates), neighbors)
    if len(months) < 12:
        raise ValueError('Hawkes estimation requires at least 12 months')
    names = ['const', *covariates]
    mean = sum(map(sum, N)) / (len(countries) * len(months))
    state = {'gamma': [math.log(max(mean * 0.5, 1e-3))] + [0.0] * len(covariates), 'es': 0.2, 'en': 0.1}
    cache = {}

    def profile(beta):
        S, Snb = _excitation(N, W, beta)
        gamma, es, en, ll, iterations, converged = _em(N, X, S, Snb, state['gamma'], max(state['es'], 1e-3), max(state['en'], 1e-3))
        state.update(gamma=gamma, es=es, en=en)
        cache[beta] = (gamma, es, en, ll, iterations, converged)
        return -ll

    grid = [0.1, 0.3, 0.5, 0.7, 0.9]
    best = min(grid, key=profile)
    lo, hi = max(decay_bounds[0], best - 0.2), min(decay_bounds[1], best + 0.2)
    beta = golden_section(profile, lo, hi, tol=2e-3)
    profile(beta)
    gamma, es, en, ll, iterations, converged = cache[beta]
    S, Snb = _excitation(N, W, beta)
    theta = gamma + [es, en, beta]
    se = _numerical_se(lambda p: _loglik(N, X, *_excitation(N, W, p[-1]), p[:-3], p[-3], p[-2]), theta)
    radius = spectral_radius(es, en, W)
    estimate = {'background_coefficients': dict(zip(names, gamma)), 'self_excitation': es, 'neighbor_excitation': en, 'decay': beta}
    diagnostics = {'loglik': ll, 'em_iterations_final': iterations, 'converged': converged, 'branching_ratio': radius,
                   'stationary': radius < 1, 'countries': len(countries), 'months': len(months),
                   'standard_errors': dict(zip(names + ['self_excitation', 'neighbor_excitation', 'decay'], se)) if se else None,
                   'mean_lag_months': 1 / (1 - beta)}
    return estimate, diagnostics, window


def _numerical_se(f, theta, h=1e-4):
    k = len(theta)
    base = f(theta)
    hess = [[0.0] * k for _ in range(k)]
    for a in range(k):
        for b in range(a, k):
            def shifted(da, db):
                p = list(theta)
                p[a] += da
                p[b] += db
                return f(p)
            try:
                if a == b:
                    value = (shifted(h, 0) - 2 * base + shifted(-h, 0)) / (h * h)
                else:
                    value = (shifted(h, h) - shifted(h, -h) - shifted(-h, h) + shifted(-h, -h)) / (4 * h * h)
            except (ValueError, OverflowError):
                return None
            hess[a][b] = hess[b][a] = -value
    try:
        cov = inverse(hess)
    except ValueError:
        return None
    if any(cov[a][a] <= 0 for a in range(k)):
        return None
    return [math.sqrt(cov[a][a]) for a in range(k)]


def fit_negative_binomial(rows, covariates=(), neighbors=None, cutoff=None):
    rows, window = apply_cutoff(rows, cutoff, time_key='month', label='country_months')
    countries, months, N, X, W = _panel(rows, list(covariates), neighbors)
    names = ['const', *covariates, 'lag_log1p_count', 'neighbor_lag_log1p_count']
    Xr, yr = [], []
    for c in range(len(countries)):
        for t in range(1, len(months)):
            neighbor = sum(W[c][d] * N[d][t - 1] for d in range(len(countries)))
            Xr.append(X[c][t] + [math.log1p(N[c][t - 1]), math.log1p(neighbor)])
            yr.append(N[c][t])
    model = negative_binomial_fit(yr, Xr, names=names)
    estimate = {'coefficients': model['coefficients'], 'dispersion': model['alpha']}
    diagnostics = {k: model[k] for k in ('standard_errors', 'loglik', 'iterations', 'converged', 'n', 'se_type')}
    return estimate, diagnostics, window


def fit(data, cutoff=None):
    model = data.get('model', 'hawkes')
    covariates = data.get('covariates', [])
    if model == 'hawkes':
        estimate, diagnostics, window = fit_hawkes(data['events'], covariates, data.get('neighbors'), cutoff)
        method = 'discrete_multivariate_hawkes_em_profile_decay'
    elif model == 'negative_binomial':
        estimate, diagnostics, window = fit_negative_binomial(data['events'], covariates, data.get('neighbors'), cutoff)
        method = 'nb2_regression_with_lagged_own_and_neighbor_counts'
    else:
        raise ValueError('model must be hawkes or negative_binomial')
    return fit_result(estimate, diagnostics, method=method, identification=FAMILY['identification'], windows=[window], data=data,
                      family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def _covariates_at(config, country, t, names):
    values = config.get('covariates', {}).get(country, {})
    row = [1.0]
    for name in names:
        value = values.get(name, 0.0)
        row.append(finite(value[min(t, len(value) - 1)] if isinstance(value, list) else value, name))
    return row


def _paths(config, params, draw=None, shock=None):
    countries = list(config['countries'])
    position = {c: i for i, c in enumerate(countries)}
    W = [[0.0] * len(countries) for _ in countries]
    for c, adjacent in config.get('neighbors', {}).items():
        valid = [a for a in adjacent if a in position and a != c]
        for a in valid:
            W[position[c]][position[a]] = 1.0 / len(valid)
    coefficients = params['background_coefficients']
    names = [k for k in coefficients if k != 'const']
    es, en, beta = params['self_excitation'], params['neighbor_excitation'], params['decay']
    S = [0.0] * len(countries)
    for c, history in config.get('history', {}).items():
        for count in history:
            S[position[c]] = (1 - beta) * finite(count, 'history count', 0) + beta * S[position[c]]
    # After the recursion S holds the excitation for the first forecast month.
    horizon = integer(config.get('horizon', 12), 'horizon', 1, 1200)
    paths = [[0.0] * horizon for _ in countries]
    intensities = [[0.0] * horizon for _ in countries]
    for t in range(horizon):
        for c, name in enumerate(countries):
            x = _covariates_at(config, name, t, names)
            mu = math.exp(coefficients.get('const', 0.0) + sum(coefficients[n] * v for n, v in zip(names, x[1:])))
            lam = mu + es * S[c] + en * sum(W[c][d] * S[d] for d in range(len(countries)) if W[c][d])
            intensities[c][t] = lam
            paths[c][t] = draw(lam) if draw else lam
            if shock and shock['country'] == name and shock.get('month_index', 0) == t:
                paths[c][t] += finite(shock['events'], 'shock events', 0)
        S = [(1 - beta) * paths[c][t] + beta * S[c] for c in range(len(countries))]
    return countries, paths, intensities, W


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    es, en = finite(params['self_excitation'], 'self_excitation', 0), finite(params['neighbor_excitation'], 'neighbor_excitation', 0)
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False}
    if mode == 'deterministic':
        countries, paths, intensities, W = _paths(config, params)
        report.update(expected_events={c: paths[i] for i, c in enumerate(countries)},
                      expected_total={c: sum(paths[i]) for i, c in enumerate(countries)})
        if config.get('shock'):
            _, shocked, _, _ = _paths(config, params, shock=config['shock'])
            report['diffusion_response'] = {c: sum(shocked[i]) - sum(paths[i]) - (config['shock']['events'] if c == config['shock']['country'] else 0.0)
                                            for i, c in enumerate(countries)}
            report['diffusion_note'] = 'Expected additional (offspring) events over the horizon caused by the shock, excluding the shock itself.'
        report['branching_ratio'] = spectral_radius(es, en, W)
        report['exact_expectation'] = 'Linear excitation makes the mean recursion exact for the expected counts.'
    elif mode == 'stochastic':
        rng = rng_from(seed)
        runs = integer(config.get('n_simulations', 500), 'n_simulations', 1, 100000)
        alpha = finite(params['dispersion'], 'dispersion', 0)
        threshold = finite(config.get('escalation_threshold', 10), 'escalation_threshold', 0)
        draw = (lambda lam: negative_binomial(rng, lam, alpha)) if alpha > 0 else (lambda lam: poisson(rng, lam))
        totals, escalations = {}, {}
        W = None
        for _ in range(runs):
            countries, paths, _, W = _paths(config, params, draw=draw, shock=config.get('shock'))
            for i, c in enumerate(countries):
                totals.setdefault(c, []).append(sum(paths[i]))
                escalations[c] = escalations.get(c, 0) + (max(paths[i]) >= threshold)
        report.update(n_simulations=runs, total_events_quantiles={c: quantiles(v) for c, v in totals.items()},
                      escalation_probability={c: escalations[c] / runs for c in totals}, escalation_threshold=threshold,
                      branching_ratio=spectral_radius(es, en, W))
    else:
        raise ValueError('mode must be deterministic or stochastic')
    return report


def synthetic(seed=0, countries=12, months=180):
    rng = rng_from(seed)
    names = [f'K{i:02d}' for i in range(countries)]
    neighbors = {n: [names[(i - 1) % countries], names[(i + 1) % countries]] for i, n in enumerate(names)}
    truth = {'background_coefficients': {'const': 0.3, 'polyarchy': -1.2, 'growth': -0.15}, 'self_excitation': 0.45,
             'neighbor_excitation': 0.25, 'decay': 0.6}
    polyarchy = {n: rng.uniform(0, 1) for n in names}
    growth = {n: [rng.gauss(0, 1) for _ in range(months)] for n in names}
    W = [[0.0] * countries for _ in range(countries)]
    for i, n in enumerate(names):
        for a in neighbors[n]:
            W[i][names.index(a)] = 0.5
    S = [0.0] * countries
    rows = []
    for t in range(months):
        month = f'{2000 + t // 12}-{t % 12 + 1:02d}'
        counts = []
        for i, n in enumerate(names):
            c = truth['background_coefficients']
            mu = math.exp(c['const'] + c['polyarchy'] * polyarchy[n] + c['growth'] * growth[n][t])
            lam = mu + truth['self_excitation'] * S[i] + truth['neighbor_excitation'] * sum(W[i][d] * S[d] for d in range(countries))
            counts.append(poisson(rng, lam))
            rows.append({'country': n, 'month': month, 'count': counts[-1], 'polyarchy': polyarchy[n], 'growth': growth[n][t]})
        S = [(1 - truth['decay']) * counts[i] + truth['decay'] * S[i] for i in range(countries)]
    config = {'countries': names, 'neighbors': neighbors, 'horizon': 12, 'parameters': truth,
              'covariates': {n: {'polyarchy': polyarchy[n], 'growth': 0.0} for n in names},
              'history': {n: [r['count'] for r in rows if r['country'] == n][-6:] for n in names},
              'shock': {'country': names[0], 'month_index': 0, 'events': 20}, 'escalation_threshold': 8, 'n_simulations': 300}
    return {'data': {'events': rows, 'neighbors': neighbors, 'covariates': ['polyarchy', 'growth']}, 'truth': truth, 'config': config}
