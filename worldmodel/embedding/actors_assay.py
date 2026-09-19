"""Pre-registered actors assay: 13F exit and increase, scored like every other attempt.

Protocol (fixed per attempt in ``plan.json``):

* origins are quarters; the origin date of quarter q is its 13F filing deadline (q end + 45 days);
* fits happen at the first origin of each block (validation block, then each test year); a fit
  trains on samples whose label filing is dated on or before the fit's origin, holding out the two
  latest such origin quarters to calibrate a temperature per target;
* candidates (the encoder on the template subgraph, and on the query position alone) are compared on
  the validation block only, then the selected candidate is scored once on the test blocks;
* baselines: the training base rate (``historical_mean``, the harness's Brier-skill reference), the
  manager's own exit rate at the origin (exit only), and LightGBM on the same template features;
* probability scoring through :func:`worldmodel.estimation.validation.score_forecasts`, with pooled
  Diebold-Mariano tests on Brier loss and, beside them, a test on per-quarter mean differences.

Requires numpy, torch and lightgbm.
"""
import math
import time
from pathlib import Path

import numpy as np

from ..estimation.acceptance import evaluate_criteria
from ..estimation.distributions import t_cdf
from ..estimation.validation import score_forecasts
from ..util import canonical, digest
from . import actors, holdings as holdings_module
from .assay import REPORTS, attempt_spec, code_commit
from .holdings import quarter_end

ENTRYPOINT = 'worldmodel.embedding.actors_assay:run_attempt'


def _batch(torch, gpu, idx, edges, node_type, seed_only):
    x = gpu['x'][idx].float()
    m = gpu['m'][idx].float()
    valid = gpu['valid'][idx]
    if seed_only:
        x, m, valid = x[:, :1], m[:, :1], valid[:, :1]
        empty = torch.zeros(0, dtype=torch.long, device=x.device)
        return {'x': x, 'm': m, 'node_type': node_type[:1].expand(len(idx), 1), 'valid': valid,
                'src': empty, 'dst': empty, 'rel': empty, 'weight': torch.zeros(0, device=x.device)}
    B, N = valid.shape
    offset = (torch.arange(B, device=x.device) * N)[:, None]
    src, dst, rel = edges[:, 0][None] + offset, edges[:, 1][None] + offset, edges[:, 2][None].expand(B, -1)
    weight = gpu['w'][idx]
    keep = valid.reshape(-1)[src] & valid.reshape(-1)[dst]
    return {'x': x, 'm': m, 'node_type': node_type[None].expand(B, -1), 'valid': valid,
            'src': src[keep], 'dst': dst[keep], 'rel': rel[keep], 'weight': weight[keep]}


