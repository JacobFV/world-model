"""Legislative behavior: spatial ideal points, party discipline, committee gates and passage.

Vote model (a logistic reduction of quadratic spatial utility, in the IDEAL/emIRT
family rather than DW-NOMINATE's Gaussian-utility kernel)::

    P(yea_ij) = logistic(a_j + b_j . x_i + delta * whip_ij)

For a new bill with proposal p and status quo q, quadratic utility gives
``a + b.x = salience * (2 x.(p - q) - (|p|^2 - |q|^2))``. Ideal points are MAP
estimates by alternating Newton updates (legislators given bills, bills given
legislators) with N(0, I) legislator priors and weak bill priors. Dimensions
are standardized each sweep; polarity is fixed by explicit anchors.
"""
from copy import deepcopy
import math
from .base import (NUMPY, apply_cutoff, finite, fit_result, integer, logistic, merged_parameters,
                   parameter, poisson_binomial, requirement, rng_from, solve, validate_family)

FAMILY = validate_family({
    'id': 'legislative',
    'title': 'Legislative spatial voting and bill passage',
    'description': 'Ideal points from roll calls; vote, committee-gate and passage probabilities for coalitions.',
    'identification': 'descriptive_measurement',
    'validated': False,
    'parameters': {
        'ideal_points': parameter({}, 'latent_ideology_sd', 'Member ideal points per dimension (standardized, anchored).',
                                  series=['voteview:HSall_votes.cast_code']),
        'bill_parameters': parameter({}, 'logit', 'Roll-call intercept a_j and discrimination b_j.'),
        'dimensions': parameter(1, 'count', 'Latent policy dimensions (1 or 2).', bounds=[1, 2], source='assumed'),
        'discipline': parameter(0.0, 'logit', 'Additive log-odds toward the leave-one-out party majority position.',
                                bounds=[-10, 10]),
        'salience': parameter(5.0, 'logit_per_squared_sd', 'Scale mapping quadratic spatial utility to log-odds for new bills.',
                              bounds=[0, 100], source='assumed'),
        'prior_sd': parameter(1.0, 'latent_ideology_sd', 'Prior standard deviation of ideal points.', source='assumed'),
        'lopsided_threshold': parameter(0.025, 'fraction', 'Roll calls with a smaller minority share are excluded.', source='assumed'),
        'min_votes': parameter(20, 'count', 'Legislators with fewer scaled votes are excluded.', source='assumed'),
    },
    'requirements': [
        requirement('voteview_votes', 'Voteview (UCLA)', 'HSall_votes.csv', ['congress', 'chamber', 'rollnumber', 'icpsr', 'cast_code'],
                    'https://voteview.com/static/data/out/votes/HSall_votes.csv', frequency='per roll call',
                    parameters=['ideal_points', 'bill_parameters', 'discipline']),
        requirement('voteview_members', 'Voteview (UCLA)', 'HSall_members.csv', ['congress', 'chamber', 'icpsr', 'party_code', 'bioname'],
                    'https://voteview.com/static/data/out/members/HSall_members.csv', frequency='per congress',
                    parameters=['ideal_points', 'discipline']),
        requirement('voteview_rollcalls', 'Voteview (UCLA)', 'HSall_rollcalls.csv', ['congress', 'chamber', 'rollnumber', 'date', 'bill_number'],
                    'https://voteview.com/static/data/out/rollcalls/HSall_rollcalls.csv', frequency='per roll call',
                    parameters=['bill_parameters']),
        requirement('congress_committees', 'Library of Congress', 'Congress.gov API committees and bill actions',
                    ['committee.systemCode', 'committee.members', 'bill.actions'], 'https://api.congress.gov/',
                    frequency='daily', role='simulation_structure', access='public_api_key'),
    ],
    'limitations': [
        'Ideal points are measurement, not causal preference; strategic voting and agenda selection bias scaling.',
        'Rotation in two dimensions is not identified beyond anchors; compare dimensions cautiously.',
        'Discipline estimated in-sample is largely absorbed by ideal points; identify with whip counts or party switchers.',
        'Different chambers are conditionally independent given member probabilities; committee and floor votes of a member coincide.',
    ],
})

YEA_CODES, NAY_CODES = {1, 2, 3}, {4, 5, 6}


