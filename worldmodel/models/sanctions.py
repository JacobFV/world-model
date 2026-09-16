"""Sanctions exposure: ownership/control propagation, counterparty exposure and route effects.

Legal-rule propagation (OFAC 50 Percent Rule, 2014 guidance): an entity is blocked
when blocked persons own 50 percent or more of it, individually or in aggregate,
directly or indirectly. Indirect ownership counts only through entities that are
themselves blocked, so the blocked set is the least fixed point of that rule.
An optional control criterion (as in EU guidance) blocks entities controlled by a
blocked person. Separately, *economic look-through* multiplies shares through any
chain from designated persons; it is a risk metric, not a legal determination.

Route effects remove edges operated by blocked entities or located in sanctioned
jurisdictions, recompute least-cost routes and convert cost increases into iceberg
trade-cost multipliers consumable by ``worldmodel.models.trade.counterfactual``.
"""
import heapq
import math
from .base import apply_cutoff, finite, fit_result, integer, merged_parameters, parameter, quantiles, requirement, rng_from, validate_family

FAMILY = validate_family({
    'id': 'sanctions',
    'title': 'Sanctions ownership propagation and exposure',
    'description': 'OFAC 50% rule fixed point, look-through exposure, counterparty exposure and route/trade-cost effects.',
    'identification': 'rule_based_propagation_plus_structural_gravity',
    'validated': False,
    'parameters': {
        'ownership_threshold': parameter(0.5, 'share', 'Aggregate blocked ownership at or above which an entity is blocked.', bounds=[0, 1], source='external'),
        'apply_control': parameter(False, 'boolean', 'Also block entities controlled by blocked persons.', source='assumed'),
        'route_cost_semi_elasticity': parameter(1.0, 'log_trade_cost_per_route_cost_unit', 'd ln(trade cost) per unit of route cost increase.'),
        'prohibitive_multiplier': parameter(1e6, 'multiplier', 'Trade-cost multiplier when no permitted route exists.', source='assumed'),
        'sanction_coefficient': parameter(0.0, 'log_points', 'Gravity coefficient on sanction indicator (from trade.fit).'),
    },
    'requirements': [
        requirement('ofac_sdn', 'U.S. Treasury OFAC', 'SDN and Consolidated Sanctions lists (XML/CSV)', ['uid', 'sdnType', 'programList', 'akaList', 'idList'],
                    'https://sanctionslist.ofac.treas.gov/Home/SdnList', frequency='as published', role='designations', parameters=['ownership_threshold']),
        requirement('opensanctions', 'OpenSanctions', 'Default dataset incl. Ownership and Directorship (FollowTheMoney)', ['Ownership.owner', 'Ownership.asset', 'Ownership.percentage', 'Sanction.program'],
                    'https://www.opensanctions.org/datasets/default/', frequency='daily', role='ownership_graph', access='non_commercial_license', parameters=['ownership_threshold']),
        requirement('gleif_level2', 'GLEIF', 'Level 2 relationship records (direct/ultimate parents)', ['StartNode.LEI', 'EndNode.LEI', 'RelationshipType', 'RelationshipPeriods'],
                    'https://www.gleif.org/en/lei-data/gleif-golden-copy', frequency='daily', role='ownership_graph', parameters=['apply_control']),
        requirement('eu_consolidated', 'European Commission', 'EU consolidated financial sanctions list', ['logicalId', 'nameAlias', 'regulation'],
                    'https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions', frequency='as published', role='designations'),
        requirement('baci_with_sanctions', 'CEPII + GSDB', 'BACI flows joined to sanction episodes', ['t', 'i', 'j', 'v', 'sanction'],
                    'https://www.cepii.fr/', frequency='annual', parameters=['sanction_coefficient', 'route_cost_semi_elasticity']),
    ],
    'limitations': [
        'Output is a screening aid, not legal advice; licenses, delistings and jurisdiction-specific rules are not modeled.',
        'Ownership graphs from registries are incomplete and stale; missing links understate exposure.',
        'Route costs are exogenous scalars; carrier capacity and insurance availability are not modeled.',
    ],
})


NON_ESTIMABLE = ('The family output is a legal-rule determination (OFAC 50 Percent Rule least fixed point, look-through exposure, '
                 'route closure), not a forecast: given designations and ownership records it is exact, so no held-out '
                 'observable scores it statistically and correctness is checked by rule tests. Its one estimated coefficient '
                 '(sanction_coefficient) is a trade-gravity regressor that is scored through the trade family holdout when '
                 'flows carry a sanction regressor.')


