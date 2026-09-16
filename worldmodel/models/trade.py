"""Trade networks: structural gravity (PPML) and tariff/sanction shocks in general equilibrium.

Estimation: PPML with exporter-year and importer-year fixed effects (optionally pair
effects). The coefficient on ln(1 + tariff) identifies minus the trade elasticity.

Counterfactuals: a simplified multi-sector Caliendo-Parro (2015) exact-hat model
with input-output linkages. With one sector and no intermediates it reduces to an
Armington/Anderson-van Wincoop endowment economy with multilateral resistance.
For importer n, exporter i, sector j (tau = ad-valorem tariff, kappa = (1+tau) d)::

    c_hat[i,j] = w_hat[i]^gva[i,j] * prod_k P_hat[i,k]^gio[i,k,j]
    P_hat[n,j] = (sum_i pi[n,i,j] (kappa_hat c_hat[i,j])^-theta_j)^(-1/theta_j)
    pi'[n,i,j] = pi[n,i,j] (kappa_hat c_hat[i,j] / P_hat[n,j])^-theta_j
    X'[n,j]    = sum_k gio[n,j,k] Y'[n,k] + alpha[n,j] I'[n]
    Y'[i,k]    = sum_n pi'[n,i,k] X'[n,k] / (1 + tau'[i,n,k])
    I'[n]      = w_hat[n] VA[n] + R'[n] + D[n]
    w_hat[i] VA[i] = sum_j gva[i,j] Y'[i,j]          (labor/value-added market clearing)

Deficits D are held fixed in world-value-added numeraire units. Accounting checks:
trade balance equals -D for every country, value-added clearing, world income =
world value added + tariff revenue, and sum(D) = 0.
"""
from copy import deepcopy
import math
from .base import (NUMPY, apply_cutoff, finite, fit_result, integer, merged_parameters, parameter, poisson_fe,
                   quantiles, requirement, rng_from, validate_family)

FAMILY = validate_family({
    'id': 'trade',
    'title': 'Structural gravity and general-equilibrium trade policy shocks',
    'description': 'PPML gravity estimation and exact-hat GE counterfactuals with input-output linkages.',
    'identification': 'structural_gravity_conditional_on_exogenous_trade_costs',
    'validated': False,
    'parameters': {
        'trade_elasticity': parameter(5.0, 'dimensionless', 'Trade elasticity theta (per sector or common).', bounds=[0.1, 50],
                                      series=['baci:v', 'wits:tariff']),
        'trade_elasticity_se': parameter(1.0, 'dimensionless', 'Standard error used for stochastic elasticity draws.', bounds=[0, 50]),
        'distance_elasticity': parameter(-1.0, 'dimensionless', 'PPML coefficient on log distance.'),
        'sanction_coefficient': parameter(0.0, 'log_points', 'PPML coefficient on a sanction indicator.'),
        'io_coefficients': parameter({}, 'share_of_gross_output', 'Input-output coefficients gio[country][input][output].', source='data'),
        'value_added_share': parameter({}, 'share_of_gross_output', 'Value-added shares gva[country][sector].', source='data'),
    },
    'requirements': [
        requirement('baci', 'CEPII', 'BACI HS92/HS17 bilateral trade flows', ['t', 'i', 'j', 'k', 'v', 'q'],
                    'https://www.cepii.fr/CEPII/en/bdd_modele/bdd_modele_item.asp?id=37', frequency='annual',
                    parameters=['trade_elasticity', 'distance_elasticity', 'sanction_coefficient']),
        requirement('comtrade', 'United Nations', 'UN Comtrade bilateral merchandise trade', ['period', 'reporterCode', 'partnerCode', 'cmdCode', 'primaryValue'],
                    'https://comtradeplus.un.org/', frequency='annual/monthly', parameters=['trade_elasticity'], access='public_api_key'),
        requirement('cepii_gravity', 'CEPII', 'Gravity database', ['iso3_o', 'iso3_d', 'dist', 'contig', 'comlang_off', 'fta_wto'],
                    'https://www.cepii.fr/CEPII/en/bdd_modele/bdd_modele_item.asp?id=8', frequency='annual', parameters=['distance_elasticity']),
        requirement('wits_tariffs', 'World Bank / UNCTAD TRAINS', 'WITS applied and MFN tariffs', ['reporter', 'partner', 'product', 'year', 'simple_average'],
                    'https://wits.worldbank.org/', frequency='annual', parameters=['trade_elasticity']),
        requirement('io_tables', 'EXIOBASE / BEA / OECD ICIO', 'Multi-regional input-output tables',
                    ['Z (intermediate)', 'Y (final demand)', 'value added'], 'https://www.exiobase.eu/ ; https://www.bea.gov/industry/input-output-accounts-data',
                    frequency='annual', role='baseline_calibration', parameters=['io_coefficients', 'value_added_share']),
        requirement('global_sanctions_db', 'Drexel / Kiel Global Sanctions Data Base', 'GSDB sanction cases', ['sanctioning_state', 'sanctioned_state', 'begin', 'end', 'trade'],
                    'https://www.globalsanctionsdatabase.com/', frequency='annual', parameters=['sanction_coefficient'], access='registration'),
    ],
    'limitations': [
        'Tariff and sanction changes are treated as exogenous; policy endogeneity biases elasticities.',
        'Single factor (value added) per country, perfect competition, no capital or dynamics.',
        'Baseline must be internally consistent (final demand nonnegative); inconsistencies are rejected, not reconciled.',
        'Prohibitive sanctions are modeled as very large iceberg costs, not as rerouting (use the sanctions family).',
    ],
})

