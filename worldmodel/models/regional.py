"""Macro-regional employment and population dynamics with migration and shift-share shocks.

    shock[r,t]     = sum_k share[r,k,base] * g[k,t]                   (Bartik; leave-one-out in estimation)
    E[r,k,t+1]     = E[r,k,t] * exp(b * g[k,t])
    out_rate[r,t]  = base_out_rate[r] * exp(delta * (er[r,t] - mean er[t])),  er = E / P
    share[o,d,t]   proportional to F0[o,d] * exp(psi * (dlnE[d,t] - mean dlnE[t]))
    P[r,t+1]       = P[r,t] + births - deaths + inflow - outflow + external

Internal migration sums to zero across regions (checked every period; exact in
integer stochastic mode). Estimation: shift-share regression with year effects and
region clusters; PPML migration gravity with origin-year effects; OLS out-migration
response. Shift-share identification requires exogenous shares (Goldsmith-Pinkham,
Sorkin and Swift 2020) or shocks (Borusyak, Hull and Jaravel 2022); default label is correlational.
"""
import math
from .base import (apply_cutoff, binomial, demean, finite, fit_result, integer, merged_parameters, multinomial, ols, parameter, poisson,
                   poisson_fe, quantiles, requirement, rng_from, validate_family)

FAMILY = validate_family({
    'id': 'regional',
    'title': 'County/state employment, population and migration dynamics',
    'description': 'Shift-share labor demand shocks, migration responses to employment, and conserved population accounting.',
    'identification': 'correlational_unless_shift_share_design_defended',
    'validated': False,
    'parameters': {
        'shift_share_elasticity': parameter(1.0, 'log_employment_per_log_national_growth', 'Local employment response to Bartik shock.', bounds=[-5, 5]),
        'destination_employment_elasticity': parameter(2.0, 'log_flow_per_log_employment_growth', 'Migration destination choice response.', bounds=[-50, 50]),
        'outmigration_employment_rate_elasticity': parameter(-1.0, 'log_rate_per_employment_rate', 'Out-migration response to local employment rate.', bounds=[-50, 50]),
        'distance_elasticity': parameter(-1.0, 'dimensionless', 'Migration gravity distance coefficient.'),
        'birth_rate': parameter(0.011, 'per_person_per_period', 'Default crude birth rate.', source='external'),
        'death_rate': parameter(0.009, 'per_person_per_period', 'Default crude death rate.', source='external'),
    },
    'requirements': [
        requirement('bls_qcew', 'U.S. Bureau of Labor Statistics', 'Quarterly Census of Employment and Wages (county x NAICS)',
                    ['area_fips', 'industry_code', 'own_code', 'year', 'annual_avg_emplvl'], 'https://www.bls.gov/cew/downloadable-data-files.htm',
                    frequency='quarterly/annual', parameters=['shift_share_elasticity']),
        requirement('census_cbp', 'U.S. Census Bureau', 'County Business Patterns', ['fipstate', 'fipscty', 'naics', 'emp'],
                    'https://www.census.gov/programs-surveys/cbp.html', frequency='annual', role='industry_shares', parameters=['shift_share_elasticity']),
        requirement('irs_soi_migration', 'Internal Revenue Service Statistics of Income', 'County-to-county migration data (returns/exemptions)',
                    ['y1_statefips', 'y1_countyfips', 'y2_statefips', 'y2_countyfips', 'n1', 'n2', 'agi'],
                    'https://www.irs.gov/statistics/soi-tax-stats-migration-data', frequency='annual',
                    parameters=['destination_employment_elasticity', 'outmigration_employment_rate_elasticity', 'distance_elasticity']),
        requirement('census_pep', 'U.S. Census Bureau', 'Population Estimates Program county components of change',
                    ['STATE', 'COUNTY', 'POPESTIMATE', 'BIRTHS', 'DEATHS', 'NETMIG', 'INTERNATIONALMIG'],
                    'https://www.census.gov/programs-surveys/popest.html', frequency='annual', parameters=['birth_rate', 'death_rate', 'outmigration_employment_rate_elasticity']),
        requirement('bls_laus', 'U.S. Bureau of Labor Statistics', 'Local Area Unemployment Statistics', ['area_code', 'year', 'labor_force', 'employment', 'unemployment'],
                    'https://www.bls.gov/lau/', frequency='monthly', role='validation'),
        requirement('census_gazetteer_distances', 'U.S. Census Bureau / NBER', 'County centroids or county distance database', ['county1', 'county2', 'mi_to_county'],
                    'https://www.nber.org/research/data/county-distance-database', frequency='decennial', parameters=['distance_elasticity']),
    ],
    'limitations': [
        'IRS SOI migration covers tax filers only and lags one filing year.',
        'Industry reallocation within regions and commuting zones are not modeled; QCEW suppression must be imputed explicitly upstream.',
        'Migration responds to employment only; housing costs, amenities and age structure are omitted.',
    ],
})


