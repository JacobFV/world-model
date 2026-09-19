"""Fitting and prediction for the root-readout encoder on as-of county subgraphs.

A *sample* is (origin year t, seed county). Its input is the subgraph around the seed in the
snapshot at t; its labels are each target's log change from the latest value public at t (the
anchor) to year t+1. A fit at origin T uses only samples whose labels were public by T-12-31,
split by label year into training samples and the two most recent label years, which calibrate
the predictive scale (split conformal) and are never trained on.

Requires numpy and torch.
"""
from dataclasses import dataclass
import math
import time

import numpy as np
import torch

from .model import WorldStateEncoder, student_t_nll
from .tensors import NODE_TYPES, RELATIONS

DEFAULTS = {'d': 128, 'layers': 3, 'slots': 16, 'passes': 12, 'top_k': 16, 'out_dim': 1024, 'max_nodes': 64,
            'epochs': 6, 'batch': 256, 'lr': 1e-3, 'weight_decay': 1e-4, 'dense_fraction': 0.3, 'gumbel': 0.5,
            'mask_rate': 0.15, 'recon_weight': 0.3, 'calibration_years': 2, 'seed': 0, 'seed_only': False}


@dataclass
class Samples:
    snap: np.ndarray        # [S] snapshot row index
    seed: np.ndarray        # [S] county index
    origin: np.ndarray      # [S] origin year
    y: np.ndarray           # [S, T] label (log change from anchor), nan where absent
    label_year: np.ndarray  # [S] t+1
    anchor: np.ndarray      # [S, T] anchor log level
    anchor_year: np.ndarray  # [S, T]


class SubgraphCache:
    """Node lists and induced edges per (snapshot, seed); shared by every fit on the same panel."""

    def __init__(self, panel, snapshots, max_nodes, seed_only=False):
        self.panel, self.snapshots, self.max_nodes, self.seed_only = panel, snapshots, max_nodes, seed_only
        self.cache, self.adjacency = {}, {}

    def _adjacency(self, row):
        if row not in self.adjacency:
            snap = self.snapshots[row]
            adj = {}
            for (a, b, r), w in zip(snap.edges.tolist(), snap.weights.tolist()):
                adj.setdefault(a, []).append((b, r, w))
            self.adjacency[row] = adj
        return self.adjacency[row]

    def get(self, row, seed):
        key = (row, seed)
        if key not in self.cache:
            if self.seed_only:
                nodes = [seed]
            else:
                nodes = self.panel.subgraph(self.snapshots[row], seed, self.max_nodes)
            position = {n: i for i, n in enumerate(nodes)}
            adj = self._adjacency(row)
            edges, weights = [], []
            for a in nodes:
                for b, r, w in adj.get(a, ()):
                    if b in position:
                        edges.append((position[a], position[b], r))
                        weights.append(w)
            self.cache[key] = (np.array(nodes, dtype=np.int64), np.array(edges, dtype=np.int64).reshape(-1, 3),
                               np.array(weights, dtype=np.float32))
        return self.cache[key]


def build_samples(panel, snapshot_rows, origins, targets):
    """Every (origin, county) with at least one label, and the anchors used by all forecasters."""
    snap, seed, origin, y, label_year, anchor, anchor_year = [], [], [], [], [], [], []
    for t in origins:
        row = snapshot_rows[t]
        for c in range(len(panel.counties)):
            labels, anchors, anchor_years = [], [], []
            for feature in targets:
                a, ay = panel.anchor(c, feature, t)
                v, _ = panel.target_values(c, feature, t + 1)
                labels.append(v - a if (a is not None and v is not None) else np.nan)
                anchors.append(a if a is not None else np.nan)
                anchor_years.append(ay if ay is not None else -1)
            if np.all(np.isnan(anchors)):
                continue
            snap.append(row); seed.append(c); origin.append(t); y.append(labels); label_year.append(t + 1)
            anchor.append(anchors); anchor_year.append(anchor_years)
    return Samples(np.array(snap), np.array(seed), np.array(origin), np.array(y, dtype=np.float64),
                   np.array(label_year), np.array(anchor, dtype=np.float64), np.array(anchor_year))


def label_public(panel, samples, targets, fit_origin_year):
    """[S, T] True where the sample's label value was public by the fit origin."""
    origin = f'{fit_origin_year}-12-31'
    out = np.zeros(samples.y.shape, dtype=bool)
    for i in range(len(samples.seed)):
        for j, feature in enumerate(targets):
            if np.isnan(samples.y[i, j]):
                continue
            _, available = panel.target_values(samples.seed[i], feature, samples.label_year[i])
            out[i, j] = available is not None and available <= origin
    return out


