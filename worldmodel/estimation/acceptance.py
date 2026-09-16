"""Explicit, declared acceptance criteria. ``validated`` is never a default.

A criteria list is evaluated against a validation report (ignoring any embedded
``acceptance`` block). Acceptance requires a nonempty list in which every
criterion passes; an unknown criterion type fails closed.
"""
import math
from ..util import digest

DEFAULT_CRITERIA = (
    {'id': 'minimum_test_forecasts', 'type': 'min_forecasts', 'minimum': 12},
    {'id': 'beats_persistence_dm', 'type': 'diebold_mariano', 'baseline': 'persistence', 'loss': 'squared',
     'max_pvalue': 0.10, 'require_lower_loss': True},
    {'id': 'interval_coverage', 'type': 'interval_coverage', 'tolerance': 0.15},
    {'id': 'parameters_within_declared_bounds', 'type': 'parameter_bounds'},
    {'id': 'no_timing_leakage', 'type': 'leakage_audit'},
    {'id': 'no_revision_leakage', 'type': 'revision_leakage'},
)

TYPES = ('min_forecasts', 'diebold_mariano', 'interval_coverage', 'parameter_bounds', 'leakage_audit',
         'revision_leakage', 'relative_metric', 'max_metric', 'brier_skill', 'vintage_modes', 'calibration_error',
         'estimate_diagnostic')


def _result(criterion, passed, observed, threshold, reason=''):
    return {'id': criterion.get('id', criterion.get('type')), 'type': criterion.get('type'), 'passed': bool(passed),
            'observed': observed, 'threshold': threshold, **({'reason': reason} if reason else {})}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate_criterion(report, criterion):
    kind = criterion.get('type')
    test = report.get('test', {})
    metrics = test.get('metrics', {})
    model = metrics.get('model', {})
    if kind == 'min_forecasts':
        count = model.get('count', 0)
        return _result(criterion, count >= criterion['minimum'], count, criterion['minimum'])
    if kind == 'diebold_mariano':
        baseline = criterion.get('baseline', 'persistence')
        loss = criterion.get('loss', 'squared')
        dm = test.get('diebold_mariano', {}).get(baseline, {}).get(loss)
        if not dm or not _finite(dm.get('pvalue')):
            return _result(criterion, False, None, criterion['max_pvalue'], 'Diebold-Mariano statistic unavailable')
        lower = dm['mean_loss_difference'] < 0
        passed = dm['pvalue'] < criterion['max_pvalue'] and (lower or not criterion.get('require_lower_loss', True))
        return _result(criterion, passed, {'pvalue': dm['pvalue'], 'mean_loss_difference': dm['mean_loss_difference']},
                       {'max_pvalue': criterion['max_pvalue'], 'alternative': dm.get('alternative')})
    if kind == 'interval_coverage':
        coverage, nominal = model.get('interval_coverage'), model.get('interval_nominal')
        if not _finite(coverage) or not _finite(nominal):
            return _result(criterion, False, None, criterion['tolerance'], 'No interval forecasts')
        nominal = criterion.get('nominal', nominal)
        return _result(criterion, abs(coverage - nominal) <= criterion['tolerance'], coverage,
                       {'nominal': nominal, 'tolerance': criterion['tolerance']})
    if kind == 'parameter_bounds':
        checks = report.get('final_estimate', {}).get('bounds_check', {})
        failing = sorted(name for name, ok in checks.items() if not ok)
        return _result(criterion, bool(checks) and not failing, failing, 'all declared parameters within bounds',
                       'No parameter checks' if not checks else '')
    if kind == 'leakage_audit':
        audit = test.get('leakage_audit', {})
        violations = audit.get('violations')
        selection = report.get('selection', {}).get('leakage_audit', {}).get('violations', 0)
        return _result(criterion, violations == 0 and selection == 0 and audit.get('origins_checked', 0) > 0,
                       {'test_violations': violations, 'selection_violations': selection}, 0)
    if kind == 'revision_leakage':
        series = report.get('final_estimate', {}).get('data_audit', {}).get('series', {})
        risky = sorted(name for name, audit in series.items() if audit.get('revision_leakage_possible'))
        allowed = set(criterion.get('allow_series', []))
        classes = set(criterion.get('allowed_classes', []))
        failing = [name for name in risky if name not in allowed and series[name].get('revisions') not in classes]
        return _result(criterion, bool(series) and not failing, failing,
                       {'rule': 'series with possible revisions must use real-time vintages',
                        'allowed_revision_classes': sorted(classes | {'none'})})
    if kind == 'estimate_diagnostic':
        value = report.get('final_estimate', {}).get('diagnostics', {})
        for key in criterion['path']:
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
                value = value[key]
            else:
                value = None
                break
        passed = _finite(value) and ('minimum' not in criterion or value >= criterion['minimum']) and ('maximum' not in criterion or value <= criterion['maximum'])
        return _result(criterion, passed, value, {k: criterion[k] for k in ('minimum', 'maximum') if k in criterion})
    if kind == 'relative_metric':
        metric, baseline = criterion['metric'], criterion['baseline']
        value = model.get(metric)
        reference = metrics.get('baselines', {}).get(baseline, {}).get(metric)
        if not _finite(value) or not _finite(reference) or reference <= 0:
            return _result(criterion, False, None, criterion['max_ratio'], 'Metric unavailable')
        ratio = value / reference
        return _result(criterion, ratio <= criterion['max_ratio'], ratio, criterion['max_ratio'])
    if kind == 'max_metric':
        value = model.get(criterion['metric'])
        return _result(criterion, _finite(value) and value <= criterion['maximum'], value, criterion['maximum'])
    if kind == 'brier_skill':
        value = model.get('brier_skill')
        return _result(criterion, _finite(value) and value >= criterion['minimum'], value, criterion['minimum'])
    if kind == 'calibration_error':
        value = model.get('calibration', {}).get('max_abs_deviation')
        return _result(criterion, _finite(value) and value <= criterion['maximum'], value, criterion['maximum'])
    if kind == 'vintage_modes':
        modes = sorted(report.get('test', {}).get('leakage_audit', {}).get('vintage_modes', []))
        allowed = set(criterion['allowed'])
        return _result(criterion, bool(modes) and set(modes) <= allowed, modes, sorted(allowed))
    return _result(criterion, False, None, None, f'Unknown criterion type {kind!r} fails closed')


def evaluate_criteria(report, criteria):
    if not isinstance(criteria, (list, tuple)) or not criteria:
        raise ValueError('Acceptance requires an explicit nonempty criteria list')
    ids = [c.get('id', c.get('type')) for c in criteria]
    if len(set(ids)) != len(ids):
        raise ValueError('Acceptance criterion ids must be unique')
    body = {k: v for k, v in report.items() if k not in ('acceptance', 'report_id', 'validated')}
    results = [evaluate_criterion(body, dict(c)) for c in criteria]
    return {'passed': all(r['passed'] for r in results), 'results': results, 'criteria': [dict(c) for c in criteria],
            'criteria_digest': digest([dict(c) for c in criteria]), 'evaluated_report_digest': digest(body)}


def default_criteria(extra=()):
    return [dict(c) for c in DEFAULT_CRITERIA] + [dict(c) for c in extra]
