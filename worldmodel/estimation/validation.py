"""Rolling-origin validation, baselines, proper scoring rules and Diebold-Mariano tests.

Protocol (mirrors :mod:`worldmodel.benchmarks`): candidates are compared only on
a validation window using a data view physically restricted to information
available by ``validation_end``; the selection is hashed and frozen; then the
selected estimator is scored on the untouched holdout ``(validation_end, cutoff]``.
Each holdout origin refits using only information available when the anchor
observation was first published, and forecasts the next ``horizon`` periods.
Declared conditional inputs (scenario drivers such as the policy rate) are taken
from realized data and labeled; such forecasts are conditional, not unconditional.
"""
import math
from ..model import instant
from ..util import canonical, digest
from . import intervals
from .distributions import norm_cdf, norm_pdf, norm_ppf, t_cdf
from .data import LeakageError
from .acceptance import evaluate_criteria

BASELINES = ('persistence', 'seasonal_naive', 'drift', 'historical_mean')
MAX_ORIGINS = 2000


# ----------------------------------------------------------------------------- metrics

def mae(errors):
    return math.fsum(abs(e) for e in errors) / len(errors)


def rmse(errors):
    return math.sqrt(math.fsum(e * e for e in errors) / len(errors))


def mase(errors, scale):
    return mae(errors) / scale if scale and scale > 0 else None


def mase_scale(history, season=1):
    diffs = [abs(history[t] - history[t - season]) for t in range(season, len(history))]
    return math.fsum(diffs) / len(diffs) if diffs else None


def pinball_loss(actual, prediction, tau):
    diff = actual - prediction
    return max(tau * diff, (tau - 1) * diff)


def crps_gaussian(actual, mean, sd):
    if sd <= 0:
        return abs(actual - mean)
    z = (actual - mean) / sd
    return sd * (z * (2 * norm_cdf(z) - 1) + 2 * norm_pdf(z) - 1 / math.sqrt(math.pi))


def crps_ensemble(actual, samples):
    """Energy form E|X-y| - 0.5 E|X-X'| computed in O(n log n)."""
    xs = sorted(samples)
    n = len(xs)
    first = math.fsum(abs(x - actual) for x in xs) / n
    second = math.fsum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * n)
    return first - second


def crps_from_quantiles(actual, quantiles):
    """Twice the mean pinball loss over supplied quantile levels (a discretized CRPS)."""
    return 2 * math.fsum(pinball_loss(actual, value, float(tau)) for tau, value in quantiles.items()) / len(quantiles)


def gaussian_log_score(actual, mean, sd):
    if sd <= 0:
        return math.inf if actual != mean else -math.inf
    return 0.5 * math.log(2 * math.pi * sd * sd) + (actual - mean) ** 2 / (2 * sd * sd)


def brier_score(probabilities, outcomes):
    return math.fsum((p - o) ** 2 for p, o in zip(probabilities, outcomes)) / len(outcomes)


def brier_skill_score(probabilities, outcomes, reference):
    ref = brier_score(reference, outcomes)
    return 1 - brier_score(probabilities, outcomes) / ref if ref > 0 else None


def calibration_curve(probabilities, outcomes, bins=10):
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError('Calibration needs aligned nonempty probabilities and outcomes')
    groups = [[] for _ in range(bins)]
    for p, o in zip(probabilities, outcomes):
        if not 0 <= p <= 1 or o not in (0, 1, 0.0, 1.0, True, False):
            raise ValueError('Calibration expects probabilities in [0,1] and binary outcomes')
        groups[min(bins - 1, int(p * bins))].append((p, float(o)))
    rows = []
    for index, group in enumerate(groups):
        if not group:
            continue
        predicted = math.fsum(p for p, _ in group) / len(group)
        observed = math.fsum(o for _, o in group) / len(group)
        rows.append({'bin': [index / bins, (index + 1) / bins], 'count': len(group), 'mean_predicted': predicted,
                     'observed_frequency': observed, 'deviation': observed - predicted})
    total = len(probabilities)
    return {'bins': rows, 'expected_calibration_error': math.fsum(abs(r['deviation']) * r['count'] for r in rows) / total,
            'max_abs_deviation': max(abs(r['deviation']) for r in rows)}


