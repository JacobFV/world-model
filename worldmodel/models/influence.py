"""Lobbying and campaign-finance flows as conserved networks, with correlational panel hooks.

Networks: client -> registrant -> issue / agency (LDA) and donor -> committee ->
candidate (FEC). Attribution rules are explicit and conserve money: LDA amounts
split equally across listed issues and agencies; committee receipts pass to
candidates pro rata with a retained remainder, and candidate transfers not
covered by itemized receipts are booked to an ``unattributed`` node.

Influence estimates are two-way fixed-effect panel regressions with unit-clustered
errors. They are labeled ``correlational`` unless a design with an instrument is
declared, in which case FE-2SLS is used and labeled with its untested assumptions.
"""
from copy import deepcopy
import math
from .base import (apply_cutoff, demean, finite, fit_result, integer, inverse, merged_parameters, ols, parameter,
                   quantiles, requirement, rng_from, validate_family)

FAMILY = validate_family({
    'id': 'influence',
    'title': 'Lobbying and campaign-finance influence networks',
    'description': 'Conserved money-flow networks, exposure metrics and panel influence regressions.',
    'identification': 'correlational',
    'validated': False,
    'parameters': {
        'influence_coefficient': parameter(0.0, 'outcome_units_per_exposure_unit', 'Within-unit association of outcome with exposure.'),
        'influence_standard_error': parameter(None, 'outcome_units_per_exposure_unit', 'Cluster-robust standard error.'),
        'lda_allocation': parameter('equal', 'rule', 'Split of LDA filing amounts across listed issues/agencies.', source='assumed'),
        'pass_through': parameter('pro_rata', 'rule', 'Attribution of committee receipts to candidate transfers.', source='assumed'),
    },
    'requirements': [
        requirement('lda_filings', 'U.S. Senate Office of Public Records', 'Lobbying Disclosure Act REST API filings',
                    ['filing_uuid', 'client.id', 'registrant.id', 'income', 'expenses', 'lobbying_activities.general_issue_code',
                     'lobbying_activities.government_entities', 'dt_posted'], 'https://lda.senate.gov/api/', frequency='quarterly',
                    parameters=['lda_allocation']),
        requirement('fec_individual_contributions', 'Federal Election Commission', 'itcont bulk (contributions by individuals)',
                    ['CMTE_ID', 'NAME', 'ZIP_CODE', 'EMPLOYER', 'OCCUPATION', 'TRANSACTION_DT', 'TRANSACTION_AMT'],
                    'https://www.fec.gov/data/browse-data/?tab=bulk-data', frequency='per cycle', parameters=['pass_through']),
        requirement('fec_committee_to_candidate', 'Federal Election Commission', 'itpas2 bulk (committee contributions to candidates)',
                    ['CMTE_ID', 'CAND_ID', 'TRANSACTION_DT', 'TRANSACTION_AMT'], 'https://www.fec.gov/data/browse-data/?tab=bulk-data',
                    frequency='per cycle', parameters=['pass_through']),
        requirement('fec_linkages', 'Federal Election Commission', 'ccl candidate-committee linkage', ['CAND_ID', 'CMTE_ID', 'CMTE_DSGN'],
                    'https://www.fec.gov/data/browse-data/?tab=bulk-data', frequency='per cycle', parameters=['pass_through']),
        requirement('voteview_outcomes', 'Voteview (UCLA)', 'HSall_votes.csv (outcomes for influence panels)', ['icpsr', 'rollnumber', 'cast_code'],
                    'https://voteview.com/data', frequency='per roll call', role='outcome', parameters=['influence_coefficient']),
    ],
    'limitations': [
        'Reported LDA amounts are per filing and rounded; issue/agency splits are an attribution assumption.',
        'Money is fungible; pro-rata attribution does not identify which dollars funded which transfer.',
        'Panel coefficients are correlational unless a credible identification design is supplied and defended.',
        'Donor identity resolution (name/ZIP/employer) is outside this module and drives exposure metrics.',
    ],
})

TOLERANCE = 1e-6


def _add(edges, source, target, amount, **attributes):
    key = (source, target)
    edge = edges.setdefault(key, {'source': source, 'target': target, 'amount': 0.0, **attributes})
    edge['amount'] += amount