def from_voteview(votes, members, rollcalls):
    """Convert Voteview CSV rows (dicts) into the fit data contract; codes 7-9/0 become missing."""
    names = {}
    for row in members:
        key = f"{row['congress']}:{row['chamber']}:{row['icpsr']}"
        names[str(row['icpsr'])] = {'id': str(row['icpsr']), 'party': str(row.get('party_code', '')),
                                   'name': row.get('bioname'), 'chamber': row.get('chamber')}
    calls = {f"{r['congress']}:{r['chamber']}:{r['rollnumber']}": {'id': f"{r['congress']}:{r['chamber']}:{r['rollnumber']}",
                                                                    'date': r['date'], 'bill': r.get('bill_number')}
             for r in rollcalls}
    out = []
    for row in votes:
        code = int(float(row['cast_code']))
        if code not in YEA_CODES | NAY_CODES:
            continue
        call = f"{row['congress']}:{row['chamber']}:{row['rollnumber']}"
        if call not in calls:
            raise ValueError(f'Vote references unknown roll call {call}')
        out.append({'member': str(row['icpsr']), 'rollcall': call, 'vote': 1 if code in YEA_CODES else 0})
    return {'members': list(names.values()), 'rollcalls': list(calls.values()), 'votes': out}


def _screen(data, cutoff, lop, min_votes):
    rollcalls, window = apply_cutoff(data['rollcalls'], cutoff, label='rollcalls')
    allowed = {r['id'] for r in rollcalls}
    parties = {m['id']: m.get('party') for m in data['members']}
    votes = {}
    for row in data['votes']:
        if row['rollcall'] not in allowed or row.get('vote') is None:
            continue
        if row['member'] not in parties:
            raise ValueError(f'Vote references unknown member {row["member"]}')
        if row['vote'] not in (0, 1):
            raise ValueError('Votes must be 1 (yea), 0 (nay) or null')
        votes[(row['member'], row['rollcall'])] = row['vote']
    dropped_lopsided, dropped_members = set(), set()
    while True:
        by_call, by_member = {}, {}
        for (m, c), v in votes.items():
            by_call.setdefault(c, []).append(v)
            by_member[m] = by_member.get(m, 0) + 1
        bad_calls = {c for c, vs in by_call.items() if min(sum(vs), len(vs) - sum(vs)) < max(1, lop * len(vs))}
        bad_members = {m for m, count in by_member.items() if count < min_votes}
        if not bad_calls and not bad_members:
            break
        dropped_lopsided |= bad_calls
        dropped_members |= bad_members
        votes = {k: v for k, v in votes.items() if k[1] not in bad_calls and k[0] not in bad_members}
    if not votes:
        raise ValueError('No informative votes remain after cutoff, lopsided and minimum-vote screens')
    return votes, parties, window, sorted(dropped_lopsided), sorted(dropped_members)


def _power_init(members, calls, votes, dims):
    mi = {m: i for i, m in enumerate(members)}
    ci = {c: j for j, c in enumerate(calls)}
    means = [0.0] * len(calls)
    counts = [0] * len(calls)
    for (m, c), v in votes.items():
        means[ci[c]] += v
        counts[ci[c]] += 1
    means = [s / n for s, n in zip(means, counts)]
    entries = [(mi[m], ci[c], v - means[ci[c]]) for (m, c), v in votes.items()]
    vectors = []
    for d in range(dims):
        u = [math.sin(1.0 + 7.3 * i * (d + 1)) for i in range(len(members))]
        for _ in range(200):
            w = [0.0] * len(calls)
            for i, j, value in entries:
                w[j] += value * u[i]
            nxt = [0.0] * len(members)
            for i, j, value in entries:
                nxt[i] += value * w[j]
            for prev in vectors:
                dot = sum(a * b for a, b in zip(nxt, prev))
                nxt = [a - dot * b for a, b in zip(nxt, prev)]
            norm = math.sqrt(sum(a * a for a in nxt)) or 1.0
            nxt = [a / norm for a in nxt]
            if max(abs(a - b) for a, b in zip(nxt, u)) < 1e-9:
                u = nxt
                break
            u = nxt
        vectors.append(u)
    return [[vectors[d][i] for d in range(dims)] for i in range(len(members))]


def _standardize(x, a, b, dims):
    n = len(x)
    for d in range(dims):
        mean = sum(row[d] for row in x) / n
        sd = math.sqrt(sum((row[d] - mean) ** 2 for row in x) / n) or 1.0
        for row in x:
            row[d] = (row[d] - mean) / sd
        for j in range(len(a)):
            a[j] += b[j][d] * mean
            b[j][d] *= sd


