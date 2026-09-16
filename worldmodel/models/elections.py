"""Elections: district two-party vote share from fundamentals, turnout and seat simulation.

    dem_share_dt = x_dt . beta + nu_t + eps_dt,  nu_t ~ N(0, s_nat^2), eps_dt ~ N(0, s_dist^2)

Regressors: constant, partisan lean (PVI as a two-party fraction), incumbency (+1 D,
-1 R, 0 open), economy x president party, midterm x president party and the log
fundraising ratio. beta is pooled OLS with cycle-clustered standard errors;
variance components are method-of-moments from within/between-cycle residuals.
Turnout is a linear model on the logit of turnout / citizen voting-age population.
Seat distributions integrate the shared national swing with deterministic
quantile quadrature; district errors are independent given the swing.
"""
import math
from .base import (apply_cutoff, finite, fit_result, integer, logistic, merged_parameters, normal_cdf,
                   normal_quadrature, ols, parameter, poisson_binomial, quantiles, requirement, rng_from, solve, validate_family)

SHARE_TERMS = ['const', 'pvi', 'incumbent', 'econ_x_president', 'midterm_x_president', 'fundraising']
TURNOUT_TERMS = ['const', 'midterm', 'closeness', 'lag_logit_turnout']

FAMILY = validate_family({
    'id': 'elections',
    'title': 'Election fundamentals, turnout and seat outcomes',
    'description': 'District vote shares from partisanship, incumbency, economy and fundraising with shared national swing.',
    'identification': 'predictive_association',
    'validated': False,
    'parameters': {
        'coefficients': parameter({'const': 0.5, 'pvi': 1.0, 'incumbent': 0.03, 'econ_x_president': 0.004,
                                   'midterm_x_president': -0.02, 'fundraising': 0.01}, 'two_party_share',
                                  'Vote-share regression coefficients (econ in percent growth; fundraising natural-log ratio).'),
        'sigma_national': parameter(0.025, 'two_party_share', 'Standard deviation of shared national swing.', bounds=[0, 0.5]),
        'sigma_district': parameter(0.04, 'two_party_share', 'Standard deviation of district-specific error.', bounds=[0, 0.5]),
        'student_t_df': parameter(None, 'degrees_of_freedom', 'Optional Student-t tails for stochastic draws (null = normal).', source='assumed'),
        'turnout_coefficients': parameter({'const': -0.3, 'midterm': -0.35, 'closeness': 2.0, 'lag_logit_turnout': 0.0}, 'logit',
                                          'Turnout logit coefficients; closeness = -|expected share - 0.5|.'),
    },
    'requirements': [
        requirement('medsl_house', 'MIT Election Data and Science Lab', 'U.S. House 1976-2022 returns (Harvard Dataverse doi:10.7910/DVN/IG0UN2)',
                    ['year', 'state_po', 'district', 'party', 'candidatevotes', 'totalvotes'], 'https://electionlab.mit.edu/data',
                    frequency='biennial', parameters=['coefficients', 'sigma_national', 'sigma_district']),
        requirement('district_presidential_lean', 'MIT Election Data and Science Lab / Redistricting Data Hub',
                    'Presidential vote by congressional district', ['year', 'district', 'dem_votes', 'rep_votes'],
                    'https://electionlab.mit.edu/data', frequency='quadrennial', parameters=['coefficients']),
        requirement('fec_candidate_summary', 'Federal Election Commission', 'All candidates summary (weball) bulk file',
                    ['CAND_ID', 'CAND_PTY_AFFILIATION', 'TTL_RECEIPTS', 'CAND_ICI', 'CAND_OFFICE_DISTRICT'],
                    'https://www.fec.gov/data/browse-data/?tab=bulk-data', frequency='per cycle', parameters=['coefficients']),
        requirement('fred_economy', 'Federal Reserve Bank of St. Louis', 'FRED', ['A229RX0 (real disposable personal income)', 'GDPC1', 'UNRATE'],
                    'https://fred.stlouisfed.org/', frequency='monthly/quarterly', parameters=['coefficients']),
        requirement('turnout_vep', 'U.S. Elections Project / Census Bureau', 'VEP turnout and ACS CVAP special tabulation',
                    ['state', 'year', 'total_ballots', 'vep', 'cvap'], 'https://election.lab.ufl.edu/', frequency='biennial',
                    parameters=['turnout_coefficients']),
    ],
    'limitations': [
        'Coefficients are predictive associations; fundraising and incumbency are endogenous to expected competitiveness.',
        'Redistricting changes district identities; lean must be recomputed on current boundaries.',
        'Uncontested races are excluded from estimation and must be handled explicitly in simulation.',
        'Few cycles make the national-swing variance imprecise.',
    ],
})