def lobbying_network(filings, cutoff=None):
    rows, window = apply_cutoff(filings, cutoff, label='lda_filings')
    edges, attributed = {}, {}
    totals = {'client_to_registrant': 0.0, 'registrant_to_issue': 0.0, 'registrant_to_agency': 0.0}
    unreported = 0
    for row in rows:
        amount = row.get('amount')
        if amount is None:
            unreported += 1
            amount = 0.0
        amount = finite(amount, 'LDA amount', 0)
        client, registrant = 'client:' + str(row['client']), 'registrant:' + str(row['registrant'])
        issues = [str(v) for v in row.get('issues') or []] or ['unspecified']
        agencies = [str(v) for v in row.get('agencies') or []] or ['unspecified']
        if len(set(issues)) != len(issues) or len(set(agencies)) != len(agencies):
            raise ValueError('Duplicate issue or agency within one filing')
        _add(edges, client, registrant, amount, kind='client_to_registrant')
        totals['client_to_registrant'] += amount
        for issue in issues:
            _add(edges, registrant, 'issue:' + issue, amount / len(issues), kind='registrant_to_issue')
            _add(attributed, client, 'issue:' + issue, amount / len(issues), kind='client_to_issue_attributed')
            totals['registrant_to_issue'] += amount / len(issues)
        for agency in agencies:
            _add(edges, registrant, 'agency:' + agency, amount / len(agencies), kind='registrant_to_agency')
            _add(attributed, client, 'agency:' + agency, amount / len(agencies), kind='client_to_agency_attributed')
            totals['registrant_to_agency'] += amount / len(agencies)
    scale = max(1.0, totals['client_to_registrant'])
    checks = {'issue_allocation_residual': totals['client_to_registrant'] - totals['registrant_to_issue'],
              'agency_allocation_residual': totals['client_to_registrant'] - totals['registrant_to_agency']}
    if any(abs(v) > TOLERANCE * scale for v in checks.values()):
        raise ValueError('LDA allocation failed to conserve reported amounts')
    return {'edges': sorted(edges.values(), key=lambda e: (e['source'], e['target'])),
            'attributed_edges': sorted(attributed.values(), key=lambda e: (e['source'], e['target'])),
            'totals': totals, 'accounting': checks, 'filings': len(rows), 'filings_without_amount': unreported,
            'window': window, 'allocation': 'equal_split_across_listed_issues_and_agencies'}


def contribution_network(contributions, transfers=(), linkages=(), cutoff=None):
    rows, window = apply_cutoff(contributions, cutoff, label='contributions')
    trows, twindow = apply_cutoff(list(transfers), cutoff, label='committee_transfers')
    receipts, donor_amounts, industries = {}, {}, {}
    for row in rows:
        amount = finite(row['amount'], 'contribution amount')
        committee, donor = 'committee:' + str(row['committee']), 'donor:' + str(row['donor'])
        receipts[committee] = receipts.get(committee, 0.0) + amount
        donor_amounts[(donor, committee)] = donor_amounts.get((donor, committee), 0.0) + amount
        if row.get('industry') is not None:
            previous = industries.setdefault(donor, str(row['industry']))
            if previous != str(row['industry']):
                raise ValueError(f'Donor {donor} has conflicting industry labels; resolve identities first')
    outflows = {}
    for row in trows:
        committee, candidate = 'committee:' + str(row['committee']), 'candidate:' + str(row['candidate'])
        outflows.setdefault(committee, {})
        outflows[committee][candidate] = outflows[committee].get(candidate, 0.0) + finite(row['amount'], 'transfer amount')
    principal = {}
    for row in linkages:
        committee, candidate = 'committee:' + str(row['committee']), 'candidate:' + str(row['candidate'])
        if committee in principal and principal[committee] != candidate:
            raise ValueError('A principal committee can link to only one candidate')
        principal[committee] = candidate
    for committee, candidate in principal.items():
        if committee in outflows and set(outflows[committee]) != {candidate}:
            raise ValueError('Principal committee transfers must go to its linked candidate')
        outflows[committee] = {candidate: receipts.get(committee, 0.0)}
    edges = {}
    for (donor, committee), amount in donor_amounts.items():
        _add(edges, donor, committee, amount, kind='donor_to_committee')
    attributed = {}
    checks = {}
    for committee in sorted(set(receipts) | set(outflows)):
        received = receipts.get(committee, 0.0)
        paid = sum(outflows.get(committee, {}).values())
        for candidate, amount in outflows.get(committee, {}).items():
            _add(edges, committee, candidate, amount, kind='committee_to_candidate')
        ratio = min(1.0, paid / received) if received > 0 else 0.0
        booked_in, booked_out = 0.0, 0.0
        for (donor, c), amount in donor_amounts.items():
            if c != committee:
                continue
            for candidate, transfer in outflows.get(committee, {}).items():
                share = amount * ratio * transfer / paid if paid > 0 else 0.0
                _add(attributed, donor, candidate, share, kind='donor_to_candidate_attributed', via=committee)
                booked_in += share
                booked_out += share
            retained = amount * (1 - ratio)
            if retained:
                _add(attributed, donor, 'retained:' + committee, retained, kind='donor_retained_by_committee')
            booked_in += retained
        unattributed = paid - booked_out
        if unattributed > TOLERANCE * max(1.0, paid):
            for candidate, transfer in outflows.get(committee, {}).items():
                _add(attributed, 'unattributed:' + committee, candidate, unattributed * transfer / paid, kind='unattributed_transfer')
        scale = max(1.0, abs(received), abs(paid))
        checks[committee] = {'receipts_residual': received - booked_in,
                             'transfers_residual': paid - booked_out - (unattributed if unattributed > TOLERANCE * max(1.0, paid) else 0.0)}
        if any(abs(v) > TOLERANCE * scale for v in checks[committee].values()):
            raise ValueError(f'Contribution attribution failed to conserve money for {committee}')
    return {'edges': sorted(edges.values(), key=lambda e: (e['source'], e['target'])),
            'attributed_edges': sorted(attributed.values(), key=lambda e: (e['source'], e['target'], e.get('via', ''))),
            'donor_industry': industries, 'accounting': checks, 'windows': [window, twindow],
            'attribution': 'pro_rata_pass_through_with_retained_and_unattributed_nodes'}