def _newton(rows, theta, prior_precision):
    """One Newton step for a logistic model sum over rows (z, y, offset) with Gaussian prior."""
    size = len(theta)
    grad = [-prior_precision[k] * theta[k] for k in range(size)]
    hess = [[prior_precision[r] if r == c else 0.0 for c in range(size)] for r in range(size)]
    for z, y, offset in rows:
        p = logistic(offset + sum(t * v for t, v in zip(theta, z)))
        w = p * (1 - p)
        for r in range(size):
            grad[r] += (y - p) * z[r]
            wr = w * z[r]
            for c in range(size):
                hess[r][c] += wr * z[c]
    step = solve(hess, grad)
    biggest = max(abs(s) for s in step)
    scale = 1.0 if biggest <= 1.0 else 1.0 / biggest
    return [t + scale * s for t, s in zip(theta, step)]


def estimate_ideal_points(data, *, dims=1, cutoff=None, lop=0.025, min_votes=20, prior_sd=1.0, bill_prior_sd=5.0,
                          anchors=None, max_iter=200, tol=1e-7, backend='auto'):
    dims = integer(dims, 'dimensions', 1, 2)
    votes, parties, window, dropped_calls, dropped_members = _screen(data, cutoff, lop, min_votes)
    members = sorted({m for m, _ in votes})
    calls = sorted({c for _, c in votes})
    if len(members) <= dims + 1 or len(calls) <= dims:
        raise ValueError('Too few legislators or roll calls to scale')
    x = _power_init(members, calls, votes, dims)
    a = [0.0] * len(calls)
    b = [[0.0] * dims for _ in calls]
    _standardize(x, a, b, dims)
    mi = {m: i for i, m in enumerate(members)}
    ci = {c: j for j, c in enumerate(calls)}
    by_member = [[] for _ in members]
    by_call = [[] for _ in calls]
    for (m, c), v in votes.items():
        by_member[mi[m]].append((ci[c], v))
        by_call[ci[c]].append((mi[m], v))
    use_numpy = NUMPY is not None and backend in ('auto', 'numpy')
    if backend == 'numpy' and NUMPY is None:
        raise ValueError('numpy backend requested but numpy is unavailable')
    xp = [1.0 / prior_sd ** 2] * dims
    bp = [1.0 / bill_prior_sd ** 2] * (dims + 1)
    previous, converged, iteration = -math.inf, False, 0
    if use_numpy:
        x, a, b, iteration, converged = _numpy_sweeps(x, a, b, by_member, len(calls), dims, xp[0], bp[0], max_iter, tol)
    else:
        for iteration in range(1, max_iter + 1):
            for j, rows in enumerate(by_call):
                theta = _newton([([1.0] + x[i], v, 0.0) for i, v in rows], [a[j]] + b[j], bp)
                a[j], b[j] = theta[0], theta[1:]
            for i, rows in enumerate(by_member):
                x[i] = _newton([(b[j], v, a[j]) for j, v in rows], x[i], xp)
            _standardize(x, a, b, dims)
            post = _log_posterior(x, a, b, votes, mi, ci, xp, bp)
            if abs(post - previous) <= tol * (1 + abs(post)):
                converged = True
                break
            previous = post
    anchors = anchors or []
    orientation = []
    for d in range(dims):
        if d < len(anchors) and anchors[d] is not None:
            if anchors[d] not in mi:
                raise ValueError(f'Anchor {anchors[d]} was not scaled')
            sign = 1 if x[mi[anchors[d]]][d] >= 0 else -1
            orientation.append({'dimension': d, 'anchor': anchors[d], 'rule': 'anchor_positive'})
        else:
            first = next((row[d] for row in x if abs(row[d]) > 1e-12), 1.0)
            sign = -1 if first > 0 else 1
            orientation.append({'dimension': d, 'anchor': members[0], 'rule': 'first_member_negative_default'})
        if sign < 0:
            for row in x:
                row[d] = -row[d]
            for row in b:
                row[d] = -row[d]
    loglik, correct, minority, total = 0.0, 0, 0, 0
    call_votes = {}
    for (m, c), v in votes.items():
        i, j = mi[m], ci[c]
        p = logistic(a[j] + sum(bb * xx for bb, xx in zip(b[j], x[i])))
        loglik += math.log(max(p if v else 1 - p, 1e-300))
        correct += int((p >= 0.5) == bool(v))
        total += 1
        call_votes.setdefault(c, []).append(v)
    minority = sum(min(sum(vs), len(vs) - sum(vs)) for vs in call_votes.values())
    errors = total - correct
    return {'members': members, 'rollcalls': calls, 'ideal_points': {m: x[mi[m]] for m in members},
            'bill_parameters': {c: {'a': a[ci[c]], 'b': b[ci[c]]} for c in calls}, 'parties': {m: parties.get(m) for m in members},
            'diagnostics': {'loglik': loglik, 'n_votes': total, 'classification_accuracy': correct / total,
                            'apre': (minority - errors) / minority if minority else None,
                            'geometric_mean_probability': math.exp(loglik / total), 'iterations': iteration,
                            'converged': converged, 'backend': 'numpy' if use_numpy else 'python',
                            'dropped_lopsided_rollcalls': len(dropped_calls), 'dropped_members': dropped_members,
                            'orientation': orientation},
            'window': window, 'votes': votes}