class Batcher:
    def __init__(self, cache, x_all, m_all, node_type, device):
        self.cache, self.x_all, self.m_all, self.device = cache, x_all, m_all, device
        self.node_type = torch.as_tensor(node_type, device=device)

    def __call__(self, rows, seeds):
        items = [self.cache.get(int(r), int(s)) for r, s in zip(rows, seeds)]
        B, N = len(items), max(len(nodes) for nodes, _, _ in items)
        index = np.zeros((B, N), dtype=np.int64)
        valid = np.zeros((B, N), dtype=bool)
        src, dst, rel, weight = [], [], [], []
        for b, (nodes, edges, weights) in enumerate(items):
            index[b, :len(nodes)] = nodes
            index[b, len(nodes):] = nodes[0]
            valid[b, :len(nodes)] = True
            if len(edges):
                src.append(edges[:, 0] + b * N); dst.append(edges[:, 1] + b * N); rel.append(edges[:, 2])
                weight.append(weights)
        device = self.device
        rows_t = torch.as_tensor(np.asarray(rows), device=device)
        index_t = torch.as_tensor(index, device=device)
        cat = (lambda parts, dtype: torch.as_tensor(np.concatenate(parts) if parts else np.zeros(0, dtype=dtype), device=device))
        return {'x': self.x_all[rows_t[:, None], index_t], 'm': self.m_all[rows_t[:, None], index_t],
                'node_type': self.node_type[index_t], 'valid': torch.as_tensor(valid, device=device),
                'src': cat(src, np.int64), 'dst': cat(dst, np.int64), 'rel': cat(rel, np.int64),
                'weight': cat(weight, np.float32)}


def _device():
    return torch.device('cuda' if torch.cuda.is_available() else 'cpu')


class Forecaster:
    """One fitted encoder: standardization of labels, Student-t head, conformal scale multipliers."""

    def __init__(self, model, y_scale, multipliers, config, diagnostics):
        self.model, self.y_scale, self.multipliers, self.config, self.diagnostics = (
            model, y_scale, multipliers, config, diagnostics)

    @torch.no_grad()
    def predict(self, batcher, rows, seeds, *, batch=512, return_state=False):
        self.model.eval()
        means, scales, states, attention = [], [], [], []
        for start in range(0, len(rows), batch):
            b = batcher(rows[start:start + batch], seeds[start:start + batch])
            with torch.autocast(device_type=b['x'].device.type, dtype=torch.bfloat16, enabled=b['x'].is_cuda):
                encoded = self.model.encode(b)
                mean, scale, df = self.model.predict(encoded)
            means.append(mean.float().cpu().numpy()); scales.append(scale.float().cpu().numpy())
            if return_state:
                states.append(encoded['state'].float().cpu().numpy())
                attention.append(encoded['attended'].float().cpu().numpy())
        df = self.model.degrees_of_freedom().detach().float().cpu().numpy()
        mean = np.concatenate(means) * self.y_scale
        scale = np.concatenate(scales) * self.y_scale * self.multipliers
        out = {'mean': mean, 'scale': scale, 'df': df}
        if return_state:
            out['state'] = np.concatenate(states)
            out['attended'] = attention
        return out


def _t_quantile(df, q):
    from ..estimation.distributions import t_ppf
    return t_ppf(q, df)