def _employment_panel(rows):
    panel = {}
    for r in rows:
        key = (str(r['region']), str(r['industry']), int(r['year']))
        if key in panel:
            raise ValueError(f'Duplicate employment row {key}')
        panel[key] = finite(r['employment'], 'employment', 0)
    return panel


def shift_share_shocks(employment_rows, shock_industries=None):
    panel = _employment_panel(employment_rows)
    regions = sorted({k[0] for k in panel})
    industries = sorted({k[1] for k in panel})
    years = sorted({k[2] for k in panel})
    base = years[0]
    totals = {(k, y): sum(panel.get((r, k, y), 0.0) for r in regions) for k in industries for y in years}
    region_totals = {(r, y): sum(panel.get((r, k, y), 0.0) for k in industries) for r in regions for y in years}
    rows = []
    for y0, y1 in zip(years, years[1:]):
        for r in regions:
            if region_totals[(r, base)] <= 0 or region_totals[(r, y0)] <= 0 or region_totals[(r, y1)] <= 0:
                continue
            shock = 0.0
            for k in (industries if shock_industries is None else [k for k in industries if k in set(shock_industries)]):
                share = panel.get((r, k, base), 0.0) / region_totals[(r, base)]
                before = totals[(k, y0)] - panel.get((r, k, y0), 0.0)
                after = totals[(k, y1)] - panel.get((r, k, y1), 0.0)
                if share and before > 0 and after > 0:
                    shock += share * math.log(after / before)
            rows.append({'region': r, 'year': y1, 'shock': shock, 'dlnE': math.log(region_totals[(r, y1)] / region_totals[(r, y0)])})
    return rows, {'base_year': base, 'regions': len(regions), 'industries': len(industries), 'years': years}


