"""Will a manager open a position it has never held? The task a per-pair model cannot cheat.

Every earlier actor task gives the forecaster a query pair with its own history: how many shares the
manager holds, how the position moved, what the manager's own exit rate is. A gradient-boosted model
on those columns matched or beat the encoder in every one of them. This task removes that crutch.

A *sample* is (manager m, security s) at quarter q where **m does not hold s**. The label is 1 when
m reports s in its next original 13F-HR. The query position node therefore carries no history at
all, and everything that can distinguish a candidate lives in the graph around it: who else holds s,
what else m holds, and whether those two neighbourhoods touch. That is the structure a fixed
neighbour-mean cannot express and attention over a subgraph can, so this is the test that
discriminates between them.

**Candidates.** Positives are the securities m actually opens at q+1, restricted to securities some
manager already held at q (so the candidate existed and could have been chosen). Negatives are drawn
from two declared pools, per positive:

* the *co-holding* pool: securities held at q by the managers who hold what m holds -- a two-hop
  walk from m, which is where a plausible new position comes from;
* the *popularity* pool: securities drawn in proportion to their holder count at q.

The base rate is therefore a property of this declared sampling, not of the world, and every
forecaster including the baselines faces the same distribution. The ``security_entry_rate`` baseline
is that rate scaled to the design's own base rate (one positive per four negatives), which is known
before any label is read, so it competes on ranking rather than losing on level.

**Visibility.** As in :mod:`worldmodel.embedding.actors`: a filing enters a feature only if it was
filed by its own quarter's deadline, and labels come from the next quarter's original filing, which
is public only after the origin.

Requires numpy.
"""
import numpy as np

from .actors import FEATURES, TaskSet, _slot_features, template

TARGETS = ('opens',)
OWN_RATE = 'security_entry_rate'
CO_HOLDING_POOL = 2          # securities pulled from the two-hop co-holding walk, per positive
POPULARITY_POOL = 2          # securities drawn by holder count, per positive
NEGATIVES_PER_POSITIVE = CO_HOLDING_POOL + POPULARITY_POOL


def security_entry_rates(holdings, q):
    """P(a manager opens s at q | it did not hold s at q-1), over managers that filed both quarters.

    This is the baseline anyone would reach for: how often this security is opened at all.
    """
    rates = np.full(holdings.n_securities, np.nan)
    if q < 1:
        return rates
    Q, prev = holdings.quarters[q], holdings.quarters[q - 1]
    both = Q.filed_managers & prev.filed_managers
    if not both.any():
        return rates
    filers = int(both.sum())
    held_prev = np.zeros(holdings.n_securities, dtype=np.int64)
    eligible_prev = both[prev.manager]
    np.add.at(held_prev, prev.security[eligible_prev], 1)
    in_prev = np.isin(Q.key, prev.key)
    opened = np.zeros(holdings.n_securities, dtype=np.int64)
    eligible_now = both[Q.manager] & ~in_prev
    np.add.at(opened, Q.security[eligible_now], 1)
    denominator = filers - held_prev
    usable = denominator > 0
    rates[usable] = opened[usable] / denominator[usable]
    return rates


def _popularity(holdings, q):
    """Securities held at q and a sampling probability proportional to holder count."""
    Q = holdings.quarters[q]
    counts = np.bincount(Q.security, minlength=holdings.n_securities)
    held = np.flatnonzero(counts)
    return held, counts[held] / counts[held].sum()


def _manager_positions(Q, holdings, manager):
    lo = np.searchsorted(Q.key, manager * holdings.n_securities)
    hi = np.searchsorted(Q.key, (manager + 1) * holdings.n_securities)
    return Q.security[lo:hi]