def diebold_mariano(loss_model, loss_baseline, *, horizon=1, alternative='less', harvey_correction=True):
    """Diebold-Mariano (1995) test with the Harvey-Leybourne-Newbold (1997) correction.

    d_t = loss_model - loss_baseline. Long-run variance uses autocovariances up to
    lag h-1 (rectangular kernel), falling back to Bartlett weights if negative.
    ``alternative='less'`` tests whether the model has lower expected loss.
    p-values use Student t with n-1 degrees of freedom when corrected, else normal.
    """
    if len(loss_model) != len(loss_baseline):
        raise ValueError('Loss series must align')
    n = len(loss_model)
    if n < 3:
        raise ValueError('Diebold-Mariano needs at least three forecasts')
    if type(horizon) is not int or horizon < 1:
        raise ValueError('horizon must be a positive integer')
    d = [a - b for a, b in zip(loss_model, loss_baseline)]
    mean = math.fsum(d) / n

    def gamma(k):
        return math.fsum((d[t] - mean) * (d[t - k] - mean) for t in range(k, n)) / n

    long_run = gamma(0) + 2 * math.fsum(gamma(k) for k in range(1, horizon))
    kernel = 'rectangular'
    if long_run <= 0:
        long_run = gamma(0) + 2 * math.fsum((1 - k / horizon) * gamma(k) for k in range(1, horizon))
        kernel = 'bartlett'
    if long_run <= 0:
        return {'statistic': None, 'pvalue': None, 'mean_loss_difference': mean, 'count': n, 'horizon': horizon,
                'alternative': alternative, 'degenerate': True,
                'reason': 'Zero variance of loss differential (identical forecasts?)'}
    statistic = mean / math.sqrt(long_run / n)
    if harvey_correction:
        statistic *= math.sqrt((n + 1 - 2 * horizon + horizon * (horizon - 1) / n) / n)
        cdf = t_cdf(statistic, n - 1)
    else:
        cdf = norm_cdf(statistic)
    if alternative == 'less':
        p = cdf
    elif alternative == 'greater':
        p = 1 - cdf
    elif alternative == 'two-sided':
        p = min(1.0, 2 * min(cdf, 1 - cdf))
    else:
        raise ValueError('alternative must be less, greater or two-sided')
    return {'statistic': statistic, 'pvalue': p, 'mean_loss_difference': mean, 'long_run_variance': long_run,
            'kernel': kernel, 'count': n, 'horizon': horizon, 'alternative': alternative,
            'harvey_correction': harvey_correction, 'distribution': f't({n - 1})' if harvey_correction else 'normal'}


# ----------------------------------------------------------------------------- baselines

def _sd(values):
    if len(values) < 2:
        return 0.0
    m = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / (len(values) - 1))


def baseline_forecast(name, history, horizon=1, season=None):
    history = list(history)
    if len(history) < 2:
        raise ValueError('Baselines need at least two history values')
    if name == 'persistence':
        diffs = [b - a for a, b in zip(history, history[1:])]
        return {'mean': history[-1], 'sd': _sd(diffs) * math.sqrt(horizon)}
    if name == 'seasonal_naive':
        if not season or len(history) <= season:
            raise ValueError('Seasonal naive needs a season shorter than the history')
        cycles = math.ceil(horizon / season)
        diffs = [history[t] - history[t - season] for t in range(season, len(history))]
        return {'mean': history[len(history) - season * cycles + (horizon - 1) % season], 'sd': _sd(diffs) * math.sqrt(cycles)}
    if name == 'drift':
        n = len(history)
        slope = (history[-1] - history[0]) / (n - 1)
        residuals = [b - a - slope for a, b in zip(history, history[1:])]
        return {'mean': history[-1] + horizon * slope, 'sd': _sd(residuals) * math.sqrt(horizon * (1 + horizon / (n - 1)))}
    if name == 'historical_mean':
        return {'mean': math.fsum(history) / len(history), 'sd': _sd(history) * math.sqrt(1 + 1 / len(history))}
    raise ValueError(f'Unknown baseline {name}')