def _log_posterior(x, a, b, votes, mi, ci, xp, bp):
    total = 0.0
    for (m, c), v in votes.items():
        i, j = mi[m], ci[c]
        eta = a[j] + sum(bb * xx for bb, xx in zip(b[j], x[i]))
        total += v * eta - (eta + math.log1p(math.exp(-eta)) if eta > 0 else math.log1p(math.exp(eta)))
    total -= 0.5 * sum(xp[d] * row[d] ** 2 for row in x for d in range(len(xp)))
    total -= 0.5 * sum(bp[0] * aj ** 2 for aj in a) + 0.5 * sum(bp[1] * v ** 2 for row in b for v in row)
    return total


def _numpy_sweeps(x, a, b, by_member, n_calls, dims, x_precision, b_precision, max_iter, tol):  # pragma: no cover - optional
    np = NUMPY
    n = len(x)
    Y = np.zeros((n, n_calls))
    M = np.zeros((n, n_calls))
    for i, rows in enumerate(by_member):
        for j, v in rows:
            Y[i, j] = v
            M[i, j] = 1.0
    X = np.asarray(x, float)
    A = np.asarray(a, float)
    B = np.asarray(b, float)
    eye_b = np.eye(dims + 1) * b_precision
    eye_x = np.eye(dims) * x_precision
    previous, converged, iteration = -np.inf, False, 0

    def capped(step):
        biggest = np.abs(step).max(axis=1, keepdims=True)
        return step * np.where(biggest > 1, 1 / np.maximum(biggest, 1e-300), 1)

    for iteration in range(1, max_iter + 1):
        Z = np.hstack([np.ones((n, 1)), X])
        theta = np.hstack([A[:, None], B])
        P = 1 / (1 + np.exp(-(Z @ theta.T)))
        R = M * (Y - P)
        W = M * P * (1 - P)
        grad = R.T @ Z - theta * b_precision
        hess = np.einsum('ij,ik,il->jkl', W, Z, Z) + eye_b
        theta = theta + capped(np.linalg.solve(hess, grad[..., None])[..., 0])
        A, B = theta[:, 0], theta[:, 1:]
        P = 1 / (1 + np.exp(-(A[None, :] + X @ B.T)))
        R = M * (Y - P)
        W = M * P * (1 - P)
        grad = R @ B - X * x_precision
        hess = np.einsum('ij,jk,jl->ikl', W, B, B) + eye_x
        X = X + capped(np.linalg.solve(hess, grad[..., None])[..., 0])
        mean, sd = X.mean(axis=0), X.std(axis=0)
        sd[sd == 0] = 1
        A = A + B @ mean
        B = B * sd
        X = (X - mean) / sd
        eta = A[None, :] + X @ B.T
        post = float((M * (Y * eta - np.logaddexp(0, eta))).sum() - 0.5 * x_precision * (X ** 2).sum()
                     - 0.5 * b_precision * ((A ** 2).sum() + (B ** 2).sum()))
        if abs(post - previous) <= tol * (1 + abs(post)):
            converged = True
            break
        previous = post
    return X.tolist(), A.tolist(), B.tolist(), iteration, converged