def _share_row(row, national=None):
    national = national or row
    president = finite(national.get('president_party', 0), 'president_party', -1, 1)
    dem, rep = finite(row.get('dem_receipts', 0), 'dem_receipts', 0), finite(row.get('rep_receipts', 0), 'rep_receipts', 0)
    return [1.0, finite(row['pvi'], 'pvi', -1, 1), finite(row.get('incumbent', 0), 'incumbent', -1, 1),
            finite(national.get('econ', 0), 'econ') * president, finite(national.get('midterm', 0), 'midterm', 0, 1) * president,
            math.log((dem + 1) / (rep + 1))]


def _logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def fit(data, cutoff=None):
    races, window = apply_cutoff(data['races'], cutoff, label='races')
    usable = [r for r in races if not r.get('uncontested') and r.get('dem_share') is not None and 0 < r['dem_share'] < 1]
    if len(usable) < 20:
        raise ValueError('At least 20 contested races are required')
    X = [_share_row(r) for r in usable]
    y = [finite(r['dem_share'], 'dem_share', 0, 1) for r in usable]
    cycles = [r['cycle'] for r in usable]
    model = ols(X, y, names=SHARE_TERMS, clusters=cycles if len(set(cycles)) >= 2 else None)
    by_cycle, design_by_cycle = {}, {}
    for c, e, row in zip(cycles, model['residuals'], X):
        by_cycle.setdefault(c, []).append(e)
        design_by_cycle.setdefault(c, []).append(row)
    within = sum((e - sum(es) / len(es)) ** 2 for es in by_cycle.values() for e in es)
    dof = len(y) - len(by_cycle)
    sigma_district = math.sqrt(within / dof) if dof > 0 else None
    means = [sum(es) / len(es) for es in by_cycle.values()]
    # Regressors constant within every cycle (constant, economy and midterm terms) absorb cycle means,
    # so the between-cycle variance has (cycles - cycle-level regressors) degrees of freedom.
    cycle_level = sum(1 for j in range(len(SHARE_TERMS))
                      if all(max(r[j] for r in rows_) - min(r[j] for r in rows_) <= 1e-12 for rows_ in design_by_cycle.values()))
    between_dof = len(means) - cycle_level
    sigma_national = None
    if between_dof >= 1 and sigma_district is not None:
        grand = sum(means) / len(means)
        between = sum((m - grand) ** 2 for m in means) / between_dof
        noise = sigma_district ** 2 * sum(1 / len(es) for es in by_cycle.values()) / len(by_cycle)
        sigma_national = math.sqrt(max(between - noise, 0.0))
    estimate = {'coefficients': model['coefficients'], 'sigma_district': sigma_district, 'sigma_national': sigma_national}
    diagnostics = {'share_model': {k: model[k] for k in ('standard_errors', 'r2', 'n', 'se_type', 'sigma')},
                   'cycles': len(by_cycle), 'cycle_level_regressors': cycle_level, 'between_cycle_dof': between_dof,
                   'excluded_uncontested_or_missing': len(races) - len(usable),
                   'national_swing_by_cycle': {str(c): sum(es) / len(es) for c, es in by_cycle.items()}}
    windows = [window]
    turnout_rows = data.get('turnout')
    if turnout_rows:
        rows, twindow = apply_cutoff(turnout_rows, cutoff, label='turnout')
        windows.append(twindow)
        TX, ty = [], []
        for r in rows:
            rate = finite(r['ballots'], 'ballots', 0) / finite(r['cvap'], 'cvap', 1)
            if not 0 < rate < 1:
                continue
            lag = r.get('lag_turnout_rate')
            TX.append([1.0, finite(r.get('midterm', 0), 'midterm', 0, 1), -abs(finite(r.get('dem_share', 0.5), 'dem_share', 0, 1) - 0.5),
                       _logit(lag) if lag is not None else 0.0])
            ty.append(_logit(rate))
        has_lag = any(row[3] != 0 for row in TX)
        names = TURNOUT_TERMS if has_lag else TURNOUT_TERMS[:3]
        tmodel = ols([row[:len(names)] for row in TX], ty, names=names)
        coefficients = dict(tmodel['coefficients'])
        coefficients.setdefault('lag_logit_turnout', 0.0)
        estimate['turnout_coefficients'] = coefficients
        diagnostics['turnout_model'] = {k: tmodel[k] for k in ('standard_errors', 'r2', 'n', 'se_type', 'sigma')}
    return fit_result(estimate, diagnostics, method='pooled_ols_cycle_clustered_with_variance_components',
                      identification=FAMILY['identification'], windows=windows, data=data, family_id=FAMILY['id'],
                      requirements=[r['id'] for r in FAMILY['requirements']])