# ----------------------------------------------------------------------------- scoring

def _interval(mean, sd, level, predictive=None):
    """Predictive interval: from a declared ``predictive`` spec, else Gaussian with ``sd``."""
    if predictive:
        return intervals.interval(predictive, mean, level)
    z = norm_ppf(0.5 + level / 2)
    return [mean - z * sd, mean + z * sd]


def summarize_forecasts(rows, *, mean_key='mean', sd_key='sd', interval_level=0.8, quantiles=(0.1, 0.5, 0.9), scale=None):
    errors = [r[mean_key] - r['actual'] for r in rows]
    if not errors:
        return {'count': 0}
    out = {'count': len(errors), 'mae': mae(errors), 'rmse': rmse(errors), 'mse': rmse(errors) ** 2,
           'bias': math.fsum(errors) / len(errors), 'mase': mase(errors, scale)}
    sds = [r.get(sd_key) for r in rows]
    if all(isinstance(s, (int, float)) and math.isfinite(s) and s >= 0 for s in sds):
        covered, widths, crps, logs = 0, [], [], []
        pinball = {str(q): [] for q in quantiles}
        families = set()
        for r, s in zip(rows, sds):
            predictive = r.get('predictive')
            families.add(predictive['family'] if predictive else 'normal')
            low, high = _interval(r[mean_key], s, interval_level, predictive)
            covered += low <= r['actual'] <= high
            widths.append(high - low)
            if predictive:
                crps.append(intervals.crps(predictive, r[mean_key], r['actual']))
                score = intervals.log_score(predictive, r[mean_key], r['actual'])
                if score is not None:
                    logs.append(score)
            else:
                crps.append(crps_gaussian(r['actual'], r[mean_key], s))
                if s > 0:
                    logs.append(gaussian_log_score(r['actual'], r[mean_key], s))
            for q in quantiles:
                if predictive:
                    value = intervals.quantile(predictive, r[mean_key], q)
                else:
                    value = r[mean_key] + (s * norm_ppf(q) if s > 0 else 0.0)
                pinball[str(q)].append(pinball_loss(r['actual'], value, q))
        out.update({'interval_nominal': interval_level, 'interval_coverage': covered / len(rows),
                    'mean_interval_width': math.fsum(widths) / len(widths), 'crps': math.fsum(crps) / len(crps),
                    'log_score': math.fsum(logs) / len(logs) if logs else None,
                    'pinball': {q: math.fsum(v) / len(v) for q, v in pinball.items()},
                    # Only reported when something other than the Gaussian default scored a row, so
                    # attempts that keep the default stay byte-identical to their earlier reports.
                    **({'predictive_families': sorted(families)} if families != {'normal'} else {})})
        out['mean_pinball'] = math.fsum(out['pinball'].values()) / len(quantiles)
    return out


def score_forecasts(forecasts, *, baselines, horizon=1, interval_level=0.8, quantiles=(0.1, 0.5, 0.9), scale=None,
                    probability=False):
    """Metrics for the model and every baseline, plus DM tests of model vs each baseline."""
    metrics = {'model': summarize_forecasts(forecasts, interval_level=interval_level, quantiles=quantiles, scale=scale),
               'baselines': {}}
    tests = {}
    for name in baselines:
        usable = [f for f in forecasts if name in f.get('baselines', {})]
        rows = [{'actual': f['actual'], 'mean': f['baselines'][name]['mean'], 'sd': f['baselines'][name]['sd']} for f in usable]
        metrics['baselines'][name] = summarize_forecasts(rows, interval_level=interval_level, quantiles=quantiles, scale=scale)
        if len(usable) >= 3:
            tests[name] = {}
            for loss, fn in (('squared', lambda e: e * e), ('absolute', abs)):
                try:
                    tests[name][loss] = diebold_mariano([fn(f['mean'] - f['actual']) for f in usable],
                                                        [fn(f['baselines'][name]['mean'] - f['actual']) for f in usable],
                                                        horizon=horizon, alternative='less')
                except ValueError as error:
                    tests[name][loss] = {'statistic': None, 'pvalue': None, 'reason': str(error)}
    if probability and forecasts:
        probs = [min(1.0, max(0.0, f['mean'])) for f in forecasts]
        outcomes = [f['actual'] for f in forecasts]
        metrics['model']['brier'] = brier_score(probs, outcomes)
        if 'historical_mean' in metrics['baselines']:
            reference = [min(1.0, max(0.0, f['baselines']['historical_mean']['mean'])) for f in forecasts]
            metrics['model']['brier_skill'] = brier_skill_score(probs, outcomes, reference)
        if all(o in (0, 1) for o in outcomes):
            metrics['model']['calibration'] = calibration_curve(probs, outcomes)
    return {'metrics': metrics, 'diebold_mariano': tests}


