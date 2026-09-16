"""Synthetic evidence observations with known true parameters for every estimation component.

Used by tests and the documented offline demo. Records follow the normalization
contract in requirements.json and carry ALFRED-style ``attributes.realtime_start``
(period end plus the declared publication lag), so ``vintage_policy='strict'`` works.
Values are fictional; they establish estimator recovery, not real-world skill.
"""
from datetime import date, timedelta
import math
import random
from .data import period_end

OBSERVED_AT = '2026-09-01T00:00:00Z'


def periods(frequency, count, start):
    out = [start]
    while len(out) < count:
        out.append(period_end(out[-1], frequency))
    return out


def observation_records(estimator, columns, dates, *, realtime=True, prefix='synthetic', extra_lag_days=0):
    records = []
    for requirement in estimator.all_requirements():
        values = columns[requirement.name]
        for index, (day, value) in enumerate(zip(dates, values)):
            end = period_end(day, requirement.frequency)
            attributes = {'source_series': requirement.source_series} if requirement.source_series else {}
            if realtime:
                attributes['realtime_start'] = (end + timedelta(days=requirement.publication_lag_days + extra_lag_days)).isoformat()
            records.append({'kind': 'observation', 'id': f'{prefix}:{requirement.name}:{index}', 'metric': requirement.metric,
                            'unit': requirement.unit, 'subject': requirement.subject or f'{prefix}:{requirement.name}',
                            'value': value, 'valid_from': day.isoformat(), 'valid_to': end.isoformat(), 'observed_at': OBSERVED_AT,
                            'dimensions': {}, 'attributes': attributes, 'epistemic_status': 'observed'})
    return records


def _walk(rng, n, start, step, low=None, high=None):
    out = [start]
    for _ in range(n - 1):
        value = out[-1] + rng.gauss(0, step)
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        out.append(value)
    return out


def _logistic(eta):
    return 1 / (1 + math.exp(-eta))