def _ownership(edges):
    incoming, total = {}, {}
    for edge in edges:
        owner, owned = str(edge['owner']), str(edge['owned'])
        if owner == owned:
            raise ValueError('Self-ownership edges are not allowed')
        share = finite(edge['share'], 'ownership share', 0, 1)
        incoming.setdefault(owned, {})
        incoming[owned][owner] = incoming[owned].get(owner, 0.0) + share
        total[owned] = total.get(owned, 0.0) + share
    over = {k: v for k, v in total.items() if v > 1 + 1e-9}
    if over:
        raise ValueError(f'Recorded ownership exceeds 100 percent for: {sorted(over)[:5]}')
    return incoming


def propagate_blocking(ownership, designated, *, threshold=0.5, control=(), apply_control=False):
    incoming = _ownership(ownership)
    threshold = finite(threshold, 'threshold', 0, 1)
    blocked = {str(d): {'basis': 'designated', 'round': 0} for d in designated}
    controlled_by = {}
    for edge in control:
        controlled_by.setdefault(str(edge['controlled']), set()).add(str(edge['controller']))
    outgoing = {}
    for owned, owners in incoming.items():
        for owner in owners:
            outgoing.setdefault(owner, set()).add(owned)
    for controlled, controllers in controlled_by.items():
        for controller in controllers:
            outgoing.setdefault(controller, set()).add(controlled)
    frontier, rounds = set(blocked), 0
    while frontier:
        rounds += 1
        candidates = set()
        for node in frontier:
            candidates |= outgoing.get(node, set())
        frontier = set()
        for entity in sorted(candidates - set(blocked)):
            owners = incoming.get(entity, {})
            share = sum(v for owner, v in owners.items() if owner in blocked)
            if share >= threshold - 1e-12 and share > 0:
                blocked[entity] = {'basis': 'ownership_aggregate', 'round': rounds, 'aggregate_blocked_ownership': share,
                                   'via': sorted(o for o in owners if o in blocked)}
                frontier.add(entity)
            elif apply_control and controlled_by.get(entity, set()) & set(blocked):
                blocked[entity] = {'basis': 'control', 'round': rounds, 'via': sorted(controlled_by[entity] & set(blocked))}
                frontier.add(entity)
    return {'blocked': dict(sorted(blocked.items())), 'rounds': rounds, 'threshold': threshold, 'rule': 'least_fixed_point_aggregate_ownership'}


def look_through_exposure(ownership, designated, *, max_iter=1000, tol=1e-12):
    """Multiplicative indirect ownership by designated persons through any chain (capped at 1)."""
    incoming = _ownership(ownership)
    designated = {str(d) for d in designated}
    entities = sorted(set(incoming) | {o for owners in incoming.values() for o in owners})
    value = {e: 0.0 for e in entities}
    for iteration in range(1, max_iter + 1):
        change = 0.0
        nxt = {}
        for e in entities:
            if e in designated:
                nxt[e] = 1.0
                continue
            total = sum(share * (1.0 if owner in designated else value.get(owner, 0.0)) for owner, share in incoming.get(e, {}).items())
            nxt[e] = min(1.0, total)
            change = max(change, abs(nxt[e] - value[e]))
        value = nxt
        if change < tol:
            return {'exposure': value, 'iterations': iteration, 'converged': True}
    return {'exposure': value, 'iterations': max_iter, 'converged': False}


def counterparty_exposure(exposures, blocked, look_through):
    holders = {}
    for row in exposures:
        amount = finite(row['amount'], 'exposure amount', 0)
        counterparty = str(row['counterparty'])
        record = holders.setdefault(str(row['holder']), {'total': 0.0, 'blocked': 0.0, 'look_through': 0.0, 'clean': 0.0, 'currency': row.get('currency')})
        if record['currency'] != row.get('currency'):
            raise ValueError('Exposure rows for one holder must share a currency; convert explicitly first')
        record['total'] += amount
        if counterparty in blocked:
            record['blocked'] += amount
        else:
            partial = amount * look_through.get(counterparty, 0.0)
            record['look_through'] += partial
            record['clean'] += amount - partial
    for holder, record in holders.items():
        residual = record['total'] - record['blocked'] - record['look_through'] - record['clean']
        if abs(residual) > 1e-9 * max(1.0, record['total']):
            raise ValueError(f'Exposure categories do not sum for {holder}')
        record['accounting_residual'] = residual
    return holders