def estimate_discipline(votes, parties, ideal_points, bill_parameters, max_iter=50):
    """Logistic slope on the leave-one-out party-majority signal with the spatial prediction as offset."""
    tallies = {}
    for (m, c), v in votes.items():
        key = (parties.get(m), c)
        yes, count = tallies.get(key, (0, 0))
        tallies[key] = (yes + v, count + 1)
    rows = []
    for (m, c), v in votes.items():
        yes, count = tallies[(parties.get(m), c)]
        yes, count = yes - v, count - 1
        if count <= 0 or parties.get(m) in (None, ''):
            continue
        signal = 1.0 if yes * 2 > count else -1.0 if yes * 2 < count else 0.0
        bill = bill_parameters[c]
        offset = bill['a'] + sum(bb * xx for bb, xx in zip(bill['b'], ideal_points[m]))
        rows.append(([signal], v, offset))
    if not rows or all(z[0] == 0 for z, _, _ in rows):
        return {'discipline': 0.0, 'standard_error': None, 'n': len(rows), 'identified': False}
    theta = [0.0]
    for _ in range(max_iter):
        new = _newton(rows, theta, [1e-6])
        if abs(new[0] - theta[0]) < 1e-10:
            theta = new
            break
        theta = new
    info = sum(logistic(o + theta[0] * z[0]) * (1 - logistic(o + theta[0] * z[0])) * z[0] ** 2 for z, _, o in rows)
    return {'discipline': theta[0], 'standard_error': 1 / math.sqrt(info) if info > 0 else None, 'n': len(rows),
            'identified': True, 'caveat': 'In-sample; conditional on ideal points that already absorb party-line voting.'}


def fit(data, cutoff=None):
    options = dict(data.get('options', {}))
    params = merged_parameters(FAMILY, {k: v for k, v in options.items() if k in FAMILY['parameters']})
    result = estimate_ideal_points(data, dims=params['dimensions'], cutoff=cutoff, lop=params['lopsided_threshold'],
                                   min_votes=params['min_votes'], prior_sd=params['prior_sd'],
                                   anchors=options.get('anchors'), max_iter=options.get('max_iter', 200),
                                   backend=options.get('backend', 'auto'))
    discipline = estimate_discipline(result['votes'], result['parties'], result['ideal_points'], result['bill_parameters'])
    estimate = {'ideal_points': result['ideal_points'], 'bill_parameters': result['bill_parameters'],
                'dimensions': params['dimensions'], 'discipline': discipline['discipline'], 'parties': result['parties']}
    diagnostics = dict(result['diagnostics'], discipline=discipline)
    return fit_result(estimate, diagnostics, method='alternating_newton_map_logistic_ideal_points',
                      identification=FAMILY['identification'], windows=[result['window']], data=data,
                      family_id=FAMILY['id'], requirements=[r['id'] for r in FAMILY['requirements']])


# ----------------------------------------------------------------------------- prediction

def _threshold(rule, voters):
    rule = rule or {'type': 'majority'}
    kind = rule.get('type', 'majority')
    if kind == 'majority':
        return voters // 2 + 1
    if kind == 'fraction':
        return math.ceil(finite(rule['value'], 'threshold fraction', 0, 1) * voters - 1e-12)
    if kind == 'count':
        return integer(rule['value'], 'threshold count', 0, voters)
    raise ValueError('Threshold type must be majority, fraction or count')


def _remove_trial(dist, p):
    """Distribution of successes excluding one Bernoulli(p) trial (numerically stable direction)."""
    n = len(dist) - 1
    out = [0.0] * n
    if p <= 0.5:
        q = 1 - p
        out[0] = dist[0] / q
        for k in range(1, n):
            out[k] = (dist[k] - p * out[k - 1]) / q
    else:
        out[n - 1] = dist[n] / p
        for k in range(n - 1, 0, -1):
            out[k - 1] = (dist[k] - (1 - p) * out[k]) / p
    return [max(0.0, v) for v in out]