def build_tasks(holdings, quarters, *, per_quarter, per_manager, k=12, history=4, seed=0,
                negatives_per_positive=NEGATIVES_PER_POSITIVE, log=print):
    """Candidate (manager, security) pairs the manager does not hold, with the label for the next quarter."""
    rng = np.random.default_rng(seed)
    n_nodes, edges, _ = template(k)
    parts, max_feature_filed = [], {}
    for q in quarters:
        if q + 1 >= holdings.n_quarters:
            continue
        Q, nxt = holdings.quarters[q], holdings.all_rows[q + 1]
        entry_rates = security_entry_rates(holdings, q)
        pool_securities, pool_p = _popularity(holdings, q)
        existing = np.zeros(holdings.n_securities, dtype=bool)
        existing[pool_securities] = True
        managers = np.flatnonzero(Q.filed_managers & nxt['filed_managers'])
        rng.shuffle(managers)
        rows = {'manager': [], 'security': [], 'label': []}
        next_filed = np.zeros(holdings.n_managers, dtype=np.int64)
        np.maximum.at(next_filed, nxt['key'] // holdings.n_securities, nxt['filed'])
        for manager in managers:
            if len(rows['manager']) >= per_quarter:
                break
            held = _manager_positions(Q, holdings, manager)
            lo = np.searchsorted(nxt['key'], manager * holdings.n_securities)
            hi = np.searchsorted(nxt['key'], (manager + 1) * holdings.n_securities)
            next_held = nxt['key'][lo:hi] % holdings.n_securities
            opened = np.setdiff1d(next_held, held, assume_unique=False)
            opened = opened[existing[opened]]
            if not len(opened):
                continue
            positives = opened[:max(1, per_manager // (1 + negatives_per_positive))]
            # Two-hop co-holding walk: what do the holders of m's securities hold?
            co_pool = []
            for security in held[:k]:
                for other in Q.top_holders.get(int(security), ())[:k]:
                    if other != manager:
                        co_pool.extend(Q.top_positions.get(int(other), ())[:k])
            co_pool = np.setdiff1d(np.array(co_pool, dtype=np.int64), held) if co_pool else np.zeros(0, dtype=np.int64)
            co_pool = np.setdiff1d(co_pool, opened)
            forbidden = np.union1d(held, opened)
            for positive in positives:
                rows['manager'].append(manager); rows['security'].append(int(positive)); rows['label'].append(1.0)
                drawn = []
                if len(co_pool):
                    drawn.extend(rng.choice(co_pool, size=min(CO_HOLDING_POOL, len(co_pool)), replace=False).tolist())
                for _ in range(negatives_per_positive - len(drawn)):
                    candidate = int(rng.choice(pool_securities, p=pool_p))
                    if candidate not in forbidden:
                        drawn.append(candidate)
                for candidate in drawn:
                    rows['manager'].append(manager); rows['security'].append(int(candidate)); rows['label'].append(0.0)
        if not rows['manager']:
            continue
        m_index = np.array(rows['manager'], dtype=np.int64)
        s_index = np.array(rows['security'], dtype=np.int64)
        y = np.array(rows['label'], dtype=float)[:, None]
        S = len(m_index)
        x = np.full((S, n_nodes, len(FEATURES), history), np.nan, dtype=np.float32)
        x[:, 0] = _slot_features(holdings, q, history, m_index, s_index, 'position')   # empty by construction
        x[:, 1] = _slot_features(holdings, q, history, m_index, s_index, 'manager')
        x[:, 2] = _slot_features(holdings, q, history, m_index, s_index, 'security')
        holders = np.full((S, k), -1, dtype=np.int64)
        own = np.full((S, k), -1, dtype=np.int64)
        for i in range(S):
            candidates = [x_ for x_ in Q.top_holders.get(int(s_index[i]), ()) if x_ != m_index[i]][:k]
            holders[i, :len(candidates)] = candidates
            mine = [x_ for x_ in Q.top_positions.get(int(m_index[i]), ()) if x_ != s_index[i]][:k]
            own[i, :len(mine)] = mine
        flat_s = np.repeat(s_index, k)
        x[:, 3:3 + k] = _slot_features(holdings, q, history, holders.ravel(), flat_s, 'position').reshape(
            S, k, len(FEATURES), history)
        flat_m, flat_own = np.repeat(m_index, k), own.ravel()
        x[:, 3 + k:3 + 2 * k] = _slot_features(holdings, q, history, flat_m, flat_own, 'position').reshape(
            S, k, len(FEATURES), history)
        x[:, 3 + 2 * k:] = _slot_features(holdings, q, history, flat_own * 0 - 1, flat_own, 'security').reshape(
            S, k, len(FEATURES), history)
        valid = np.ones((S, n_nodes), dtype=bool)
        valid[:, 3:3 + k] = holders >= 0
        valid[:, 3 + k:3 + 2 * k] = own >= 0
        valid[:, 3 + 2 * k:] = own >= 0
        weights = np.ones((S, len(edges)), dtype=np.float32)
        security_value = np.exp(Q.sec[s_index, 0]) - 1
        holder_value = np.where(holders >= 0, np.exp(x[:, 3:3 + k, 1, 0]) - 1, 0)
        share = holder_value / np.maximum(security_value, 1.0)[:, None]
        own_weight = np.nan_to_num(x[:, 3 + k:3 + 2 * k, 2, 0])
        for i in range(k):
            base = 4 + 6 * i
            weights[:, base:base + 2] = np.nan_to_num(share[:, i])[:, None]
            weights[:, base + 2:base + 4] = own_weight[:, i][:, None]
        max_feature_filed[q] = max(holdings.quarters[qq].max_filed
                                   for qq in range(max(0, q - history + 1), q + 1))
        # The own-rate baseline: the security's entry rate, scaled to the sampling design's own base rate
        # (one positive per ``negatives_per_positive`` negatives, known before any label is read) so that it
        # competes as a probability rather than losing on level alone. Rank order is the entry rate's.
        base_security = np.full((S, 1), np.nan)
        rates = entry_rates[s_index]
        known = np.isfinite(rates)                # a quarter early enough to have no prior quarter has none
        average = rates[known].mean() if known.any() else np.nan
        design_rate = 1.0 / (1.0 + negatives_per_positive)
        if np.isfinite(average) and average > 0:
            base_security[:, 0] = np.clip(rates * design_rate / average, 1e-4, 0.99)
        parts.append((x, valid, weights, y, np.full(S, q), next_filed[m_index], m_index, s_index,
                      base_security, np.full((S, 1), np.nan)))
        if log:
            log(f'    quarter {q}: {S} candidates, {int(y.sum())} opened')
    if not parts:
        raise ValueError('No candidates: every sampled quarter had no manager opening a security that already existed')
    cat = lambda i: np.concatenate([p[i] for p in parts])
    x = cat(0)
    mask = np.isfinite(x)
    return TaskSet(np.nan_to_num(x).astype(np.float16), mask.astype(np.uint8), cat(1), cat(2), cat(3), cat(4),
                   cat(5), cat(6), cat(7), cat(8), cat(9), max_feature_filed)