def _shortest(adjacency, source):
    distance = {source: 0.0}
    queue = [(0.0, source)]
    while queue:
        cost, node = heapq.heappop(queue)
        if cost > distance.get(node, math.inf):
            continue
        for target, weight in adjacency.get(node, []):
            candidate = cost + weight
            if candidate < distance.get(target, math.inf):
                distance[target] = candidate
                heapq.heappush(queue, (candidate, target))
    return distance


def route_impacts(routes, pairs, *, blocked=(), sanctioned_jurisdictions=(), semi_elasticity=1.0, prohibitive_multiplier=1e6, sector='*'):
    blocked, sanctioned = set(blocked), set(sanctioned_jurisdictions)
    before, after, removed = {}, {}, []
    for edge in routes:
        weight = finite(edge['cost'], 'route cost', 0)
        before.setdefault(edge['from'], []).append((edge['to'], weight))
        reasons = []
        if edge.get('operator') is not None and str(edge['operator']) in blocked:
            reasons.append('blocked_operator')
        if sanctioned & {edge['from'], edge['to'], *edge.get('jurisdictions', [])}:
            reasons.append('sanctioned_jurisdiction')
        if reasons:
            removed.append({'from': edge['from'], 'to': edge['to'], 'operator': edge.get('operator'), 'reasons': reasons})
        else:
            after.setdefault(edge['from'], []).append((edge['to'], weight))
    rows, hats = [], []
    cache_before, cache_after = {}, {}
    for pair in pairs:
        origin, destination = pair['exporter'], pair['importer']
        old = cache_before.setdefault(origin, _shortest(before, origin)).get(destination, math.inf)
        new = cache_after.setdefault(origin, _shortest(after, origin)).get(destination, math.inf)
        if old == math.inf:
            raise ValueError(f'No baseline route for {origin}->{destination}')
        if new == math.inf:
            multiplier, status = finite(prohibitive_multiplier, 'prohibitive_multiplier', 1), 'no_permitted_route'
        else:
            multiplier, status = math.exp(finite(semi_elasticity, 'semi_elasticity', 0) * (new - old)), 'rerouted' if new > old else 'unchanged'
        rows.append({'exporter': origin, 'importer': destination, 'baseline_cost': old, 'new_cost': None if new == math.inf else new,
                     'status': status, 'trade_cost_multiplier': multiplier})
        if multiplier != 1:
            hats.append({'exporter': origin, 'importer': destination, 'sector': sector, 'multiplier': multiplier})
    return {'pairs': rows, 'removed_edges': removed, 'trade_cost_hat': hats}


def fit(data, cutoff=None):
    """Estimate the sanction trade-cost effect with PPML gravity (delegates to the trade family)."""
    from .trade import fit_gravity
    regressors = tuple(data.get('regressors', ('ln_distance', 'contiguous', 'ln_one_plus_tariff', 'sanction')))
    if 'sanction' not in regressors:
        raise ValueError('Sanctions gravity fit requires a sanction regressor')
    model, window = fit_gravity(data['flows'], cutoff, regressors, tuple(data.get('fixed_effects', ('exporter_year', 'importer_year'))))
    beta = model['coefficients']['sanction']
    estimate = {'sanction_coefficient': beta, 'sanction_coefficient_se': model['standard_errors']['sanction'],
                'trade_reduction_share': 1 - math.exp(beta)}
    if 'ln_one_plus_tariff' in model['coefficients'] and model['coefficients']['ln_one_plus_tariff'] < 0:
        theta = -model['coefficients']['ln_one_plus_tariff']
        estimate['tariff_equivalent'] = math.exp(-beta / theta) - 1
        estimate['route_cost_semi_elasticity_hint'] = 'Map route-cost increases to tariff equivalents with theta=%.3f' % theta
    diagnostics = {k: model[k] for k in ('standard_errors', 'converged', 'iterations', 'n', 'dropped_separated_observations')}
    return fit_result(estimate, diagnostics, method='ppml_gravity_sanction_indicator', identification='correlational_unless_sanction_timing_exogenous',
                      windows=[window], data=data, family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    ownership = config.get('ownership', [])
    designated = list(config.get('designated', []))
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'legal_status': 'screening_estimate_not_legal_determination'}

    def one(designations):
        result = propagate_blocking(ownership, designations, threshold=params['ownership_threshold'],
                                    control=config.get('control', []), apply_control=params['apply_control'])
        look = look_through_exposure(ownership, designations)
        exposure = counterparty_exposure(config.get('exposures', []), result['blocked'], look['exposure'])
        routes = None
        if config.get('routes'):
            routes = route_impacts(config['routes'], config['route_pairs'], blocked=result['blocked'],
                                   sanctioned_jurisdictions=config.get('sanctioned_jurisdictions', []),
                                   semi_elasticity=params['route_cost_semi_elasticity'], prohibitive_multiplier=params['prohibitive_multiplier'])
        return result, look, exposure, routes

    if mode == 'deterministic':
        result, look, exposure, routes = one(designated)
        report.update(blocked=result['blocked'], propagation_rounds=result['rounds'], look_through_exposure=look['exposure'],
                      counterparty_exposure=exposure)
        if routes is not None:
            report['routes'] = routes
            if config.get('trade'):
                from .trade import counterfactual
                trade = config['trade']
                shock = {'tariffs': trade.get('shock', {}).get('tariffs', []),
                         'trade_cost_hat': trade.get('shock', {}).get('trade_cost_hat', []) + routes['trade_cost_hat']}
                report['trade_effects'] = counterfactual(trade['baseline'], shock, trade.get('parameters', {}))
    elif mode == 'stochastic':
        rng = rng_from(seed)
        runs = integer(config.get('n_simulations', 500), 'n_simulations', 1, 100000)
        candidates = config.get('candidates', [])
        frequency, blocked_amounts = {}, {}
        for _ in range(runs):
            draw = designated + [c['id'] for c in candidates if rng.random() < finite(c['probability'], 'designation probability', 0, 1)]
            result, _, exposure, _ = one(draw)
            for entity in result['blocked']:
                frequency[entity] = frequency.get(entity, 0) + 1
            for holder, record in exposure.items():
                blocked_amounts.setdefault(holder, []).append(record['blocked'])
        report.update(n_simulations=runs, blocked_probability={k: v / runs for k, v in sorted(frequency.items())},
                      blocked_exposure_quantiles={k: quantiles(v) for k, v in blocked_amounts.items()},
                      uncertainty='independent designation draws for candidates')
    else:
        raise ValueError('mode must be deterministic or stochastic')
    return report


