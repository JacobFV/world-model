"""String similarity features: Jaro-Winkler, token Jaccard and TF-IDF cosine."""
import math


def jaro(a, b):
    if a == b:
        return 1.0 if a else 0.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(la, lb) // 2 - 1
    if window < 0:
        window = 0
    matched_b = [False] * lb
    matches_a = []
    for i, char in enumerate(a):
        lo, hi = max(0, i - window), min(lb, i + window + 1)
        for j in range(lo, hi):
            if not matched_b[j] and b[j] == char:
                matched_b[j] = True
                matches_a.append(char)
                break
    m = len(matches_a)
    if not m:
        return 0.0
    matches_b = [b[j] for j in range(lb) if matched_b[j]]
    transpositions = sum(x != y for x, y in zip(matches_a, matches_b)) / 2
    return (m / la + m / lb + (m - transpositions) / m) / 3


def jaro_winkler(a, b, prefix_scale=0.1, max_prefix=4):
    """Jaro-Winkler similarity in [0, 1] (Winkler 1990)."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError('Jaro-Winkler compares strings')
    score = jaro(a, b)
    prefix = 0
    for x, y in zip(a[:max_prefix], b[:max_prefix]):
        if x != y:
            break
        prefix += 1
    return score + prefix * prefix_scale * (1 - score)


def token_jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class TfIdf:
    """Token IDF table built from document frequencies; cosine on sparse L2-normalized vectors."""

    def __init__(self, document_frequencies, documents):
        if documents <= 0:
            raise ValueError('TF-IDF needs at least one document')
        self.documents = documents
        self.df = document_frequencies

    @classmethod
    def fit(cls, token_lists):
        df, n = {}, 0
        for tokens in token_lists:
            n += 1
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1
        return cls(df, n)

    def idf(self, token):
        return math.log((1 + self.documents) / (1 + self.df.get(token, 0))) + 1

    def vector(self, tokens):
        counts = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        vec = {t: c * self.idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {t: v / norm for t, v in vec.items()} if norm else {}

    def cosine(self, a, b):
        va, vb = self.vector(a), self.vector(b)
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(v * vb.get(t, 0.0) for t, v in va.items())