def exposure_metrics(edges, attributes=None, top=10):
    """Inflow concentration and attribute exposure for each target; weighted PageRank over flows."""
    attributes = attributes or {}
    inflows, outflows = {}, {}
    for edge in edges:
        amount = edge['amount']
        inflows.setdefault(edge['target'], {})
        inflows[edge['target']][edge['source']] = inflows[edge['target']].get(edge['source'], 0.0) + amount
        outflows.setdefault(edge['source'], {})
        outflows[edge['source']][edge['target']] = outflows[edge['source']].get(edge['target'], 0.0) + amount
    targets = {}
    for target, sources in inflows.items():
        total = sum(sources.values())
        positive = {k: v for k, v in sources.items() if v > 0}
        shares = [v / total for v in positive.values()] if total > 0 else []
        by_attribute = {}
        for source, amount in positive.items():
            label = attributes.get(source, 'unlabeled')
            by_attribute[label] = by_attribute.get(label, 0.0) + amount
        targets[target] = {'total_inflow': total, 'sources': len(positive), 'hhi': sum(s * s for s in shares),
                           'top_source_share': max(shares) if shares else None,
                           'attribute_exposure': {k: v / total for k, v in sorted(by_attribute.items())} if total > 0 else {}}
    nodes = sorted(set(inflows) | set(outflows))
    index = {n: i for i, n in enumerate(nodes)}
    rank = [1.0 / len(nodes)] * len(nodes) if nodes else []
    damping = 0.85
    for _ in range(200):
        nxt = [(1 - damping) / len(nodes)] * len(nodes)
        dangling = sum(rank[index[n]] for n in nodes if n not in outflows or sum(max(v, 0) for v in outflows[n].values()) <= 0)
        for n in nodes:
            nxt[index[n]] += damping * dangling / len(nodes)
        for source, targets_out in outflows.items():
            total = sum(max(v, 0) for v in targets_out.values())
            if total <= 0:
                continue
            for target, amount in targets_out.items():
                if amount > 0:
                    nxt[index[target]] += damping * rank[index[source]] * amount / total
        if max(abs(a - b) for a, b in zip(nxt, rank)) < 1e-12:
            rank = nxt
            break
        rank = nxt
    pagerank = {n: rank[index[n]] for n in nodes}
    return {'targets': targets, 'pagerank_top': sorted(pagerank.items(), key=lambda kv: (-kv[1], kv[0]))[:top],
            'nodes': len(nodes), 'edges': len(edges)}


