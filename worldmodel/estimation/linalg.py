"""Small dense linear algebra in pure Python, with optional NumPy acceleration.

Matrices are lists of row lists. Every routine returns fresh Python floats, so
results are JSON-serializable whichever backend performed the arithmetic. The
pure-Python path is the reference; NumPy is used only for inversion/solves of
larger systems when importable and never changes the public types.
"""
import math

try:  # Optional acceleration; absence is the supported default.
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - depends on the environment
    _np = None

ACCELERATION_THRESHOLD = 40


def backend():
    return 'numpy' if _np is not None else 'python'


def finite(value, label='value'):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def shape(a):
    return len(a), (len(a[0]) if a else 0)


def zeros(n, m):
    return [[0.0] * m for _ in range(n)]


def identity(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def transpose(a):
    return [list(col) for col in zip(*a)] if a else []


def matmul(a, b):
    if not a or not b:
        return []
    if len(a[0]) != len(b):
        raise ValueError('Matrix dimensions disagree')
    bt = transpose(b)
    return [[math.fsum(x * y for x, y in zip(row, col)) for col in bt] for row in a]


def matvec(a, v):
    if a and len(a[0]) != len(v):
        raise ValueError('Matrix/vector dimensions disagree')
    return [math.fsum(x * y for x, y in zip(row, v)) for row in a]


def dot(u, v):
    return math.fsum(x * y for x, y in zip(u, v))


def outer(u, v):
    return [[x * y for y in v] for x in u]


def add(a, b, scale=1.0):
    return [[x + scale * y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def scale(a, s):
    return [[x * s for x in row] for row in a]


def xtwx(x, w=None):
    """X' diag(w) X without forming the diagonal matrix."""
    k = len(x[0])
    out = zeros(k, k)
    for i in range(k):
        for j in range(i, k):
            if w is None:
                value = math.fsum(row[i] * row[j] for row in x)
            else:
                value = math.fsum(wi * row[i] * row[j] for wi, row in zip(w, x))
            out[i][j] = out[j][i] = value
    return out


def xtwy(x, y, w=None):
    k = len(x[0])
    if w is None:
        return [math.fsum(row[i] * yi for row, yi in zip(x, y)) for i in range(k)]
    return [math.fsum(wi * row[i] * yi for wi, row, yi in zip(w, x, y)) for i in range(k)]


def inverse(a):
    """Gauss-Jordan inverse with partial pivoting; raises on (near) singularity."""
    n = len(a)
    if any(len(row) != n for row in a):
        raise ValueError('Inverse requires a square matrix')
    if _np is not None and n >= ACCELERATION_THRESHOLD:
        result = _np.linalg.inv(_np.array(a, dtype=float))
        return [[float(v) for v in row] for row in result]
    m = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    norm = max((abs(v) for row in a for v in row), default=0.0)
    tolerance = max(norm, 1.0) * n * 1e-13
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) <= tolerance:
            raise ValueError('Matrix is singular or ill-conditioned (collinear regressors?)')
        m[col], m[pivot] = m[pivot], m[col]
        p = m[col][col]
        m[col] = [v / p for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                f = m[r][col]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[col])]
    return [row[n:] for row in m]


def solve(a, b):
    return matvec(inverse(a), b)


def cholesky(a):
    n = len(a)
    lower = zeros(n, n)
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - math.fsum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if s <= 0:
                    raise ValueError('Matrix is not positive definite')
                lower[i][j] = math.sqrt(s)
            else:
                lower[i][j] = s / lower[j][j]
    return lower


def log_det_spd(a):
    return 2.0 * math.fsum(math.log(lower[i]) for i, lower in enumerate(cholesky(a)))


def symmetrize(a):
    return [[(a[i][j] + a[j][i]) / 2 for j in range(len(a))] for i in range(len(a))]


def column(x, j):
    return [row[j] for row in x]


def mean(values):
    values = list(values)
    if not values:
        raise ValueError('Mean of empty sequence')
    return math.fsum(values) / len(values)


def variance(values, ddof=1):
    values = list(values)
    if len(values) <= ddof:
        raise ValueError('Too few values for variance')
    m = mean(values)
    return math.fsum((v - m) ** 2 for v in values) / (len(values) - ddof)