# ----------------------------------------------------------------------------- backtests

def rolling_origin_backtest(estimator, data, *, start, end, evaluation_cutoff, horizon=1, window='expanding',
                            window_size=None, refit_every=1, vintage_policy='strict', interval_level=0.8,
                            quantiles=(0.1, 0.5, 0.9), baselines=('persistence', 'drift', 'historical_mean'),
                            season=None, fit_options=None, max_origins=MAX_ORIGINS):
    """Score ``estimator`` on targets with valid time in ``(start, end]``.

    Actual values are the latest vintages available by ``evaluation_cutoff``.
    For target row i the origin is the first publication time of anchor row i-h
    (latest across required series); fits and baseline histories see only that
    information. Rows beyond the anchor are dropped; if the target period was
    already published at the origin the origin is skipped as uninformative.
    """
    if window not in ('expanding', 'rolling') or (window == 'rolling' and (type(window_size) is not int or window_size < 3)):
        raise ValueError('window must be expanding, or rolling with integer window_size >= 3')
    if type(refit_every) is not int or refit_every < 1:
        raise ValueError('refit_every must be a positive integer')
    if instant(end) > instant(evaluation_cutoff):
        raise LeakageError('Targets after the evaluation cutoff cannot be scored')
    if horizon > 1 and getattr(estimator, 'conditional_inputs', ()):
        raise ValueError('Conditional-input estimators support one-step origins only')
    data = data.subset(estimator.all_requirements()) if hasattr(estimator, 'all_requirements') else data
    truth = estimator.frame(data, cutoff=evaluation_cutoff, vintage_policy=vintage_policy)
    targets = list(getattr(estimator, 'targets', None) or [estimator.target])
    lo, hi = instant(start), instant(end)
    indices = [i for i, t in enumerate(truth.times) if lo < instant(t) <= hi and i - horizon >= 0]
    if len(indices) > max_origins:
        raise ValueError(f'Backtest origin budget exceeded ({len(indices)} > {max_origins})')
    forecasts, skipped, modes, rebase_audit = [], [], set(), {}
    estimate, fits, truncated, checked, violations, scale = None, 0, 0, 0, 0, None
    for position, i in enumerate(indices):
        anchor = truth.times[i - horizon]
        first = [instant(s.points[[p.time for p in s.points].index(anchor)].first_available_at)
                 for s in truth.series.values() if anchor in [p.time for p in s.points]]
        if not first:
            skipped.append({'time': truth.times[i], 'reason': 'anchor_missing'})
            continue
        origin_cutoff = max(first).isoformat()
        frame = estimator.frame(data, cutoff=origin_cutoff, vintage_policy=vintage_policy)
        if truth.times[i] in frame.times and any(truth.times[i] in [p.time for p in frame.series[t].points] for t in targets if t in frame.series):
            skipped.append({'time': truth.times[i], 'reason': 'target_already_published_at_origin', 'origin_cutoff': origin_cutoff})
            continue
        keep = [k for k, t in enumerate(frame.times) if instant(t) <= instant(anchor)]
        truncated += len(frame.times) - len(keep)
        frame = frame.restrict(keep[-window_size:] if window == 'rolling' else keep)
        if not frame.times or frame.times[-1] != anchor:
            skipped.append({'time': truth.times[i], 'reason': 'anchor_not_available_in_real_time', 'origin_cutoff': origin_cutoff})
            continue
        try:
            audit = frame.audit()  # Raises LeakageError on any point after the origin cutoff.
        except LeakageError:
            violations += 1
            raise
        checked += 1
        for series_audit in audit['series'].values():
            modes.update(series_audit['vintage_modes'])
        if estimate is None or position % refit_every == 0:
            try:
                estimate = estimator.fit_frame(frame, cutoff=origin_cutoff, vintage_policy=vintage_policy, **(fit_options or {}))
                fits += 1
            except ValueError as error:
                skipped.append({'time': truth.times[i], 'reason': 'fit_failed', 'error': str(error)})
                estimate = None
                continue
        if instant(estimate.cutoff) > instant(origin_cutoff):
            violations += 1
            raise LeakageError('Estimate cutoff after forecast origin')
        conditional, rebased = _conditional_inputs(estimator, truth, frame, i, horizon, anchor)
        for name, detail in rebased.items():
            rebase_audit.setdefault(name, {'mode': detail['mode'], 'rows': 0, 'largest_adjustment': 0.0})
            entry = rebase_audit[name]
            entry['rows'] += 1
            entry['largest_adjustment'] = max(entry['largest_adjustment'], abs(detail['adjustment']))
        for target in targets:
            history = frame.columns[target]
            if scale is None:
                scale = mase_scale(history, season or 1)
            try:
                prediction = estimator.predict(estimate, frame, horizon=horizon, conditional=conditional, target=target)
            except ValueError as error:
                skipped.append({'time': truth.times[i], 'target': target, 'reason': 'predict_failed', 'error': str(error)})
                continue
            predictive = prediction.get('predictive')
            row = {'time': truth.times[i], 'target': target, 'origin_cutoff': origin_cutoff, 'anchor_time': anchor,
                   'train_rows': len(frame.times), 'actual': truth.columns[target][i], 'mean': prediction['mean'],
                   'sd': prediction['sd'],
                   'interval': _interval(prediction['mean'], prediction['sd'], interval_level, predictive),
                   **({'predictive': predictive} if predictive else {}),
                   'parameters': estimate.parameters, 'conditional_inputs': sorted(conditional), 'baselines': {}}
            for name in baselines:
                try:
                    row['baselines'][name] = baseline_forecast(name, history, horizon, season)
                except ValueError:
                    pass
            forecasts.append(row)
    scored = score_forecasts(forecasts, baselines=baselines, horizon=horizon, interval_level=interval_level,
                             quantiles=quantiles, scale=scale, probability=getattr(estimator, 'probability_target', False))
    return {'forecasts': forecasts, 'skipped': skipped, **scored, 'mase_scale': scale, 'fits': fits,
            'leakage_audit': {'origins_checked': checked, 'violations': violations, 'truncated_future_rows': truncated,
                              'vintage_modes': sorted(modes), 'conditional_inputs': list(getattr(estimator, 'conditional_inputs', ())),
                              'forecast_type': 'conditional_on_realized_inputs' if getattr(estimator, 'conditional_inputs', ()) else 'unconditional',
                              # Only reported where a declared rebasing actually moved a value, so every
                              # attempt that declares none stays byte-identical to its earlier report.
                              **({'conditional_input_rebase': rebase_audit} if rebase_audit else {}),
                              'evaluation_cutoff': evaluation_cutoff}}