def panel_design(rows, outcome, exposure, controls=(), fixed_effects=('unit', 'period')):
    """Export a demeaned design for external estimators (estimation layer hook)."""
    columns = [[finite(r[outcome], outcome) for r in rows], [finite(r[exposure], exposure) for r in rows]]
    columns += [[finite(r[c], c) for r in rows] for c in controls]
    groups = [[str(r[f]) for r in rows] for f in fixed_effects]
    demeaned, iterations = demean(columns, groups)
    return {'y': demeaned[0], 'X': [list(v) for v in zip(*demeaned[1:])], 'names': [exposure, *controls],
            'clusters': [str(r['unit']) for r in rows], 'fixed_effects': list(fixed_effects), 'demeaning_iterations': iterations}


def fit(data, cutoff=None):
    rows, window = apply_cutoff(data['panel'], cutoff, label='panel')
    outcome, exposure = data.get('outcome', 'outcome'), data.get('exposure', 'exposure')
    controls = list(data.get('controls', []))
    fixed = tuple(data.get('fixed_effects', ['unit', 'period']))
    design = dict(data.get('design', {'type': 'observational'}))
    units = {str(r['unit']) for r in rows}
    if len(units) < 3 or len(rows) < 10:
        raise ValueError('Panel requires at least three units and ten rows')
    matrix = panel_design(rows, outcome, exposure, controls, fixed)
    X, y, clusters = matrix['X'], matrix['y'], matrix['clusters']
    names = matrix['names']
    if design.get('type', 'observational') == 'observational':
        model = ols(X, y, names=names, clusters=clusters)
        identification = 'correlational'
        method = 'two_way_fixed_effects_within_ols_unit_clustered'
        extra = {}
    elif design['type'] == 'instrumental_variable':
        instrument = design['instrument']
        z = demean([[finite(r[instrument], instrument) for r in rows]], [[str(r[f]) for r in rows] for f in fixed])[0][0]
        Z = [[z[i]] + X[i][1:] for i in range(len(rows))]
        first = ols(Z, [row[0] for row in X], names=[instrument, *controls], clusters=clusters)
        fitted = first['fitted']
        Xhat = [[fitted[i]] + X[i][1:] for i in range(len(rows))]
        second = ols(Xhat, y, names=names, clusters=clusters)
        beta = [second['coefficients'][n] for n in names]
        residuals = [y[i] - sum(b * v for b, v in zip(beta, X[i])) for i in range(len(rows))]
        k = len(names)
        bread = inverse([[sum(Xhat[i][a] * Xhat[i][b] for i in range(len(rows))) for b in range(k)] for a in range(k)])
        scores = {}
        for i, g in enumerate(clusters):
            row = scores.setdefault(g, [0.0] * k)
            for a in range(k):
                row[a] += Xhat[i][a] * residuals[i]
        meat = [[sum(s[a] * s[b] for s in scores.values()) for b in range(k)] for a in range(k)]
        g = len(scores)
        factor = g / (g - 1)
        cov = [[factor * sum(bread[a][c] * sum(meat[c][d] * bread[d][b] for d in range(k)) for c in range(k)) for b in range(k)] for a in range(k)]
        model = {'coefficients': dict(zip(names, beta)), 'standard_errors': {n: math.sqrt(max(cov[a][a], 0)) for a, n in enumerate(names)},
                 'n': len(rows), 'se_type': 'cluster_robust_2sls', 'r2': None}
        t = first['coefficients'][instrument] / first['standard_errors'][instrument] if first['standard_errors'][instrument] else None
        extra = {'first_stage': {'coefficient': first['coefficients'][instrument], 'standard_error': first['standard_errors'][instrument],
                                 'cluster_robust_f': t * t if t is not None else None}}
        identification = 'instrumental_variable_assumed_exclusion_untested'
        method = 'two_way_fixed_effects_2sls_unit_clustered'
    else:
        raise ValueError('design type must be observational or instrumental_variable')
    estimate = {'influence_coefficient': model['coefficients'][exposure],
                'influence_standard_error': model['standard_errors'][exposure], 'controls': {c: model['coefficients'][c] for c in controls}}
    diagnostics = {'n': model['n'], 'units': len(units), 'se_type': model['se_type'], 'within_r2': model.get('r2'),
                   'demeaning_iterations': matrix['demeaning_iterations'], **extra,
                   'interpretation': 'Association within units over time conditional on fixed effects; not a causal effect.'
                   if identification == 'correlational' else 'Causal only if the instrument is relevant and excludable.'}
    return fit_result(estimate, diagnostics, method=method, identification=identification, windows=[window], data=data,
                      family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


def simulate(config, mode='deterministic', seed=0):
    params = merged_parameters(FAMILY, config.get('parameters'))
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'identification': config.get('identification', 'correlational')}
    network = config.get('network', {})
    if network.get('filings'):
        lobbying = lobbying_network(network['filings'])
        report['lobbying'] = {**lobbying, 'exposure': exposure_metrics(lobbying['edges'])}
    if network.get('contributions'):
        money = contribution_network(network['contributions'], network.get('transfers', []), network.get('linkages', []))
        report['campaign_finance'] = {**money, 'exposure': exposure_metrics(money['attributed_edges'], money['donor_industry'])}
    counterfactual = config.get('counterfactual')
    if counterfactual:
        beta = finite(params['influence_coefficient'], 'influence_coefficient')
        se = params.get('influence_standard_error')
        changes = counterfactual['exposure_change']
        shifts = {}
        if mode == 'deterministic':
            for unit, delta in changes.items():
                point = beta * finite(delta, 'exposure change')
                band = None if se is None else [point - 1.96 * se * abs(delta), point + 1.96 * se * abs(delta)]
                shifts[unit] = {'predicted_outcome_shift': point, 'interval_95': band}
        elif mode == 'stochastic':
            if se is None:
                raise ValueError('Stochastic counterfactual requires influence_standard_error')
            rng = rng_from(seed)
            runs = integer(counterfactual.get('n_draws', 1000), 'n_draws', 1, 100000)
            draws = [rng.gauss(beta, finite(se, 'influence_standard_error', 0)) for _ in range(runs)]
            shifts = {unit: quantiles([b * delta for b in draws]) for unit, delta in changes.items()}
        else:
            raise ValueError('mode must be deterministic or stochastic')
        report['counterfactual'] = {'shifts': shifts, 'label': 'Associational projection; not an estimated causal effect unless identified.'}
    if len(report) == 4:
        raise ValueError('Supply network data and/or a counterfactual')
    return report


