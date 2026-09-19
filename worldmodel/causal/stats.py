"""Small numerical helpers for the causal layer (standard library only)."""
import math


def mean(values):
    values = list(values)
    if not values:
        raise ValueError('mean of an empty sequence')
    return math.fsum(values) / len(values)


def normal_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_two_sided_p(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


def normal_ppf(p):
    """Inverse standard normal CDF (Acklam's rational approximation, refined by Newton steps)."""
    if not 0.0 < p < 1.0:
        raise ValueError('p must be in (0, 1)')
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    low = 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p > 1 - low:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    for _ in range(2):
        e = normal_cdf(x) - p
        x -= e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x


def _gamma_series(a, x):
    total = term = 1.0 / a
    ap = a
    for _ in range(10000):
        ap += 1
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_continued_fraction(a, x):
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x, df):
    """Upper tail of the chi-square distribution (regularized upper incomplete gamma)."""
    if df <= 0:
        raise ValueError('df must be positive')
    if x <= 0:
        return 1.0
    a, half = df / 2.0, x / 2.0
    if half < a + 1:
        return max(0.0, 1.0 - _gamma_series(a, half))
    return min(1.0, _gamma_continued_fraction(a, half))


def solve(matrix, vector):
    """Solve A x = b by Gaussian elimination with partial pivoting; raises on a singular A."""
    n = len(matrix)
    aug = [list(map(float, row)) + [float(vector[i])] for i, row in enumerate(matrix)]
    scale = max((abs(v) for row in matrix for v in row), default=0.0) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) <= 1e-12 * scale:
            raise ValueError('singular matrix')
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for r in range(n):
            if r != col:
                factor = aug[r][col] / aug[col][col]
                if factor:
                    row, top = aug[r], aug[col]
                    for k in range(col, n + 1):
                        row[k] -= factor * top[k]
    return [aug[i][n] / aug[i][i] for i in range(n)]


def quantile(values, q):
    """Linear-interpolation quantile (type 7)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError('quantile of an empty sequence')
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def ranks(values):
    """Average ranks (1-based), ties receive the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            result[order[k]] = avg
        i = j + 1
    return result


def pearson(x, y):
    if len(x) != len(y) or len(x) < 2:
        raise ValueError('pearson needs two equal-length sequences of length >= 2')
    mx, my = mean(x), mean(y)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = math.fsum((a - mx) ** 2 for a in x)
    syy = math.fsum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(x, y):
    """Spearman rank correlation with average ranks for ties; None when either side is constant."""
    return pearson(ranks(list(x)), ranks(list(y)))