def fit_encoder(torch, gpu, edges, node_type, y, train, calibrate, cfg, *, seed_only, log=None, forecast_weight=1.0,
                dims=(len(actors.NODE_TYPES), len(actors.RELATIONS))):
    """``forecast_weight=0`` trains on masked reconstruction alone: a task-agnostic, label-free embedding."""
    from .model import WorldStateEncoder
    torch.manual_seed(cfg['seed'])
    device = gpu['x'].device
    F, H = gpu['x'].shape[2], gpu['x'].shape[3]
    model = WorldStateEncoder(F, dims[0], dims[1], H, d=cfg['d'], layers=cfg['layers'],
                              slots=cfg['slots'], passes=cfg['passes'], top_k=cfg['top_k'], out_dim=cfg['out_dim'],
                              n_targets=y.shape[1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg['lr'], weight_decay=cfg['weight_decay'])
    rows = np.flatnonzero(train)
    steps = max(1, cfg['epochs'] * math.ceil(len(rows) / cfg['batch']))
    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=cfg['lr'], total_steps=steps, pct_start=0.1)
    y_t = torch.as_tensor(np.nan_to_num(y), dtype=torch.float32, device=device)
    have_t = torch.as_tensor(np.isfinite(y), device=device)
    rng = np.random.default_rng(cfg['seed'])
    started, step = time.time(), 0
    model.train()
    for _ in range(cfg['epochs']):
        rng.shuffle(rows)
        for start in range(0, len(rows), cfg['batch']):
            chosen = rows[start:start + cfg['batch']]
            idx = torch.as_tensor(chosen, device=device)
            progress = step / steps
            model.readout.top_k = None if progress < cfg['dense_fraction'] else cfg['top_k']
            model.readout.gumbel = cfg['gumbel'] * (1 - progress)
            b = _batch(torch, gpu, idx, edges, node_type, seed_only)
            present = b['m'].amax(-1) > 0
            eligible = present & b['valid'].unsqueeze(-1)
            eligible[:, 0] = False
            token_mask = eligible & (torch.rand(present.shape, device=device) < cfg['mask_rate'])
            with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == 'cuda'):
                encoded = model.encode(b, token_mask=token_mask)
                logits, _, _ = model.predict(encoded)
                have = have_t[idx]
                bce = torch.nn.functional.binary_cross_entropy_with_logits(logits.float(), y_t[idx], reduction='none')
                loss_forecast = (bce * have).sum() / have.sum().clamp_min(1)
                index, predicted = model.reconstruct(encoded, token_mask)
                if predicted is not None:
                    bb, nn_, ff = index.unbind(-1)
                    target, weight = b['x'][bb, nn_, ff], b['m'][bb, nn_, ff]
                    recon = ((predicted.float() - target) ** 2 * weight).sum() / weight.sum().clamp_min(1)
                else:
                    recon = torch.zeros((), device=device)
                loss = forecast_weight * loss_forecast + cfg['recon_weight'] * recon
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            step += 1
            if log and step % 500 == 0:
                log(f'    step {step}/{steps} bce={float(loss_forecast.detach()):.4f} recon={float(recon.detach()):.4f}')
    model.readout.top_k, model.readout.gumbel = cfg['top_k'], 0.0
    temperature = np.ones(y.shape[1])
    cal = np.flatnonzero(calibrate)
    if len(cal) and forecast_weight > 0:
        logits = predict_logits(torch, model, gpu, edges, node_type, cal, seed_only)
        for j in range(y.shape[1]):
            ok = np.isfinite(y[cal, j])
            if ok.sum() >= 100:
                temperature[j] = _temperature(logits[ok, j], y[cal, j][ok])
    peak = torch.cuda.max_memory_allocated() / 2 ** 30 if device.type == 'cuda' else None
    if device.type == 'cuda':
        torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    return model, temperature, {'train_samples': int(len(rows)), 'calibration_samples': int(len(cal)), 'steps': steps,
                                'seconds': round(time.time() - started, 1), 'temperature': temperature.tolist(),
                                'peak_gpu_gib': round(peak, 2) if peak is not None else None}


def predict_logits(torch, model, gpu, edges, node_type, rows, seed_only, batch=1024, state=False):
    """Logits for ``rows``; with ``state=True``, the 1,024-d state vectors instead."""
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(rows), batch):
            idx = torch.as_tensor(rows[start:start + batch], device=gpu['x'].device)
            b = _batch(torch, gpu, idx, edges, node_type, seed_only)
            with torch.autocast(device_type=b['x'].device.type, dtype=torch.bfloat16, enabled=b['x'].is_cuda):
                encoded = model.encode(b)
                value = encoded['state'] if state else model.predict(encoded)[0]
            out.append(value.float().cpu().numpy())
    return np.concatenate(out)


def principal_components(train_states, k):
    """Mean and top-``k`` right singular vectors, fitted on training rows only."""
    mean = train_states.mean(axis=0)
    sample = train_states[np.random.default_rng(0).permutation(len(train_states))[:50000]] - mean
    _, _, vt = np.linalg.svd(sample, full_matrices=False)
    return mean, vt[:k].T