def member_probabilities(config):
    """Yea probabilities for every member given spatial bill, coalition commitments and discipline."""
    params = merged_parameters(FAMILY, config.get('parameters'))
    members = config['members']
    bill = config['bill']
    coalition = config.get('coalition', {})
    committed_yea, committed_nay = set(coalition.get('yea', [])), set(coalition.get('nay', []))
    if committed_yea & committed_nay:
        raise ValueError('A member cannot be committed to both yea and nay')
    ids = [m['id'] for m in members]
    if len(ids) != len(set(ids)):
        raise ValueError('Member IDs must be unique')
    dims = None

    def utility_gap(point):
        if 'a' in bill:
            return None, bill['a'] + sum(finite(bb, 'b') * finite(xx, 'ideal point') for bb, xx in zip(bill['b'], point))
        p, q = bill['proposal'], bill['status_quo']
        if not len(p) == len(q) == len(point):
            raise ValueError('Proposal, status quo and ideal points must share dimensions')
        gap = sum(2 * xx * (pp - qq) for xx, pp, qq in zip(point, p, q)) - (sum(v * v for v in p) - sum(v * v for v in q))
        return gap, params['salience'] * gap

    spatial = {}
    for m in members:
        point = m['ideal_point']
        if dims is None:
            dims = len(point)
        if len(point) != dims:
            raise ValueError('All ideal points must share dimensions')
        spatial[m['id']] = utility_gap(point)[1]
    whip = dict(config.get('party_positions', {}))
    by_party = {}
    for m in members:
        by_party.setdefault(m.get('party'), []).append(spatial[m['id']])
    for party, gaps in by_party.items():
        if party is None or party in whip:
            continue
        ordered = sorted(gaps)
        median = ordered[len(ordered) // 2] if len(ordered) % 2 else 0.5 * (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2])
        whip[party] = 'yea' if median > 0 else 'nay' if median < 0 else 'free'
    probabilities = {}
    for m in members:
        if m['id'] in committed_yea:
            probabilities[m['id']] = 1.0
            continue
        if m['id'] in committed_nay:
            probabilities[m['id']] = 0.0
            continue
        signal = {'yea': 1.0, 'nay': -1.0, 'free': 0.0}[whip.get(m.get('party'), 'free')] if m.get('party') is not None else 0.0
        probabilities[m['id']] = logistic(spatial[m['id']] + params['discipline'] * signal)
    return probabilities, whip, params


def _chamber_passage(chamber, probabilities, all_ids):
    ids = chamber.get('members', all_ids)
    unknown = set(ids) - set(probabilities)
    if unknown:
        raise ValueError(f'Chamber references unknown members: {sorted(unknown)[:5]}')
    need = _threshold(chamber.get('threshold'), len(ids))
    ps = [probabilities[i] for i in ids]
    dist = poisson_binomial(ps)
    floor = sum(dist[need:])
    pivotal = {}
    for i, p in zip(ids, ps):
        without = _remove_trial(dist, p) if 0 < p < 1 else None
        pivotal[i] = without[need - 1] if without is not None and 0 <= need - 1 < len(without) else 0.0
    gate, joint = 1.0, floor
    committee = chamber.get('committee')
    if committee:
        cm = committee['members']
        chair = committee.get('chair')
        if chair is not None and chair not in cm:
            raise ValueError('Committee chair must be a committee member')
        if set(cm) - set(ids):
            raise ValueError('Committee members must belong to the chamber')
        cneed = _threshold(committee.get('threshold'), len(cm))
        veto = committee.get('chair_veto', True)
        others = [probabilities[i] for i in cm if i != chair]
        odist = poisson_binomial(others)
        cset = set(cm)
        ndist = poisson_binomial([probabilities[i] for i in ids if i not in cset])
        tail = [0.0] * (len(ndist) + 1)
        for k in range(len(ndist) - 1, -1, -1):
            tail[k] = tail[k + 1] + ndist[k]
        at_least_nc = lambda k: tail[max(k, 0)] if k < len(tail) else 0.0
        gate = joint = 0.0
        chair_cases = [(1, 1.0)] if chair is None else [(1, probabilities[chair]), (0, 1 - probabilities[chair])]
        for chair_yes, weight in chair_cases:
            if weight == 0:
                continue
            base = chair_yes if chair is not None else 0
            for k, mass in enumerate(odist):
                reported = base + k >= cneed and (chair is None or not veto or chair_yes)
                if reported:
                    gate += weight * mass
                    joint += weight * mass * at_least_nc(need - base - k)
    return {'chamber': chamber['id'], 'voters': len(ids), 'threshold': need, 'expected_yeas': sum(ps),
            'floor_passage_probability': floor, 'committee_report_probability': gate,
            'passage_probability': joint, 'yea_distribution': dist,
            'most_pivotal': sorted(pivotal.items(), key=lambda kv: (-kv[1], kv[0]))[:10]}


def simulate(config, mode='deterministic', seed=0):
    probabilities, whip, params = member_probabilities(config)
    ids = [m['id'] for m in config['members']]
    chambers = config.get('chambers') or [{'id': 'chamber'}]
    report = {'family': FAMILY['id'], 'mode': mode, 'validated': False, 'whip_positions': whip,
              'member_yea_probability': probabilities, 'parameters_used': {k: params[k] for k in ('salience', 'discipline')}}
    analytic = [_chamber_passage(chamber, probabilities, ids) for chamber in chambers]
    report['chambers'] = [{k: v for k, v in row.items() if k != 'yea_distribution'} for row in analytic]
    report['passage_probability'] = math.prod(row['passage_probability'] for row in analytic)
    if mode == 'stochastic':
        rng = rng_from(seed)
        runs = integer(config.get('n_simulations', 1000), 'n_simulations', 1, 200000)
        if runs * len(ids) * len(chambers) > 5_000_000:
            raise ValueError('Legislative simulation work budget exceeded')
        passed, first = 0, None
        for run in range(runs):
            votes = {i: int(rng.random() < probabilities[i]) for i in ids}
            outcome = True
            for chamber in chambers:
                members = chamber.get('members', ids)
                committee = chamber.get('committee')
                if committee:
                    cm = committee['members']
                    yes = sum(votes[i] for i in cm)
                    reported = yes >= _threshold(committee.get('threshold'), len(cm))
                    if committee.get('chair') is not None and committee.get('chair_veto', True):
                        reported = reported and votes[committee['chair']] == 1
                    outcome = outcome and reported
                outcome = outcome and sum(votes[i] for i in members) >= _threshold(chamber.get('threshold'), len(members))
            passed += outcome
            if first is None:
                first = {'votes': votes, 'passed': outcome}
        report.update(simulated_passage_frequency=passed / runs, n_simulations=runs, example_realization=first,
                      monte_carlo_standard_error=math.sqrt(max(passed / runs * (1 - passed / runs), 0) / runs))
    elif mode != 'deterministic':
        raise ValueError('mode must be deterministic or stochastic')
    report['assumptions'] = ['Conditional independence of member votes given spatial position and whip signal.',
                             'Committee members vote identically in committee and on the floor (exact joint gate); chambers are treated as independent.']
    return report


def synthetic(seed=0, members=60, rollcalls=150, dims=1):
    rng = rng_from(seed)
    truth, rows, votes = {}, [], []
    member_rows = []
    for i in range(members):
        party = 'R' if i % 2 else 'D'
        point = [rng.gauss(1.0 if party == 'R' else -1.0, 0.5)] + [rng.gauss(0, 1) for _ in range(dims - 1)]
        truth[f'm{i:03d}'] = point
        member_rows.append({'id': f'm{i:03d}', 'party': party})
    bills = {}
    for j in range(rollcalls):
        cid = f'rc{j:04d}'
        b = [rng.gauss(0, 2.5) for _ in range(dims)]
        a = rng.gauss(0, 1.0)
        bills[cid] = {'a': a, 'b': b}
        rows.append({'id': cid, 'date': f'20{10 + j * 10 // rollcalls:02d}-{1 + j % 12:02d}-15'})
        for m, point in truth.items():
            p = logistic(a + sum(bb * xx for bb, xx in zip(b, point)))
            votes.append({'member': m, 'rollcall': cid, 'vote': int(rng.random() < p)})
    data = {'members': member_rows, 'rollcalls': rows, 'votes': votes, 'options': {'dimensions': dims, 'anchors': ['m001']}}
    config = {'members': [dict(row, ideal_point=truth[row['id']]) for row in member_rows],
              'bill': {'proposal': [0.3] + [0.0] * (dims - 1), 'status_quo': [-0.8] + [0.0] * (dims - 1)},
              'chambers': [{'id': 'house', 'threshold': {'type': 'majority'},
                            'committee': {'members': [r['id'] for r in member_rows[:9]], 'chair': member_rows[1]['id']}}],
              'parameters': {'salience': 3.0, 'discipline': 0.5}, 'n_simulations': 500}
    return {'data': data, 'truth': {'ideal_points': truth, 'bill_parameters': bills}, 'config': config}


# ----------------------------------------------------------------------------- holdout forecaster

REVEALED_SHARE = 'rollcall_revealed_share'


def revealed_members(members, options=None):
    """Members whose votes on a held-out roll call are treated as known (default: every third member by id)."""
    options = options or {}
    if options.get('holdout_revealed_members') is not None:
        return {str(m) for m in options['holdout_revealed_members']}
    ordered = sorted(str(m['id']) for m in members)
    return {m for index, m in enumerate(ordered) if index % 3 == 0}


def _party_signal(votes, parties, member):
    party = parties.get(member)
    if party in (None, ''):
        return 0.0
    same = [v for m, v in votes.items() if m != member and parties.get(m) == party]
    yes = sum(same)
    return 1.0 if 2 * yes > len(same) else -1.0 if 2 * yes < len(same) else 0.0


def _holdout_forecast(parameters, history, rows, data, bill_prior_sd=5.0, max_iter=50):
    """Held-out roll-call votes conditional on the votes of revealed members on that roll call.

    Ideal points and discipline come from the fit at the origin. The new roll call's
    (a, b) are MAP estimates from revealed members only (N(0, bill_prior_sd^2) prior);
    predicted yea probabilities for the other members integrate the Laplace posterior
    of (a, b) with the probit approximation logistic(mu / sqrt(1 + pi var / 8)).
    """
    ideal = parameters['ideal_points']
    parties = {str(k): v for k, v in (parameters.get('parties') or {}).items()}
    discipline = float(parameters.get('discipline') or 0.0)
    dims = len(next(iter(ideal.values()))) if ideal else 0
    revealed = revealed_members(data['members'], data.get('options'))
    wanted = {r['id'] for r in rows}
    past_ids = [r['id'] for r in history]
    past_set = set(past_ids)
    by_call = {}
    for vote in data['votes']:
        if vote.get('vote') in (0, 1) and (vote['rollcall'] in wanted or vote['rollcall'] in past_set):
            by_call.setdefault(vote['rollcall'], {})[str(vote['member'])] = vote['vote']
    past = {}
    for call in past_ids:
        for member, value in by_call.get(call, {}).items():
            past.setdefault(member, []).append(value)
    precision = [1.0 / bill_prior_sd ** 2] * (dims + 1)
    out = []
    for call in sorted(wanted):
        votes = by_call.get(call, {})
        known = {m: v for m, v in votes.items() if m in revealed}
        fitting = [([1.0] + list(ideal[m]), v, discipline * _party_signal(known, parties, m)) for m, v in known.items() if m in ideal]
        if len(fitting) < dims + 3:
            continue
        theta = [0.0] * (dims + 1)
        for _ in range(max_iter):
            new = _newton(fitting, theta, precision)
            done = max(abs(a - b) for a, b in zip(new, theta)) < 1e-9
            theta = new
            if done:
                break
        hess = [[precision[r] if r == c else 0.0 for c in range(dims + 1)] for r in range(dims + 1)]
        for z, _, offset in fitting:
            p = logistic(offset + sum(t * v for t, v in zip(theta, z)))
            for r in range(dims + 1):
                for c in range(dims + 1):
                    hess[r][c] += p * (1 - p) * z[r] * z[c]
        share = (sum(known.values()) + 0.5) / (len(known) + 1)
        for member in sorted(votes):
            if member in revealed or member not in ideal:
                continue
            z = [1.0] + list(ideal[member])
            variance = sum(a * b for a, b in zip(z, solve(hess, z)))
            mean = sum(t * v for t, v in zip(theta, z)) + discipline * _party_signal(known, parties, member)
            p = logistic(mean / math.sqrt(1 + math.pi * variance / 8))
            out.append({'target': f'vote:{call}:{member}', 'actual': votes[member], 'mean': p, 'sd': math.sqrt(p * (1 - p)),
                        'history_values': past.get(member, []),
                        'baselines': {REVEALED_SHARE: {'mean': share, 'sd': math.sqrt(share * (1 - share))}}})
    return out


holdout_forecaster = {
    'rows_key': 'rollcalls', 'time_key': 'date', 'target': 'rollcall_vote', 'forecast': _holdout_forecast,
    'fit_keys': ['members', 'rollcalls', 'votes', 'options', 'information_time', 'revisions'],
    'conditional_inputs': ['revealed_member_votes_on_target_rollcall'], 'probability': True, 'baselines': [REVEALED_SHARE],
    'description': 'Yea probability of unrevealed members on held-out roll calls given revealed members\' votes; Brier/log score '
                   'against the member base rate and the revealed yea share.',
}
