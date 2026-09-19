"""Scoring exposure rankings against measured post-event outcomes on held-out events."""
import itertools
import math
import random

from .stats import mean, spearman


def top_k_capture(scores, outcomes, k):
    """Share of the k units with the largest outcome that the k highest scores also select.

    Ties in scores are broken by unit order, which is fixed by the caller. Null expectation k/n.
    """
    n = len(scores)
    k = max(1, min(k, n))
    predicted = set(sorted(range(n), key=lambda i: (-scores[i], i))[:k])
    actual = set(sorted(range(n), key=lambda i: (-outcomes[i], i))[:k])
    return len(predicted & actual) / k


def top_k_lift(scores, outcomes, k):
    """Mean outcome among the k highest-scored units minus the mean outcome of all units."""
    n = len(scores)
    k = max(1, min(k, n))
    predicted = sorted(range(n), key=lambda i: (-scores[i], i))[:k]
    return mean(outcomes[i] for i in predicted) - mean(outcomes)


def score_event(units, rankings, outcome, *, k_frac=0.1):
    """Per-event skill of each named ranking: Spearman, top-k capture and top-k lift.

    ``rankings`` maps a ranking name to ``{unit: score}``; ``outcome`` maps unit to the measured
    post-event change (oriented so that larger means more affected). Units missing a score or
    an outcome are dropped for every ranking alike.
    """
    units = [u for u in units if u in outcome and all(u in r for r in rankings.values())]
    y = [outcome[u] for u in units]
    k = max(1, round(k_frac * len(units)))
    out = {'n_units': len(units), 'k': k}
    for name, ranking in rankings.items():
        s = [ranking[u] for u in units]
        out[name] = {'spearman': spearman(s, y) if len(units) >= 3 else None,
                     'top_k_capture': top_k_capture(s, y, k) if units else None,
                     'top_k_lift': top_k_lift(s, y, k) if units else None}
    out['top_k_capture_null'] = k / len(units) if units else None
    return out


def paired_sign_flip(differences, *, replications=9999, seed=0):
    """One-sided paired permutation test that the mean difference exceeds zero.

    Exact enumeration of all sign flips for up to 16 events, Monte Carlo otherwise.
    """
    d = [x for x in differences if x is not None and math.isfinite(x)]
    if not d:
        return {'n': 0, 'mean_difference': None, 'p': None}
    observed = mean(d)
    if len(d) <= 16:
        null = [mean(s * x for s, x in zip(signs, d)) for signs in itertools.product((1, -1), repeat=len(d))]
        p = sum(1 for v in null if v >= observed - 1e-15) / len(null)
        method = 'exact'
    else:
        rng = random.Random(seed)
        hits = 0
        for _ in range(replications):
            if mean(x if rng.random() < 0.5 else -x for x in d) >= observed - 1e-15:
                hits += 1
        p = (1 + hits) / (replications + 1)
        method = f'monte_carlo_{replications}'
    return {'n': len(d), 'mean_difference': observed, 'p': p, 'method': method}


def compare_rankings(event_scores, candidate, baseline, *, metric='spearman', replications=9999, seed=0):
    """Mean per-event skill of ``candidate`` minus ``baseline`` with a paired sign-flip test."""
    diffs, rows = [], []
    for event in event_scores:
        a = event.get(candidate, {}).get(metric)
        b = event.get(baseline, {}).get(metric)
        if a is None or b is None:
            continue
        diffs.append(a - b)
        rows.append((a, b))
    test = paired_sign_flip(diffs, replications=replications, seed=seed)
    return {'metric': metric, 'candidate': candidate, 'baseline': baseline, 'events': len(rows),
            'candidate_mean': mean(a for a, _ in rows) if rows else None,
            'baseline_mean': mean(b for _, b in rows) if rows else None,
            'events_candidate_better': sum(1 for a, b in rows if a > b), **{k: v for k, v in test.items() if k != 'n'}}