def _conditional_inputs(estimator, truth, frame, index, horizon, anchor):
    """Realized values of the declared conditional inputs for the target periods.

    A conditional forecast conditions on the realized *movement* of a declared driver.
    The realized value lives in the evaluation vintage while every other entry of the
    design row comes from the origin's vintage, so a design that reads the driver
    against its own lag — ``log(output_t / output_{t-1})`` — compares two vintages and
    picks up whatever rebasing or benchmark revision happened between them. Where the
    component declares ``conditional_rebase``, the realized value is carried onto the
    origin's vintage using the two frames' overlap at the anchor period, which preserves
    the realized movement exactly and introduces no information the origin did not have
    beyond that movement. Series the design reads as a level at the target period, and
    series that are never revised or rebased, declare ``'none'`` and are handed over
    unchanged.
    """
    modes = getattr(estimator, 'conditional_rebase', {}) or {}
    conditional, rebased = {}, {}
    for name in getattr(estimator, 'conditional_inputs', ()):
        values = [truth.columns[name][j] for j in range(index - horizon + 1, index + 1)]
        mode = modes.get(name, 'none')
        if mode == 'none' or name not in frame.columns or name not in truth.columns:
            conditional[name] = values
            continue
        try:
            origin_anchor = frame.columns[name][frame.times.index(anchor)]
            truth_anchor = truth.columns[name][index - horizon]
        except (ValueError, IndexError):
            conditional[name] = values
            continue
        if origin_anchor is None or truth_anchor is None:
            conditional[name] = values
            continue
        if mode == 'ratio':
            if truth_anchor == 0 or origin_anchor <= 0 or truth_anchor <= 0:
                conditional[name] = values
                continue
            factor = origin_anchor / truth_anchor
            conditional[name] = [None if v is None else v * factor for v in values]
            adjustment = math.log(factor)
        else:
            shift = origin_anchor - truth_anchor
            conditional[name] = [None if v is None else v + shift for v in values]
            adjustment = shift
        if adjustment:
            rebased[name] = {'mode': mode, 'adjustment': adjustment}
    return conditional, rebased


