"""Unit-by-period panels with staggered first-treatment cohorts."""
import hashlib
import json
import math


class Panel:
    """Outcomes ``{unit: {period: value}}`` plus first-treatment periods.

    ``cohorts`` maps a unit to the first period in which it is treated (an int) or to
    ``None`` when it is never treated within the study. Units missing from ``cohorts`` are
    never treated. Treatment is absorbing: once treated, always treated. ``strata`` optionally
    maps units to a discrete stratum; comparisons are then made only within a stratum (exact
    matching on that covariate). ``clusters`` maps units to the level at which standard errors
    are clustered (default: the unit itself).
    """

    def __init__(self, outcomes, cohorts=None, strata=None, clusters=None):
        self.outcomes = {}
        for unit, series in outcomes.items():
            clean = {}
            for period, value in series.items():
                if value is None:
                    continue
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError(f'non-finite outcome for {unit} at {period}')
                clean[int(period)] = value
            if clean:
                self.outcomes[unit] = clean
        cohorts = cohorts or {}
        self.cohorts = {unit: (None if cohorts.get(unit) is None else int(cohorts[unit])) for unit in self.outcomes}
        self.strata = {unit: (strata or {}).get(unit, '_all') for unit in self.outcomes}
        self.clusters = {unit: (clusters or {}).get(unit, unit) for unit in self.outcomes}
        self.periods = sorted({p for series in self.outcomes.values() for p in series})

    @property
    def units(self):
        return sorted(self.outcomes, key=str)

    def treated_units(self):
        return [u for u in self.units if self.cohorts[u] is not None]

    def never_treated_units(self):
        return [u for u in self.units if self.cohorts[u] is None]

    def cohort_sizes(self):
        sizes = {}
        for unit in self.outcomes:
            g = self.cohorts[unit]
            if g is not None:
                sizes[g] = sizes.get(g, 0) + 1
        return dict(sorted(sizes.items()))

    def n_clusters(self):
        return len(set(self.clusters.values()))

    def subset(self, units):
        units = [u for u in units if u in self.outcomes]
        return Panel({u: self.outcomes[u] for u in units}, {u: self.cohorts[u] for u in units},
                     {u: self.strata[u] for u in units}, {u: self.clusters[u] for u in units})

    def replace(self, outcomes=None, cohorts=None):
        outcomes = self.outcomes if outcomes is None else outcomes
        cohorts = self.cohorts if cohorts is None else cohorts
        return Panel(outcomes, {u: cohorts.get(u) for u in outcomes},
                     {u: self.strata.get(u, '_all') for u in outcomes},
                     {u: self.clusters.get(u, u) for u in outcomes})

    def summary(self):
        obs = sum(len(s) for s in self.outcomes.values())
        return {'units': len(self.outcomes), 'observations': obs, 'periods': [self.periods[0], self.periods[-1]] if self.periods else [],
                'treated_units': len(self.treated_units()), 'never_treated_units': len(self.never_treated_units()),
                'cohort_sizes': {str(k): v for k, v in self.cohort_sizes().items()},
                'strata': len(set(self.strata.values())), 'clusters': self.n_clusters()}

    def digest(self):
        """Content digest of outcomes, cohorts, strata and clusters (order independent)."""
        h = hashlib.sha256()
        for unit in self.units:
            row = [str(unit), self.cohorts[unit], str(self.strata[unit]), str(self.clusters[unit]),
                   sorted((p, repr(v)) for p, v in self.outcomes[unit].items())]
            h.update(json.dumps(row, separators=(',', ':')).encode())
            h.update(b'\n')
        return h.hexdigest()
