"""Pre-registered assay of the world-state encoder on dated county subgraphs.

An attempt in ``plan.json`` fixes, before any holdout is scored: the panel, the history window,
the forecast origins of the validation and test windows, the candidates (the encoder on the full
subgraph and on the seed alone), the baselines (persistence, drift, and a gradient-boosted model
fed the same subgraph), the hyperparameters, and the acceptance criteria.

Protocol per target, mirroring :func:`worldmodel.estimation.validation.validate_process`:

* every evaluation origin ``T`` refits every forecaster on labels public by ``T-12-31``, with the
  two most recent public label years held out to calibrate predictive scales (split conformal);
* candidates are compared on the validation origins only; the selection is hashed and frozen;
* the selected candidate is scored once on the test origins, against every baseline, with pooled
  Diebold-Mariano tests (the repository's criterion) and, as a stated robustness check, a test on
  the per-year mean loss differences, which pooled tests overstate under common shocks.

Every report is published to ``embedding_reports`` with the panel version pinned. Requires numpy,
torch and lightgbm.
"""
import json
import math
import os
import pickle
import time
from pathlib import Path

import numpy as np

from ..estimation import intervals
from ..estimation.acceptance import evaluate_criteria
from ..estimation.distributions import t_cdf
from ..estimation.validation import baseline_forecast, score_forecasts
from ..util import canonical, digest
from .county_panel import DATASET as PANEL_DATASET, SOURCES, load as load_panel_records
from .tensors import Panel

PLAN = Path(__file__).with_name('plan.json')
REPORTS = 'embedding_reports'
ENTRYPOINT = 'worldmodel.embedding.assay:run_attempt'


def load_plan():
    return json.loads(PLAN.read_text())


def code_commit():
    """The checkout's git commit and whether its tracked files are modified (None outside a git checkout)."""
    import subprocess
    root = Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no', '--', 'worldmodel'],
                               capture_output=True, text=True, check=True).stdout.strip()
        return {'commit': head, 'worldmodel_modified': bool(dirty)}
    except (OSError, subprocess.CalledProcessError):
        # An exported, immutable run directory (git archive) records its commit in REVISION.
        revision = root / 'REVISION'
        if revision.exists():
            return {'commit': revision.read_text().strip(), 'worldmodel_modified': False, 'source': 'REVISION (git archive)'}
        return None


from .publish import publish_saved, save_reports  # noqa: E402,F401  (stdlib-only; re-exported)


def attempt_spec(attempt_id):
    for attempt in load_plan()['attempts']:
        if attempt['id'] == attempt_id:
            return attempt
    raise ValueError(f'No attempt {attempt_id!r} in {PLAN}')


def _panel_records(store, ref):
    """Values, availability, units and edges of one published panel version, cached beside it."""
    cache = Path(store.root) / ref['dataset'] / 'scratch' / f'panel-{ref["version"]}.pkl'
    if cache.exists():
        with cache.open('rb') as handle:
            return pickle.load(handle)
    store.verify(ref)
    _, values, available, units, edges = load_panel_records(store, ref)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open('wb') as handle:
        pickle.dump((values, available, units, edges), handle, protocol=pickle.HIGHEST_PROTOCOL)
    return values, available, units, edges


def load_panel(store, *, history, ref=None, edges_ref=None, node_feature=None):
    """The published panel as a :class:`Panel`.

    ``edges_ref`` takes the dated county edges (migration flows, CBSA membership) from another
    published panel: a first-release panel of vintaged series carries no edges of its own, and those
    edges are dated assertions that are never revised.
    """
    ref = ref or store.latest(PANEL_DATASET)
    values, available, units, edges = _panel_records(store, ref)
    if edges_ref is not None:
        edges = _panel_records(store, edges_ref)[3]
    return ref, Panel(values, available, units, edges, history=history, node_feature=node_feature)


# ----------------------------------------------------------------------------- gradient-boosted baseline

def gbdt_features(panel, snapshot, seed, cache_row):
    """Seed history and differences, subgraph means of the two latest slots, state and nation slots."""
    x = np.where(snapshot.m[seed] > 0, snapshot.x[seed], np.nan)                # [F, H]
    diff = x[:, :-1] - x[:, 1:]
    nodes = cache_row
    others = [n for n in nodes[1:] if panel.node_type[n] == 0]
    if others:
        block = np.where(snapshot.m[others][:, :, :2] > 0, snapshot.x[others][:, :, :2], np.nan)
        with np.errstate(all='ignore'):
            neighbour = np.nanmean(block, axis=0)
    else:
        neighbour = np.full((x.shape[0], 2), np.nan)
    state, nation = nodes[1], nodes[2]
    upper = [np.where(snapshot.m[n][:, :3] > 0, snapshot.x[n][:, :3], np.nan) for n in (state, nation)]
    return np.concatenate([x.ravel(), diff.ravel(), neighbour.ravel(), upper[0].ravel(), upper[1].ravel()]).astype(np.float32)