def _compact_backtest(result):
    return {k: v for k, v in result.items() if k != 'forecasts'} | {'forecasts': [
        {k: v for k, v in f.items() if k != 'parameters'} for f in result['forecasts']]}


def validate_process(estimator, data, *, train_end, validation_end, cutoff, candidates=None, criteria=None,
                     horizon=1, window='expanding', window_size=None, refit_every=1, vintage_policy='strict',
                     interval_level=0.8, baselines=('persistence', 'drift', 'historical_mean'), season=None,
                     fit_options=None, data_inputs=()):
    """Select on validation, freeze, score the untouched holdout, refit at cutoff and evaluate acceptance."""
    if not instant(train_end) < instant(validation_end) < instant(cutoff):
        raise ValueError('Require train_end < validation_end < cutoff')
    named = list(candidates) if candidates else [(type(estimator).__name__, estimator)]
    if len({name for name, _ in named}) != len(named):
        raise ValueError('Candidate names must be unique')
    requirements = []
    for _, candidate in named:
        requirements.extend(candidate.all_requirements())
    if hasattr(data, 'visible'):
        selection_view = data.visible(validation_end, requirements, vintage_policy=vintage_policy)
        visible = len(selection_view.records)
    else:  # Native mappings (model families) are restricted by the estimator itself.
        selection_view = named[0][1].visible_data(data, validation_end)
        visible = None
    common = dict(horizon=horizon, window=window, window_size=window_size, refit_every=refit_every,
                  vintage_policy=vintage_policy, interval_level=interval_level, baselines=baselines, season=season,
                  fit_options=fit_options)
    scores, selection_audit = {}, {'violations': 0, 'origins_checked': 0, 'data_cutoff': validation_end,
                                   'records_visible': visible}
    for name, candidate in named:
        try:
            runner = getattr(candidate, 'backtest', None)
            if runner is not None:
                result = runner(selection_view, start=train_end, end=validation_end, evaluation_cutoff=validation_end, **common)
            else:
                result = rolling_origin_backtest(candidate, selection_view, start=train_end, end=validation_end,
                                                 evaluation_cutoff=validation_end, **common)
            model = result['metrics']['model']
            scores[name] = {'count': model.get('count', 0), 'mse': model.get('mse'), 'mae': model.get('mae')}
            selection_audit['violations'] += result['leakage_audit']['violations']
            selection_audit['origins_checked'] += result['leakage_audit']['origins_checked']
        except ValueError as error:
            scores[name] = {'count': 0, 'error': str(error)}
    usable = [(name, c) for name, c in named if scores[name].get('count')]
    if not usable:
        if len(named) > 1:
            raise ValueError('No candidate produced validation forecasts; cannot select')
        selected_name, selected = named[0]
    else:
        order = {name: k for k, (name, _) in enumerate(named)}
        selected_name, selected = min(usable, key=lambda item: (scores[item[0]]['mse'], order[item[0]]))
    selection = {'candidates': [name for name, _ in named], 'selected': selected_name, 'scores': scores,
                 'train_end': train_end, 'validation_end': validation_end, 'leakage_audit': selection_audit,
                 'refit_after_selection': False}
    selection['selection_hash'] = digest(selection)
    if getattr(selected, 'backtest', None) is not None:
        test = selected.backtest(data, start=validation_end, end=cutoff, evaluation_cutoff=cutoff, **common)
    else:
        test = rolling_origin_backtest(selected, data, start=validation_end, end=cutoff, evaluation_cutoff=cutoff, **common)
    final = selected.fit(data, cutoff=cutoff, vintage_policy=vintage_policy, **(fit_options or {})).to_dict()
    report = {'schema': 'worldmodel.validation_report/1', 'process_id': selected.process_id, 'component': selected.component,
              'targets': list(getattr(selected, 'targets', None) or [selected.target]), 'estimator': type(selected).__name__,
              'protocol': {'train_end': train_end, 'validation_end': validation_end, 'cutoff': cutoff, 'horizon': horizon,
                           'window': window, 'window_size': window_size, 'refit_every': refit_every,
                           'vintage_policy': vintage_policy, 'interval_level': interval_level, 'baselines': list(baselines),
                           'season': season, 'origin_rule': 'first publication of anchor row across required series',
                           'actuals': 'latest vintage available by cutoff', 'fit_options': fit_options or {}},
              'selection': selection, 'test': _compact_backtest(test), 'final_estimate': final,
              'data_inputs': [dict(ref) for ref in data_inputs], 'causally_identified': False,
              'limitations': ['Out-of-sample skill on the holdout does not establish causal response to interventions.',
                              'Conditional forecasts use realized declared inputs; they do not test forecasting those inputs.',
                              'Gaussian predictive intervals are model-based approximations.']}
    criteria = list(criteria) if criteria is not None else selected.default_criteria()
    acceptance = evaluate_criteria(report, criteria)
    report['acceptance'] = acceptance
    report['validated'] = bool(acceptance['passed'])
    canonical(report)
    report['report_id'] = digest({k: v for k, v in report.items() if k != 'report_id'})
    return report


