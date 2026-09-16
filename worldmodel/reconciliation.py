"""Audited selection over dated claims; source records remain immutable.

``reconcile_claims`` selects among claims for one selector (original contract).
``materialize_beliefs`` builds a deterministic "current belief" table per
(subject, variable, period, dimensions) under an explicit policy: source reliability
priors, vintage/recency rules, retractions and supersession, stale marking, unit
normalization, and an explicit distinction between unknown (only missing evidence)
and false (an observed ``False`` value). Conflicts are reported, never hidden.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import math
from .model import instant
from .util import canonical


def reconcile_claims(records,selector,at,known_at,policy='retain',source_priority=()):
    if policy not in ('retain','latest','source_priority'):raise ValueError('Unsupported reconciliation policy')
    if not isinstance(selector,dict) or not {'entity','variable'}<=set(selector) or set(selector)-{'entity','variable','unit','dimensions'}:raise ValueError('Unsupported reconciliation selector')
    if 'dimensions' in selector and not isinstance(selector['dimensions'],dict):raise ValueError('Selector dimensions must be an object')
    at,known=instant(at),instant(known_at)
    candidates=[r for r in records if r.get('kind') in ('observation','assertion') and 'value' in r
                and r.get('subject')==selector['entity'] and r.get('metric',r.get('predicate'))==selector['variable']
                and ('unit' not in selector or r.get('unit')==selector['unit'])
                and all(r.get('dimensions',{}).get(k)==v and k in r.get('dimensions',{}) for k,v in selector.get('dimensions',{}).items())
                and r.get('epistemic_status') in (None,'observed') and instant(r['observed_at'])<=known
                and (not r.get('valid_from') or instant(r['valid_from'])<=at)
                and (not r.get('valid_to') or at<instant(r['valid_to']))]
    if len(candidates)>10000:raise ValueError('Reconciliation candidate budget exceeded')
    available=[r for r in candidates if r['value'] is not None]
    selected=available
    if policy=='latest' and selected:
        latest=max(instant(r['observed_at']) for r in selected)
        selected=[r for r in selected if instant(r['observed_at'])==latest]
    if policy=='source_priority' and selected:
        if len(set(source_priority))!=len(source_priority) or not source_priority:raise ValueError('Distinct explicit source priority required')
        def rank(row):
            source=row.get('attributes',{}).get('source_dataset')
            if source is None:source=(row.get('evidence') or [{}])[0].get('input',{}).get('dataset')
            return source_priority.index(source) if source in source_priority else len(source_priority)
        best=min(map(rank,selected));selected=[r for r in selected if rank(r)==best]
    alternatives={canonical([r['value'],r.get('unit')]) for r in selected}
    status='missing' if not selected else 'resolved' if len(alternatives)==1 else 'conflicting'
    ids=[r['id'] for r in selected]
    return {'status':status,'policy':policy,'selector':deepcopy(selector),'at':at.isoformat(),'known_at':known.isoformat(),
            'selected_record_ids':ids,'rejected_record_ids':[r['id'] for r in candidates if r['id'] not in ids],
            'value':deepcopy(selected[0]['value']) if status=='resolved' else None,
            'unit':selected[0].get('unit') if status=='resolved' else None,'candidates':deepcopy(candidates),
            'temporal_caveat':'Missing validity bounds remain unspecified; selection does not infer a historical interval.'}


# ---------------------------------------------------------------------------
# Belief materialization

DEFAULT_POLICY = {
    'rule': 'latest_vintage',            # latest_vintage | reliability_weighted | source_priority
    'source_reliability': {},            # dataset -> prior reliability in (0, 1]
    'default_reliability': 0.5,
    'source_priority': [],
    'half_life_days': None,              # reliability_weighted: evidence weight halves every N days of age
    'stale_after_days': None,            # default staleness horizon (None = never stale)
    'stale_after_days_by_variable': {},
    'conflict_share': 0.2,               # runner-up weight share at or above this makes the belief conflicting
    'numeric_relative_tolerance': 1e-9,
    'units': {},                         # variable -> canonical unit (dimensional conversion only)
    'include_inferred': False,           # include epistemic_status 'inferred' claims
    'max_candidates': 5000000,
}
_POLICY_KEYS = set(DEFAULT_POLICY)


def validate_policy(policy=None):
    merged = deepcopy(DEFAULT_POLICY)
    if policy is None:
        return merged
    if not isinstance(policy, dict) or set(policy) - _POLICY_KEYS:
        raise ValueError('Unknown belief policy keys: ' + ', '.join(sorted(set(policy or {}) - _POLICY_KEYS)))
    merged.update(deepcopy(policy))
    if merged['rule'] not in ('latest_vintage', 'reliability_weighted', 'source_priority'):
        raise ValueError('Unsupported belief rule')
    for source, value in list(merged['source_reliability'].items()) + [('default', merged['default_reliability'])]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise ValueError(f'Reliability for {source} must be in (0, 1]')
    if merged['rule'] == 'source_priority' and (not merged['source_priority'] or len(set(merged['source_priority'])) != len(merged['source_priority'])):
        raise ValueError('source_priority rule needs a distinct nonempty source_priority list')
    for key in ('half_life_days', 'stale_after_days'):
        if merged[key] is not None and (isinstance(merged[key], bool) or not isinstance(merged[key], (int, float)) or merged[key] <= 0):
            raise ValueError(key + ' must be positive or null')
    if not 0 < merged['conflict_share'] <= 0.5:
        raise ValueError('conflict_share must be in (0, 0.5]')
    return merged


def _source(record):
    source = (record.get('attributes') or {}).get('source_dataset')
    if source is None:
        source = ((record.get('evidence') or [{}])[0].get('input') or {}).get('dataset')
    return source


def _period(record):
    return (instant(record['valid_from']).isoformat() if record.get('valid_from') else None,
            instant(record['valid_to']).isoformat() if record.get('valid_to') else None)


def _same_value(a, b, tolerance):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=tolerance, abs_tol=0.0) or a == b
    return canonical(a) == canonical(b)


def materialize_beliefs(records, *, known_at, at=None, policy=None, subjects=None, variables=None):
    """Deterministic belief table and conflict report.

    Statuses: ``known`` (one supported value), ``conflicting`` (competing values above
    conflict_share; ``value`` is None and ``leading_value`` is reported), ``unknown``
    (only explicit missing evidence, e.g. suppressed), ``retracted`` (all evidence retracted).
    A belief whose newest support is older than the staleness horizon keeps its value with
    ``stale: true``. ``truth_value`` is True/False only for boolean values; None means unknown.
    """
    policy = validate_policy(policy)
    known = instant(known_at)
    at_time = instant(at) if at else None
    allowed = (None, 'observed', 'inferred') if policy['include_inferred'] else (None, 'observed')
    visible, retracted_by, superseded_by = [], {}, {}
    for r in records:
        if r.get('kind') not in ('observation', 'assertion') or instant(r['observed_at']) > known:
            continue
        for target in r.get('retracts', ()):
            retracted_by.setdefault(target, []).append(r['id'])
        for target in r.get('supersedes', ()):
            superseded_by.setdefault(target, []).append(r['id'])
        if 'value' not in r or r.get('epistemic_status') not in allowed + ('retracted',):
            continue
        if subjects is not None and r.get('subject') not in subjects:
            continue
        variable = r.get('metric', r.get('predicate'))
        if variables is not None and variable not in variables:
            continue
        if at_time and ((r.get('valid_from') and instant(r['valid_from']) > at_time) or (r.get('valid_to') and at_time >= instant(r['valid_to']))):
            continue
        visible.append(r)
        if len(visible) > policy['max_candidates']:
            raise ValueError('Belief candidate budget exceeded')
    groups = {}
    for r in visible:
        key = (r.get('subject'), r.get('metric', r.get('predicate')), _period(r), canonical(r.get('dimensions') or {}).decode())
        groups.setdefault(key, []).append(r)
    beliefs = []
    for (subject, variable, period, dims), rows in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], str(kv[0][2]), kv[0][3])):
        rows = sorted(rows, key=lambda r: r['id'])
        retracted = sorted(r['id'] for r in rows if r['id'] in retracted_by or r.get('epistemic_status') == 'retracted')
        superseded = sorted(r['id'] for r in rows if r['id'] in superseded_by and r['id'] not in retracted)
        active = [r for r in rows if r['id'] not in retracted and r['id'] not in superseded]
        unknown = sorted(r['id'] for r in active if r['value'] is None)
        unconvertible, support = [], []
        target_unit = policy['units'].get(variable)
        for r in active:
            if r['value'] is None:
                continue
            value, unit = r['value'], r.get('unit')
            if target_unit and unit != target_unit and isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    from .units import convert
                    value, unit = convert(value, unit, target_unit)['value'], target_unit
                except ValueError as error:
                    unconvertible.append({'record_id': r['id'], 'unit': unit, 'reason': str(error)})
                    continue
            source = _source(r)
            reliability = policy['source_reliability'].get(source, policy['default_reliability'])
            vintage = instant(r.get('vintage') or r['observed_at'])
            weight = reliability
            if policy['rule'] == 'reliability_weighted' and policy['half_life_days']:
                age = max((known - vintage).total_seconds() / 86400, 0.0)
                weight *= 0.5 ** (age / policy['half_life_days'])
            support.append({'record': r, 'value': value, 'unit': unit, 'source': source, 'reliability': reliability,
                            'vintage': vintage, 'weight': weight})
        rule = policy['rule']
        if support and rule == 'latest_vintage':
            newest = max(s['vintage'] for s in support)
            support = [s for s in support if s['vintage'] == newest]
        if support and rule == 'source_priority':
            order = policy['source_priority']
            rank = lambda s: order.index(s['source']) if s['source'] in order else len(order)
            best = min(map(rank, support))
            support = [s for s in support if rank(s) == best]
        alternatives = []
        for s in sorted(support, key=lambda s: (canonical([s['unit']]), s['record']['id'])):
            for alt in alternatives:
                if alt['unit'] == s['unit'] and _same_value(alt['value'], s['value'], policy['numeric_relative_tolerance']):
                    alt['weight'] += s['weight']
                    alt['record_ids'].append(s['record']['id'])
                    alt['sources'].add(s['source'])
                    break
            else:
                alternatives.append({'value': s['value'], 'unit': s['unit'], 'weight': s['weight'],
                                     'record_ids': [s['record']['id']], 'sources': {s['source']}})
        total = sum(a['weight'] for a in alternatives)
        for alt in alternatives:
            alt['share'] = alt['weight'] / total if total else 0.0
            alt['sources'] = sorted(str(x) for x in alt['sources'])
            alt['record_ids'].sort()
        alternatives.sort(key=lambda a: (-a['weight'], canonical([a['value'], a['unit']])))
        newest_support = max((s['vintage'] for s in support), default=None)
        horizon = policy['stale_after_days_by_variable'].get(variable, policy['stale_after_days'])
        stale = bool(horizon and newest_support and known - newest_support > timedelta(days=horizon))
        if alternatives:
            conflicting = len(alternatives) > 1 and (alternatives[1]['share'] >= policy['conflict_share']
                                                     or math.isclose(alternatives[0]['weight'], alternatives[1]['weight']))
            status = 'conflicting' if conflicting else 'known'
        elif unknown:
            status = 'unknown'
        elif retracted:
            status = 'retracted'
        else:
            status = 'unknown'
        lead = alternatives[0] if alternatives else None
        value = deepcopy(lead['value']) if status == 'known' else None
        beliefs.append({
            'subject': subject, 'variable': variable, 'period': {'valid_from': period[0], 'valid_to': period[1]},
            'dimensions': deepcopy(rows[0].get('dimensions') or {}), 'status': status, 'value': value,
            'unit': lead['unit'] if lead and status == 'known' else None,
            'truth_value': value if isinstance(value, bool) else None,
            'leading_value': deepcopy(lead['value']) if lead else None, 'confidence': lead['share'] if lead else None,
            'stale': stale, 'newest_support_at': newest_support.isoformat() if newest_support else None,
            'alternatives': alternatives, 'unknown_record_ids': unknown, 'retracted_record_ids': retracted,
            'retracted_by': {k: sorted(retracted_by[k]) for k in retracted if k in retracted_by},
            'superseded_record_ids': superseded, 'unconvertible': unconvertible, 'rule': rule})
    conflicts = [{'subject': b['subject'], 'variable': b['variable'], 'period': b['period'], 'dimensions': b['dimensions'],
                  'alternatives': [{k: a[k] for k in ('value', 'unit', 'share', 'record_ids', 'sources')} for a in b['alternatives']]}
                 for b in beliefs if b['status'] == 'conflicting']
    counts = {}
    for b in beliefs:
        counts[b['status']] = counts.get(b['status'], 0) + 1
    body = {'known_at': known.isoformat(), 'at': at_time.isoformat() if at_time else None, 'policy': policy, 'beliefs': beliefs}
    return {**body, 'conflicts': conflicts, 'counts': counts, 'stale': sum(b['stale'] for b in beliefs),
            'digest': hashlib.sha256(canonical(body)).hexdigest(),
            'interpretation': 'Beliefs are policy-dependent selections over retained evidence; they are not new observations.'}