def expected_shares(config):
    params = merged_parameters(FAMILY, config.get('parameters'))
    coefficients = params['coefficients']
    missing = set(SHARE_TERMS) - set(coefficients)
    if missing:
        raise ValueError(f'Missing vote-share coefficients: {sorted(missing)}')
    national = config.get('national', {})
    shares = {}
    for district in config['districts']:
        if district.get('uncontested'):
            shares[district['id']] = 1.0 if district['uncontested'] == 'D' else 0.0
            continue
        row = _share_row(district, national)
        shares[district['id']] = sum(coefficients[name] * value for name, value in zip(SHARE_TERMS, row))
    return shares, params


def _turnout(config, shares, params):
    t = params['turnout_coefficients']
    midterm = config.get('national', {}).get('midterm', 0)
    result = {}
    for district in config['districts']:
        if 'cvap' not in district:
            continue
        lag = district.get('lag_turnout_rate')
        eta = (t['const'] + t['midterm'] * midterm + t['closeness'] * -abs(shares[district['id']] - 0.5)
               + t.get('lag_logit_turnout', 0.0) * (_logit(lag) if lag is not None else 0.0))
        result[district['id']] = district['cvap'] * logistic(eta)
    return result


def simulate(config, mode='deterministic', seed=0):
    shares, params = expected_shares(config)
    ids = [d['id'] for d in config['districts']]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError('District IDs must be unique and nonempty')
    sn, sd = finite(params['sigma_national'], 'sigma_national', 0), finite(params['sigma_district'], 'sigma_district', 0)
    majority = integer(config.get('majority_seats', len(ids) // 2 + 1), 'majority_seats', 1, len(ids))
    fixed = {i for i in ids if next(d for d in config['districts'] if d['id'] == i).get('uncontested')}
    total = math.sqrt(sn * sn + sd * sd)
    win = {}
    for i in ids:
        if i in fixed:
            win[i] = 1.0 if shares[i] > 0.5 else 0.0
        elif total == 0:
            win[i] = 1.0 if shares[i] > 0.5 else 0.0
        else:
            win[i] = normal_cdf((shares[i] - 0.5) / total)
    turnout = _turnout(config, shares, params)
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'expected_dem_share': shares,
              'dem_win_probability': win, 'expected_dem_seats': sum(win.values()), 'majority_seats': majority,
              'expected_turnout': turnout, 'expected_total_turnout': sum(turnout.values()),
              'parameters_used': {'sigma_national': sn, 'sigma_district': sd, 'coefficients': params['coefficients']}}
    if mode == 'deterministic':
        order = integer(config.get('quadrature_nodes', 32), 'quadrature_nodes', 2, 512)
        nodes, weights = normal_quadrature(order) if sn > 0 else ([0.0], [1.0])
        distribution = [0.0] * (len(ids) + 1)
        approximate = len(ids) > 150
        for z, w in zip(nodes, weights):
            probs = [win[i] if i in fixed else (normal_cdf((shares[i] + sn * z - 0.5) / sd) if sd > 0 else float(shares[i] + sn * z > 0.5))
                     for i in ids]
            if approximate:
                mean = sum(probs)
                sdev = math.sqrt(sum(p * (1 - p) for p in probs))
                for k in range(len(ids) + 1):
                    distribution[k] += w * ((normal_cdf((k + 0.5 - mean) / sdev) - normal_cdf((k - 0.5 - mean) / sdev)) if sdev > 0 else float(round(mean) == k))
            else:
                for k, mass in enumerate(poisson_binomial(probs)):
                    distribution[k] += w * mass
        norm = sum(distribution)
        distribution = [v / norm for v in distribution]
        report.update(dem_seat_distribution=distribution, dem_majority_probability=sum(distribution[majority:]),
                      approximation=['Quantile quadrature over national swing with %d nodes.' % order] +
                      (['Conditional seat counts use a normal approximation for more than 150 districts.'] if approximate else []))
    elif mode == 'stochastic':
        rng = rng_from(seed)
        runs = integer(config.get('n_simulations', 2000), 'n_simulations', 1, 1_000_000)
        if runs * len(ids) > 20_000_000:
            raise ValueError('Election simulation work budget exceeded')
        df = params.get('student_t_df')

        def draw(scale):
            if scale == 0:
                return 0.0
            if df is None:
                return rng.gauss(0, scale)
            df_value = finite(df, 'student_t_df', 2.0001)
            chi = rng.gammavariate(df_value / 2, 2)
            return rng.gauss(0, 1) / math.sqrt(chi / df_value) * scale * math.sqrt((df_value - 2) / df_value)

        seats, wins, turnouts = [], {i: 0 for i in ids}, []
        for _ in range(runs):
            swing = draw(sn)
            count = 0
            for i in ids:
                share = shares[i] if i in fixed else min(1.0, max(0.0, shares[i] + swing + draw(sd)))
                if share > 0.5:
                    count += 1
                    wins[i] += 1
            seats.append(count)
            if turnout:
                turnouts.append(sum(turnout.values()))
        report.update(simulated_dem_seats=quantiles(seats), simulated_dem_majority_frequency=sum(s >= majority for s in seats) / runs,
                      simulated_dem_win_frequency={i: wins[i] / runs for i in ids}, n_simulations=runs,
                      tails='normal' if df is None else f'student_t_{df}')
    else:
        raise ValueError('mode must be deterministic or stochastic')
    return report


def synthetic(seed=0, cycles=10, districts=120):
    rng = rng_from(seed)
    truth = {'coefficients': {'const': 0.49, 'pvi': 0.9, 'incumbent': 0.035, 'econ_x_president': 0.006,
                              'midterm_x_president': -0.025, 'fundraising': 0.012},
             'sigma_national': 0.03, 'sigma_district': 0.035}
    leans = [rng.gauss(0, 0.12) for _ in range(districts)]
    races = []
    for c in range(cycles):
        year = 2004 + 2 * c
        national = {'econ': rng.gauss(2, 1.5), 'president_party': 1 if (c // 4) % 2 else -1, 'midterm': c % 2}
        swing = rng.gauss(0, truth['sigma_national'])
        for d, lean in enumerate(leans):
            incumbent = rng.choice([-1, 0, 1])
            dem, rep = math.exp(rng.gauss(13 + 2 * lean, 1)), math.exp(rng.gauss(13 - 2 * lean, 1))
            row = {'district': f'd{d:03d}', 'cycle': year, 'date': f'{year}-11-05', 'pvi': lean, 'incumbent': incumbent,
                   'dem_receipts': dem, 'rep_receipts': rep, **national}
            x = _share_row(row)
            share = sum(truth['coefficients'][k] * v for k, v in zip(SHARE_TERMS, x)) + swing + rng.gauss(0, truth['sigma_district'])
            row['dem_share'] = min(0.99, max(0.01, share))
            races.append(row)
    config = {'national': {'econ': 1.5, 'president_party': 1, 'midterm': 1},
              'districts': [{'id': f'd{d:03d}', 'pvi': lean, 'incumbent': 0, 'dem_receipts': 1e6, 'rep_receipts': 1e6,
                             'cvap': 550000, 'lag_turnout_rate': 0.45} for d, lean in enumerate(leans[:40])],
              'parameters': truth, 'n_simulations': 2000}
    return {'data': {'races': races}, 'truth': truth, 'config': config}


# ----------------------------------------------------------------------------- holdout forecaster

INCUMBENT_PARTY_HOLDS = 'incumbent_party_holds'


def _mean_sd(values):
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, 0.0
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _seat_holders(history):
    """District -> party that won its most recent pre-origin election ('D' or 'R')."""
    latest = {}
    for race in history:
        district, date = race['district'], str(race['date'])
        if race.get('uncontested'):
            won = race['uncontested'] == 'D'
        elif race.get('dem_share') is not None:
            won = race['dem_share'] > 0.5
        else:
            continue
        if district not in latest or date >= latest[district][0]:
            latest[district] = (date, 'D' if won else 'R')
    return {district: party for district, (_, party) in latest.items()}


def _incumbent_party_holds(history):
    """Naive rule: each seat stays with its party, at that party's average holding share.

    For every contested pre-origin race the share is grouped by the party that held the
    seat going in (the winner of that district's previous election), so the rule predicts
    two shares — one for Democratic-held seats and one for Republican-held ones — and a
    seat count equal to the current split. It is the seat-level naive rule that political
    reporting uses, and it is a much stronger reference than the district mean where
    incumbency is worth several points.
    """
    ordered = {}
    for race in sorted(history, key=lambda r: (str(r['district']), str(r['date']))):
        ordered.setdefault(race['district'], []).append(race)
    groups = {'D': [], 'R': []}
    for races in ordered.values():
        for previous, race in zip(races, races[1:]):
            if race.get('uncontested') or race.get('dem_share') is None:
                continue
            if previous.get('uncontested'):
                holder = 'D' if previous['uncontested'] == 'D' else 'R'
            elif previous.get('dem_share') is not None:
                holder = 'D' if previous['dem_share'] > 0.5 else 'R'
            else:
                continue
            groups[holder].append(race['dem_share'])
    return {party: _mean_sd(values) for party, values in groups.items() if values}


def _holdout_forecast(parameters, history, rows, data, nodes=32):
    """Next-cycle district two-party shares from realized fundamentals, plus the Democratic seat count.

    Share: mean x.beta with variance sigma_national^2 + sigma_district^2 + x'Vx, where V is
    the coefficient covariance implied by cycle random effects on the pre-origin design
    (cycle-level coefficients are estimated from few cycles). Seats (group ``dem_seats``):
    mean and variance of the seat count integrating the shared swing (inflated by the
    common coefficient uncertainty x_bar'V x_bar) with quantile quadrature, districts
    independent given the swing. Without an identified sigma_national no forecast is made.

    Two reference forecasts are supplied alongside the naive ones: the district's own
    previous share (``persistence``) and its pre-origin mean (``historical_mean``) come
    from the history values, and ``incumbent_party_holds`` is the seat-level rule that
    every seat stays with the party holding it.
    """
    coefficients = parameters['coefficients']
    if parameters.get('sigma_national') is None or parameters.get('sigma_district') is None:
        raise ValueError('Election holdout forecasts require identified national and district variance components')
    sd_district, sd_national = float(parameters['sigma_district']), float(parameters['sigma_national'])
    if sd_district <= 0:
        raise ValueError('Election holdout forecasts require positive district variance')
    k = len(SHARE_TERMS)
    gram, meat, sums = [[0.0] * k for _ in range(k)], [[0.0] * k for _ in range(k)], {}
    for race in history:
        if race.get('uncontested') or race.get('dem_share') is None or not 0 < race['dem_share'] < 1:
            continue
        x = _share_row(race)
        total_x = sums.setdefault(race['cycle'], [0.0] * k)
        for a in range(k):
            total_x[a] += x[a]
            for b in range(k):
                gram[a][b] += x[a] * x[b]
    for total_x in sums.values():
        for a in range(k):
            for b in range(k):
                meat[a][b] += sd_national ** 2 * total_x[a] * total_x[b]
    meat = [[meat[a][b] + sd_district ** 2 * gram[a][b] for b in range(k)] for a in range(k)]

    def coefficient_variance(x):
        u = solve(gram, x)
        return sum(a * b for a, b in zip(u, [sum(meat[r][c] * u[c] for c in range(k)) for r in range(k)]))

    past, seats_by_cycle = {}, {}
    for race in history:
        if race.get('uncontested') or race.get('dem_share') is None:
            if race.get('uncontested'):
                seats_by_cycle[race['date']] = seats_by_cycle.get(race['date'], 0) + (race['uncontested'] == 'D')
            continue
        past.setdefault(race['district'], []).append(race['dem_share'])
        seats_by_cycle[race['date']] = seats_by_cycle.get(race['date'], 0) + (race['dem_share'] > 0.5)
    holders, holding = _seat_holders(history), _incumbent_party_holds(history)
    out, means, designs, fixed_seats, actual_seats = [], [], [], 0, 0
    naive_seats, naive_seat_history = 0, [seats_by_cycle[c] for c in sorted(seats_by_cycle, key=str)]
    for race in sorted(rows, key=lambda r: str(r['district'])):
        if race.get('uncontested'):
            fixed_seats += race['uncontested'] == 'D'
            actual_seats += race['uncontested'] == 'D'
            naive_seats += race['uncontested'] == 'D'
            continue
        if race.get('dem_share') is None:
            continue
        x = _share_row(race)
        mean = sum(coefficients[name] * value for name, value in zip(SHARE_TERMS, x))
        means.append(mean)
        designs.append(x)
        actual_seats += race['dem_share'] > 0.5
        sd = math.sqrt(sd_district ** 2 + sd_national ** 2 + coefficient_variance(x))
        holder = holders.get(race['district'])
        naive_seats += holder == 'D'
        supplied = {}
        if holder in holding:
            hold_mean, hold_sd = holding[holder]
            supplied[INCUMBENT_PARTY_HOLDS] = {'mean': hold_mean, 'sd': hold_sd}
        out.append({'target': f'dem_share:{race["district"]}', 'actual': race['dem_share'], 'mean': mean, 'sd': sd,
                    'history_values': past.get(race['district'], []), 'baselines': supplied})
    if means:
        center = [sum(x[a] for x in designs) / len(designs) for a in range(k)]
        sd_national = math.sqrt(sd_national ** 2 + coefficient_variance(center))
        grid = normal_quadrature(nodes)[0] if sd_national > 0 else [0.0]
        counts, variances = [], []
        for z in grid:
            probs = [normal_cdf((m + sd_national * z - 0.5) / sd_district) if sd_district > 0 else float(m + sd_national * z > 0.5)
                     for m in means]
            counts.append(sum(probs))
            variances.append(sum(p * (1 - p) for p in probs))
        expected = sum(counts) / len(counts)
        variance = sum(variances) / len(variances) + sum((c - expected) ** 2 for c in counts) / len(counts)
        changes = [b - a for a, b in zip(naive_seat_history, naive_seat_history[1:])]
        out.append({'target': 'dem_seats', 'group': 'dem_seats', 'actual': actual_seats, 'mean': fixed_seats + expected,
                    'sd': math.sqrt(variance), 'history_values': list(naive_seat_history),
                    'baselines': {INCUMBENT_PARTY_HOLDS: {
                        'mean': float(naive_seats),
                        'sd': math.sqrt(math.fsum(c * c for c in changes) / len(changes)) if changes else 0.0}}})
    return out


holdout_forecaster = {
    'rows_key': 'races', 'time_key': 'date', 'target': 'dem_share', 'forecast': _holdout_forecast,
    'fit_keys': ['races', 'turnout', 'information_time', 'revisions'],
    'conditional_inputs': ['pvi', 'incumbent', 'econ', 'president_party', 'midterm', 'dem_receipts', 'rep_receipts'],
    'baselines': [INCUMBENT_PARTY_HOLDS],
    'description': 'Next-cycle contested district two-party share given realized fundamentals (secondary group dem_seats: seat count '
                   'from the swing-integrated seat distribution). Reference forecasts: the district\'s previous share, its '
                   'pre-origin mean, and the incumbent-party-holds seat rule.',
}
