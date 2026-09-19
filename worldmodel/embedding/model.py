"""The root-readout world-state encoder.

Input: a batch of subgraphs, each a set of typed nodes carrying per-feature history windows
(values and availability masks) plus typed, weighted edges. Output: one state vector per
subgraph and a small set of slot vectors.

1. **Feature tokens.** Each (node, feature) history window -- values, masks and first differences
   -- is encoded by a shared MLP and added to a learned feature embedding. Nothing is keyed by
   node identity, so the encoder is inductive: it embeds subgraphs it has never seen.
2. **Node pooling.** A node's tokens are attention-pooled (only tokens with any public value
   take part) and added to a node-type embedding.
3. **Message passing.** ``layers`` rounds of relation-gated mean aggregation with residual MLPs.
4. **Root readout.** ``slots`` learned queries, conditioned on the seed (root) node, make
   ``passes`` rounds of top-k sparse attention over the subgraph's nodes. Each pass attends to
   only the ``k`` best-scoring nodes, then the slots update and attend to one another. Training
   anneals ``k`` from dense to sparse and perturbs selection with Gumbel noise, so nodes the
   early readout ignores still receive gradient.
5. **Heads.** The state vector feeds a Student-t forecast head (mean, scale, learned degrees of
   freedom per target: fat tails by construction) and a reconstruction decoder that predicts
   masked feature windows from the node's context and the slots.

Requires torch.
"""
import math

import torch
from torch import nn
import torch.nn.functional as F


def scatter_mean(values, index, size):
    out = values.new_zeros((size, values.shape[-1]))
    out.index_add_(0, index, values)
    count = values.new_zeros((size, 1))
    count.index_add_(0, index, values.new_ones((index.shape[0], 1)))
    return out / count.clamp_min(1.0)


class TokenEncoder(nn.Module):
    """One token per (node, feature): its history, availability mask and first differences.

    With a dense feature axis (a fixed template domain) slot ``f`` *is* feature ``f``. With
    ``feature_ids`` (an arbitrary subgraph, where each node carries whichever metrics it has) slot
    ``f`` carries the id in ``feature_ids[b, n, f]``, so one encoder serves any vocabulary.
    """

    def __init__(self, n_features, history, d):
        super().__init__()
        width = 2 * history + 2 * (history - 1)
        self.mlp = nn.Sequential(nn.Linear(width, d), nn.GELU(), nn.Linear(d, d))
        self.feature = nn.Embedding(n_features, d)
        self.masked = nn.Parameter(torch.zeros(d))

    def forward(self, x, m, token_mask=None, feature_ids=None):
        # x, m: [B, N, F, H]; feature_ids: [B, N, F] or None for a dense feature axis
        diff = (x[..., :-1] - x[..., 1:]) * (m[..., :-1] * m[..., 1:])
        dmask = m[..., :-1] * m[..., 1:]
        if token_mask is not None:            # hide masked tokens' contents entirely
            keep = (~token_mask).unsqueeze(-1).float()
            x, m, diff, dmask = x * keep, m * keep, diff * keep, dmask * keep
        tokens = self.mlp(torch.cat([x, m, diff, dmask], dim=-1))
        tokens = tokens + (self.feature.weight[None, None] if feature_ids is None else self.feature(feature_ids))
        if token_mask is not None:
            tokens = tokens + token_mask.unsqueeze(-1).float() * self.masked
        return tokens                          # [B, N, F, d]


class NodePool(nn.Module):
    def __init__(self, d, n_types):
        super().__init__()
        self.score = nn.Linear(d, 1)
        self.type_embedding = nn.Embedding(n_types, d)
        self.norm = nn.LayerNorm(d)

    def forward(self, tokens, present, node_type):
        # present: [B, N, F] tokens with any public value (or masked tokens, which carry the mask embedding)
        logits = self.score(tokens).squeeze(-1).masked_fill(~present, -1e4)
        weights = torch.softmax(logits, dim=-1) * present.any(-1, keepdim=True).float()
        pooled = (weights.unsqueeze(-1) * tokens).sum(-2)
        return self.norm(pooled + self.type_embedding(node_type))


