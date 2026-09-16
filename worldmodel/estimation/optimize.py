"""Deterministic derivative-free optimization and numerical derivatives."""
import math


def to_unbounded(value, bounds):
    low, high = bounds if bounds else (None, None)
    if low is not None and high is not None:
        p = (value - low) / (high - low)
        p = min(1 - 1e-12, max(1e-12, p))
        return math.log(p / (1 - p))
    if low is not None:
        return math.log(max(value - low, 1e-300))
    if high is not None:
        return math.log(max(high - value, 1e-300))
    return value


def from_unbounded(z, bounds):
    low, high = bounds if bounds else (None, None)
    if low is not None and high is not None:
        z = max(-700.0, min(700.0, z))
        return low + (high - low) / (1 + math.exp(-z))
    if low is not None:
        return low + math.exp(min(700.0, z))
    if high is not None:
        return high - math.exp(min(700.0, z))
    return z


def nelder_mead(f, x0, *, step=None, max_evaluations=2000, xtol=1e-9, ftol=1e-12, bounds=None):
    """Minimize f over R^n (bounded coordinates are transformed internally).

    Returns {'x', 'fun', 'evaluations', 'converged'}. Nonfinite objective values
    are treated as +inf so the simplex retreats from infeasible regions.
    """
    n = len(x0)
    if n == 0:
        raise ValueError('Nelder-Mead requires at least one parameter')
    bounds = bounds or [None] * n
    evaluations = 0

    def objective(z):
        nonlocal evaluations
        evaluations += 1
        x = [from_unbounded(v, b) for v, b in zip(z, bounds)]
        try:
            value = f(x)
        except (ValueError, ZeroDivisionError, OverflowError):
            return math.inf
        return value if isinstance(value, (int, float)) and math.isfinite(value) else math.inf

    z0 = [to_unbounded(v, b) for v, b in zip(x0, bounds)]
    steps = step or [0.1 * abs(v) if v else 0.1 for v in z0]
    simplex = [z0] + [[z0[j] + (steps[j] if i == j else 0.0) for j in range(n)] for i in range(n)]
    values = [objective(z) for z in simplex]
    converged = False
    while evaluations < max_evaluations:
        order = sorted(range(n + 1), key=lambda i: (values[i], i))
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        spread = max(abs(v - values[0]) for v in values[1:]) if math.isfinite(values[0]) else math.inf
        size = max(abs(simplex[i][j] - simplex[0][j]) for i in range(1, n + 1) for j in range(n))
        if spread <= ftol * max(1.0, abs(values[0])) and size <= xtol * max(1.0, max(abs(v) for v in simplex[0])):
            converged = True
            break
        centroid = [math.fsum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        worst = simplex[-1]
        reflected = [c + (c - w) for c, w in zip(centroid, worst)]
        fr = objective(reflected)
        if values[0] <= fr < values[-2]:
            simplex[-1], values[-1] = reflected, fr
            continue
        if fr < values[0]:
            expanded = [c + 2 * (c - w) for c, w in zip(centroid, worst)]
            fe = objective(expanded)
            simplex[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
            continue
        contracted = [c + 0.5 * (w - c) for c, w in zip(centroid, worst)]
        fc = objective(contracted)
        if fc < values[-1]:
            simplex[-1], values[-1] = contracted, fc
            continue
        best = simplex[0]
        simplex = [best] + [[b + 0.5 * (v - b) for b, v in zip(best, z)] for z in simplex[1:]]
        values = [values[0]] + [objective(z) for z in simplex[1:]]
    best = min(range(n + 1), key=lambda i: (values[i], i))
    return {'x': [from_unbounded(v, b) for v, b in zip(simplex[best], bounds)], 'fun': values[best],
            'evaluations': evaluations, 'converged': converged}


def jacobian(f, x, *, relative_step=1e-5):
    """Central-difference Jacobian of a vector function; rows are outputs."""
    base = f(list(x))
    columns = []
    for j, value in enumerate(x):
        h = relative_step * max(1.0, abs(value))
        up, down = list(x), list(x)
        up[j] += h
        down[j] -= h
        fu, fd = f(up), f(down)
        columns.append([(a - b) / (2 * h) for a, b in zip(fu, fd)])
    return [[columns[j][i] for j in range(len(x))] for i in range(len(base))]


def hessian(f, x, *, relative_step=1e-4):
    n = len(x)
    h = [relative_step * max(1.0, abs(v)) for v in x]
    out = [[0.0] * n for _ in range(n)]
    f0 = f(list(x))
    for i in range(n):
        for j in range(i, n):
            if i == j:
                up, down = list(x), list(x)
                up[i] += h[i]
                down[i] -= h[i]
                value = (f(up) - 2 * f0 + f(down)) / (h[i] ** 2)
            else:
                pp, pm, mp, mm = list(x), list(x), list(x), list(x)
                pp[i] += h[i]; pp[j] += h[j]
                pm[i] += h[i]; pm[j] -= h[j]
                mp[i] -= h[i]; mp[j] += h[j]
                mm[i] -= h[i]; mm[j] -= h[j]
                value = (f(pp) - f(pm) - f(mp) + f(mm)) / (4 * h[i] * h[j])
            out[i][j] = out[j][i] = value
    return out