def fit_gbdt(features, y, train, calibrate, params):
    import lightgbm
    models, sds = [], []
    for j in range(y.shape[1]):
        rows = train & np.isfinite(y[:, j])
        model = lightgbm.LGBMRegressor(**params)
        model.fit(features[rows], y[rows, j])
        cal = calibrate & np.isfinite(y[:, j])
        if cal.sum() >= 30:
            residual = np.abs(y[cal, j] - model.predict(features[cal]))
            sd = float(np.quantile(residual, 0.8) / 1.2815515655446004)
        else:
            sd = float(np.std(y[rows, j] - model.predict(features[rows])))
        models.append(model)
        sds.append(sd)
    return models, sds


# ----------------------------------------------------------------------------- scoring helpers

def year_clustered_dm(forecasts, baseline):
    """One-sided t-test on per-year mean squared-loss differences (model minus baseline)."""
    by_year = {}
    for f in forecasts:
        if baseline in f['baselines']:
            d = (f['mean'] - f['actual']) ** 2 - (f['baselines'][baseline]['mean'] - f['actual']) ** 2
            by_year.setdefault(f['year'], []).append(d)
    means = [sum(v) / len(v) for _, v in sorted(by_year.items())]
    if len(means) < 3:
        return {'years': len(means), 'pvalue': None, 'reason': 'fewer than three years'}
    mean = sum(means) / len(means)
    sd = math.sqrt(sum((m - mean) ** 2 for m in means) / (len(means) - 1))
    if sd == 0:
        return {'years': len(means), 'pvalue': None, 'reason': 'no variation across years'}
    statistic = mean / (sd / math.sqrt(len(means)))
    return {'years': len(means), 'per_year_mean_difference': means, 'statistic': statistic,
            'pvalue': t_cdf(statistic, len(means) - 1), 'alternative': 'model loss lower'}


def _mse(rows):
    return sum((r['mean'] - r['actual']) ** 2 for r in rows) / len(rows) if rows else None


# ----------------------------------------------------------------------------- the attempt