def synthetic(seed=0, units=40, periods=12, beta=0.3):
    rng = rng_from(seed)
    panel = []
    for u in range(units):
        alpha = rng.gauss(0, 1)
        for t in range(periods):
            gamma = 0.1 * t
            exposure = 2 * alpha + rng.gauss(0, 1) + 0.05 * t
            z = rng.gauss(0, 1)
            panel.append({'unit': f'u{u}', 'period': t, 'date': f'{2010 + t}-06-30', 'exposure': exposure, 'z': z,
                          'outcome': beta * exposure + 1.5 * alpha + gamma + rng.gauss(0, 0.5)})
    filings = [{'client': 'acme', 'registrant': 'firm_a', 'amount': 90000, 'issues': ['TRD', 'TAX'], 'agencies': ['USTR', 'Treasury', 'Senate'], 'date': '2024-04-20'},
               {'client': 'globex', 'registrant': 'firm_a', 'amount': 40000, 'issues': ['TRD'], 'agencies': ['USTR'], 'date': '2024-04-20'},
               {'client': 'globex', 'registrant': 'firm_b', 'amount': None, 'issues': ['ENG'], 'agencies': [], 'date': '2024-07-20'}]
    contributions = [{'donor': 'd1', 'committee': 'pac1', 'amount': 5000, 'industry': 'energy', 'date': '2024-02-01'},
                     {'donor': 'd2', 'committee': 'pac1', 'amount': 3000, 'industry': 'finance', 'date': '2024-02-02'},
                     {'donor': 'd3', 'committee': 'camp_x', 'amount': 2000, 'industry': 'energy', 'date': '2024-02-03'}]
    transfers = [{'committee': 'pac1', 'candidate': 'X', 'amount': 4000, 'date': '2024-03-01'},
                 {'committee': 'pac1', 'candidate': 'Y', 'amount': 2000, 'date': '2024-03-01'}]
    linkages = [{'committee': 'camp_x', 'candidate': 'X'}]
    return {'data': {'panel': panel, 'design': {'type': 'observational'}}, 'truth': {'influence_coefficient': beta},
            'config': {'network': {'filings': filings, 'contributions': contributions, 'transfers': transfers, 'linkages': linkages},
                       'parameters': {'influence_coefficient': beta, 'influence_standard_error': 0.05},
                       'counterfactual': {'exposure_change': {'u1': 1.0, 'u2': -2.0}, 'n_draws': 500}}}