def stacked_probabilities(lightgbm, params, features, extra, y, train, evaluate):
    """LightGBM on the template features plus ``extra`` columns; probabilities for ``evaluate`` per target."""
    X = np.concatenate([features, extra.astype(np.float32)], axis=1)
    out = np.zeros((len(evaluate), y.shape[1]))
    for j in range(y.shape[1]):
        rows = train & np.isfinite(y[:, j])
        model = lightgbm.LGBMClassifier(**params)
        model.fit(X[rows], y[rows, j].astype(int))
        out[:, j] = model.predict_proba(X[evaluate])[:, 1]
    return out


def _temperature(logits, labels):
    """Temperature minimizing log loss, by golden-section search on [0.25, 4]."""
    def loss(t):
        p = 1 / (1 + np.exp(-logits / t))
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return -np.mean(labels * np.log(p) + (1 - labels) * np.log(1 - p))
    a, b = 0.25, 4.0
    g = (math.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(40):
        if loss(c) < loss(d):
            b = d
        else:
            a = c
        c, d = b - g * (b - a), a + g * (b - a)
    return (a + b) / 2


def gbdt_features(tasks, singles, groups):
    """Each single node's features as they are, and the mean of each group of neighbour nodes."""
    x = np.where(tasks.m.astype(bool), tasks.x.astype(np.float32), np.nan)
    S = x.shape[0]
    blocks = [x[:, i] for i in singles]
    with np.errstate(all='ignore'):
        for lo, hi in groups:
            blocks.append(np.nanmean(x[:, lo:hi], axis=1))
    return np.concatenate([b.reshape(S, -1) for b in blocks], axis=1)


def _prepare_13f(store, attempt, protocol, config, log):
    source_ref = attempt['input']
    path, meta = holdings_module.build(store, source_ref, log=log)
    arrays, _ = holdings_module.load(path)
    log(f'{attempt["id"]}: holdings {meta["counts"]["holdings"]:,} rows, {meta["managers"]:,} managers, '
        f'{meta["securities"]:,} securities')
    data = actors.Holdings13F(arrays, top=protocol['k'] + 1)
    del arrays
    q_of = {quarter_end(q): q for q in range(data.n_quarters)}
    first = q_of[protocol['first_origin_quarter']]
    blocks = [[q_of[a], q_of[b]] for a, b in protocol['blocks']]
    tasks = actors.build_tasks(data, range(first, blocks[-1][1] + 1), per_quarter=protocol['per_quarter'],
                               per_manager=protocol['per_manager'], k=protocol['k'], history=protocol['history'],
                               seed=config['seed'])
    k = protocol['k']
    deadline_day = lambda q: actors.day_number(actors.deadline(q))
    return {'tasks': tasks, 'q_of': q_of, 'first': first, 'blocks': blocks, 'period_end': quarter_end,
            'origin_day': deadline_day, 'label_public_by': lambda label_day, origin: label_day <= origin,
            'feature_leakage': lambda q: int(tasks.max_feature_filed[q] > deadline_day(q)),
            'template': actors.template(k), 'dims': (len(actors.NODE_TYPES), len(actors.RELATIONS)),
            'targets': list(actors.TARGETS), 'own_rate': 'manager_rate',
            'gbdt_nodes': ((0, 1, 2), ((3, 3 + k), (3 + k, 3 + 2 * k), (3 + 2 * k, 3 + 3 * k))),
            'inputs': [dict(source_ref)], 'component': '13f', 'target_suffix': 'next_quarter',
            'series': {'sec_13f_history': {
                'series': 'sec_13f_history', 'revisions': 'none', 'vintage_modes': ['original_filing'],
                'revision_leakage_possible': False,
                'note': 'Original 13F-HR filings only; amendments (13F-HR/A) are excluded, so no later restatement enters.'}},
            'audit_extra': {'input': dict(source_ref), 'holdings_meta': meta},
            'origin_text': 'quarter end + 45 days (13F deadline)', 'vintage_policy': 'original_13F-HR_filings_only',
            'actuals': 'original 13F-HR filing for the next quarter',
            'limitations': [
                'Samples are conditioned on the manager filing again next quarter.',
                '13F covers long US-listed equity positions of managers above the reporting threshold; exits may be '
                'sales below the threshold, transfers or reclassifications, not trades.']}


def _prepare_votes(store, attempt, protocol, config, log):
    from . import votes
    from datetime import date
    refs = attempt['inputs']
    data = votes.load(store, refs=refs, log=log)
    q_of = {votes.quarter_end(q): q for q in range(0, 120)}
    first = q_of[protocol['first_origin_quarter']]
    blocks = [[q_of[a], q_of[b]] for a, b in protocol['blocks']]
    tasks = votes.build_tasks(data, first_quarter=first, last_quarter=blocks[-1][1], per_quarter=protocol['per_quarter'],
                              per_member=protocol['per_member'], history=protocol['history'], seed=config['seed'], log=log)
    start_day = lambda q: votes.day_number(date.fromordinal(votes.quarter_start_ordinal(q)))
    K = votes.K
    return {'tasks': tasks, 'q_of': q_of, 'first': first, 'blocks': blocks, 'period_end': votes.quarter_end,
            'origin_day': start_day, 'label_public_by': lambda label_day, origin: label_day < origin,
            'feature_leakage': lambda q: 0,
            'template': votes.template(), 'dims': (len(votes.NODE_TYPES), len(votes.RELATIONS)),
            'targets': list(votes.TARGETS), 'own_rate': votes.OWN_RATE,
            'gbdt_nodes': ((0, 1, 2, 3), ((4, 4 + K), (4 + K, 4 + 2 * K))),
            'inputs': [dict(refs['voteview_rollcalls']), dict(refs['influence_bills'])], 'component': 'votes',
            'target_suffix': 'on_party_unity_vote',
            'series': {'voteview_rollcalls': {'series': 'voteview_rollcalls', 'revisions': 'none',
                                              'vintage_modes': ['recorded_vote'], 'revision_leakage_possible': False,
                                              'note': 'Recorded votes are final. NOMINATE scores, which are re-estimated '
                                                      'from later votes, are not used.'},
                       'influence_bills': {'series': 'influence_bills', 'revisions': 'none',
                                           'vintage_modes': ['sponsor_at_introduction'], 'revision_leakage_possible': False,
                                           'note': 'Only the sponsor and introduction date are used; undated cosponsor '
                                                   'lists are not.'}},
            'audit_extra': {'inputs': refs, 'structural_leakage_check': 'feature windows end the day before each vote',
                            'leakage_violations_counted_in_build': int(tasks.leakage_violations)},
            'origin_text': 'the day of the roll call; features from roll calls strictly before it',
            'vintage_policy': 'recorded_votes', 'actuals': 'the recorded vote',
            'limitations': ['Party-unity votes only; a member who abstains has no sample.',
                            'Defection is relative to the party majority on that roll call, which is determined by the '
                            'same roll call (a definition, not a feature).']}


def quarter_clustered_dm(rows, baseline):
    by = {}
    for r in rows:
        if baseline in r['baselines']:
            d = (r['mean'] - r['actual']) ** 2 - (r['baselines'][baseline]['mean'] - r['actual']) ** 2
            by.setdefault(r['quarter'], []).append(d)
    means = [sum(v) / len(v) for _, v in sorted(by.items())]
    if len(means) < 3:
        return {'quarters': len(means), 'pvalue': None}
    mean = sum(means) / len(means)
    sd = math.sqrt(sum((v - mean) ** 2 for v in means) / (len(means) - 1))
    if sd == 0:
        return {'quarters': len(means), 'pvalue': None}
    statistic = mean / (sd / math.sqrt(len(means)))
    return {'quarters': len(means), 'statistic': statistic, 'pvalue': t_cdf(statistic, len(means) - 1),
            'per_quarter_mean_difference': means}


def run_attempt(store, attempt_id, *, log=print, publish=True, device=None, spec=None):
    import lightgbm
    import torch
    from ..artifacts import publish_report
    from .train import limit_gpu_memory
    limit_gpu_memory()
    started = time.time()
    code = code_commit()          # at start: the checkout could be changed while a long run is going
    if spec is not None and publish:
        raise ValueError('An attempt that is not in plan.json cannot be published')
    attempt = spec if spec is not None else attempt_spec(attempt_id)
    protocol, config = attempt['protocol'], attempt['config']
    prepare = {'13f': _prepare_13f, 'votes': _prepare_votes}[attempt.get('domain', '13f')]
    dom = prepare(store, attempt, protocol, config, log)
    tasks, q_of, first, blocks = dom['tasks'], dom['q_of'], dom['first'], dom['blocks']
    quarter_end_ = dom['period_end']
    last = blocks[-1][1]
    log(f'  samples {len(tasks.quarter):,} over periods {first}..{last}')
    standard_rows = tasks.quarter <= q_of[protocol['standardize_through']]
    mean, std = actors.standardize(tasks, standard_rows)
    device = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
    gpu = {'x': torch.as_tensor(tasks.x, device=device), 'm': torch.as_tensor(tasks.m, device=device),
           'valid': torch.as_tensor(tasks.node_valid, device=device), 'w': torch.as_tensor(tasks.weights, device=device)}
    n_nodes, edges_np, node_type_np = dom['template']
    edges, node_type = torch.as_tensor(edges_np, device=device), torch.as_tensor(node_type_np, device=device)
    features = gbdt_features(tasks, *dom['gbdt_nodes'])
    targets = dom['targets']
    forecasts = {name: {j: [] for j in range(len(targets))} for name in attempt['candidates']}
    fits, selected, validation_scores = [], {}, {}
    leakage = {'origins_checked': 0, 'violations': 0}
    for block_index, (lo, hi) in enumerate(blocks):
        stage = 'validation' if block_index == 0 else 'test'
        origin = dom['origin_day'](lo)
        usable = dom['label_public_by'](tasks.label_filed, origin) & (tasks.quarter < lo)
        usable_quarters = np.unique(tasks.quarter[usable])
        calibration_quarters = usable_quarters[-config['calibration_quarters']:]
        calibrate = usable & np.isin(tasks.quarter, calibration_quarters)
        train = usable & ~calibrate
        y_fit = np.where(usable[:, None], tasks.y, np.nan)
        evaluate = np.flatnonzero((tasks.quarter >= lo) & (tasks.quarter <= hi))
        for q in range(lo, hi + 1):
            leakage['origins_checked'] += 1
            leakage['violations'] += dom['feature_leakage'](q)
        # Leakage of labels into the fit: every training label was public by the fit origin (by construction).
        leakage['violations'] += int((~dom['label_public_by'](tasks.label_filed[train], origin)).sum())
        base_rate = np.nanmean(y_fit[train], axis=0)
        gbdt = []
        for j in range(len(targets)):
            rows = train & np.isfinite(tasks.y[:, j])
            model = lightgbm.LGBMClassifier(**attempt['gbdt'])
            model.fit(features[rows], tasks.y[rows, j].astype(int))
            gbdt.append(model.predict_proba(features[evaluate])[:, 1])
        for name, candidate in attempt['candidates'].items():
            if stage == 'test' and name not in selected.values():
                continue
            kind = candidate.get('kind', 'encoder')
            block = [quarter_end_(lo), quarter_end_(hi)]
            if kind == 'encoder':
                model, temperature, diag = fit_encoder(torch, gpu, edges, node_type, y_fit, train, calibrate, config,
                                                       seed_only=candidate['seed_only'], log=log, dims=dom['dims'])
                logits = predict_logits(torch, model, gpu, edges, node_type, evaluate, candidate['seed_only'])
                probability = 1 / (1 + np.exp(-logits / temperature[None]))
                fits.append({'block': block, 'stage': stage, 'candidate': name, **diag})
                del model
            elif kind == 'gbdt_plus_self_supervised_embedding':
                # Label-free: reconstruction only, on every row whose features were public before the block.
                seen = train | calibrate
                model, _, diag = fit_encoder(torch, gpu, edges, node_type, y_fit, seen, np.zeros_like(seen), config,
                                             seed_only=False, log=log, forecast_weight=0.0, dims=dom['dims'])
                train_rows = np.flatnonzero(train)
                states_train = predict_logits(torch, model, gpu, edges, node_type, train_rows, False, state=True)
                states_eval = predict_logits(torch, model, gpu, edges, node_type, evaluate, False, state=True)
                mean_, basis = principal_components(states_train, candidate['components'])
                extra = np.full((len(tasks.quarter), candidate['components']), np.nan, dtype=np.float32)
                extra[train_rows] = (states_train - mean_) @ basis
                extra[evaluate] = (states_eval - mean_) @ basis
                probability = stacked_probabilities(lightgbm, attempt['gbdt'], features, extra, tasks.y, train, evaluate)
                fits.append({'block': block, 'stage': stage, 'candidate': name, 'self_supervised': diag})
                del model, states_train, states_eval
            elif kind == 'gbdt_plus_crossfit_encoder':
                # Two encoders on alternating training quarters; each training row gets logits only from the encoder
                # that never saw it, and evaluation rows get the mean of both.
                fold = (tasks.quarter % 2).astype(int)
                extra = np.full((len(tasks.quarter), len(targets)), np.nan, dtype=np.float32)
                eval_logits, diags = np.zeros((len(evaluate), len(targets))), []
                for f in (0, 1):
                    model, _, diag = fit_encoder(torch, gpu, edges, node_type, y_fit, train & (fold != f),
                                                 np.zeros_like(train), config, seed_only=False, log=log, dims=dom['dims'])
                    held = np.flatnonzero(train & (fold == f))
                    extra[held] = predict_logits(torch, model, gpu, edges, node_type, held, False)
                    eval_logits += predict_logits(torch, model, gpu, edges, node_type, evaluate, False) / 2
                    diags.append(diag)
                    del model
                extra[evaluate] = eval_logits
                probability = stacked_probabilities(lightgbm, attempt['gbdt'], features, extra, tasks.y, train, evaluate)
                fits.append({'block': block, 'stage': stage, 'candidate': name, 'folds': diags})
            else:
                raise ValueError(f'Unknown candidate kind {kind!r}')
            log(f'  {stage} block {block[0]}..{block[1]} {name}: done')
            for j in range(len(targets)):
                for k_, i in enumerate(evaluate):
                    if not np.isfinite(tasks.y[i, j]):
                        continue
                    baselines = {'historical_mean': {'mean': float(base_rate[j]), 'sd': 0.0},
                                 'gbdt': {'mean': float(gbdt[j][k_]), 'sd': 0.0}}
                    if j == 0 and np.isfinite(tasks.base_manager[i, 0]):
                        baselines[dom['own_rate']] = {'mean': float(tasks.base_manager[i, 0]), 'sd': 0.0}
                    forecasts[name][j].append({'target': f'{targets[j]}:{int(tasks.manager[i])}:{int(tasks.security[i])}',
                                               'quarter': quarter_end_(int(tasks.quarter[i])), 'actual': float(tasks.y[i, j]),
                                               'mean': float(probability[k_, j]), 'baselines': baselines})
            if device.type == 'cuda':
                torch.cuda.empty_cache()
        if stage == 'validation':
            for j, target in enumerate(targets):
                scores = {n: float(np.mean([(f['mean'] - f['actual']) ** 2 for f in forecasts[n][j]])) for n in attempt['candidates']}
                validation_scores[target] = scores
                selected[target] = min(scores, key=lambda n: (scores[n], list(attempt['candidates']).index(n)))
            log(f'  selection: {selected}')
    reports = []
    validation_quarters = {quarter_end_(q) for q in range(blocks[0][0], blocks[0][1] + 1)}
    for j, target in enumerate(targets):
        chosen = selected[target]
        test_rows = [f for f in forecasts[chosen][j] if f['quarter'] not in validation_quarters]
        # A baseline missing on some rows (the manager's own rate needs a prior quarter) is scored on the rows
        # that have it, as score_forecasts does; dropping it would leave its declared criterion unevaluable.
        baselines = [b for b in attempt['baselines'] if any(b in f['baselines'] for f in test_rows)]
        scored = score_forecasts(test_rows, baselines=baselines, probability=True)
        selection = {'candidates': list(attempt['candidates']), 'selected': chosen, 'scores': validation_scores[target],
                     'validation_block': protocol['blocks'][0], 'refit_after_selection': False}
        selection['selection_hash'] = digest(selection)
        report = {
            'schema': 'worldmodel.validation_report/1', 'attempt': attempt_id, 'process_id': 'actors_model',
            'component': f'actors_embedding:{dom["component"]}_{target}',
            'targets': [f'{dom["component"]}_{target}_{dom["target_suffix"]}'],
            'estimator': f'WorldStateEncoder[{chosen}]',
            'protocol': {**protocol, 'origin': dom['origin_text'], 'interval_level': None,
                         'baselines': baselines, 'vintage_policy': dom['vintage_policy'], 'actuals': dom['actuals']},
            'selection': selection,
            'test': {**scored, 'forecast_count': len(test_rows),
                     'leakage_audit': {**leakage, 'vintage_modes': [m for v in dom['series'].values() for m in v['vintage_modes']]},
                     'quarter_clustered_dm': {b: quarter_clustered_dm(test_rows, b) for b in baselines},
                     'base_rate_test': float(np.mean([f['actual'] for f in test_rows])) if test_rows else None},
            'final_estimate': {'data_audit': {'series': dom['series'], **dom['audit_extra']},
                               'diagnostics': {'fits': fits, 'config': config, 'code': code,
                                               'standardization': {'mean': mean.tolist(), 'std': std.tolist()}},
                               'bounds_check': {}},
            'data_inputs': dom['inputs'], 'causally_identified': False,
            'limitations': dom['limitations'] + [
                'Pooled Diebold-Mariano tests treat samples as independent; test.quarter_clustered_dm is the '
                'conservative check under common quarterly shocks.',
                'Out-of-sample skill is predictive association, not a response to any intervention.']}
        report['acceptance'] = evaluate_criteria(report, attempt['criteria'] + attempt.get('extra_criteria', {}).get(target, []))
        report['validated'] = bool(report['acceptance']['passed'])
        canonical(report)
        report['report_id'] = digest({k: v for k, v in report.items() if k != 'report_id'})
        publication = {'parameters': {'attempt': attempt_id, 'target': target}, 'inputs': dom['inputs'],
                       'entrypoint': ENTRYPOINT}
        ref = None
        if publish:
            ref = publish_report(store, REPORTS, report, publication['parameters'], inputs=publication['inputs'],
                                 entrypoint=ENTRYPOINT)
        reports.append({'target': target, 'ref': ref, 'report': report, 'publication': publication})
        log(f'  {target}: selected={chosen} validated={report["validated"]} '
            + ' '.join(f'{r["id"]}={"pass" if r["passed"] else "FAIL"}' for r in report['acceptance']['results']))
    log(f'{attempt_id}: {round(time.time() - started)}s')
    return reports