def run_attempt(store, attempt_id, *, log=print, publish=True, device=None, spec=None):
    """Run one attempt. ``spec`` replaces the plan entry for smoke tests only; it cannot be published."""
    import torch
    from .train import SubgraphCache, build_samples, fit, label_public, limit_gpu_memory
    limit_gpu_memory()

    started = time.time()
    code = code_commit()          # at start: the checkout could be changed while a long run is going
    if spec is not None and publish:
        raise ValueError('An attempt that is not in plan.json cannot be published')
    attempt = json.loads(json.dumps(spec)) if spec is not None else attempt_spec(attempt_id)
    protocol, config = attempt['protocol'], attempt['config']
    targets = attempt['targets']
    panel_ref, panel = load_panel(store, history=protocol['history'], ref=attempt.get('panel'),
                                 edges_ref=attempt.get('edges_from'), node_feature=attempt.get('node_feature'))
    panel.fit_standardization(protocol['standardize_through'])
    validation = list(range(protocol['validation_origins'][0], protocol['validation_origins'][1] + 1))
    test = list(range(protocol['test_origins'][0], protocol['test_origins'][1] + 1))
    origins = list(range(protocol['first_origin'], test[-1] + 1))
    log(f'{attempt_id}: panel {panel_ref["version"][:12]} counties={len(panel.counties)} features={len(panel.features)}')
    snapshots = [panel.snapshot(t) for t in origins]
    rows = {t: i for i, t in enumerate(origins)}
    leakage = {'origins_checked': 0, 'violations': 0, 'max_available_by_origin': {}}
    for snap in snapshots:
        leakage['origins_checked'] += 1
        leakage['max_available_by_origin'][snap.origin] = snap.max_available
        leakage['violations'] += int(snap.max_available > snap.origin)
    device = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
    x_all = torch.as_tensor(np.stack([s.x for s in snapshots]), device=device)
    m_all = torch.as_tensor(np.stack([s.m for s in snapshots]), device=device)
    samples = build_samples(panel, rows, origins, targets)
    caches = {name: SubgraphCache(panel, snapshots, config['max_nodes'], seed_only=spec.get('seed_only', False))
              for name, spec in attempt['candidates'].items()}
    graph_cache = caches[attempt['gbdt_subgraph_from']]
    log(f'  samples={len(samples.seed)}; building gradient-boosted features')
    features = np.stack([gbdt_features(panel, snapshots[r], s, graph_cache.get(int(r), int(s))[0])
                         for r, s in zip(samples.snap, samples.seed)])

    forecasts = {name: {j: [] for j in range(len(targets))} for name in attempt['candidates']}
    fits, checkpoints = [], {}
    for T in validation + test:
        public = label_public(panel, samples, targets, T)
        leakage['origins_checked'] += 1
        evaluate = np.flatnonzero(samples.origin == T)
        stage = 'validation' if T in validation else 'test'
        last = int(samples.label_year[public.any(axis=1)].max())
        calibrate = public.any(axis=1) & (samples.label_year > last - config['calibration_years'])
        train = public.any(axis=1) & ~calibrate
        y_public = np.where(public, samples.y, np.nan)
        gbdt, gbdt_sd = fit_gbdt(features, y_public, train, calibrate, attempt['gbdt'])
        gbdt_pred = np.stack([m.predict(features[evaluate]) for m in gbdt], axis=1)
        for name, spec in attempt['candidates'].items():
            if stage == 'test' and name not in attempt.get('_selected', {}).values():
                continue
            cfg = dict(config, **spec)
            forecaster, batcher = fit(panel, snapshots, samples, public, targets, config=cfg, x_all=x_all, m_all=m_all,
                                      cache=caches[name], log=None)
            pred = forecaster.predict(batcher, samples.snap[evaluate], samples.seed[evaluate])
            fits.append({'origin': T, 'stage': stage, 'candidate': name, **forecaster.diagnostics})
            log(f'    peak GPU {forecaster.diagnostics["peak_gpu_gib"]} GiB')
            if T == test[-1]:          # the encoder of the last origin, for embed-query; not part of the scoring
                from .query import save_checkpoint
                path = Path(store.root) / REPORTS / 'scratch' / 'checkpoints' / f'{attempt_id}.{name}.{T}.pt'
                save_checkpoint(path, forecaster, panel_ref, panel, targets, cfg, T)
                checkpoints[name] = str(path)
            log(f'  {stage} origin {T} {name}: {forecaster.diagnostics["seconds"]}s '
                f'train={forecaster.diagnostics["train_samples"]}')
            for j, feature in enumerate(targets):
                for k, i in enumerate(evaluate):
                    anchor = samples.anchor[i, j]
                    if not np.isfinite(samples.y[i, j]) or not np.isfinite(anchor):
                        continue
                    horizon = int(samples.label_year[i] - samples.anchor_year[i, j])
                    history = [v for _, v in panel.history_values(int(samples.seed[i]), feature, T)]
                    if len(history) < 3:
                        continue
                    df = float(pred['df'][j])
                    scale = float(pred['scale'][k, j])
                    predictive = intervals.student_t(scale, df)
                    persistence = baseline_forecast('persistence', history, horizon=horizon)
                    drift = baseline_forecast('drift', history, horizon=horizon)
                    forecasts[name][j].append({
                        'target': f'{feature}:{panel.counties[samples.seed[i]]}', 'year': int(samples.label_year[i]),
                        'origin': T, 'actual': anchor + float(samples.y[i, j]),
                        'mean': anchor + float(pred['mean'][k, j]), 'sd': intervals.standard_deviation(predictive),
                        'predictive': predictive,
                        'baselines': {'persistence': persistence, 'drift': drift,
                                      'gbdt': {'mean': anchor + float(gbdt_pred[k, j]), 'sd': gbdt_sd[j]}}})
            del forecaster, batcher, pred
            if device.type == 'cuda':
                torch.cuda.empty_cache()
        if T == validation[-1]:
            attempt['_selected'] = {}
            for j, feature in enumerate(targets):
                scores = {name: _mse([f for f in forecasts[name][j] if f['origin'] in validation])
                          for name in attempt['candidates']}
                attempt['_selected'][feature] = min((n for n in scores if scores[n] is not None),
                                                    key=lambda n: (scores[n], list(attempt['candidates']).index(n)))
                attempt.setdefault('_validation_scores', {})[feature] = scores
            log(f'  selection: {attempt["_selected"]}')

    # An attempt on another panel declares its own data audit; the dated panel's sources are the default.
    series = attempt.get('series')
    if series is None:
        series = {source: {'series': source, 'revisions': spec['revisions'], 'vintage_modes': ['valid_time_rows'],
                           'revision_leakage_possible': spec['revisions'] not in ('none', 'static')}
                  for source, spec in SOURCES.items() if source not in ('migration', 'cbsa')}
        series.update({source: {'series': source, 'revisions': 'none', 'vintage_modes': ['valid_time_rows'],
                                'revision_leakage_possible': False} for source in ('migration', 'cbsa')})
    reports = []
    for j, feature in enumerate(targets):
        selected = attempt['_selected'][feature]
        test_rows = [f for f in forecasts[selected][j] if f['origin'] in test]
        scored = score_forecasts(test_rows, baselines=attempt['baselines'], interval_level=0.8)
        validation_scores = {name: {'mse': score, 'count': len([f for f in forecasts[name][j] if f['origin'] in validation])}
                             for name, score in attempt['_validation_scores'][feature].items()}
        gbdt_validation = [{'mean': f['baselines']['gbdt']['mean'], 'actual': f['actual']}
                           for f in forecasts[selected][j] if f['origin'] in validation]
        selection = {'candidates': list(attempt['candidates']), 'selected': selected, 'scores': validation_scores,
                     'baseline_validation_mse': {'gbdt': _mse(gbdt_validation)},
                     'validation_origins': validation, 'refit_after_selection': False}
        selection['selection_hash'] = digest(selection)
        report = {
            'schema': 'worldmodel.validation_report/1', 'attempt': attempt_id, 'process_id': 'places_model',
            'component': f'places_embedding:{feature}', 'targets': [f'log_level_next_year:{feature}'],
            'estimator': f'WorldStateEncoder[{selected}]',
            'protocol': {**protocol, 'horizon': 'label year t+1 from origin t-12-31; anchor = latest public value',
                         'refit_every': 1, 'interval_level': 0.8, 'baselines': attempt['baselines'],
                         'vintage_policy': 'retrospective_with_declared_publication_lags',
                         'actuals': 'current vintage at retrieval'},
            'selection': selection,
            'test': {**scored, 'forecast_count': len(test_rows),
                     'leakage_audit': {'origins_checked': leakage['origins_checked'], 'violations': leakage['violations'],
                                       'vintage_modes': ['valid_time_rows']},
                     'year_clustered_dm': {b: year_clustered_dm(test_rows, b) for b in attempt['baselines']},
                     'per_year': {str(y): {'count': len(v), 'model_mse': _mse(v),
                                           **{b: _mse([{'mean': f['baselines'][b]['mean'], 'actual': f['actual']} for f in v])
                                              for b in attempt['baselines']}}
                                  for y, v in sorted(_by(test_rows, 'year').items())}},
            'final_estimate': {'data_audit': {'series': series, 'max_available_by_origin': leakage['max_available_by_origin']},
                               'diagnostics': {'fits': fits, 'config': config, 'checkpoints': checkpoints,
                                               'code': code}, 'bounds_check': {}},
            'data_inputs': [dict(panel_ref)], 'causally_identified': False,
            'limitations': ['Out-of-sample skill does not establish the response of any place to an intervention.',
                            'The panel is the current vintage: sources that revise leak their revisions into the '
                            'origins that precede them (declared in final_estimate.data_audit.series).',
                            'Publication dates are declared per-source rules, not measured release timestamps.',
                            'Pooled Diebold-Mariano tests treat county-years as independent; common annual shocks make '
                            'them overstate significance. test.year_clustered_dm is the conservative check.']}
        report['acceptance'] = evaluate_criteria(report, attempt['criteria'])
        report['validated'] = bool(report['acceptance']['passed'])
        canonical(report)
        report['report_id'] = digest({k: v for k, v in report.items() if k != 'report_id'})
        publication = {'parameters': {'attempt': attempt_id, 'target': feature}, 'inputs': [dict(panel_ref)],
                       'entrypoint': ENTRYPOINT}
        ref = None
        if publish:
            from ..artifacts import publish_report
            ref = publish_report(store, REPORTS, report, publication['parameters'], inputs=publication['inputs'],
                                 entrypoint=ENTRYPOINT)
        reports.append({'target': feature, 'ref': ref, 'report': report, 'publication': publication})
        log(f'  {feature}: selected={selected} validated={report["validated"]} '
            + ' '.join(f'{r["id"]}={"pass" if r["passed"] else "FAIL"}' for r in report['acceptance']['results']))
    log(f'{attempt_id}: {round(time.time() - started)}s')
    return reports


def _by(rows, key):
    out = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return out
