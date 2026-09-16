"""Bounded USD obligation stress with proportional net clearing, not default forecasting.

Scale and backends. Size and work are bounded by the named limits
``exposure_max_entities``, ``exposure_max_obligations``,
``exposure_max_clearing_iterations`` and ``exposure_max_clearing_work`` (see
worldmodel.limits; raise per call with ``limits={...}``). Clearing runs on index
arrays. ``backend='python'`` is the reference; ``backend='numpy'`` (or ``'auto'``
for large networks when numpy is installed) is bit-identical: every elementwise
operation has the same order and incoming payments are accumulated in obligation
order. ``detail='summary'`` returns aggregate totals and the largest entity
shortfalls instead of one dict per entity and obligation.
``stress_exposure_arrays`` accepts array-native networks (million-actor scale).
"""
from datetime import date
import heapq
import math

from .backends import load_numpy, resolve_backend
from .limits import LimitExceeded, resolve_limits

TOLERANCE = 1e-9
MODES = ('baseline', 'stressed')


def _assumptions():
    return ['Supplied obligations and initial cash are scenario assumptions.',
            'ACT/365 simple interest; floating shock applies immediately over entire horizon.',
            'One terminal proportional net settlement; cyclic obligations may offset without gross cash.',
            'No collateral priority, bankruptcy recovery, interim payments, FX, or new funding.',
            'Liquidity shortfall is not a calibrated default probability.']