def fit(data, cutoff=None):
    estimate, diagnostics, windows = {}, {}, []
    growth = {}
    if data.get('employment'):
        rows, window = apply_cutoff(data['employment'], cutoff, label='employment')
        windows.append(window)
        shocks, meta = shift_share_shocks(rows, data.get('shock_industries'))
        growth = {(s['region'], s['year']): s['dlnE'] for s in shocks}
        years = [s['year'] for s in shocks]
        demeaned, _ = demean([[s['dlnE'] for s in shocks], [s['shock'] for s in shocks]], [years])
        model = ols([[v] for v in demeaned[1]], demeaned[0], names=['shock'], clusters=[s['region'] for s in shocks])
        estimate['shift_share_elasticity'] = model['coefficients']['shock']
        diagnostics['shift_share'] = {'standard_error': model['standard_errors']['shock'], 'n': model['n'], 'se_type': model['se_type'],
                                      'fixed_effects': ['year'], 'shares_base_year': meta['base_year'], 'leave_one_out': True}
    if data.get('migration'):
        rows, window = apply_cutoff(data['migration'], cutoff, label='migration')
        windows.append(window)
        distances = {(d['origin'], d['destination']): finite(d['km'], 'km', 1e-9) for d in data.get('distances', [])}
        usable = [r for r in rows if r['origin'] != r['destination'] and (r['destination'], int(r['year'])) in growth]
        if len(usable) < 20:
            raise ValueError('Migration gravity requires at least 20 flows with destination employment growth')
        names = ['destination_dlnE'] + (['ln_distance'] if distances else [])
        X = [[growth[(r['destination'], int(r['year']))]] + ([math.log(distances[(r['origin'], r['destination'])])] if distances else []) for r in usable]
        model = poisson_fe([finite(r['flow'], 'flow', 0) for r in usable], X,
                           [[f"{r['origin']}|{r['year']}" for r in usable], [str(r['destination']) for r in usable]], names=names,
                           clusters=[r['origin'] for r in usable])
        estimate['destination_employment_elasticity'] = model['coefficients']['destination_dlnE']
        if distances:
            estimate['distance_elasticity'] = model['coefficients']['ln_distance']
        diagnostics['migration_gravity'] = {'standard_errors': model['standard_errors'], 'converged': model['converged'], 'n': model['n'],
                                            'fixed_effects': ['origin_year', 'destination']}
        if data.get('population'):
            prow, pwindow = apply_cutoff(data['population'], cutoff, label='population')
            windows.append(pwindow)
            population = {(str(r['region']), int(r['year'])): finite(r['population'], 'population', 1) for r in prow}
            employment_totals = {}
            for r in apply_cutoff(data['employment'], cutoff, label='employment')[0]:
                key = (str(r['region']), int(r['year']))
                employment_totals[key] = employment_totals.get(key, 0.0) + r['employment']
            outflow = {}
            for r in rows:
                if r['origin'] != r['destination']:
                    key = (str(r['origin']), int(r['year']))
                    outflow[key] = outflow.get(key, 0.0) + r['flow']
            obs = []
            for (region, year), flow in outflow.items():
                lag = (region, year - 1)
                if lag in population and lag in employment_totals and flow > 0:
                    obs.append((year, region, math.log(flow / population[lag]), employment_totals[lag] / population[lag]))
            if len(obs) >= 10:
                demeaned, _ = demean([[o[2] for o in obs], [o[3] for o in obs]], [[o[0] for o in obs]])
                out = ols([[v] for v in demeaned[1]], demeaned[0], names=['er'], clusters=[o[1] for o in obs])
                estimate['outmigration_employment_rate_elasticity'] = out['coefficients']['er']
                diagnostics['outmigration'] = {'standard_error': out['standard_errors']['er'], 'n': out['n'], 'fixed_effects': ['year']}
    if not estimate:
        raise ValueError('Supply employment and/or migration data')
    identification = data.get('design', FAMILY['identification'])
    return fit_result(estimate, diagnostics, method='shift_share_ols_plus_ppml_migration_gravity', identification=identification, windows=windows,
                      data=data, family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    regions = config['regions']
    ids = [r['id'] for r in regions]
    if len(ids) != len(set(ids)) or len(ids) < 2:
        raise ValueError('At least two uniquely identified regions required')
    periods = integer(config.get('periods', 5), 'periods', 1, 1000)
    stochastic = mode == 'stochastic'
    if mode not in ('deterministic', 'stochastic'):
        raise ValueError('mode must be deterministic or stochastic')
    rng = rng_from(seed)
    base_flows = config['baseline_migration']
    population = {r['id']: (integer(r['population'], 'population', 0, 10**10) if stochastic else finite(r['population'], 'population', 0)) for r in regions}
    industry = {r['id']: {k: finite(v, 'industry employment', 0) for k, v in r['industry_employment'].items()} for r in regions}
    base_out = {}
    for o in ids:
        total = sum(finite(v, 'baseline flow', 0) for d, v in base_flows.get(o, {}).items() if d != o)
        base_out[o] = total / population[o] if population[o] > 0 else 0.0
    b, psi, delta = params['shift_share_elasticity'], params['destination_employment_elasticity'], params['outmigration_employment_rate_elasticity']
    history = []
    for t in range(periods):
        growth = (config.get('national_industry_growth') or [{}])[min(t, len(config.get('national_industry_growth') or [{}]) - 1)]
        employment_before = {r: sum(industry[r].values()) for r in ids}
        shocks = {}
        for r in ids:
            total = employment_before[r]
            shocks[r] = sum((v / total) * finite(growth.get(k, 0.0), 'industry growth') for k, v in industry[r].items()) if total > 0 else 0.0
            industry[r] = {k: v * math.exp(b * finite(growth.get(k, 0.0), 'industry growth')) for k, v in industry[r].items()}
        employment = {r: sum(industry[r].values()) for r in ids}
        dlnE = {r: math.log(employment[r] / employment_before[r]) if employment_before[r] > 0 and employment[r] > 0 else 0.0 for r in ids}
        mean_d = sum(dlnE.values()) / len(ids)
        er = {r: employment[r] / population[r] if population[r] > 0 else 0.0 for r in ids}
        mean_er = sum(er.values()) / len(ids)
        flows = {o: {} for o in ids}
        births, deaths = {}, {}
        for o in ids:
            rate = min(1.0, base_out[o] * math.exp(delta * (er[o] - mean_er)))
            weights = {d: finite(base_flows.get(o, {}).get(d, 0.0), 'baseline flow', 0) * math.exp(psi * (dlnE[d] - mean_d)) for d in ids if d != o}
            mass = sum(weights.values())
            region = next(r for r in regions if r['id'] == o)
            birth_rate, death_rate = region.get('birth_rate', params['birth_rate']), region.get('death_rate', params['death_rate'])
            if stochastic:
                movers = binomial(rng, population[o], rate) if mass > 0 else 0
                destinations = sorted(weights)
                draws = multinomial(rng, movers, [weights[d] / mass for d in destinations]) if movers else [0] * len(destinations)
                flows[o] = dict(zip(destinations, draws))
                births[o] = poisson(rng, population[o] * birth_rate)
                deaths[o] = min(population[o] - movers, poisson(rng, population[o] * death_rate))
            else:
                movers = population[o] * rate if mass > 0 else 0.0
                flows[o] = {d: movers * w / mass for d, w in weights.items()} if mass > 0 else {}
                births[o] = population[o] * birth_rate
                deaths[o] = population[o] * death_rate
        external = config.get('external_net_migration', {})
        before_total = sum(population.values())
        inflow = {d: sum(flows[o].get(d, 0) for o in ids) for d in ids}
        outflow = {o: sum(flows[o].values()) for o in ids}
        new_population = {}
        for r in ids:
            ext = external.get(r, 0)
            if stochastic:
                ext = integer(ext, 'external migration', -10**10, 10**10)
            new_population[r] = population[r] + births[r] - deaths[r] + inflow[r] - outflow[r] + ext
            if new_population[r] < 0:
                raise ValueError(f'Negative population in {r}; external migration exceeds residents')
        expected_total = before_total + sum(births.values()) - sum(deaths.values()) + sum(external.get(r, 0) for r in ids)
        residual = sum(new_population.values()) - expected_total
        internal = sum(inflow.values()) - sum(outflow.values())
        if (stochastic and (residual != 0 or internal != 0)) or abs(residual) > 1e-9 * max(1.0, expected_total) or abs(internal) > 1e-9 * max(1.0, before_total):
            raise ValueError('Population accounting violated')
        population = new_population
        history.append({'period': t, 'population': dict(population), 'employment': employment, 'shift_share_shock': shocks,
                        'net_internal_migration': {r: inflow[r] - outflow[r] for r in ids}, 'births': births, 'deaths': deaths,
                        'accounting': {'population_residual': residual, 'internal_migration_sum': internal}})
    return {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'periods': history, 'final_flows': flows,
            'units': {'population': 'people', 'employment': 'jobs', 'flows': 'people_per_period'}}


def synthetic(seed=0, regions=30, years=8):
    """Tradable industries follow national growth; a local sector multiplies the tradable shock.

    With a constant local share s_L and multiplier m, total employment growth is
    shock * (1 + m s_L), so the true shift-share elasticity is 1 + m s_L.
    """
    rng = rng_from(seed)
    names = [f'R{i:02d}' for i in range(regions)]
    tradables = ['manufacturing', 'energy', 'agriculture', 'finance']
    local_share, multiplier = 0.4, 1.5
    truth = {'shift_share_elasticity': 1 + multiplier * local_share, 'destination_employment_elasticity': 3.0, 'distance_elasticity': -1.1}
    position = {r: (rng.uniform(0, 1000), rng.uniform(0, 1000)) for r in names}
    employment = {}
    for r in names:
        weights = [rng.uniform(0.05, 1) ** 2 for _ in tradables]
        size = rng.uniform(5e4, 2e5)
        employment[r] = {k: size * (1 - local_share) * w / sum(weights) for k, w in zip(tradables, weights)}
        employment[r]['local_services'] = size * local_share
    emp_rows, mig_rows, distances = [], [], []
    for r in names:
        for d in names:
            if r != d:
                distances.append({'origin': r, 'destination': d, 'km': math.dist(position[r], position[d]) + 10})
    for y in range(years):
        year = 2010 + y
        for r in names:
            for k, v in employment[r].items():
                emp_rows.append({'region': r, 'industry': k, 'year': year, 'date': f'{year}-12-31', 'employment': v})
        if y == years - 1:
            break
        g = {k: rng.gauss(0, 0.06) for k in tradables}
        before = {r: sum(employment[r].values()) for r in names}
        for r in names:
            total = before[r]
            shock = sum(employment[r][k] / total * g[k] for k in tradables)
            for k in tradables:
                employment[r][k] *= math.exp(g[k] + rng.gauss(0, 0.002))
            employment[r]['local_services'] *= math.exp(multiplier * shock + rng.gauss(0, 0.002))
        after = {r: sum(employment[r].values()) for r in names}
        dlnE = {r: math.log(after[r] / before[r]) for r in names}
        for o in names:
            for d in names:
                if o != d:
                    mean = math.exp(10 + truth['destination_employment_elasticity'] * dlnE[d] + truth['distance_elasticity'] * math.log(math.dist(position[o], position[d]) + 10))
                    mig_rows.append({'origin': o, 'destination': d, 'year': year + 1, 'date': f'{year + 1}-12-31', 'flow': poisson(rng, mean)})
    config = {'periods': 3, 'regions': [{'id': r, 'population': 200000, 'industry_employment': {k: round(v) for k, v in employment[r].items()}} for r in names[:5]],
              'baseline_migration': {o: {d: 1500.0 for d in names[:5] if d != o} for o in names[:5]},
              'national_industry_growth': [{'manufacturing': -0.08, 'finance': 0.03, 'energy': 0.1, 'agriculture': 0.0, 'local_services': 0.0}],
              'parameters': {'shift_share_elasticity': 1.0, 'destination_employment_elasticity': 3.0, 'outmigration_employment_rate_elasticity': -1.0}}
    return {'data': {'employment': emp_rows, 'migration': mig_rows, 'distances': distances, 'shock_industries': tradables}, 'truth': truth, 'config': config}


# ----------------------------------------------------------------------------- holdout forecaster

YEAR_EFFECT_ONLY = 'year_effect_only'


def _variance(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


INTERVAL_METHODS = ('pooled_year_draw', 'per_unit_year_mean', 'per_unit_year_draw')
#: Methods whose common term is the sampling variance of the estimated year level rather than
#: the variance of next year's own draw.
_YEAR_LEVEL_METHODS = ('per_unit_year_mean',)
#: Methods that give each region its own within-year residual scale.
_PER_UNIT_METHODS = ('per_unit_year_mean', 'per_unit_year_draw')


def _holdout_forecast(parameters, history, rows, data, options=None):
    """Next-year regional employment growth from the leave-one-out shift-share shock.

    Shares are fixed at the first pre-origin year (as in the fit); national industry
    growth excludes the target region and uses realized other-region employment at the
    target year (declared conditional input). The year effect is forecast by its
    pre-origin mean. ``year_effect_only`` drops the shift-share term. Population growth
    is not scored: the fit contract carries no population components.

    ``options['interval_method']`` declares the predictive spread:

    ``pooled_year_draw`` (default)
        ``sqrt(pooled within-year residual variance + between-year variance x (1+1/Y))``:
        one scale for every region, treating next year's effect as a fresh draw from
        the distribution of the Y observed year effects.
    ``per_unit_year_mean``
        A per-region residual variance (each region's own residual mean square shrunk
        toward the pooled one by a single prior observation, because a short panel gives
        each region only Y residuals) plus ``between-year variance / Y``, the sampling
        variance of the estimated year level. Declared where regions differ in scale by
        an order of magnitude, which makes one pooled scale simultaneously far too wide
        for stable regions and too narrow for volatile ones.
    ``per_unit_year_draw``
        The same per-region within-year variance with the *fresh-draw* common term
        ``between x (1 + 1/Y)``. The forecast error decomposes into next year's own
        common shock and the sampling error of the mean of the Y observed year effects,
        so its variance is ``between + between/Y``; ``between/Y`` alone is the predictive
        variance of a year effect treated as a fixed level being estimated, which
        contradicts the existence of a between-year variance in the first place. This is
        the correct pairing of the two corrections and is a statement about the
        decomposition, not about any realized coverage.
    """
    method = (options or {}).get('interval_method', 'pooled_year_draw')
    if method not in INTERVAL_METHODS:
        raise ValueError(f'Unknown regional interval method {method!r}; declared: {list(INTERVAL_METHODS)}')
    b = finite(parameters['shift_share_elasticity'], 'shift_share_elasticity')
    shocks, meta = shift_share_shocks(history, data.get('shock_industries'))
    if not shocks:
        raise ValueError('Regional holdout forecasts need two pre-origin years of employment')
    base, previous = meta['base_year'], meta['years'][-1]
    before, after = _employment_panel(history), _employment_panel(rows)
    target_years = {k[2] for k in after}
    if len(target_years) != 1:
        raise ValueError('Regional holdout rows must share one target year')
    year = target_years.pop()
    by_year = {}
    for s in shocks:
        by_year.setdefault(s['year'], []).append(s)
    effects = {name: {} for name in ('model', 'null')}
    residuals = {name: [] for name in effects}
    by_region = {name: {} for name in effects}
    for y, members in by_year.items():
        effects['model'][y] = sum(s['dlnE'] - b * s['shock'] for s in members) / len(members)
        effects['null'][y] = sum(s['dlnE'] for s in members) / len(members)
        for s in members:
            for name, value in (('model', s['dlnE'] - b * s['shock'] - effects['model'][y]),
                                ('null', s['dlnE'] - effects['null'][y])):
                residuals[name].append(value)
                by_region[name].setdefault(s['region'], []).append(value)
    scale, region_scale = {}, {name: {} for name in effects}
    for name in effects:
        values = list(effects[name].values())
        dof = max(len(residuals[name]) - len(values), 1)
        within = sum(e * e for e in residuals[name]) / dof
        between = _variance(values)
        scale[name] = (sum(values) / len(values), math.sqrt(within + between * (1 + 1 / len(values))))
        if method in _PER_UNIT_METHODS:
            pooled = sum(e * e for e in residuals[name]) / max(len(residuals[name]), 1)
            common = between / len(values) if method in _YEAR_LEVEL_METHODS else between * (1 + 1 / len(values))
            for region, errors in by_region[name].items():
                shrunk = (sum(e * e for e in errors) + pooled) / (len(errors) + 1)
                region_scale[name][region] = math.sqrt(shrunk + common)
    selected = data.get('shock_industries')
    industries = sorted({k[1] for k in before} | {k[1] for k in after})
    if selected is not None:
        industries = [k for k in industries if k in set(selected)]
    regions = sorted({k[0] for k in after})
    past = {}
    for s in sorted(shocks, key=lambda s: s['year']):
        past.setdefault(s['region'], []).append(s['dlnE'])
    out = []
    for r in regions:
        base_total = sum(v for (region, _, y), v in before.items() if region == r and y == base)
        previous_total = sum(v for (region, _, y), v in before.items() if region == r and y == previous)
        target_total = sum(v for (region, _, y), v in after.items() if region == r)
        if base_total <= 0 or previous_total <= 0 or target_total <= 0:
            continue
        shock = 0.0
        for k in industries:
            share = before.get((r, k, base), 0.0) / base_total
            others_before = sum(before.get((o, k, previous), 0.0) for o in regions if o != r)
            others_after = sum(after.get((o, k, year), 0.0) for o in regions if o != r)
            if share and others_before > 0 and others_after > 0:
                shock += share * math.log(others_after / others_before)
        spread = {name: region_scale[name].get(r, scale[name][1]) if method in _PER_UNIT_METHODS else scale[name][1]
                  for name in ('model', 'null')}
        out.append({'target': f'employment_growth:{r}', 'actual': math.log(target_total / previous_total),
                    'mean': scale['model'][0] + b * shock, 'sd': spread['model'], 'history_values': past.get(r, []),
                    'baselines': {YEAR_EFFECT_ONLY: {'mean': scale['null'][0], 'sd': spread['null']}}})
    return out


holdout_forecaster = {
    'rows_key': 'employment', 'time_key': 'date', 'target': 'employment_growth', 'forecast': _holdout_forecast,
    'fit_keys': ['employment', 'shock_industries', 'design', 'information_time', 'revisions'],
    'conditional_inputs': ['other_region_industry_employment_at_target_year'], 'baselines': [YEAR_EFFECT_ONLY],
    'options': True,
    'description': 'Next-year log employment growth per region from the leave-one-out shift-share shock versus the year-effect-only '
                   'forecast; migration and population growth are not scored.',
}
