"""Actor tasks from 13F holdings: will an institutional manager exit, or add to, a position?

A *sample* is one reported position (manager m, security s) in m's original 13F-HR for quarter q.
Its origin is q's filing deadline (quarter end + 45 days); its labels come from m's original
13F-HR for quarter q+1, which is public only after the origin:

* ``exit``: s is absent from m's q+1 filing;
* ``increase``: s is held at q+1 and m's log change in shares exceeds, by 0.1, the median log
  change of every holder that kept s (which removes splits and other security-wide share events).

Samples are conditioned on m filing again for q+1 (a manager that stops filing has no label).

**Visibility.** A filing enters any feature only if it was filed by its own quarter's deadline:
rows of quarter q' are used at an origin only if filed on or before q'+45 days, which is on or
before the origin for every q' <= q. Late filings are dropped from features rather than admitted
early, and the audit reports the latest filing date used.

**Subgraph template.** Every sample has the same topology, so batching is pure indexing:

====  =========================================================================
node  meaning
====  =========================================================================
0     the query position (m, s): its own share, value, weight and presence history
1     manager m: portfolio value, position count, exit and entry rates
2     security s: 13F value held, holder count, holder exit and entry rates
3..   the top ``K`` other holders' positions in s (by value at q)
..    m's top ``K`` other positions (by value at q), and each one's security node
====  =========================================================================

Requires numpy.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import math

import numpy as np

from .holdings import quarter_end

NODE_TYPES = ('position', 'manager', 'security')
RELATIONS = ('position_of', 'has_position', 'position_in', 'security_position')
FEATURES = ('pos_log_shares', 'pos_log_value', 'pos_weight', 'pos_present', 'pos_share_change',
            'mgr_log_value', 'mgr_log_positions', 'mgr_exit_rate', 'mgr_entry_rate',
            'sec_log_value', 'sec_log_holders', 'sec_holder_exit_rate', 'sec_holder_entry_rate')
TARGETS = ('exit', 'increase')
DEADLINE_DAYS = 45
INCREASE_MARGIN = 0.1


def deadline(quarter):
    return date.fromisoformat(quarter_end(quarter)) + timedelta(days=DEADLINE_DAYS)


def day_number(day):
    return day.year * 10000 + day.month * 100 + day.day


@dataclass
class Quarter:
    """Visible rows of one quarter, sorted by (manager, security), plus aggregates."""
    manager: np.ndarray
    security: np.ndarray
    shares: np.ndarray
    value: np.ndarray
    key: np.ndarray               # manager * n_securities + security, sorted
    filed_managers: np.ndarray    # bool [n_managers]: m has a visible filing this quarter
    mgr: np.ndarray               # [n_managers, 4] manager features (nan where absent)
    sec: np.ndarray               # [n_securities, 4] security features
    top_holders: dict             # security -> managers by value, descending
    top_positions: dict           # manager -> securities by value, descending
    max_filed: int


class Holdings13F:
    """All quarters of visible 13F rows, with the lookups the task builder needs."""

    def __init__(self, arrays, *, top=13):
        self.n_managers = int(arrays['manager'].max()) + 1
        self.n_securities = int(arrays['security'].max()) + 1
        self.manager_ids, self.security_ids = arrays['manager_ids'], arrays['security_ids']
        quarter = arrays['quarter'].astype(np.int64)
        self.n_quarters = int(quarter.max()) + 1
        bounds = np.searchsorted(quarter, np.arange(self.n_quarters + 1))
        self.all_rows = []          # every original-filing row (labels)
        self.quarters = []          # visible rows only (features)
        self.late_rows_dropped = 0
        for q in range(self.n_quarters):
            lo, hi = bounds[q], bounds[q + 1]
            m, s = arrays['manager'][lo:hi].astype(np.int64), arrays['security'][lo:hi].astype(np.int64)
            sh, v, f = arrays['shares'][lo:hi], arrays['value'][lo:hi], arrays['filed'][lo:hi]
            key = m * self.n_securities + s
            # Rows were sorted by (quarter, manager, security); a manager may report one CUSIP on several
            # rows (share classes, sole/shared authority): aggregate them into one position.
            unique_key, start = np.unique(key, return_index=True)
            shares = np.add.reduceat(np.nan_to_num(sh), start)
            value = np.add.reduceat(v, start)
            filed = np.minimum.reduceat(f, start)
            um, us = unique_key // self.n_securities, unique_key % self.n_securities
            self.all_rows.append({'key': unique_key, 'shares': shares, 'filed': filed,
                                  'filed_managers': np.bincount(um, minlength=self.n_managers) > 0})
            visible = filed <= day_number(deadline(q))
            self.late_rows_dropped += int((~visible).sum())
            self.quarters.append(self._quarter(q, um[visible], us[visible], shares[visible], value[visible],
                                               unique_key[visible], filed[visible], top))

    def _quarter(self, q, m, s, shares, value, key, filed, top):
        filed_managers = np.bincount(m, minlength=self.n_managers) > 0
        mv = np.bincount(m, weights=value, minlength=self.n_managers)
        mn = np.bincount(m, minlength=self.n_managers)
        sv = np.bincount(s, weights=value, minlength=self.n_securities)
        sn = np.bincount(s, minlength=self.n_securities)
        mgr = np.full((self.n_managers, 4), np.nan)
        sec = np.full((self.n_securities, 4), np.nan)
        mgr[filed_managers, 0] = np.log1p(mv[filed_managers])
        mgr[filed_managers, 1] = np.log1p(mn[filed_managers])
        held = sn > 0
        sec[held, 0] = np.log1p(sv[held])
        sec[held, 1] = np.log1p(sn[held])
        if q > 0:
            prev = self.quarters[q - 1]
            both = filed_managers & prev.filed_managers
            # Exit rate of m at q: share of m's q-1 positions absent at q (m filed both quarters).
            in_prev = np.isin(prev.key, key)
            prev_m = prev.manager
            eligible = both[prev_m]
            exits = np.bincount(prev_m[eligible], weights=(~in_prev[eligible]).astype(float), minlength=self.n_managers)
            base = np.bincount(prev_m[eligible], minlength=self.n_managers)
            ok = both & (base > 0)
            mgr[ok, 2] = exits[ok] / base[ok]
            in_now = np.isin(key, prev.key)
            eligible_now = both[m]
            entries = np.bincount(m[eligible_now], weights=(~in_now[eligible_now]).astype(float), minlength=self.n_managers)
            count = np.bincount(m[eligible_now], minlength=self.n_managers)
            ok = both & (count > 0)
            mgr[ok, 3] = entries[ok] / count[ok]
            # Holder exit/entry rates of s, over holders that filed both quarters.
            prev_s = prev.security
            sec_exit = np.bincount(prev_s[eligible], weights=(~in_prev[eligible]).astype(float), minlength=self.n_securities)
            sec_base = np.bincount(prev_s[eligible], minlength=self.n_securities)
            ok = sec_base > 0
            sec[ok, 2] = sec_exit[ok] / sec_base[ok]
            sec_entry = np.bincount(s[eligible_now], weights=(~in_now[eligible_now]).astype(float), minlength=self.n_securities)
            sec_count = np.bincount(s[eligible_now], minlength=self.n_securities)
            ok = sec_count > 0
            sec[ok, 3] = sec_entry[ok] / sec_count[ok]
        # Top holders per security and top positions per manager, by value. Sort once, and copy each
        # short slice so it does not keep the quarter-sized sorted array alive.
        top_holders = _top_by_group(s, m, value, top)
        top_positions = _top_by_group(m, s, value, top)
        return Quarter(m.astype(np.int32), s.astype(np.int32), shares, value.astype(np.float32), key, filed_managers,
                       mgr.astype(np.float32), sec.astype(np.float32), top_holders, top_positions,
                       int(filed.max()) if len(filed) else 0)

    # ------------------------------------------------------------------ lookups
    def position_features(self, q, managers, securities):
        """[len, 5] position features of (m, s) pairs at quarter q (nan where unknown)."""
        out = np.full((len(managers), 5), np.nan)
        if q < 0:
            return out
        Q = self.quarters[q]
        key = managers.astype(np.int64) * self.n_securities + securities
        index = np.searchsorted(Q.key, key)
        index = np.minimum(index, len(Q.key) - 1)
        found = (Q.key[index] == key) & (managers >= 0) & (securities >= 0)
        filed = Q.filed_managers[np.maximum(managers, 0)] & (managers >= 0)
        V = np.exp(Q.mgr[np.maximum(managers, 0), 0]) - 1
        out[found, 0] = np.log1p(Q.shares[index[found]])
        out[found, 1] = np.log1p(Q.value[index[found]])
        out[found, 2] = Q.value[index[found]] / np.maximum(V[found], 1.0)
        out[filed, 3] = 0.0
        out[found, 3] = 1.0
        out[filed & ~found, 2] = 0.0
        if q > 0:
            prev = self.position_features_raw(q - 1, managers, securities)
            ok = found & np.isfinite(prev)
            out[ok, 4] = np.log1p(Q.shares[index[ok]]) - prev[ok]
        return out

    def position_features_raw(self, q, managers, securities):
        Q = self.quarters[q]
        key = managers.astype(np.int64) * self.n_securities + securities
        index = np.minimum(np.searchsorted(Q.key, key), len(Q.key) - 1)
        found = Q.key[index] == key
        return np.where(found, np.log1p(Q.shares[index]), np.nan)


def _top_by_group(group, member, value, top):
    """group id -> up to ``top`` member ids by descending value (small independent arrays)."""
    order = np.lexsort((-value, group))
    g, mem = group[order], member[order].astype(np.int32)
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    ends = np.r_[starts[1:], len(order)]
    return {int(g[a]): mem[a:min(b, a + top)].copy() for a, b in zip(starts, ends)}


def _slot_features(holdings, q_origin, history, managers, securities, kind):
    """[len, F, H] features for nodes of one kind over slots q, q-1, ..."""
    n = len(managers)
    x = np.full((n, len(FEATURES), history), np.nan, dtype=np.float32)
    for h in range(history):
        q = q_origin - h
        if q < 0:
            continue
        Q = holdings.quarters[q]
        if kind == 'position':
            x[:, 0:5, h] = holdings.position_features(q, managers, securities)
        elif kind == 'manager':
            ok = managers >= 0
            x[ok, 5:9, h] = Q.mgr[managers[ok]]
        else:
            ok = securities >= 0
            x[ok, 9:13, h] = Q.sec[securities[ok]]
    return x


@dataclass
class TaskSet:
    x: np.ndarray            # [S, N, F, H] float16 (nan-filled then zeroed; see m)
    m: np.ndarray            # [S, N, F, H] uint8 availability
    node_valid: np.ndarray   # [S, N] bool
    weights: np.ndarray      # [S, E] float32 edge weights for the template
    y: np.ndarray            # [S, T] labels (nan where undefined)
    quarter: np.ndarray      # [S] origin quarter
    label_filed: np.ndarray  # [S] date number of the label filing (public date of the label)
    manager: np.ndarray
    security: np.ndarray
    base_manager: np.ndarray  # [S, T] manager's own rate at the origin (baseline), nan if none
    base_security: np.ndarray  # [S, T] security holders' rate at the origin
    max_feature_filed: dict  # origin quarter -> latest filing date number used by its features


def template(k):
    """Node count and the fixed (src, dst, relation) edges of the subgraph template."""
    n = 3 + 3 * k
    rel = {name: i for i, name in enumerate(RELATIONS)}
    edges = [(0, 1, rel['position_of']), (1, 0, rel['has_position']), (0, 2, rel['position_in']),
             (2, 0, rel['security_position'])]
    for i in range(k):
        holder = 3 + i                       # another manager's position in s
        edges += [(holder, 2, rel['position_in']), (2, holder, rel['security_position'])]
        own = 3 + k + i                      # m's other position
        security = 3 + 2 * k + i             # ...and its security
        edges += [(own, 1, rel['position_of']), (1, own, rel['has_position']),
                  (own, security, rel['position_in']), (security, own, rel['security_position'])]
    node_type = np.array([0, 1, 2] + [0] * k + [0] * k + [2] * k, dtype=np.int64)
    return n, np.array(edges, dtype=np.int64), node_type


def build_tasks(holdings, quarters, *, per_quarter, per_manager, k=12, history=4, seed=0):
    """Sample positions at each origin quarter and assemble template subgraphs, labels and baselines."""
    rng = np.random.default_rng(seed)
    n_nodes, edges, _ = template(k)
    parts = []
    max_feature_filed = {}
    for q in quarters:
        if q + 1 >= holdings.n_quarters:
            continue
        Q, nxt = holdings.quarters[q], holdings.all_rows[q + 1]
        continuing = nxt['filed_managers'][Q.manager]
        candidates = np.flatnonzero(continuing)
        order = rng.permutation(candidates)
        # Cap positions per manager so a few very large filers do not dominate the sample.
        _, first = np.unique(Q.manager[order], return_index=True)
        rank = np.empty(len(order), dtype=np.int64)
        sorted_by_manager = np.argsort(Q.manager[order], kind='stable')
        managers_sorted = Q.manager[order][sorted_by_manager]
        group_start = np.searchsorted(managers_sorted, managers_sorted)
        rank[sorted_by_manager] = np.arange(len(order)) - group_start
        chosen = order[rank < per_manager][:per_quarter]
        m, s = Q.manager[chosen], Q.security[chosen]
        S = len(chosen)
        # Labels.
        key_next = m.astype(np.int64) * holdings.n_securities + s
        index = np.minimum(np.searchsorted(nxt['key'], key_next), len(nxt['key']) - 1)
        present_next = nxt['key'][index] == key_next
        manager_filed_next = np.zeros(holdings.n_managers, dtype=np.int64)
        np.maximum.at(manager_filed_next, nxt['key'] // holdings.n_securities, nxt['filed'])
        label_filed = manager_filed_next[m]
        y = np.full((S, len(TARGETS)), np.nan)
        y[:, 0] = (~present_next).astype(float)
        # Increase: own log share change minus the median over every continuing holder of s.
        prev_shares = np.log1p(Q.shares[chosen])
        next_shares = np.where(present_next, np.log1p(nxt['shares'][index]), np.nan)
        own_change = next_shares - prev_shares
        all_next_key = Q.key
        all_index = np.minimum(np.searchsorted(nxt['key'], all_next_key), len(nxt['key']) - 1)
        all_present = nxt['key'][all_index] == all_next_key
        all_change = np.where(all_present, np.log1p(nxt['shares'][all_index]) - np.log1p(Q.shares), np.nan)
        median = np.full(holdings.n_securities, np.nan)
        ok = np.isfinite(all_change)
        order_s = np.lexsort((all_change[ok], Q.security[ok]))
        sec_sorted, change_sorted = Q.security[ok][order_s], all_change[ok][order_s]
        starts = np.flatnonzero(np.r_[True, sec_sorted[1:] != sec_sorted[:-1]])
        ends = np.r_[starts[1:], len(sec_sorted)]
        median[sec_sorted[starts]] = [np.median(change_sorted[a:b]) for a, b in zip(starts, ends)]
        cont = present_next & np.isfinite(own_change) & np.isfinite(median[s])
        y[cont, 1] = (own_change[cont] - median[s][cont] > INCREASE_MARGIN).astype(float)
        # Baselines at the origin: the manager's own exit rate and the security's holder exit rate at q.
        base_manager = np.full((S, len(TARGETS)), np.nan)
        base_security = np.full((S, len(TARGETS)), np.nan)
        base_manager[:, 0] = Q.mgr[m, 2]
        base_security[:, 0] = Q.sec[s, 2]
        # Neighbours.
        holders = np.full((S, k), -1, dtype=np.int64)
        own = np.full((S, k), -1, dtype=np.int64)
        for i in range(S):
            h = [x for x in Q.top_holders.get(int(s[i]), ()) if x != m[i]][:k]
            holders[i, :len(h)] = h
            o = [x for x in Q.top_positions.get(int(m[i]), ()) if x != s[i]][:k]
            own[i, :len(o)] = o
        x = np.full((S, n_nodes, len(FEATURES), history), np.nan, dtype=np.float32)
        x[:, 0] = _slot_features(holdings, q, history, m, s, 'position')
        x[:, 1] = _slot_features(holdings, q, history, m, s, 'manager')
        x[:, 2] = _slot_features(holdings, q, history, m, s, 'security')
        flat_h, flat_s = holders.ravel(), np.repeat(s, k)
        x[:, 3:3 + k] = _slot_features(holdings, q, history, flat_h, flat_s, 'position').reshape(S, k, len(FEATURES), history)
        flat_m, flat_o = np.repeat(m, k), own.ravel()
        x[:, 3 + k:3 + 2 * k] = _slot_features(holdings, q, history, flat_m, flat_o, 'position').reshape(S, k, len(FEATURES), history)
        x[:, 3 + 2 * k:] = _slot_features(holdings, q, history, flat_o * 0 - 1, flat_o, 'security').reshape(S, k, len(FEATURES), history)
        valid = np.ones((S, n_nodes), dtype=bool)
        valid[:, 3:3 + k] = holders >= 0
        valid[:, 3 + k:3 + 2 * k] = own >= 0
        valid[:, 3 + 2 * k:] = own >= 0
        # Edge weights: holder positions weighted by their share of s's 13F value, own positions by weight in m.
        w = np.ones((S, len(edges)), dtype=np.float32)
        sec_value = np.exp(Q.sec[s, 0]) - 1
        holder_value = np.where(holders >= 0, np.exp(x[:, 3:3 + k, 1, 0]) - 1, 0)
        share = holder_value / np.maximum(sec_value, 1.0)[:, None]
        own_weight = np.nan_to_num(x[:, 3 + k:3 + 2 * k, 2, 0])
        for i in range(k):
            base = 4 + 6 * i
            w[:, base:base + 2] = np.nan_to_num(share[:, i])[:, None]
            w[:, base + 2:base + 4] = own_weight[:, i][:, None]
        max_feature_filed[q] = max(holdings.quarters[qq].max_filed for qq in range(max(0, q - history + 1), q + 1))
        parts.append((x, valid, w, y, np.full(S, q), label_filed, m, s, base_manager, base_security))
    cat = lambda i: np.concatenate([p[i] for p in parts])
    x = cat(0)
    mask = np.isfinite(x)
    x = np.nan_to_num(x).astype(np.float16)
    return TaskSet(x, mask.astype(np.uint8), cat(1), cat(2), cat(3), cat(4), cat(5), cat(6), cat(7), cat(8), cat(9),
                   max_feature_filed)


def standardize(tasks, train_rows):
    """Per-feature mean/std over available training values; applied in place."""
    x, m = tasks.x.astype(np.float32), tasks.m.astype(bool)
    mean = np.zeros(len(FEATURES))
    std = np.ones(len(FEATURES))
    for j in range(len(FEATURES)):
        values = x[train_rows][:, :, j][m[train_rows][:, :, j]]
        if values.size:
            mean[j], std[j] = float(values.mean()), float(values.std()) or 1.0
    x = (x - mean[None, None, :, None]) / std[None, None, :, None]
    tasks.x = np.where(m, x, 0).astype(np.float16)
    return mean, std