def _number(value, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError('Expected finite ' + ('nonnegative ' if nonnegative else '') + 'number')
    return float(value)


def _horizon(as_of, end):
    start, finish = date.fromisoformat(as_of), date.fromisoformat(end)
    if not start < finish or (finish - start).days > 3650:
        raise ValueError('Horizon must be positive and at most ten years')
    return start, finish


def _sizes(entities, obligations, limits):
    if entities < 1:
        raise ValueError('Exposure size budget exceeded')
    limits.check('exposure_max_entities', entities, 'Exposure size budget exceeded: entities')
    limits.check('exposure_max_obligations', obligations, 'Exposure size budget exceeded: obligations')


def _options(detail, top):
    if detail not in ('full', 'summary'):
        raise ValueError("detail must be 'full' or 'summary'")
    if type(top) is not int or top < 0:
        raise ValueError('top must be a nonnegative integer')


def _iterations_exceeded(limits):
    maximum = limits.exposure_max_clearing_iterations
    return LimitExceeded('exposure_max_clearing_iterations', f'more than {maximum} iterations', maximum,
                         'Clearing failed to converge')


def _clear_python(cash, borrower, lender, row_due, limits, work):
    n = len(cash)
    due = [0.0] * n
    for b, d in zip(borrower, row_due):
        due[b] += d
    if not all(math.isfinite(v) for v in due) or not math.isfinite(sum(cash)):
        raise ValueError('Aggregate financial state overflow')
    ratio = [d / due[b] if due[b] else 0 for b, d in zip(borrower, row_due)]
    paid = list(due)
    step = len(row_due) + n
    for iteration in range(limits.exposure_max_clearing_iterations):
        work[0] += step
        limits.check('exposure_max_clearing_work', work[0], 'Clearing work budget exceeded before convergence')
        incoming = [0.0] * n
        for b, l, r in zip(borrower, lender, ratio):
            incoming[l] += paid[b] * r
        updated = [min(d, c + i) for d, c, i in zip(due, cash, incoming)]
        error = max(abs(u - p) for u, p in zip(updated, paid))
        paid = updated
        if error <= TOLERANCE:
            break
    else:
        raise _iterations_exceeded(limits)
    payments = [paid[b] * r for b, r in zip(borrower, ratio)]
    incoming = [0.0] * n
    for l, payment in zip(lender, payments):
        incoming[l] += payment
    ending = [c + i - p for c, i, p in zip(cash, incoming, paid)]
    shortfall = [d - p for d, p in zip(row_due, payments)]
    entity_shortfall = [d - p for d, p in zip(due, paid)]
    residual = sum(ending) - sum(cash)
    return {'iterations': iteration + 1, 'due': due, 'paid': paid, 'received': incoming, 'ending_cash': ending,
            'entity_shortfall': entity_shortfall, 'obligation_paid': payments, 'obligation_shortfall': shortfall,
            'residual': residual, 'minimum_ending': min(ending), 'total_shortfall': sum(shortfall),
            'cash_total': sum(cash), 'total_due': sum(row_due), 'total_paid': sum(payments),
            'entities_with_shortfall': sum(1 for v in entity_shortfall if v > 0),
            'obligations_with_shortfall': sum(1 for v in shortfall if v > 0)}


def _sequential_sum(np, values):
    """Python's builtin sum over the same order (compensated since Python 3.12), for identity."""
    return sum(values.tolist())


def _clear_numpy(cash, borrower, lender, row_due, limits, work):
    np = load_numpy()
    n = len(cash)
    due = np.bincount(borrower, weights=row_due, minlength=n).astype(np.float64, copy=False)
    cash_total = _sequential_sum(np, cash)
    if not np.isfinite(due).all() or not math.isfinite(cash_total):
        raise ValueError('Aggregate financial state overflow')
    owed = due[borrower]
    ratio = np.zeros(len(row_due), dtype=np.float64)
    np.divide(row_due, owed, out=ratio, where=owed != 0)
    del owed
    paid = due.copy()
    step = len(row_due) + n
    for iteration in range(limits.exposure_max_clearing_iterations):
        work[0] += step
        limits.check('exposure_max_clearing_work', work[0], 'Clearing work budget exceeded before convergence')
        incoming = np.bincount(lender, weights=paid[borrower] * ratio, minlength=n).astype(np.float64, copy=False)
        updated = np.minimum(due, cash + incoming)
        error = float(np.max(np.abs(updated - paid)))
        paid = updated
        if error <= TOLERANCE:
            break
    else:
        raise _iterations_exceeded(limits)
    payments = paid[borrower] * ratio
    incoming = np.bincount(lender, weights=payments, minlength=n).astype(np.float64, copy=False)
    ending = cash + incoming - paid
    shortfall = row_due - payments
    entity_shortfall = due - paid
    return {'iterations': iteration + 1, 'due': due, 'paid': paid, 'received': incoming, 'ending_cash': ending,
            'entity_shortfall': entity_shortfall, 'obligation_paid': payments, 'obligation_shortfall': shortfall,
            'residual': _sequential_sum(np, ending) - cash_total, 'minimum_ending': float(np.min(ending)),
            'total_shortfall': _sequential_sum(np, shortfall), 'cash_total': cash_total,
            'total_due': _sequential_sum(np, row_due), 'total_paid': _sequential_sum(np, payments),
            'entities_with_shortfall': int(np.count_nonzero(entity_shortfall > 0)),
            'obligations_with_shortfall': int(np.count_nonzero(shortfall > 0))}


def _check(result, n):
    if result['minimum_ending'] < -TOLERANCE * 2 or abs(result['residual']) > max(TOLERANCE * n, abs(result['cash_total']) * 1e-12):
        raise ValueError('Clearing conservation or nonnegative cash check failed')


def _top(result, backend, top, entity_ids):
    if not top:
        return []
    values = result['entity_shortfall']
    if backend == 'numpy':
        np = load_numpy()
        positive = np.flatnonzero(values > 0)
        if len(positive) > top:
            threshold = np.partition(values[positive], len(positive) - top)[len(positive) - top]
            positive = positive[values[positive] >= threshold]
        order = positive[np.lexsort((positive, -values[positive]))][:top].tolist()
    else:
        order = heapq.nsmallest(top, (i for i, v in enumerate(values) if v > 0), key=lambda i: (-values[i], i))
    label = (lambda i: entity_ids[i]) if entity_ids is not None else (lambda i: i)
    return [{'id': label(i), 'due': float(result['due'][i]), 'paid': float(result['paid'][i]),
             'shortfall': float(values[i])} for i in order]


def _summary(result, backend, top, entity_ids, obligations, arrays):
    summary = {'detail': 'summary', 'iterations': result['iterations'], 'total_shortfall': result['total_shortfall'],
               'cash_conservation_residual': result['residual'], 'entity_count': len(result['due']),
               'obligation_count': obligations, 'total_due': result['total_due'], 'total_paid': result['total_paid'],
               'entities_with_shortfall': result['entities_with_shortfall'],
               'obligations_with_shortfall': result['obligations_with_shortfall'],
               'largest_entity_shortfalls': _top(result, backend, top, entity_ids)}
    if arrays:
        summary['arrays'] = {key: result[key] for key in ('due', 'paid', 'received', 'ending_cash', 'obligation_paid', 'obligation_shortfall')}
    return summary


def _run(cash, borrower, lender, dues, *, backend, detail, top, limits, entity_ids=None, full=None, arrays=False):
    clear = _clear_numpy if backend == 'numpy' else _clear_python
    work = [0]
    outputs = {}
    for mode in MODES:
        result = clear(cash, borrower, lender, dues[mode], limits, work)
        _check(result, len(cash))
        outputs[mode] = full(result, dues[mode]) if detail == 'full' else _summary(result, backend, top, entity_ids, len(dues[mode]), arrays)
    return {'baseline': outputs['baseline'], 'stressed': outputs['stressed'],
            'incremental_shortfall': outputs['stressed']['total_shortfall'] - outputs['baseline']['total_shortfall'],
            'execution': {'backend': backend, 'clearing_work': work[0], 'detail': detail},
            'epistemic_status': 'synthetic_scenario', 'causally_calibrated': False, 'assumptions': _assumptions()}


def stress_exposures(config, *, backend=None, detail='full', top=10, limits=None):
    """Clear baseline and stressed obligations; see module docstring for scale options."""
    limits = resolve_limits(limits)
    _options(detail, top)
    start, end = _horizon(config['as_of'], config['end'])
    if config.get('currency') != 'USD': raise ValueError('Only explicit USD obligations supported; no implicit FX conversion')
    actors, obligations = config['entities'], config['obligations']
    _sizes(len(actors), len(obligations), limits)
    ids = [r['id'] for r in actors]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids): raise ValueError('Unique entity IDs required')
    index = {key: i for i, key in enumerate(ids)}
    cash = [_number(r['cash']) for r in actors]
    shock = _number(config['shock_bps'], False) / 10000
    borrower, lender, principal_due, obligation_ids = [], [], [], []
    dues = {mode: [] for mode in MODES}
    seen = set()
    for row in obligations:
        if not isinstance(row['id'], str) or not row['id'] or row['id'] in seen: raise ValueError('Unique obligation IDs required')
        seen.add(row['id'])
        if row['borrower'] not in index or row['lender'] not in index or row['borrower'] == row['lender']:
            raise ValueError('Distinct borrower and lender must have explicit cash state')
        if row.get('currency', config['currency']) != 'USD': raise ValueError('Obligation currency mismatch')
        maturity = date.fromisoformat(row['maturity'])
        if maturity <= start: raise ValueError('Overdue obligations require explicit arrears treatment')
        if row['rate_type'] not in ('fixed', 'floating'): raise ValueError('Unknown rate type')
        principal, rate = _number(row['principal']), _number(row['annual_rate'])
        stressed = _number(rate + (shock if row['rate_type'] == 'floating' else 0))
        fraction = (min(end, maturity) - start).days / 365
        due_principal = principal if maturity <= end else 0
        borrower.append(index[row['borrower']]); lender.append(index[row['lender']])
        principal_due.append(due_principal); obligation_ids.append(row['id'])
        dues['baseline'].append(due_principal + principal * rate * fraction)
        dues['stressed'].append(due_principal + principal * stressed * fraction)
    backend = resolve_backend(backend, len(obligations) + len(ids))
    if backend == 'numpy':
        np = load_numpy()
        cash_in, borrower_in, lender_in = np.asarray(cash, dtype=np.float64), np.asarray(borrower, dtype=np.intp), np.asarray(lender, dtype=np.intp)
        dues_in = {mode: np.asarray(values, dtype=np.float64) for mode, values in dues.items()}
    else:
        cash_in, borrower_in, lender_in, dues_in = cash, borrower, lender, dues

    def full(result, row_due):
        listed = {key: (result[key].tolist() if hasattr(result[key], 'tolist') else result[key])
                  for key in ('due', 'paid', 'received', 'ending_cash', 'entity_shortfall', 'obligation_paid', 'obligation_shortfall')}
        liabilities = row_due.tolist() if hasattr(row_due, 'tolist') else row_due
        loans = [{'id': obligation_ids[k], 'borrower': ids[borrower[k]], 'lender': ids[lender[k]], 'due': liabilities[k],
                  'principal_due': principal_due[k], 'paid': listed['obligation_paid'][k], 'shortfall': listed['obligation_shortfall'][k]}
                 for k in range(len(obligation_ids))]
        state = [{'id': key, 'due': listed['due'][i], 'paid': listed['paid'][i], 'received': listed['received'][i],
                  'ending_cash': listed['ending_cash'][i], 'shortfall': listed['entity_shortfall'][i]} for i, key in enumerate(ids)]
        return {'entities': state, 'obligations': loans, 'iterations': result['iterations'],
                'total_shortfall': result['total_shortfall'], 'cash_conservation_residual': result['residual']}

    return _run(cash_in, borrower_in, lender_in, dues_in, backend=backend, detail=detail, top=top, limits=limits,
                entity_ids=ids, full=full)