def synthetic(seed=0):
    from .trade import synthetic as trade_synthetic, example_config
    rng = rng_from(seed)
    data = trade_synthetic(seed, countries=10, years=4)['data']
    sanctioned_pairs = {(a, b) for a, b in (('C0', 'C1'), ('C2', 'C3'), ('C4', 'C5'), ('C6', 'C7'), ('C8', 'C9'))} | \
        {(b, a) for a, b in (('C0', 'C1'), ('C2', 'C3'), ('C4', 'C5'), ('C6', 'C7'), ('C8', 'C9'))}
    for row in data['flows']:
        row['sanction'] = int((row['exporter'], row['importer']) in sanctioned_pairs and row['year'] >= 2017)
        if row['sanction']:
            row['value'] *= math.exp(-1.2) * rng.gammavariate(50, 1 / 50)
    trade = example_config()
    config = {
        'designated': ['X'],
        'ownership': [{'owner': 'X', 'owned': 'A', 'share': 0.5}, {'owner': 'A', 'owned': 'C', 'share': 0.5},
                      {'owner': 'X', 'owned': 'D', 'share': 0.25}, {'owner': 'Y', 'owned': 'D', 'share': 0.25},
                      {'owner': 'X', 'owned': 'E', 'share': 0.4}, {'owner': 'E', 'owned': 'F', 'share': 1.0},
                      {'owner': 'C', 'owned': 'ShipCo', 'share': 0.6}],
        'candidates': [{'id': 'Y', 'probability': 0.3}],
        'exposures': [{'holder': 'Bank1', 'counterparty': 'C', 'amount': 100.0, 'currency': 'USD'},
                      {'holder': 'Bank1', 'counterparty': 'F', 'amount': 50.0, 'currency': 'USD'},
                      {'holder': 'Bank1', 'counterparty': 'Z', 'amount': 25.0, 'currency': 'USD'}],
        'routes': [{'from': 'CHN', 'to': 'USA', 'cost': 1.0, 'operator': 'ShipCo'}, {'from': 'CHN', 'to': 'ROW', 'cost': 0.4},
                   {'from': 'ROW', 'to': 'USA', 'cost': 0.8}, {'from': 'USA', 'to': 'CHN', 'cost': 1.0}],
        'route_pairs': [{'exporter': 'CHN', 'importer': 'USA'}, {'exporter': 'USA', 'importer': 'CHN'}],
        'parameters': {'route_cost_semi_elasticity': 0.5},
        'trade': {'baseline': trade['baseline'], 'parameters': trade['parameters']},
        'n_simulations': 400,
    }
    return {'data': {'flows': data['flows']}, 'truth': {'sanction_coefficient': -1.2}, 'config': config}