class MessageLayer(nn.Module):
    def __init__(self, d, n_relations):
        super().__init__()
        self.message = nn.Linear(d, d)
        self.gate = nn.Embedding(n_relations, d)
        self.update = nn.Sequential(nn.LayerNorm(2 * d), nn.Linear(2 * d, 2 * d), nn.GELU(), nn.Linear(2 * d, d))

    def forward(self, h, src, dst, rel, weight):
        # h: [M, d] flattened over the batch; src/dst index into it.
        msg = self.message(h)[src] * torch.sigmoid(self.gate(rel)) * weight.unsqueeze(-1)
        agg = scatter_mean(msg, dst, h.shape[0])
        return h + self.update(torch.cat([h, agg], dim=-1))


class RootReadout(nn.Module):
    def __init__(self, d, slots, passes, heads=4):
        super().__init__()
        self.slots, self.passes = slots, passes
        self.init = nn.Parameter(torch.randn(slots, d) * 0.02)
        self.from_root = nn.Linear(d, d)
        self.pass_embedding = nn.Embedding(passes, d)
        self.q, self.k, self.v = nn.Linear(d, d), nn.Linear(d, d), nn.Linear(d, d)
        self.norm_q, self.norm_kv = nn.LayerNorm(d), nn.LayerNorm(d)
        self.update = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, 2 * d), nn.GELU(), nn.Linear(2 * d, d))
        self.mix = nn.MultiheadAttention(d, heads, batch_first=True)
        self.norm_mix = nn.LayerNorm(d)
        self.top_k = None            # None: dense; otherwise nodes attended per slot per pass
        self.gumbel = 0.0            # selection noise scale during training

    def forward(self, h, valid, root):
        # h: [B, N, d]; valid: [B, N]; root: [B, d]
        B, N, d = h.shape
        slots = self.init[None].expand(B, -1, -1) + self.from_root(root).unsqueeze(1)
        kv = self.norm_kv(h)
        keys, values = self.k(kv), self.v(kv)
        attended = torch.zeros(B, N, device=h.device)
        for p in range(self.passes):
            query = self.q(self.norm_q(slots + self.pass_embedding.weight[p]))
            scores = torch.einsum('bsd,bnd->bsn', query, keys) / math.sqrt(d)
            scores = scores.masked_fill(~valid.unsqueeze(1), -1e4)
            k = N if self.top_k is None else min(self.top_k, N)
            if k < N:
                selector = scores
                if self.training and self.gumbel > 0:
                    u = torch.rand_like(scores).clamp(1e-6, 1 - 1e-6)
                    selector = scores - self.gumbel * torch.log(-torch.log(u))
                index = selector.topk(k, dim=-1).indices                         # [B, S, k]
                picked = scores.gather(-1, index)
                weights = torch.softmax(picked, dim=-1)
                gathered = values.unsqueeze(1).expand(B, self.slots, N, d).gather(
                    2, index.unsqueeze(-1).expand(B, self.slots, k, d))
                read = (weights.unsqueeze(-1) * gathered).sum(2)
                attended.scatter_add_(1, index.reshape(B, -1), weights.reshape(B, -1).detach())
            else:
                weights = torch.softmax(scores, dim=-1)
                read = torch.einsum('bsn,bnd->bsd', weights, values)
                attended += weights.detach().sum(1)
            slots = slots + self.update(slots + read)
            mixed, _ = self.mix(self.norm_mix(slots), self.norm_mix(slots), self.norm_mix(slots), need_weights=False)
            slots = slots + mixed
        return slots, attended / (self.passes * self.slots)