def stress_exposure_arrays(*, as_of, end, shock_bps, cash, borrower, lender, principal, annual_rate, floating,
                           maturity_days, entity_ids=None, backend=None, detail='summary', top=10,
                           return_arrays=False, limits=None):
    """Array-native USD network: indices into ``cash``; maturity in whole days after as_of.

    Semantics match stress_exposures (ACT/365, principal due when maturity is at or
    before ``end``). ``detail='full'`` requires ``entity_ids`` and builds dicts.
    ``return_arrays`` adds per-entity/obligation arrays to summaries.
    """
    limits = resolve_limits(limits)
    _options(detail, top)
    start, finish = _horizon(as_of, end)
    horizon = (finish - start).days
    lengths = {len(borrower), len(lender), len(principal), len(annual_rate), len(floating), len(maturity_days)}
    if len(lengths) != 1:
        raise ValueError('Obligation arrays must have equal lengths')
    n, m = len(cash), len(borrower)
    _sizes(n, m, limits)
    if entity_ids is not None and (len(entity_ids) != n or len(set(entity_ids)) != n):
        raise ValueError('Unique entity IDs required')
    if detail == 'full' and entity_ids is None:
        raise ValueError("detail='full' requires entity_ids")
    shock = _number(shock_bps, False) / 10000
    backend = resolve_backend(backend, n + m)
    if backend == 'numpy':
        np = load_numpy()
        cash_a = np.asarray(cash, dtype=np.float64)
        b, l = np.asarray(borrower), np.asarray(lender)
        if b.dtype.kind not in 'iu' or l.dtype.kind not in 'iu':
            raise ValueError('Borrower and lender must be integer entity indices')
        b, l = b.astype(np.intp, copy=False), l.astype(np.intp, copy=False)
        p = np.asarray(principal, dtype=np.float64); r = np.asarray(annual_rate, dtype=np.float64)
        f = np.asarray(floating, dtype=bool); days = np.asarray(maturity_days)
        if days.dtype.kind not in 'iu':
            raise ValueError('maturity_days must be integers')
        if not np.isfinite(cash_a).all() or (cash_a < 0).any() or not np.isfinite(p).all() or (p < 0).any() or not np.isfinite(r).all() or (r < 0).any():
            raise ValueError('Expected finite nonnegative number')
        if m and (b.min() < 0 or l.min() < 0 or b.max() >= n or l.max() >= n or (b == l).any()):
            raise ValueError('Distinct borrower and lender must have explicit cash state')
        if m and days.min() <= 0:
            raise ValueError('Overdue obligations require explicit arrears treatment')
        stressed = np.where(f, r + shock, r)
        if not np.isfinite(stressed).all() or (stressed < 0).any():
            raise ValueError('Expected finite nonnegative number')
        fraction = np.minimum(days, horizon) / 365
        due_principal = np.where(days <= horizon, p, 0.0)
        dues = {'baseline': due_principal + p * r * fraction, 'stressed': due_principal + p * stressed * fraction}
        cash_in = cash_a
    else:
        tolist = lambda values: values.tolist() if hasattr(values, 'tolist') else list(values)
        cash_in = [_number(v) for v in tolist(cash)]
        b, l = tolist(borrower), tolist(lender)
        p, r, f, days = tolist(principal), tolist(annual_rate), tolist(floating), tolist(maturity_days)
        dues = {mode: [] for mode in MODES}; due_principal = []
        for k in range(m):
            if type(b[k]) is not int or type(l[k]) is not int:
                raise ValueError('Borrower and lender must be integer entity indices')
            if not (0 <= b[k] < n and 0 <= l[k] < n) or b[k] == l[k]:
                raise ValueError('Distinct borrower and lender must have explicit cash state')
            if type(days[k]) is not int:
                raise ValueError('maturity_days must be integers')
            if days[k] <= 0:
                raise ValueError('Overdue obligations require explicit arrears treatment')
            principal_k, rate_k = _number(p[k]), _number(r[k])
            stressed_k = _number(rate_k + (shock if f[k] else 0))
            fraction = min(horizon, days[k]) / 365
            principal_due_k = principal_k if days[k] <= horizon else 0
            due_principal.append(principal_due_k)
            dues['baseline'].append(principal_due_k + principal_k * rate_k * fraction)
            dues['stressed'].append(principal_due_k + principal_k * stressed_k * fraction)

    def full(result, row_due):
        tolist = lambda values: values.tolist() if hasattr(values, 'tolist') else values
        listed = {key: tolist(result[key]) for key in ('due', 'paid', 'received', 'ending_cash', 'entity_shortfall', 'obligation_paid', 'obligation_shortfall')}
        borrowers, lenders, liabilities, principals = tolist(b), tolist(l), tolist(row_due), tolist(due_principal)
        loans = [{'id': k, 'borrower': entity_ids[borrowers[k]], 'lender': entity_ids[lenders[k]], 'due': liabilities[k],
                  'principal_due': principals[k], 'paid': listed['obligation_paid'][k], 'shortfall': listed['obligation_shortfall'][k]} for k in range(m)]
        state = [{'id': key, 'due': listed['due'][i], 'paid': listed['paid'][i], 'received': listed['received'][i],
                  'ending_cash': listed['ending_cash'][i], 'shortfall': listed['entity_shortfall'][i]} for i, key in enumerate(entity_ids)]
        return {'entities': state, 'obligations': loans, 'iterations': result['iterations'],
                'total_shortfall': result['total_shortfall'], 'cash_conservation_residual': result['residual']}

    return _run(cash_in, b, l, dues, backend=backend, detail=detail, top=top, limits=limits,
                entity_ids=entity_ids, full=full, arrays=return_arrays)