# ----------------------------------------------------------------------------- holdout forecaster

FIXED_EFFECTS_ONLY = 'fixed_effects_only'


def _two_way(rows, residual):
    """Unit and period effects of residuals on a possibly unbalanced panel (alternating means)."""
    units, periods = {}, {}
    for r in rows:
        units.setdefault(str(r['unit']), []).append(r)
        periods.setdefault(r['date'], []).append(r)
    alpha, gamma = {u: 0.0 for u in units}, {p: 0.0 for p in periods}
    for _ in range(200):
        change = 0.0
        for p, members in periods.items():
            value = sum(residual[id(r)] - alpha[str(r['unit'])] for r in members) / len(members)
            change, gamma[p] = max(change, abs(value - gamma[p])), value
        for u, members in units.items():
            value = sum(residual[id(r)] - gamma[r['date']] for r in members) / len(members)
            change, alpha[u] = max(change, abs(value - alpha[u])), value
        if change < 1e-12:
            break
    errors = [residual[id(r)] - alpha[str(r['unit'])] - gamma[r['date']] for r in rows]
    dof = max(len(rows) - len(units) - len(periods) + 1, 1)
    return alpha, gamma, sum(e * e for e in errors) / dof


def _period_step(gamma):
    order = sorted(gamma, key=lambda p: _period_key(p))
    values = [gamma[p] for p in order]
    if len(values) < 3:
        return values[-1], 0.0
    diffs = [b - a for a, b in zip(values, values[1:])]
    drift = sum(diffs) / len(diffs)
    spread = sum((d - drift) ** 2 for d in diffs) / (len(diffs) - 1)
    return values[-1] + drift, spread * (1 + 1 / len(diffs))


def _period_key(value):
    from .base import _time
    return _time(value)


def _holdout_forecast(parameters, history, rows, data):
    """Next-period outcomes given realized exposure: beta x + unit effect + drift-extrapolated period effect.

    Unit and period effects are recomputed from pre-origin residuals; the period effect
    of the target period follows a random walk with drift. The ``fixed_effects_only``
    baseline repeats the calculation with beta = 0 and no controls.
    """
    if tuple(data.get('fixed_effects', ['unit', 'period'])) != ('unit', 'period'):
        raise ValueError('The influence holdout forecaster supports unit and period fixed effects only')
    outcome, exposure = data.get('outcome', 'outcome'), data.get('exposure', 'exposure')
    controls = list(data.get('controls', []))
    beta = finite(parameters['influence_coefficient'], 'influence_coefficient')
    control_coefficients = parameters.get('controls') or {}

    def linear(row):
        return beta * finite(row[exposure], exposure) + sum(control_coefficients.get(c, 0.0) * finite(row[c], c) for c in controls)

    model = _two_way(history, {id(r): finite(r[outcome], outcome) - linear(r) for r in history})
    null = _two_way(history, {id(r): finite(r[outcome], outcome) for r in history})
    steps = [_period_step(model[1]), _period_step(null[1])]
    past = {}
    for r in sorted(history, key=lambda r: _period_key(r['date'])):
        past.setdefault(str(r['unit']), []).append(r[outcome])
    out = []
    for row in sorted(rows, key=lambda r: str(r['unit'])):
        unit = str(row['unit'])
        if unit not in model[0]:
            continue
        mean = linear(row) + model[0][unit] + steps[0][0]
        base = null[0][unit] + steps[1][0]
        out.append({'target': f'{outcome}:{unit}', 'actual': row[outcome], 'mean': mean, 'sd': math.sqrt(model[2] + steps[0][1]),
                    'history_values': past.get(unit, []),
                    'baselines': {FIXED_EFFECTS_ONLY: {'mean': base, 'sd': math.sqrt(null[2] + steps[1][1])}}})
    return out


holdout_forecaster = {
    'rows_key': 'panel', 'time_key': 'date', 'target': 'panel_outcome', 'forecast': _holdout_forecast,
    'fit_keys': ['panel', 'outcome', 'exposure', 'controls', 'fixed_effects', 'design', 'information_time', 'revisions'],
    'conditional_inputs': ['exposure_at_target_period', 'controls_at_target_period'], 'baselines': [FIXED_EFFECTS_ONLY],
    'description': 'Next-period unit outcome given realized exposure (two-way FE with drifting period effect) versus the same '
                   'fixed-effects forecast without the influence coefficient.',
}
