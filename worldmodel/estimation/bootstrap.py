"""Seeded iid, moving/circular block and stationary bootstraps for estimator uncertainty."""
import math
import random

METHODS = ('iid', 'moving', 'circular', 'stationary')
MAX_RETAINED_DRAWS = 1000


def default_block_length(n):
    return max(1, int(math.ceil(n ** (1.0 / 3.0))))


def resample_indices(n, rng, *, method='circular', block_length=None):
    if n < 2:
        raise ValueError('Bootstrap needs at least two observations')
    if method not in METHODS:
        raise ValueError('Unknown bootstrap method')
    block = block_length or default_block_length(n)
    if type(block) is not int or not 1 <= block <= n:
        raise ValueError('Block length must be an integer in 1..n')
    out = []
    if method == 'iid':
        return [rng.randrange(n) for _ in range(n)]
    if method == 'stationary':
        p = 1.0 / block
        position = rng.randrange(n)
        while len(out) < n:
            out.append(position)
            position = rng.randrange(n) if rng.random() < p else (position + 1) % n
        return out
    while len(out) < n:
        start = rng.randrange(n if method == 'circular' else n - block + 1)
        out.extend((start + i) % n for i in range(block))
    return out[:n]


def quantile(sorted_values, q):
    if not sorted_values:
        raise ValueError('Quantile of empty sequence')
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def bootstrap(rows, statistic, *, replications=499, seed=0, method='circular', block_length=None, confidence=0.9):
    """Resample ``rows`` (time-ordered for block methods) and summarize ``statistic``.

    ``statistic(sample)`` returns a mapping of names to numbers. Replicates that
    raise ``ValueError`` (e.g. singular designs) are counted as failures, not hidden.
    """
    if type(seed) is not int or type(replications) is not int or not 10 <= replications <= 100000:
        raise ValueError('Bootstrap needs an integer seed and 10..100000 replications')
    rng = random.Random(seed)
    rows = list(rows)
    point = statistic(rows)
    names = sorted(point)
    draws = {name: [] for name in names}
    failures = 0
    block = block_length or default_block_length(len(rows))
    for _ in range(replications):
        sample = [rows[i] for i in resample_indices(len(rows), rng, method=method, block_length=None if method == 'iid' else block)]
        try:
            value = statistic(sample)
        except (ValueError, ZeroDivisionError, OverflowError):
            failures += 1
            continue
        for name in names:
            if isinstance(value.get(name), (int, float)) and math.isfinite(value[name]):
                draws[name].append(float(value[name]))
    alpha = (1 - confidence) / 2
    summary = {}
    for name in names:
        values = sorted(draws[name])
        if len(values) < 2:
            summary[name] = {'estimate': point[name], 'se': None, 'interval': None, 'draws': len(values)}
            continue
        mean = math.fsum(values) / len(values)
        sd = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))
        summary[name] = {'estimate': point[name], 'se': sd, 'bias': mean - point[name],
                         'interval': [quantile(values, alpha), quantile(values, 1 - alpha)], 'draws': len(values)}
    return {'method': method, 'block_length': None if method == 'iid' else block, 'replications': replications,
            'failures': failures, 'seed': seed, 'confidence': confidence, 'summary': summary,
            'retained_draws': {name: draws[name][:MAX_RETAINED_DRAWS] for name in names}}