def schema():
    return {'entity_types': {'financial_obligation': {'parent': 'contract'}}, 'relations': {},
            'variables': {'obligation_terms': {'type': 'object', 'unit': None, 'domain': 'financial_obligation'}}}


def exposure_from_evidence(store, graph_ref, request, *, backend=None, detail='full', limits=None):
    """Select explicitly pinned dated records from a verified graph; never infer missing terms."""
    from .model import instant
    from .ontology import is_a
    rows = list(store.records(graph_ref))  # Store.records verifies the complete artifact lineage.
    by_id = {r['id']: r for r in rows}
    entities = {r.get('entity_id', r['id']): r for r in rows if r['kind'] == 'entity' and r.get('epistemic_status') in (None, 'observed')}
    at, known = instant(request['as_of']), instant(request['known_at'])
    evidence = []

    def select(record_id, subject, metric, unit, domain):
        record = by_id.get(record_id)
        entity = entities.get(subject)
        if not entity or not is_a(entity['entity_type'], domain): raise ValueError('Missing or incompatible evidence entity: ' + subject)
        if not record or record.get('kind') not in ('observation', 'assertion') or record.get('subject') != subject or record.get('metric', record.get('predicate')) != metric:
            raise ValueError('Pinned evidence subject or metric mismatch')
        if record.get('unit') != unit or record.get('value') is None: raise ValueError('Missing state or incompatible evidence unit')
        if record.get('epistemic_status') not in (None, 'observed'): raise ValueError('Scenario state cannot seed observed obligations')
        if not record.get('valid_from') or not record.get('valid_to') or not instant(record['valid_from']) <= at < instant(record['valid_to']) or instant(record['observed_at']) > known:
            raise ValueError('Evidence must explicitly cover as_of and be available by known_at')
        evidence.append({'input': graph_ref, 'record_id': record_id})
        return record['value']
    actors = [{'id': r['id'], 'cash': select(r['cash_record'], r['id'], 'cash', 'USD', 'agent')} for r in request['entities']]
    loans = []
    required = {'borrower', 'lender', 'principal', 'annual_rate', 'rate_type', 'maturity', 'currency'}
    for row in request['obligations']:
        terms = select(row['terms_record'], row['id'], 'obligation_terms', None, 'financial_obligation')
        if not isinstance(terms, dict) or set(terms) != required: raise ValueError('Obligation terms require exact documented fields')
        loans.append({**terms, 'id': row['id']})
    config = {key: request[key] for key in ('as_of', 'end', 'currency', 'shock_bps')}
    result = stress_exposures({**config, 'entities': actors, 'obligations': loans}, backend=backend, detail=detail, limits=limits)
    result['initial_state_evidence'] = evidence
    result['initial_state_origin'] = 'pinned observed records; forecast shock and settlement remain scenario assumptions'
    result['assumptions'][0] = 'Cash and obligation terms are selected from explicit dated evidence; completeness of the obligation network is unknown.'
    return result
