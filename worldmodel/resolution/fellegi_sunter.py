"""Fellegi-Sunter record linkage with EM-estimated m/u probabilities.

Comparison vectors are tuples of discrete agreement levels (0 = disagree, higher =
stronger agreement) or ``None`` for missing values, which contribute no evidence.
EM runs over *pattern counts* (identical vectors are aggregated), so its cost depends
on the number of distinct patterns, not on the number of candidate pairs.
Conditional independence of comparisons given match status is assumed and reported.
"""
import math

EPSILON = 1e-6


class FellegiSunter:
    def __init__(self, comparisons):
        """comparisons: ordered list of (name, number_of_levels)."""
        if not comparisons:
            raise ValueError('Fellegi-Sunter needs at least one comparison')
        self.names = [name for name, _ in comparisons]
        self.levels = [int(levels) for _, levels in comparisons]
        if len(set(self.names)) != len(self.names) or any(n < 2 for n in self.levels):
            raise ValueError('Comparison names must be unique with at least two levels')
        # Informative start: matches concentrate on top agreement, non-matches on disagreement.
        self.m = [[(0.7 if l == n - 1 else 0.3 / (n - 1)) for l in range(n)] for n in self.levels]
        self.u = [[(0.7 if l == 0 else 0.3 / (n - 1)) for l in range(n)] for n in self.levels]
        self.lam = 0.05
        self.history, self.converged, self.iterations = [], False, 0
        self.fixed_u = False

    def _check(self, pattern):
        if len(pattern) != len(self.levels):
            raise ValueError('Pattern length does not match comparisons')
        for level, n in zip(pattern, self.levels):
            if level is not None and not (isinstance(level, int) and 0 <= level < n):
                raise ValueError('Invalid agreement level in pattern')

    def _likelihoods(self, pattern):
        pm, pu = 1.0, 1.0
        for k, level in enumerate(pattern):
            if level is None:
                continue
            pm *= self.m[k][level]
            pu *= self.u[k][level]
        return pm, pu

    def set_u(self, pattern_counts, fixed=True, *, exclude=(), source=None):
        """Estimate u from randomly sampled (overwhelmingly non-matching) pairs.

        fixed=True keeps u constant during EM (appropriate when candidate pairs are not
        pre-filtered on the same comparisons); fixed=False uses it only as the EM start,
        which is more robust when blocking makes non-matching candidates look similar.
        """
        counts = [[0.0] * n for n in self.levels]
        for pattern, count in pattern_counts.items():
            self._check(pattern)
            for k, level in enumerate(pattern):
                if level is not None:
                    counts[k][level] += count
        for k, row in enumerate(counts):
            if self.names[k] in set(exclude):
                continue
            total = sum(row) + 0.5 * len(row)
            self.u[k] = [(c + 0.5) / total for c in row]
        self.fixed_u = bool(fixed)
        self.u_source = source or ('random_pairs_fixed' if fixed else 'random_pairs_initialization')
        return self

    def set_m(self, pattern_counts, *, exclude=(), exclude_m=None, fixed=True):
        """Estimate m from labeled matching pairs (e.g. pairs sharing a published unique identifier).

        Comparisons in ``exclude`` were used to select the labels, so their m cannot be learned
        from them; they keep ``exclude_m`` (name -> level probabilities) or the current start values.
        """
        exclude = set(exclude)
        counts = [[0.0] * n for n in self.levels]
        for pattern, count in pattern_counts.items():
            self._check(pattern)
            for k, level in enumerate(pattern):
                if level is not None:
                    counts[k][level] += count
        for k, row in enumerate(counts):
            name = self.names[k]
            if name in exclude:
                if exclude_m and name in exclude_m:
                    if len(exclude_m[name]) != self.levels[k] or abs(sum(exclude_m[name]) - 1) > 1e-9:
                        raise ValueError('exclude_m must give a probability for every level')
                    self.m[k] = list(exclude_m[name])
                continue
            total = sum(row) + 0.5 * len(row)
            self.m[k] = [(c + 0.5) / total for c in row]
        self.fixed_m = bool(fixed)
        self.m_source = 'labeled_pairs'
        return self

    def fit(self, pattern_counts, *, max_iter=500, tol=1e-7, initial_lambda=None):
        """EM over aggregated pattern counts {pattern_tuple: count}."""
        items = [(tuple(p), float(c)) for p, c in pattern_counts.items() if c > 0]
        if not items:
            raise ValueError('No comparison patterns to fit')
        for pattern, _ in items:
            self._check(pattern)
        if initial_lambda is not None:
            if not 0 < initial_lambda < 1:
                raise ValueError('initial_lambda must be in (0, 1)')
            self.lam = initial_lambda
        total = sum(c for _, c in items)
        self.history, self.converged = [], False
        for iteration in range(1, max_iter + 1):
            m_acc = [[EPSILON] * n for n in self.levels]
            u_acc = [[EPSILON] * n for n in self.levels]
            matched, loglik = 0.0, 0.0
            for pattern, count in items:
                pm, pu = self._likelihoods(pattern)
                numerator = self.lam * pm
                denominator = numerator + (1 - self.lam) * pu
                g = numerator / denominator if denominator > 0 else 0.0
                loglik += count * math.log(max(denominator, 1e-300))
                matched += count * g
                for k, level in enumerate(pattern):
                    if level is None:
                        continue
                    m_acc[k][level] += count * g
                    u_acc[k][level] += count * (1 - g)
            new_lam = min(max(matched / total, EPSILON), 1 - EPSILON)
            new_m = self.m if getattr(self, 'fixed_m', False) else [[v / sum(row) for v in row] for row in m_acc]
            new_u = self.u if self.fixed_u else [[v / sum(row) for v in row] for row in u_acc]
            change = max([abs(new_lam - self.lam)] + [abs(a - b) for ra, rb in zip(new_m, self.m) for a, b in zip(ra, rb)]
                         + [abs(a - b) for ra, rb in zip(new_u, self.u) for a, b in zip(ra, rb)])
            self.lam, self.m, self.u = new_lam, new_m, new_u
            self.history.append({'iteration': iteration, 'log_likelihood': loglik, 'lambda': new_lam, 'max_change': change})
            self.iterations = iteration
            if change < tol:
                self.converged = True
                break
        # Resolve label switching: the "match" class must favor top agreement levels.
        votes = sum(self.m[k][-1] > self.u[k][-1] for k in range(len(self.levels)))
        if votes * 2 < len(self.levels) and not self.fixed_u and not getattr(self, 'fixed_m', False):
            self.m, self.u, self.lam = self.u, self.m, 1 - self.lam
            self.history.append({'label_switch_corrected': True})
        return self

    def weight(self, pattern):
        """log2 Bayes factor of match vs non-match (prior excluded)."""
        self._check(pattern)
        total = 0.0
        for k, level in enumerate(pattern):
            if level is not None:
                total += math.log2(max(self.m[k][level], EPSILON) / max(self.u[k][level], EPSILON))
        return total

    def probability(self, pattern, prior=None):
        prior = self.lam if prior is None else prior
        logit = self.weight(pattern) + math.log2(prior / (1 - prior))
        if logit > 60:
            return 1.0
        if logit < -60:
            return 0.0
        return 1 / (1 + 2 ** -logit)

    def to_dict(self):
        return {'method': 'fellegi_sunter_em', 'comparisons': [{'name': n, 'levels': l} for n, l in zip(self.names, self.levels)],
                'm': self.m, 'u': self.u, 'lambda': self.lam, 'converged': self.converged, 'iterations': self.iterations,
                'fixed_u': self.fixed_u, 'fixed_m': getattr(self, 'fixed_m', False),
                'm_source': getattr(self, 'm_source', 'em'), 'u_source': getattr(self, 'u_source', 'em'),
                'assumption': 'comparisons conditionally independent given match status',
                'log_likelihood': next((h['log_likelihood'] for h in reversed(self.history) if 'log_likelihood' in h), None),
                'weights': {n: [math.log2(max(m, EPSILON) / max(u, EPSILON)) for m, u in zip(self.m[k], self.u[k])]
                            for k, n in enumerate(self.names)}}

    @classmethod
    def from_dict(cls, data):
        model = cls([(c['name'], c['levels']) for c in data['comparisons']])
        model.m, model.u, model.lam = data['m'], data['u'], data['lambda']
        model.converged, model.iterations, model.fixed_u = data.get('converged'), data.get('iterations', 0), data.get('fixed_u', False)
        return model