def verify_report_id(report):
    if 'report_id' not in report:
        # Estimates and validation reports are published side by side, so naming an
        # estimate here is an easy mistake; say so instead of reporting a hash mismatch.
        kind = 'estimate' if 'estimate_id' in report else 'artifact'
        raise ValueError(f'Expected a validation report but received an {kind} '
                         f'(no report_id); pass the report artifact instead')
    expected = digest({k: v for k, v in report.items() if k != 'report_id'})
    if report.get('report_id') != expected:
        raise ValueError('Validation report content does not match report_id')
    return True


def publish_validation_report(store, report, *, inputs=(), raw_inputs=(), dataset='validation_reports', parameters=None):
    """Publish an immutable, content-addressed report through the shared artifact path."""
    from ..artifacts import publish_report
    verify_report_id(report)
    return publish_report(store, dataset, report, parameters or {'process_id': report['process_id'], 'component': report['component'],
                          'protocol': report['protocol']}, inputs=list(inputs), raw_inputs=list(raw_inputs),
                          entrypoint='worldmodel.estimation.validation:validate_process')


def publish_estimate(store, estimate, *, inputs=(), raw_inputs=(), dataset='estimates', parameters=None):
    from ..artifacts import publish_report
    body = estimate if isinstance(estimate, dict) else estimate.to_dict()
    return publish_report(store, dataset, body, parameters or {'process_id': body['process_id'], 'component': body['component'],
                          'cutoff': body['cutoff'], 'vintage_policy': body['vintage_policy'], 'options': body.get('options', {})},
                          inputs=list(inputs), raw_inputs=list(raw_inputs), entrypoint='worldmodel.estimation.families:fit_component')
