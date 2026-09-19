"""Nearest places in embedded state: "which counties, at which time, looked like this one does now".

A checkpoint written by the assay holds the encoder, the panel version it was fit on and the
feature standardization. Every county's state vector is computed from the snapshot at its own
origin, so a match across time compares what was *public then* with what is public now; nothing
from after a candidate's origin enters its vector.

Requires numpy and torch.
"""
from pathlib import Path

import numpy as np


def save_checkpoint(path, forecaster, panel_ref, panel, targets, config, origin_year):
    import torch
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save({'state_dict': forecaster.model.state_dict(), 'model_config': forecaster.model.config,
                'panel_ref': dict(panel_ref), 'mean': panel.mean, 'std': panel.std, 'features': panel.features,
                'targets': list(targets), 'config': dict(config), 'origin_year': int(origin_year),
                'y_scale': forecaster.y_scale, 'multipliers': forecaster.multipliers}, path)


def load_encoder(path, device):
    import torch
    from .model import WorldStateEncoder
    checkpoint = torch.load(path, map_location=device, weights_only=False)
    config = dict(checkpoint['model_config'])
    model = WorldStateEncoder(config.pop('n_features'), config.pop('n_node_types'), config.pop('n_relations'),
                              config.pop('history'), **config).to(device)
    model.load_state_dict(checkpoint['state_dict'])
    model.eval()
    return model, checkpoint


def embed_origin(panel, snapshot, model, cache, row, x_all, m_all, device, batch=512):
    import torch
    from .train import Batcher
    batcher = Batcher(cache, x_all, m_all, panel.node_type, device)
    seeds = np.arange(len(panel.counties))
    states = []
    with torch.no_grad():
        for start in range(0, len(seeds), batch):
            chunk = seeds[start:start + batch]
            b = batcher(np.full(len(chunk), row), chunk)
            states.append(model.encode(b)['state'].float().cpu().numpy())
    return np.concatenate(states)


def nearest(store, county, as_of, *, checkpoint, across='time', limit=10, include_self=False):
    import torch
    from .assay import load_panel
    from .train import SubgraphCache, limit_gpu_memory
    limit_gpu_memory()
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model, meta = load_encoder(checkpoint, device)
    panel_ref, panel = load_panel(store, history=model.config['history'], ref=meta['panel_ref'])
    panel.mean, panel.std = meta['mean'], meta['std']
    if county not in panel.index or panel.index[county] >= len(panel.counties):
        raise ValueError(f'{county} is not a county of panel {panel_ref["version"][:12]}')
    first = max(panel.first_year + 2, as_of - 25) if across == 'time' else as_of
    origins = list(range(first, as_of + 1))
    snapshots = [panel.snapshot(t) for t in origins]
    x_all = torch.as_tensor(np.stack([s.x for s in snapshots]), device=device)
    m_all = torch.as_tensor(np.stack([s.m for s in snapshots]), device=device)
    cache = SubgraphCache(panel, snapshots, meta['config']['max_nodes'])
    vectors = {t: embed_origin(panel, snapshots[i], model, cache, i, x_all, m_all, device) for i, t in enumerate(origins)}
    query = vectors[as_of][panel.index[county]]
    candidates = []
    for t, block in vectors.items():
        if across == 'time' and t == as_of:
            continue
        distance = np.linalg.norm(block - query, axis=1) / np.sqrt(block.shape[1])
        for i in np.argsort(distance)[:limit + 1]:
            # The county's own earlier states are the nearest by construction; they answer a different question.
            if panel.counties[i] == county and not include_self:
                continue
            candidates.append((float(distance[i]), panel.counties[i], t))
    candidates.sort()
    return {'query': {'county': county, 'as_of': f'{as_of}-12-31', 'include_self': include_self}, 'across': across,
            'panel': panel_ref, 'checkpoint': str(checkpoint), 'encoder_fit_origin': meta['origin_year'],
            'nearest': [{'county': c, 'as_of': f'{t}-12-31', 'distance': round(d, 4)} for d, c, t in candidates[:limit]],
            'does_not_establish': [
                'Nearness is in the encoder\'s learned state space, which was trained to forecast employment, '
                'establishments and population and to reconstruct masked features; it is not a similarity of every '
                'attribute a reader may care about.',
                'A past match does not imply the query county will follow the matched county\'s later path.',
                f'The encoder was fit on labels public by {meta["origin_year"]}-12-31; states at later origins are '
                'embedded with it but were not in its training labels.']}