TOLERANCE = 1e-8


# ----------------------------------------------------------------------------- estimation

def _regressor(row, name):
    if name == 'ln_distance':
        return math.log(finite(row['distance_km'], 'distance_km', 1e-9))
    if name == 'ln_one_plus_tariff':
        return math.log1p(finite(row.get('tariff', 0.0), 'tariff', 0))
    return finite(row[name], name)


def fit_gravity(rows, cutoff=None, regressors=('ln_distance', 'contiguous', 'ln_one_plus_tariff'), fixed_effects=('exporter_year', 'importer_year'),
                include_domestic=True):
    rows, window = apply_cutoff(rows, cutoff, label='bilateral_flows')
    rows = [r for r in rows if include_domestic or r['exporter'] != r['importer']]
    if len(rows) < len(regressors) + 10:
        raise ValueError('Too few bilateral observations for PPML')
    keys = {'exporter_year': lambda r: f"{r['exporter']}|{r['year']}", 'importer_year': lambda r: f"{r['importer']}|{r['year']}",
            'pair': lambda r: f"{r['exporter']}|{r['importer']}", 'exporter': lambda r: r['exporter'], 'importer': lambda r: r['importer']}
    for name in fixed_effects:
        if name not in keys:
            raise ValueError(f'Unknown fixed effect {name}')
    y = [finite(r['value'], 'trade value', 0) for r in rows]
    X = [[_regressor(r, name) for name in regressors] for r in rows]
    groups = [[keys[name](r) for r in rows] for name in fixed_effects]
    pairs = [f"{r['exporter']}|{r['importer']}" for r in rows]
    model = poisson_fe(y, X, groups, names=list(regressors), clusters=pairs if len(set(pairs)) > 2 else None)
    return model, window