def component_dataset(component, *, seed=0):
    """Return records, true parameters (value, absolute tolerance), options and suggested cutoffs."""
    from .families import estimator_for
    rng = random.Random(seed)
    options = {}
    if component == 'population_growth_rate':
        n, dates = 70, periods('annual', 70, date(1950, 1, 1))
        pop = [1e6]
        for _ in range(n - 1):
            pop.append(pop[-1] * math.exp(0.01 + rng.gauss(0, 0.001)))
        columns, truth = {'population': pop}, {'growth_rate_per_year': (0.01, 0.001)}
        cut = ('1985-12-31', '2000-12-31', '2018-12-31')
    elif component == 'inventory_balance':
        n, dates = 400, periods('weekly', 400, date(2015, 1, 5))
        prod = [11000 + rng.gauss(0, 150) for _ in range(n)]
        imports = [6000 + rng.gauss(0, 400) for _ in range(n)]
        exports = [3000 + rng.gauss(0, 400) for _ in range(n)]
        refinery = [13950 + rng.gauss(0, 300) for _ in range(n)]
        stocks = [450000.0]
        for t in range(1, n):
            stocks.append(stocks[-1] + 50 + 1.0 * 7 * (prod[t] + imports[t] - exports[t] - refinery[t]) + rng.gauss(0, 300))
        columns = {'crude_stocks': stocks, 'production': prod, 'imports': imports, 'exports': exports, 'refinery_input': refinery}
        truth = {'flow_scale': (1.0, 0.02), 'unmeasured_net_flow': (50 * 1000 / 604800, 0.1)}
        cut = ('2018-12-31', '2020-12-31', '2022-08-31')
    elif component == 'cash_balance':
        n, dates = 100, periods('quarterly', 100, date(2000, 1, 1))
        revenue = [1e9 + rng.gauss(0, 1e8) for _ in range(n)]
        costs = [8e8 + rng.gauss(0, 8e7) for _ in range(n)]
        capex = [1e8 + rng.gauss(0, 3e7) for _ in range(n)]
        cash = [5e9]
        for t in range(1, n):
            cash.append(cash[-1] + 2e7 + 0.9 * (revenue[t] - costs[t] - capex[t]) + rng.gauss(0, 1e7))
        columns = {'cash': cash, 'revenue': revenue, 'operating_costs': costs, 'capital_expenditure': capex}
        truth = {'cash_conversion': (0.9, 0.03), 'unmeasured_net_cash_flow': (2e7 / (31557600 / 4), 0.1)}
        cut = ('2010-12-31', '2016-12-31', '2024-09-30')
    elif component in ('interest_pass_through', 'deposit_rate_pass_through'):
        n, dates = 300, periods('monthly', 300, date(2000, 1, 1))
        spread, passthrough, speed, impact = (3.0, 1.0, 0.3, 0.6) if component == 'interest_pass_through' else (-0.5, 0.4, 0.2, 0.1)
        policy = _walk(rng, n, 2.0, 0.25, low=0.0)
        rate = [spread + passthrough * policy[0]]
        for t in range(1, n):
            rate.append(rate[-1] + impact * (policy[t] - policy[t - 1]) - speed * (rate[-1] - spread - passthrough * policy[t - 1]) + rng.gauss(0, 0.05))
        columns = {('loan_rate' if component == 'interest_pass_through' else 'deposit_rate'): rate, 'policy_rate': policy}
        truth = {'spread': (spread / 100, 0.004), 'pass_through': (passthrough, 0.05), 'adjustment_speed_per_month': (speed, 0.06),
                 'impact_pass_through': (impact, 0.06)}
        cut = ('2010-12-31', '2016-12-31', '2024-10-31')
    elif component == 'default_hazard':
        n, dates = 200, periods('quarterly', 200, date(1975, 1, 1))
        unemployment = _walk(rng, n, 6.0, 0.4, low=3.0, high=11.0)
        policy = _walk(rng, n, 4.0, 0.5, low=0.0, high=12.0)
        rate = [2.0]
        for t in range(1, n):
            eta = -2.0 + 0.5 * math.log(rate[-1] / 100 / (1 - rate[-1] / 100)) + 8 * unemployment[t] / 100 + 5 * policy[t - 1] / 100 + rng.gauss(0, 0.03)
            rate.append(100 * _logistic(eta))
        columns = {'delinquency_rate': rate, 'unemployment_rate': unemployment, 'policy_rate': policy}
        truth = {'hazard_intercept': (-2.0, 0.25), 'persistence': (0.5, 0.06), 'unemployment_sensitivity': (8.0, 1.5), 'rate_sensitivity': (5.0, 1.5)}
        cut = ('1995-12-31', '2010-12-31', '2024-09-30')
    elif component == 'deposit_growth':
        n, dates = 360, periods('monthly', 360, date(1990, 1, 1))
        policy = _walk(rng, n, 4.0, 0.25, low=0.0)
        growth, deposits = [0.004], [1000.0]
        for t in range(1, n):
            g = 0.004 * 0.7 + 0.3 * growth[-1] - 2.0 * (policy[t] - policy[t - 1]) / 100 + rng.gauss(0, 0.002)
            growth.append(g)
            deposits.append(deposits[-1] * math.exp(g))
        columns = {'deposits': deposits, 'policy_rate': policy}
        truth = {'mean_growth_per_month': (0.004, 0.0007), 'persistence': (0.3, 0.08), 'rate_semi_elasticity': (-2.0, 0.3)}
        cut = ('2005-12-31', '2014-12-31', '2019-11-30')
    elif component == 'credit_growth':
        n, dates = 480, periods('monthly', 480, date(1980, 1, 1))
        growth, credit = [0.005, 0.005], [100.0, 100.5]
        for t in range(2, n):
            g = 0.002 + 0.4 * growth[-1] + 0.2 * growth[-2] + rng.gauss(0, 0.003)
            growth.append(g)
            credit.append(credit[-1] * math.exp(g))
        columns = {'consumer_credit': credit}
        truth = {'mean_growth_per_month': (0.005, 0.001), 'persistence_sum': (0.6, 0.08)}
        cut = ('2000-12-31', '2012-12-31', '2019-11-30')
    elif component == 'demand_price_elasticity':
        n, dates = 360, periods('monthly', 360, date(1990, 1, 1))
        crude = [math.exp(v) for v in _walk(rng, n, math.log(40), 0.06)]
        quantity, price = [math.exp(4.0)], []
        for t in range(n):
            demand_shock = rng.gauss(0, 0.02)
            log_price = 0.5 + 0.6 * math.log(crude[t]) + 0.5 * demand_shock + rng.gauss(0, 0.01)
            price.append(math.exp(log_price))
            if t:
                quantity.append(math.exp(2.0 + 0.5 * math.log(quantity[-1]) - 0.1 * log_price + demand_shock))
        columns = {'quantity': quantity, 'retail_price': price, 'crude_price': crude}
        truth = {'elasticity': (0.2, 0.06), 'short_run_elasticity': (-0.1, 0.03), 'persistence': (0.5, 0.08)}
        cut = ('2005-12-31', '2012-12-31', '2019-11-30')
    elif component == 'price_adjustment':
        n, dates = 360, periods('monthly', 360, date(1990, 1, 1))
        inventory = [230000.0]
        for _ in range(n - 1):
            inventory.append(230000 + 0.7 * (inventory[-1] - 230000) + rng.gauss(0, 6000))
        crude = [math.exp(v) for v in _walk(rng, n, math.log(40), 0.05)]
        price = [2.0] * 13
        for t in range(13, n):
            target = sum(inventory[t - 13:t - 1]) / 12
            gap = (target - inventory[t - 1]) / target
            price.append(price[-1] * math.exp(0.001 + 0.5 * gap + 0.4 * math.log(crude[t] / crude[t - 1]) + rng.gauss(0, 0.005)))
        columns = {'retail_price': price, 'inventory': inventory, 'crude_price': crude}
        truth = {'adjustment_per_month': (0.5, 0.12), 'cost_pass_through': (0.4, 0.04), 'drift_per_month': (0.001, 0.002)}
        cut = ('2005-12-31', '2012-12-31', '2019-11-30')
    elif component in ('energy_purchasing', 'labor_demand'):
        n, dates = 360, periods('monthly', 360, date(1990, 1, 1))
        if component == 'energy_purchasing':
            crude = [math.exp(v) for v in _walk(rng, n, math.log(40), 0.08)]
            policy = _walk(rng, n, 4.0, 0.25, low=0.0)
            growth, sales = [0.002, 0.002], [1e5, 1e5 * math.exp(0.002)]
            for t in range(2, n):
                g = 0.002 + 0.2 * growth[-1] - 0.05 * math.log(crude[t] / crude[t - 1]) - 1.5 * (policy[t] - policy[t - 1]) / 100 + rng.gauss(0, 0.004)
                growth.append(g)
                sales.append(sales[-1] * math.exp(g))
            columns = {'real_sales': sales, 'crude_price': crude, 'policy_rate': policy}
            truth = {'energy_response': (0.05, 0.015), 'rate_response': (1.5, 0.4), 'persistence': (0.2, 0.08)}
        else:
            output = [100.0]
            for _ in range(n - 1):
                output.append(output[-1] * math.exp(0.002 + rng.gauss(0, 0.008)))
            growth, employment = [0.001, 0.001], [130000.0, 130130.0]
            for t in range(2, n):
                g = 0.0005 + 0.4 * growth[-1] + 0.3 * math.log(output[t] / output[t - 1]) + rng.gauss(0, 0.0005)
                growth.append(g)
                employment.append(employment[-1] * math.exp(g))
            columns = {'employment': employment, 'output': output}
            truth = {'employment_output_elasticity': (0.5, 0.05), 'impact_elasticity': (0.3, 0.02), 'persistence': (0.4, 0.05)}
        cut = ('2005-12-31', '2012-12-31', '2019-11-30')
    elif component == 'policy_rule':
        n, dates = 240, periods('quarterly', 240, date(1960, 1, 1))
        quarterly_inflation, prices = [0.5], [30.0]
        for _ in range(n - 1):
            quarterly_inflation.append(0.5 + 0.8 * (quarterly_inflation[-1] - 0.5) + rng.gauss(0, 0.25))
            prices.append(prices[-1] * math.exp(quarterly_inflation[-1] / 100))
        potential = [3000 * 1.006 ** t for t in range(n)]
        gap = _walk(rng, n, 0.0, 0.6, low=-8, high=8)
        real = [p * (1 + g / 100) for p, g in zip(potential, gap)]
        policy = [4.0] * 4
        for t in range(4, n):
            inflation = 100 * (prices[t] / prices[t - 4] - 1)
            policy.append(0.8 * policy[-1] + 0.2 * (1.0 + 1.5 * inflation + 0.5 * gap[t]) + rng.gauss(0, 0.1))
        columns = {'policy_rate': policy, 'price_index': prices, 'real_gdp': real, 'potential_gdp': potential}
        truth = {'smoothing': (0.8, 0.04), 'inflation_response': (1.5, 0.3), 'output_response': (0.5, 0.15), 'reference_rate': (0.04, 0.015)}
        cut = ('1990-12-31', '2005-12-31', '2019-09-30')
    elif component == 'field_diffusion_transport':
        n, dates = 200, periods('daily', 200, date(2024, 1, 1))
        cells = {'cell:a': 1e6, 'cell:b': 2e6, 'cell:c': 1.5e6, 'cell:d': 1e6}
        edges = [('cell:a', 'cell:b'), ('cell:b', 'cell:c'), ('cell:c', 'cell:d'), ('cell:a', 'cell:c')]
        options = {'topology': {'cells': [{'id': c, 'measure': m} for c, m in cells.items()],
                                'edges': [{'source': a, 'target': b} for a, b in edges]}}
        estimator = estimator_for(component, options=options)
        coefficients = {'conductance': 1.0, 'transport_rate': 1e-6, 'decay_rate': 2e-7, 'source_rate': 5e-6}
        state = {'cell:a': 200.0, 'cell:b': 20.0, 'cell:c': 80.0, 'cell:d': 5.0}
        columns = {c: [] for c in cells}
        for _ in range(n):
            for c in cells:
                columns[c].append(state[c])
            state = estimator._step(state, coefficients, estimator.options)
            state = {c: max(0.0, v * (1 + rng.gauss(0, 0.05))) for c, v in state.items()}
        truth = {'conductance': (1.0, 0.25), 'transport_rate': (1e-6, 3e-7), 'decay_rate': (2e-7, 3e-7), 'source_rate': (5e-6, 8e-6)}
        cut = ('2024-03-01', '2024-05-01', '2024-07-10')
        return {'records': observation_records(estimator, columns, dates), 'truth': truth, 'options': options,
                'train_end': cut[0], 'validation_end': cut[1], 'cutoff': cut[2]}
    elif component == 'bilateral_flow_gravity':
        regions = [f'r{i}' for i in range(8)]
        origin_effect = {r: rng.gauss(0, 0.5) for r in regions}
        destination_effect = {r: rng.gauss(0, 0.5) for r in regions}
        distance = {(o, d): rng.uniform(100, 3000) for o in regions for d in regions if o != d}
        records = []
        for year in range(2012, 2025):
            year_effect = 0.02 * (year - 2012)
            for (o, d), km in distance.items():
                mean = math.exp(8.0 + origin_effect[o] + destination_effect[d] + year_effect - 1.0 * math.log(km))
                flow = mean * rng.gammavariate(20, 1 / 20)
                released = date(year + 1, 1, 1) + timedelta(days=540)
                records.append({'kind': 'observation', 'id': f'synthetic:flow:{o}:{d}:{year}', 'metric': 'freight_flow', 'unit': 'thousand_short_tons',
                                'subject': f'synthetic:od:{o}:{d}', 'value': flow, 'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01',
                                'observed_at': OBSERVED_AT, 'dimensions': {'origin': o, 'destination': d},
                                'attributes': {'realtime_start': released.isoformat()}})
        for (o, d), km in distance.items():
            records.append({'kind': 'observation', 'id': f'synthetic:distance:{o}:{d}', 'metric': 'od_distance', 'unit': 'km',
                            'subject': f'synthetic:od:{o}:{d}', 'value': km, 'valid_from': '2000-01-01', 'observed_at': OBSERVED_AT,
                            'dimensions': {'origin': o, 'destination': d}, 'attributes': {'realtime_start': '2000-01-01'}})
        return {'records': records, 'truth': {'distance_elasticity': (-1.0, 0.05)}, 'options': {},
                'train_end': '2016-12-31', 'validation_end': '2020-12-31', 'cutoff': '2026-08-31'}
    else:
        raise ValueError(f'No synthetic generator for {component}')
    estimator = estimator_for(component, options=options)
    return {'records': observation_records(estimator, columns, dates), 'truth': truth, 'options': options,
            'train_end': cut[0], 'validation_end': cut[1], 'cutoff': cut[2]}
