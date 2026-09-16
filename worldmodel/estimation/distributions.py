"""Reference distribution functions (normal, Student t, chi-square, F) in pure Python."""
import math
from statistics import NormalDist

_STANDARD = NormalDist()


def norm_cdf(x):
    return _STANDARD.cdf(x)


def norm_pdf(x):
    return _STANDARD.pdf(x)


def norm_ppf(p):
    if not 0 < p < 1:
        raise ValueError('Normal quantile probability must be in (0,1)')
    return _STANDARD.inv_cdf(p)


def _betacf(a, b, x):
    """Continued fraction for the regularized incomplete beta (Numerical Recipes)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-15:
            break
    return h


def betainc(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t, df):
    if df <= 0:
        raise ValueError('Degrees of freedom must be positive')
    if math.isinf(df):
        return norm_cdf(t)
    x = df / (df + t * t)
    tail = 0.5 * betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_sf_two_sided(t, df):
    return min(1.0, 2.0 * (1.0 - t_cdf(abs(t), df)))


def gammainc_lower(s, x):
    """Regularized lower incomplete gamma P(s, x)."""
    if x <= 0:
        return 0.0
    if x < s + 1:
        term = total = 1.0 / s
        n = s
        for _ in range(1000):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    tiny = 1e-300
    b = x + 1 - s
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - s)
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
    return 1.0 - math.exp(-x + s * math.log(x) - math.lgamma(s)) * h


def chi2_sf(x, df):
    return 1.0 - gammainc_lower(df / 2.0, x / 2.0)


def f_sf(f, df1, df2):
    if f <= 0:
        return 1.0
    return betainc(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def normal_quantiles(mean, sd, probabilities):
    return {str(p): mean + sd * norm_ppf(p) for p in probabilities}