def fit(data, cutoff=None):
    regressors = tuple(data.get('regressors', ('ln_distance', 'contiguous', 'ln_one_plus_tariff')))
    fixed_effects = tuple(data.get('fixed_effects', ('exporter_year', 'importer_year')))
    model, window = fit_gravity(data['flows'], cutoff, regressors, fixed_effects)
    coefficients = model['coefficients']
    estimate = {'coefficients': coefficients}
    if 'ln_one_plus_tariff' in coefficients:
        estimate['trade_elasticity'] = -coefficients['ln_one_plus_tariff']
        estimate['trade_elasticity_se'] = model['standard_errors']['ln_one_plus_tariff']
    if 'ln_distance' in coefficients:
        estimate['distance_elasticity'] = coefficients['ln_distance']
    if 'sanction' in coefficients:
        estimate['sanction_coefficient'] = coefficients['sanction']
        theta = estimate.get('trade_elasticity')
        if theta and theta > 0:
            estimate['sanction_tariff_equivalent'] = math.exp(-coefficients['sanction'] / theta) - 1
    diagnostics = {k: model[k] for k in ('standard_errors', 'loglik', 'deviance', 'iterations', 'converged', 'dropped_separated_observations', 'n', 'se_type')}
    diagnostics['fixed_effects'] = list(fixed_effects)
    return fit_result(estimate, diagnostics, method='ppml_high_dimensional_fixed_effects', identification=FAMILY['identification'],
                      windows=[window], data=data, family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


# ----------------------------------------------------------------------------- baseline calibration

def _nested(table, countries, sectors, default, name):
    """Read table[sector][exporter][importer] into [j][i][n] lists."""
    out = []
    table = table or {}
    for s in sectors:
        by_exporter = table.get(s, {})
        out.append([[finite(by_exporter.get(i, {}).get(n, default), f'{name}[{s}][{i}][{n}]', 0) for n in countries] for i in countries])
    return out


def calibrate(baseline):
    countries, sectors = list(baseline['countries']), list(baseline['sectors'])
    if len(set(countries)) != len(countries) or len(set(sectors)) != len(sectors) or not countries or not sectors:
        raise ValueError('Countries and sectors must be unique nonempty lists')
    N, J = len(countries), len(sectors)
    if N * N * J > 2_000_000:
        raise ValueError('Trade baseline exceeds 2 million bilateral-sector cells')
    Z = _nested(baseline['flows'], countries, sectors, 0.0, 'flows')
    tau = _nested(baseline.get('tariffs'), countries, sectors, 0.0, 'tariffs')
    io = baseline.get('io_coefficients', {})
    gio = [[[finite(io.get(countries[n], {}).get(sectors[k], {}).get(sectors[j], 0.0), 'io coefficient', 0, 1) for j in range(J)] for k in range(J)]
           for n in range(N)]  # gio[n][input k][output j]
    gva = []
    for n in range(N):
        row = []
        for j in range(J):
            inputs = sum(gio[n][k][j] for k in range(J))
            supplied = baseline.get('value_added_share', {}).get(countries[n], {}).get(sectors[j])
            share = 1 - inputs if supplied is None else finite(supplied, 'value-added share', 0, 1)
            if share <= 0 or abs(share + inputs - 1) > 1e-9:
                raise ValueError(f'Value-added and input shares must sum to one with positive value added ({countries[n]}, {sectors[j]})')
            row.append(share)
        gva.append(row)
    M = [[[Z[j][i][n] * (1 + tau[j][i][n]) for n in range(N)] for i in range(N)] for j in range(J)]
    X = [[sum(M[j][i][n] for i in range(N)) for j in range(J)] for n in range(N)]
    pi = [[[M[j][i][n] / X[n][j] if X[n][j] > 0 else 0.0 for i in range(N)] for n in range(N)] for j in range(J)]  # pi[j][n][i]
    Y = [[sum(Z[j][i][n] for n in range(N)) for j in range(J)] for i in range(N)]
    VA = [sum(gva[i][j] * Y[i][j] for j in range(J)) for i in range(N)]
    if any(v <= 0 for v in VA):
        raise ValueError('Every country requires positive value added')
    F = [[X[n][k] - sum(gio[n][k][j] * Y[n][j] for j in range(J)) for k in range(J)] for n in range(N)]
    for n in range(N):
        for k in range(J):
            if F[n][k] < -1e-9 * max(1.0, X[n][k]):
                raise ValueError(f'Inconsistent baseline: negative final demand for {countries[n]} {sectors[k]}')
            F[n][k] = max(F[n][k], 0.0)
    I = [sum(F[n]) for n in range(N)]
    alpha = [[F[n][k] / I[n] if I[n] > 0 else 1.0 / J for k in range(J)] for n in range(N)]
    R = [sum(M[j][i][n] * tau[j][i][n] / (1 + tau[j][i][n]) for j in range(J) for i in range(N)) for n in range(N)]
    D = [I[n] - VA[n] - R[n] for n in range(N)]
    theta = baseline.get('trade_elasticity', 5.0)
    return {'countries': countries, 'sectors': sectors, 'N': N, 'J': J, 'Z': Z, 'tau': tau, 'gio': gio, 'gva': gva, 'X': X,
            'pi': pi, 'Y': Y, 'VA': VA, 'alpha': alpha, 'R': R, 'I': I, 'D': D, 'theta_default': theta}


def _thetas(parameters, sectors):
    value = parameters.get('trade_elasticity', 5.0)
    if isinstance(value, dict):
        return [finite(value[s], f'trade_elasticity[{s}]', 0.1, 50) for s in sectors]
    return [finite(value, 'trade_elasticity', 0.1, 50)] * len(sectors)


def _shock_arrays(cal, shock):
    N, J, countries, sectors = cal['N'], cal['J'], cal['countries'], cal['sectors']
    tau_new = deepcopy(cal['tau'])
    index_c = {c: i for i, c in enumerate(countries)}
    index_s = {s: j for j, s in enumerate(sectors)}
    for item in shock.get('tariffs', []):
        targets = sectors if item.get('sector', '*') == '*' else [item['sector']]
        exporters = countries if item.get('exporter', '*') == '*' else [item['exporter']]
        for s in targets:
            for e in exporters:
                if e == item['importer'] and item.get('exporter', '*') == '*':
                    continue
                j, i, n = index_s[s], index_c[e], index_c[item['importer']]
                if 'tariff' in item:
                    tau_new[j][i][n] = finite(item['tariff'], 'tariff', 0)
                else:
                    tau_new[j][i][n] = finite(tau_new[j][i][n] + item['add'], 'tariff', 0)
    dhat = [[[1.0] * N for _ in range(N)] for _ in range(J)]
    for item in shock.get('trade_cost_hat', []):
        targets = sectors if item.get('sector', '*') == '*' else [item['sector']]
        for s in targets:
            j, i, n = index_s[s], index_c[item['exporter']], index_c[item['importer']]
            dhat[j][i][n] *= finite(item['multiplier'], 'trade cost multiplier', 1e-6, 1e12)
    kappa = [[[(1 + tau_new[j][i][n]) / (1 + cal['tau'][j][i][n]) * dhat[j][i][n] for n in range(N)] for i in range(N)] for j in range(J)]
    return tau_new, kappa


def counterfactual(baseline, shock, parameters=None, *, fidelity='general_equilibrium', max_iter=5000, tol=1e-10, damping=0.5, backend='auto'):
    parameters = parameters or {}
    cal = calibrate(baseline)
    N, J = cal['N'], cal['J']
    thetas = _thetas({**{'trade_elasticity': cal['theta_default']}, **parameters}, cal['sectors'])
    tau_new, kappa = _shock_arrays(cal, shock)
    pi, X, gio, gva, VA, alpha, D = cal['pi'], cal['X'], cal['gio'], cal['gva'], cal['VA'], cal['alpha'], cal['D']
    if fidelity == 'partial':
        Zn = [[[cal['Z'][j][i][n] * (1 + cal['tau'][j][i][n]) * kappa[j][i][n] ** (-thetas[j]) / (1 + tau_new[j][i][n])
                for n in range(N)] for i in range(N)] for j in range(J)]
        return _report(cal, Zn, tau_new, None, None, [1.0] * N, None, None, thetas, fidelity, 0, True, None)
    if fidelity != 'general_equilibrium':
        raise ValueError('fidelity must be general_equilibrium or partial')
    use_numpy = NUMPY is not None and backend in ('auto', 'numpy') and N * N * J > 5000
    if backend == 'numpy' and NUMPY is None:
        raise ValueError('numpy backend requested but numpy is unavailable')
    w = [1.0] * N
    P = [[1.0] * J for _ in range(N)]
    Xn = deepcopy(X)
    converged = False
    iteration = 0
    for iteration in range(1, max_iter + 1):
        if use_numpy:
            P, pin = _prices_numpy(w, P, pi, kappa, gio, gva, thetas)
        else:
            P, pin = _prices(w, P, pi, kappa, gio, gva, thetas, N, J)
        Xn, Yn, Rn, In = _expenditure(Xn, pin, tau_new, gio, alpha, VA, D, w, N, J)
        demand = [sum(gva[i][j] * Yn[i][j] for j in range(J)) for i in range(N)]
        excess = [(demand[i] - w[i] * VA[i]) / VA[i] for i in range(N)]
        if max(abs(e) for e in excess) < tol:
            converged = True
            break
        w = [w[i] * (1 + damping * excess[i]) for i in range(N)]
        scale = sum(VA) / sum(w[i] * VA[i] for i in range(N))
        w = [v * scale for v in w]
    if not converged:
        raise ValueError('Trade equilibrium did not converge; reduce shock size or damping')
    Zn = [[[pin[j][n][i] * Xn[n][j] / (1 + tau_new[j][i][n]) for n in range(N)] for i in range(N)] for j in range(J)]
    return _report(cal, Zn, tau_new, P, pin, w, Xn, In, thetas, fidelity, iteration, converged, Rn, backend='numpy' if use_numpy else 'python')


def _prices(w, P, pi, kappa, gio, gva, thetas, N, J):
    for _ in range(10000):
        c = [[w[i] ** gva[i][j] * math.prod(P[i][k] ** gio[i][k][j] for k in range(J) if gio[i][k][j]) for j in range(J)] for i in range(N)]
        nxt = [[0.0] * J for _ in range(N)]
        for j in range(J):
            th = thetas[j]
            for n in range(N):
                total = sum(pi[j][n][i] * (kappa[j][i][n] * c[i][j]) ** (-th) for i in range(N) if pi[j][n][i] > 0)
                nxt[n][j] = total ** (-1 / th) if total > 0 else 1.0
        change = max(abs(a - b) for ra, rb in zip(nxt, P) for a, b in zip(ra, rb))
        P = nxt
        if change < 1e-13:
            break
    c = [[w[i] ** gva[i][j] * math.prod(P[i][k] ** gio[i][k][j] for k in range(J) if gio[i][k][j]) for j in range(J)] for i in range(N)]
    pin = [[[pi[j][n][i] * (kappa[j][i][n] * c[i][j] / P[n][j]) ** (-thetas[j]) if pi[j][n][i] > 0 else 0.0 for i in range(N)]
            for n in range(N)] for j in range(J)]
    return P, pin


def _prices_numpy(w, P, pi, kappa, gio, gva, thetas):  # pragma: no cover - optional acceleration
    np = NUMPY
    W = np.asarray(w)[:, None]
    GVA = np.asarray(gva)                      # [i, j]
    GIO = np.asarray(gio)                      # [i, k, j]
    PI = np.transpose(np.asarray(pi), (1, 2, 0))       # [n, i, j]
    K = np.transpose(np.asarray(kappa), (2, 1, 0))     # [n, i, j]
    TH = np.asarray(thetas)[None, None, :]
    Pm = np.asarray(P, float)
    positive = PI > 0
    for _ in range(10000):
        logc = GVA * np.log(W) + np.einsum('ikj,ik->ij', GIO, np.log(Pm))
        term = np.where(positive, PI * (K * np.exp(logc)[None, :, :]) ** (-TH), 0.0)
        total = term.sum(axis=1)
        nxt = np.where(total > 0, total ** (-1 / TH[0]), 1.0)
        change = np.abs(nxt - Pm).max()
        Pm = nxt
        if change < 1e-13:
            break
    logc = GVA * np.log(W) + np.einsum('ikj,ik->ij', GIO, np.log(Pm))
    pin = np.where(positive, PI * (K * np.exp(logc)[None, :, :] / Pm[:, None, :]) ** (-TH), 0.0)
    return Pm.tolist(), np.transpose(pin, (2, 0, 1)).tolist()


def _expenditure(Xn, pin, tau_new, gio, alpha, VA, D, w, N, J):
    for _ in range(100000):
        Yn = [[sum(pin[k][n][i] * Xn[n][k] / (1 + tau_new[k][i][n]) for n in range(N)) for k in range(J)] for i in range(N)]
        Rn = [sum(Xn[n][j] * sum(pin[j][n][i] * tau_new[j][i][n] / (1 + tau_new[j][i][n]) for i in range(N)) for j in range(J)) for n in range(N)]
        In = [w[n] * VA[n] + Rn[n] + D[n] for n in range(N)]
        nxt = [[sum(gio[n][j][k] * Yn[n][k] for k in range(J)) + alpha[n][j] * In[n] for j in range(J)] for n in range(N)]
        change = max(abs(a - b) / max(1.0, abs(b)) for ra, rb in zip(nxt, Xn) for a, b in zip(ra, rb))
        Xn = nxt
        if change < 1e-13:
            break
    Yn = [[sum(pin[k][n][i] * Xn[n][k] / (1 + tau_new[k][i][n]) for n in range(N)) for k in range(J)] for i in range(N)]
    Rn = [sum(Xn[n][j] * sum(pin[j][n][i] * tau_new[j][i][n] / (1 + tau_new[j][i][n]) for i in range(N)) for j in range(J)) for n in range(N)]
    In = [w[n] * VA[n] + Rn[n] + D[n] for n in range(N)]
    return Xn, Yn, Rn, In


def _accounting(Z, VA_new, R, I, D, gva, N, J, label):
    Y = [[sum(Z[j][i][n] for n in range(N)) for j in range(J)] for i in range(N)]
    exports = [sum(Z[j][i][n] for j in range(J) for n in range(N) if n != i) for i in range(N)]
    imports = [sum(Z[j][i][n] for j in range(J) for i in range(N) if i != n) for n in range(N)]
    scale = max(1.0, sum(VA_new))
    checks = {'trade_balance_plus_deficit_max_residual': max(abs(exports[n] - imports[n] + D[n]) for n in range(N)) / scale,
              'value_added_clearing_max_residual': max(abs(sum(gva[i][j] * Y[i][j] for j in range(J)) - VA_new[i]) for i in range(N)) / scale,
              'world_income_identity_residual': abs(sum(I) - sum(VA_new) - sum(R)) / scale,
              'deficits_sum_residual': abs(sum(D)) / scale}
    if any(v > 1e-6 for v in checks.values()):
        raise ValueError(f'Trade accounting identity violated in {label}: {checks}')
    return checks, Y, exports, imports


def _report(cal, Zn, tau_new, P, pin, w, Xn, In, thetas, fidelity, iterations, converged, Rn, backend='python'):
    N, J, countries, sectors = cal['N'], cal['J'], cal['countries'], cal['sectors']
    base_checks, Y0, ex0, im0 = _accounting(cal['Z'], cal['VA'], cal['R'], cal['I'], cal['D'], cal['gva'], N, J, 'baseline')
    report = {'family': FAMILY['id'], 'fidelity': fidelity, 'validated': False, 'trade_elasticity': dict(zip(sectors, thetas)),
              'iterations': iterations, 'converged': converged, 'backend': backend, 'accounting': {'baseline': base_checks}}
    Y1 = [[sum(Zn[j][i][n] for n in range(N)) for j in range(J)] for i in range(N)]
    total0 = {(i, n): sum(cal['Z'][j][i][n] for j in range(J)) for i in range(N) for n in range(N)}
    total1 = {(i, n): sum(Zn[j][i][n] for j in range(J)) for i in range(N) for n in range(N)}
    report['bilateral_trade_change'] = {f'{countries[i]}->{countries[n]}': (total1[(i, n)] / total0[(i, n)] - 1 if total0[(i, n)] > 0 else None)
                                        for i in range(N) for n in range(N) if i != n}
    report['sector_output_change'] = {countries[i]: {sectors[j]: (Y1[i][j] / Y0[i][j] - 1 if Y0[i][j] > 0 else None) for j in range(J)} for i in range(N)}
    report['flows'] = {sectors[j]: {countries[i]: {countries[n]: Zn[j][i][n] for n in range(N)} for i in range(N)} for j in range(J)}
    if fidelity == 'partial':
        report['approximation'] = ['Partial equilibrium: wages, prices and multilateral resistance held fixed; no accounting closure.']
        return report
    VA_new = [w[i] * cal['VA'][i] for i in range(N)]
    checks, _, ex1, im1 = _accounting(Zn, VA_new, Rn, In, cal['D'], cal['gva'], N, J, 'counterfactual')
    report['accounting']['counterfactual'] = checks
    price_index = [math.prod(P[n][j] ** cal['alpha'][n][j] for j in range(J)) for n in range(N)]
    report.update({
        'wage_change': {countries[i]: w[i] - 1 for i in range(N)},
        'sector_price_change': {countries[n]: {sectors[j]: P[n][j] - 1 for j in range(J)} for n in range(N)},
        'consumer_price_index_change': {countries[n]: price_index[n] - 1 for n in range(N)},
        'real_income_change': {countries[n]: (In[n] / cal['I'][n]) / price_index[n] - 1 for n in range(N)},
        'tariff_revenue': {countries[n]: Rn[n] for n in range(N)},
        'exports_change': {countries[i]: ex1[i] / ex0[i] - 1 if ex0[i] > 0 else None for i in range(N)},
        'domestic_share_change': {countries[n]: {sectors[j]: pin[j][n][n] - cal['pi'][j][n][n] for j in range(J)} for n in range(N)},
        'numeraire': 'world value added held at baseline level',
    })
    return report


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    fidelity = config.get('fidelity', 'general_equilibrium')
    if mode == 'deterministic':
        return counterfactual(config['baseline'], config.get('shock', {}), params, fidelity=fidelity)
    if mode != 'stochastic':
        raise ValueError('mode must be deterministic or stochastic')
    rng = rng_from(seed)
    draws = integer(config.get('n_draws', 50), 'n_draws', 1, 2000)
    base = counterfactual(config['baseline'], config.get('shock', {}), params, fidelity=fidelity)
    sectors = config['baseline']['sectors']
    theta = params['trade_elasticity']
    se = finite(params['trade_elasticity_se'], 'trade_elasticity_se', 0)
    welfare, exports = {}, {}
    for _ in range(draws):
        if isinstance(theta, dict):
            sample = {s: max(0.5, rng.gauss(theta[s], se)) for s in sectors}
        else:
            sample = max(0.5, rng.gauss(theta, se))
        result = counterfactual(config['baseline'], config.get('shock', {}), {**params, 'trade_elasticity': sample}, fidelity=fidelity)
        for country, value in result.get('real_income_change', {}).items():
            welfare.setdefault(country, []).append(value)
        for country, value in result.get('exports_change', {}).items():
            if value is not None:
                exports.setdefault(country, []).append(value)
    base.update(mode='stochastic', n_draws=draws, uncertainty='trade elasticity drawn from N(theta, se) truncated at 0.5',
                real_income_change_quantiles={k: quantiles(v) for k, v in welfare.items()},
                exports_change_quantiles={k: quantiles(v) for k, v in exports.items()})
    return base


def synthetic(seed=0, countries=8, years=3):
    rng = rng_from(seed)
    names = [f'C{i}' for i in range(countries)]
    position = {c: (rng.uniform(0, 10), rng.uniform(0, 10)) for c in names}
    truth = {'ln_distance': -0.9, 'contiguous': 0.5, 'ln_one_plus_tariff': -4.0}
    rows = []
    for year in range(2015, 2015 + years):
        o = {c: rng.gauss(0, 0.8) for c in names}
        d = {c: rng.gauss(0, 0.8) for c in names}
        for i in names:
            for n in names:
                dist = 50.0 if i == n else 1000 * math.dist(position[i], position[n]) + 100
                contiguous = int(i != n and math.dist(position[i], position[n]) < 3)
                tariff = 0.0 if i == n else rng.choice([0.0, 0.05, 0.1, 0.2, 0.35])
                mean = math.exp(12 + o[i] + d[n] + truth['ln_distance'] * math.log(dist) + truth['contiguous'] * contiguous
                                + truth['ln_one_plus_tariff'] * math.log1p(tariff))
                rows.append({'exporter': i, 'importer': n, 'year': year, 'date': f'{year}-12-31', 'distance_km': dist,
                             'contiguous': contiguous, 'tariff': tariff, 'value': mean * rng.gammavariate(20, 1 / 20)})
    return {'data': {'flows': rows}, 'truth': {'coefficients': truth, 'trade_elasticity': 4.0}, 'config': example_config()}


def example_config():
    countries, sectors = ['USA', 'CHN', 'ROW'], ['goods', 'services']
    size = {'USA': 3.0, 'CHN': 2.5, 'ROW': 4.0}
    flows = {}
    for s, openness in (('goods', 0.35), ('services', 0.08)):
        flows[s] = {}
        for i in countries:
            flows[s][i] = {}
            for n in countries:
                base = 100 * size[i] * size[n] / sum(size.values())
                flows[s][i][n] = round(base * (1 if i != n else (1 / openness)), 3)
    io = {c: {'goods': {'goods': 0.3, 'services': 0.1}, 'services': {'goods': 0.15, 'services': 0.2}} for c in countries}
    return {'baseline': {'countries': countries, 'sectors': sectors, 'flows': flows, 'io_coefficients': io,
                         'tariffs': {'goods': {'CHN': {'USA': 0.03}, 'USA': {'CHN': 0.07}}}},
            'shock': {'tariffs': [{'importer': 'USA', 'exporter': 'CHN', 'sector': 'goods', 'tariff': 0.25}]},
            'parameters': {'trade_elasticity': {'goods': 4.0, 'services': 5.0}, 'trade_elasticity_se': 0.8},
            'fidelity': 'general_equilibrium', 'n_draws': 10}


# ----------------------------------------------------------------------------- holdout forecaster

FRICTIONLESS = 'frictionless_marginals'


def balance_flows(cost, supply, demand, *, max_iter=2000, tol=1e-12):
    """Flows a_i b_n cost[i,n] matching exporter supply and importer demand (the PPML FE score equations given slopes)."""
    exporters, importers = sorted(supply), sorted(demand)
    a, b = {i: 1.0 for i in exporters}, {n: 1.0 for n in importers}
    for _ in range(max_iter):
        for i in exporters:
            denominator = sum(b[n] * cost[(i, n)] for n in importers if (i, n) in cost)
            a[i] = supply[i] / denominator if denominator > 0 else 0.0
        change = 0.0
        for n in importers:
            denominator = sum(a[i] * cost[(i, n)] for i in exporters if (i, n) in cost)
            value = demand[n] / denominator if denominator > 0 else 0.0
            change = max(change, abs(value - b[n]) / max(abs(b[n]), 1e-300))
            b[n] = value
        if change < tol:
            break
    return {key: a[key[0]] * b[key[1]] * value for key, value in cost.items()}


def _year_fits(rows, coefficients, regressors):
    supply, demand, costs, frictionless = {}, {}, {}, {}
    for r in rows:
        key = (r['exporter'], r['importer'])
        value = finite(r['value'], 'trade value', 0)
        supply[key[0]] = supply.get(key[0], 0.0) + value
        demand[key[1]] = demand.get(key[1], 0.0) + value
        costs[key] = math.exp(sum(coefficients[name] * _regressor(r, name) for name in regressors))
        frictionless[key] = 1.0
    return balance_flows(costs, supply, demand), balance_flows(frictionless, supply, demand)


def _holdout_forecast(parameters, history, rows, data, dispersion_years=3):
    """Held-out bilateral flows: estimated trade costs with exporter/importer terms matched to realized target-year totals.

    Given PPML slopes, exporter-year and importer-year effects are the unique scaling that
    reproduces each exporter's total shipments and each importer's total purchases, so the
    forecast conditions on those realized totals (not on individual target flows). The
    predictive sd is cv * mean with cv from the same construction on the last pre-origin
    years. ``frictionless_marginals`` uses identical totals with no trade costs. The GE
    counterfactual is not scored.
    """
    regressors = tuple(data.get('regressors', ('ln_distance', 'contiguous', 'ln_one_plus_tariff')))
    if tuple(data.get('fixed_effects', ('exporter_year', 'importer_year'))) != ('exporter_year', 'importer_year'):
        raise ValueError('The trade holdout forecaster requires exporter-year and importer-year fixed effects')
    coefficients = parameters['coefficients']
    domestic = bool(data.get('score_domestic_flows', False))
    years = {}
    for r in history:
        years.setdefault(r['year'], []).append(r)
    squares = [[], []]
    for year in sorted(years)[-dispersion_years:]:
        for fitted, bucket in zip(_year_fits(years[year], coefficients, regressors), squares):
            for r in years[year]:
                mu = fitted[(r['exporter'], r['importer'])]
                if (domestic or r['exporter'] != r['importer']) and mu > 0:
                    bucket.append(((r['value'] - mu) / mu) ** 2)
    if not squares[0]:
        raise ValueError('Trade holdout forecasts need at least one pre-origin year of flows')
    cv = [math.sqrt(sum(b) / len(b)) for b in squares]
    model, null = _year_fits(rows, coefficients, regressors)
    past = {}
    for r in sorted(history, key=lambda r: r['year']):
        past.setdefault((r['exporter'], r['importer']), []).append(r['value'])
    out = []
    for r in sorted(rows, key=lambda r: (r['exporter'], r['importer'])):
        key = (r['exporter'], r['importer'])
        if not domestic and key[0] == key[1]:
            continue
        out.append({'target': f'flow:{key[0]}->{key[1]}', 'actual': r['value'], 'mean': model[key], 'sd': cv[0] * model[key],
                    'history_values': past.get(key, []), 'baselines': {FRICTIONLESS: {'mean': null[key], 'sd': cv[1] * null[key]}}})
    return out


holdout_forecaster = {
    'rows_key': 'flows', 'time_key': 'date', 'target': 'bilateral_flow', 'forecast': _holdout_forecast,
    'fit_keys': ['flows', 'regressors', 'fixed_effects', 'information_time', 'revisions'],
    'conditional_inputs': ['exporter_total_shipments_at_target_year', 'importer_total_purchases_at_target_year',
                           'trade_cost_regressors_at_target_year'],
    'baselines': [FRICTIONLESS],
    'description': 'Next-year international bilateral flows from PPML trade costs balanced to realized exporter/importer totals, '
                   'versus frictionless flows with the same totals; GE counterfactuals are not scored.',
}