def fit(panel, snapshots, samples, public, targets, *, config=None, x_all=None, m_all=None, cache=None, log=None):
    """Fit on public labels, holding out the latest ``calibration_years`` label years for the scale."""
    cfg = dict(DEFAULTS, **(config or {}))
    torch.manual_seed(cfg['seed']); np.random.seed(cfg['seed'])
    device = x_all.device
    label_years = samples.label_year[public.any(axis=1)]
    if not len(label_years):
        raise ValueError('No public labels at this origin')
    last = int(label_years.max())
    calibration_years = set(range(last - cfg['calibration_years'] + 1, last + 1))
    usable = public.any(axis=1)
    calibrate = usable & np.isin(samples.label_year, sorted(calibration_years))
    train = usable & ~calibrate
    y = np.where(public, samples.y, np.nan)
    y_scale = np.nanstd(y[train], axis=0)
    y_scale = np.where(np.isfinite(y_scale) & (y_scale > 0), y_scale, 1.0)
    model = WorldStateEncoder(len(panel.features), len(NODE_TYPES), len(RELATIONS), panel.history, d=cfg['d'],
                              layers=cfg['layers'], slots=cfg['slots'], passes=cfg['passes'], top_k=cfg['top_k'],
                              out_dim=cfg['out_dim'], n_targets=len(targets)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg['lr'], weight_decay=cfg['weight_decay'])
    batcher = Batcher(cache, x_all, m_all, panel.node_type, device)
    order = np.flatnonzero(train)
    steps = max(1, cfg['epochs'] * math.ceil(len(order) / cfg['batch']))
    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=cfg['lr'], total_steps=steps, pct_start=0.1)
    y_t = torch.as_tensor(np.nan_to_num(y / y_scale), dtype=torch.float32, device=device)
    have_t = torch.as_tensor(np.isfinite(y), device=device)
    rng = np.random.default_rng(cfg['seed'])
    started, step, history = time.time(), 0, []
    model.train()
    for epoch in range(cfg['epochs']):
        rng.shuffle(order)
        for start in range(0, len(order), cfg['batch']):
            chosen = order[start:start + cfg['batch']]
            progress = step / steps
            model.readout.top_k = None if progress < cfg['dense_fraction'] else cfg['top_k']
            model.readout.gumbel = cfg['gumbel'] * (1 - progress)
            b = batcher(samples.snap[chosen], samples.seed[chosen])
            idx = torch.as_tensor(chosen, device=device)
            with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == 'cuda'):
                encoded = model.encode(b)
                mean, scale, df = model.predict(encoded)
                have = have_t[idx]
                nll = student_t_nll(y_t[idx].float(), mean.float(), scale.float(), df.float())
                forecast_loss = (nll * have).sum() / have.sum().clamp_min(1)
                present = b['m'].amax(-1) > 0
                token_mask = present & (torch.rand(present.shape, device=device) < cfg['mask_rate']) & b['valid'].unsqueeze(-1)
                masked = model.encode(b, token_mask=token_mask)
                index, predicted = model.reconstruct(masked, token_mask)
                if predicted is not None:
                    bb, nn_, ff = index.unbind(-1)
                    target, weight = b['x'][bb, nn_, ff], b['m'][bb, nn_, ff]
                    recon = ((predicted.float() - target) ** 2 * weight).sum() / weight.sum().clamp_min(1)
                else:
                    recon = torch.zeros((), device=device)
                loss = forecast_loss + cfg['recon_weight'] * recon
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            step += 1
            if step % 100 == 0 or step == steps:
                history.append({'step': step, 'forecast_nll': float(forecast_loss), 'recon_mse': float(recon)})
                if log:
                    log(f'  step {step}/{steps} nll={float(forecast_loss):.3f} recon={float(recon):.3f}')
    model.readout.top_k = cfg['top_k']
    model.readout.gumbel = 0.0
    forecaster = Forecaster(model, y_scale, np.ones(len(targets)), cfg, {})
    # Split-conformal scale multiplier per target on the held-out calibration label years.
    multipliers, coverage_before = np.ones(len(targets)), {}
    rows = np.flatnonzero(calibrate)
    if len(rows):
        pred = forecaster.predict(batcher, samples.snap[rows], samples.seed[rows])
        df = pred['df']
        for j in range(len(targets)):
            ok = np.isfinite(y[rows, j])
            if ok.sum() < 30:
                continue
            u = np.abs(y[rows, j][ok] - pred['mean'][ok, j]) / pred['scale'][ok, j]
            t80 = _t_quantile(float(df[j]), 0.9)
            coverage_before[targets[j]] = float(np.mean(u <= t80))
            multipliers[j] = float(np.quantile(u, 0.8) / t80)
    forecaster.multipliers = multipliers
    forecaster.diagnostics = {'train_samples': int(train.sum()), 'calibration_samples': int(calibrate.sum()),
                              'calibration_label_years': sorted(calibration_years), 'steps': steps,
                              'seconds': round(time.time() - started, 1), 'loss_history': history,
                              'degrees_of_freedom': [float(v) for v in model.degrees_of_freedom().detach().cpu()],
                              'conformal_multipliers': [float(v) for v in multipliers],
                              'calibration_coverage_before_conformal': coverage_before,
                              'label_scale': [float(v) for v in y_scale]}
    return forecaster, batcher