class WorldStateEncoder(nn.Module):
    def __init__(self, n_features, n_node_types, n_relations, history, *, d=128, layers=3, slots=16, passes=12,
                 top_k=16, out_dim=1024, n_targets=3):
        super().__init__()
        self.config = dict(n_features=n_features, n_node_types=n_node_types, n_relations=n_relations, history=history,
                           d=d, layers=layers, slots=slots, passes=passes, top_k=top_k, out_dim=out_dim,
                           n_targets=n_targets)
        self.tokens = TokenEncoder(n_features, history, d)
        self.pool = NodePool(d, n_node_types)
        self.layers = nn.ModuleList(MessageLayer(d, n_relations) for _ in range(layers))
        self.readout = RootReadout(d, slots, passes)
        self.readout.top_k = top_k
        self.state = nn.Sequential(nn.Linear(3 * d, out_dim), nn.LayerNorm(out_dim))
        self.forecast = nn.Sequential(nn.Linear(out_dim + d, 2 * d), nn.GELU(), nn.Linear(2 * d, 2 * n_targets))
        self.df_raw = nn.Parameter(torch.full((n_targets,), 1.5))
        self.decode_query = nn.Linear(d, d)
        self.decode_attn = nn.MultiheadAttention(d, 4, batch_first=True)
        self.decoder = nn.Sequential(nn.LayerNorm(2 * d), nn.Linear(2 * d, 2 * d), nn.GELU(), nn.Linear(2 * d, history))

    def degrees_of_freedom(self):
        return 2.1 + F.softplus(self.df_raw)

    def encode(self, batch, token_mask=None):
        x, m, node_type, valid = batch['x'], batch['m'], batch['node_type'], batch['valid']
        B, N, Fn, H = x.shape
        tokens = self.tokens(x, m, token_mask, batch.get('feature_ids'))
        present = m.amax(-1) > 0
        if token_mask is not None:
            present = present | token_mask
        h = self.pool(tokens, present, node_type)                  # [B, N, d]
        flat = h.reshape(B * N, -1)
        for layer in self.layers:
            flat = layer(flat, batch['src'], batch['dst'], batch['rel'], batch['weight'])
        h = flat.reshape(B, N, -1) * valid.unsqueeze(-1).float()
        root = h[:, 0]                                              # the seed is node 0 of every subgraph
        slots, attended = self.readout(h, valid, root)
        state = self.state(torch.cat([slots.mean(1), slots[:, 0], root], dim=-1))
        return {'h': h, 'root': root, 'slots': slots, 'state': state, 'attended': attended, 'tokens': tokens,
                'feature_ids': batch.get('feature_ids')}

    def predict(self, encoded):
        out = self.forecast(torch.cat([encoded['state'], encoded['root']], dim=-1))
        mean, log_scale = out.chunk(2, dim=-1)
        return mean, F.softplus(log_scale) + 1e-3, self.degrees_of_freedom()

    def reconstruct(self, encoded, token_mask):
        """Predict every masked token's standardized history window from its node and the slots."""
        index = token_mask.nonzero(as_tuple=False)                  # [T, 3] (b, n, f)
        if index.numel() == 0:
            return index, None
        b, n, f = index.unbind(-1)
        ids = encoded.get('feature_ids')
        feature = self.tokens.feature.weight[f] if ids is None else self.tokens.feature(ids[b, n, f])
        query = self.decode_query(encoded['h'][b, n] + feature)
        read, _ = self.decode_attn(query.unsqueeze(1), encoded['slots'][b], encoded['slots'][b], need_weights=False)
        return index, self.decoder(torch.cat([query, read.squeeze(1)], dim=-1))


def student_t_nll(y, mean, scale, df):
    z = (y - mean) / scale
    return (torch.lgamma(df / 2) - torch.lgamma((df + 1) / 2) + 0.5 * torch.log(df * math.pi) + torch.log(scale)
            + (df + 1) / 2 * torch.log1p(z * z / df))
