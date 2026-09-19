"""As-of tensors over the county panel: what was public on an origin date, as arrays.

A *snapshot* at origin year ``T`` (origin date ``T-12-31``) holds, for every node (county,
state, nation), a history window of ``H`` annual slots per feature (slot ``h`` is year ``T-h``).
A slot is filled only if the panel's ``available_at`` for that value is on or before the origin
date, and every snapshot records the latest ``available_at`` it actually used, so the leakage
audit is a property of the arrays and not a promise.

States and the nation are aggregates of the counties' *available* values (sums for extensive
features, means for intensive ones), so they obey the same as-of rule. Edges are typed and
dated: county->state membership, geographic nearest neighbours (static internal points),
IRS migration flows public by the origin, and co-membership of a CBSA under the latest
delineation public by the origin.

Requires numpy.
"""
from collections import defaultdict
from dataclasses import dataclass, field
import math

import numpy as np

from .county_panel import COUNTY, TARGETS, state_of

NODE_TYPES = ('county', 'state', 'nation')
RELATIONS = ('in_state', 'state_has', 'in_nation', 'nation_has', 'near', 'migration_out', 'migration_in', 'same_cbsa')
IDENTITY_FEATURES = ('laus:unemployment_rate', 'geography:latitude', 'geography:longitude')
INTENSIVE_PREFIXES = ('climdiv:', 'geography:', 'laus:unemployment_rate', 'qcew:average_annual_pay',
                      'bea:per_capita_personal_income')
NATION = 'geo:US'


def is_log_feature(feature):
    return not (feature in IDENTITY_FEATURES or feature.startswith('climdiv:'))


def transform(feature, value):
    if is_log_feature(feature):
        return math.log1p(max(value, 0.0))
    return float(value)


def day_number(iso):
    """'YYYY-MM-DD' as the integer YYYYMMDD, which orders exactly as the dates do."""
    return int(str(iso)[:10].replace('-', ''))


def day_text(number):
    text = f'{int(number):08d}'
    return f'{text[:4]}-{text[4:6]}-{text[6:]}'


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(h))


@dataclass
class Snapshot:
    origin_year: int
    origin: str
    x: np.ndarray            # [N, F, H] transformed, standardized values (0 where missing)
    m: np.ndarray            # [N, F, H] 1.0 where a value was public by the origin
    edges: np.ndarray        # [E, 3] (src, dst, relation)
    weights: np.ndarray      # [E]
    max_available: str       # latest available_at used by any filled slot
    neighbours: dict = field(default_factory=dict)   # node -> list of (node, relation) used by the sampler


class Panel:
    """The published county panel, indexed for as-of snapshots."""

    def __init__(self, values, available, units, edges, *, history=10, features=None):
        self.history = history
        self.values, self.available, self.units = values, available, units
        self.counties = sorted({c for (c, f, y) in values if f == TARGETS[0]})
        county_set = set(self.counties)
        self.states = sorted({state_of(c) for c in self.counties})
        self.nodes = self.counties + self.states + [NATION]
        self.index = {n: i for i, n in enumerate(self.nodes)}
        self.node_type = np.array([0] * len(self.counties) + [1] * len(self.states) + [2], dtype=np.int64)
        self.features = list(features) if features else sorted({f for (_, f, _) in values})
        self.feature_index = {f: i for i, f in enumerate(self.features)}
        # Raw (untransformed) arrays over every year, with availability dates, for fast as-of cuts.
        years = sorted({y for (_, _, y) in values})
        self.first_year, self.last_year = years[0], years[-1]
        Y = self.last_year - self.first_year + 1
        self.raw = np.full((len(self.counties), len(self.features), Y), np.nan, dtype=np.float64)
        self.avail = np.full((len(self.counties), len(self.features), Y), 99991231, dtype=np.int64)
        for (county, feature, year), value in values.items():
            if county not in county_set or feature not in self.feature_index:
                continue
            i, j, k = self.index[county], self.feature_index[feature], year - self.first_year
            self.raw[i, j, k] = transform(feature, value)
            self.avail[i, j, k] = day_number(available[(county, feature, year)])
        self.static = {}
        for feature in ('geography:latitude', 'geography:longitude'):
            j = self.feature_index.get(feature)
            if j is not None:
                self.static[feature] = np.nanmax(self.raw[:, j, :], axis=1)
        self.edge_rows = edges
        self._near = self._nearest(k=8)
        self.mean = np.zeros(len(self.features))
        self.std = np.ones(len(self.features))

    # ------------------------------------------------------------------ structure
    def _nearest(self, k):
        lat, lon = self.static.get('geography:latitude'), self.static.get('geography:longitude')
        out = {}
        if lat is None or lon is None:
            return out
        points = np.radians(np.stack([lat, lon], axis=1))
        ok = np.isfinite(points).all(axis=1)
        xyz = np.stack([np.cos(points[:, 0]) * np.cos(points[:, 1]), np.cos(points[:, 0]) * np.sin(points[:, 1]),
                        np.sin(points[:, 0])], axis=1)
        xyz[~ok] = np.nan
        for i in range(len(self.counties)):
            if not ok[i]:
                continue
            d = np.nansum((xyz - xyz[i]) ** 2, axis=1)
            d[~ok] = np.inf
            d[i] = np.inf
            out[i] = [int(j) for j in np.argsort(d)[:k]]
        return out

    def fit_standardization(self, last_year):
        """Per-feature mean/std over county values with year <= ``last_year`` (training period only)."""
        k = last_year - self.first_year + 1
        block = self.raw[:, :, :max(k, 1)]
        self.mean = np.nan_to_num(np.nanmean(block, axis=(0, 2)))
        std = np.nan_to_num(np.nanstd(block, axis=(0, 2)))
        self.std = np.where(std > 1e-9, std, 1.0)

    # ------------------------------------------------------------------ snapshots
    def snapshot(self, origin_year):
        origin = f'{origin_year}-12-31'
        H, C, F = self.history, len(self.counties), len(self.features)
        N = len(self.nodes)
        x = np.zeros((N, F, H), dtype=np.float32)
        m = np.zeros((N, F, H), dtype=np.float32)
        latest = 0
        cut = day_number(origin)
        raw_levels = np.full((N, F, H), np.nan)
        for h in range(H):
            k = origin_year - h - self.first_year
            if k < 0 or k >= self.raw.shape[2]:
                continue
            values, dates = self.raw[:, :, k], self.avail[:, :, k]
            public = (dates <= cut) & np.isfinite(values)
            if public.any():
                latest = max(latest, int(dates[public].max()))
            raw_levels[:C, :, h] = np.where(public, values, np.nan)
        # State and nation aggregates of public county values (sum extensive, mean intensive), in raw units.
        intensive = np.array([f.startswith(INTENSIVE_PREFIXES) for f in self.features])
        logged = np.array([is_log_feature(f) for f in self.features])
        level = np.where(logged[None, :, None], np.expm1(raw_levels[:C]), raw_levels[:C])
        members = defaultdict(list)
        for i, county in enumerate(self.counties):
            members[self.index[state_of(county)]].append(i)
        members[self.index[NATION]] = list(range(C))
        for node, rows in members.items():
            block = level[rows]
            count = np.isfinite(block).sum(axis=0)
            total = np.nansum(block, axis=0)
            aggregate = np.where(intensive[:, None], total / np.maximum(count, 1), total)
            aggregate = np.where(count > 0, aggregate, np.nan)
            raw_levels[node] = np.where(logged[:, None], np.log1p(np.maximum(aggregate, 0)), aggregate)
        filled = np.isfinite(raw_levels)
        standardized = (raw_levels - self.mean[None, :, None]) / self.std[None, :, None]
        x[filled] = standardized[filled].astype(np.float32)
        m[filled] = 1.0
        edges, weights, neighbours = self._edges(origin, origin_year)
        return Snapshot(origin_year, origin, x, m, edges, weights, day_text(latest) if latest else '0000-01-01', neighbours)

    def _edges(self, origin, origin_year):
        rows, weights = [], []
        neighbours = defaultdict(list)

        def add(a, b, relation, weight=1.0):
            rows.append((a, b, RELATIONS.index(relation)))
            weights.append(weight)
            neighbours[a].append((b, relation))

        nation = self.index[NATION]
        for i, county in enumerate(self.counties):
            s = self.index[state_of(county)]
            add(i, s, 'in_state')
            add(s, i, 'state_has')
            for rank, j in enumerate(self._near.get(i, ())):
                add(i, j, 'near', 1.0 / (rank + 1))
        for s in (self.index[state] for state in self.states):
            add(s, nation, 'in_nation')
            add(nation, s, 'nation_has')
        # Migration: the most recent flow year public by the origin, top flows per origin county.
        public = [e for e in self.edge_rows if e[1] == 'migration_flow' and e[5] <= origin]
        if public:
            year = max(str(e[4])[:4] for e in public)
            outflow = defaultdict(list)
            for a, _, b, w, start, _ in public:
                if str(start)[:4] == year and a in self.index and b in self.index:
                    outflow[self.index[a]].append((float(w), self.index[b]))
            for a, flows in outflow.items():
                total = sum(w for w, _ in flows)
                for w, b in sorted(flows, reverse=True)[:8]:
                    add(a, b, 'migration_out', w / total)
                    add(b, a, 'migration_in', w / total)
        # CBSA co-membership under the latest delineation public by the origin.
        cbsa = [e for e in self.edge_rows if e[1] == 'within_cbsa' and e[5] <= origin]
        if cbsa:
            latest = max(str(e[4])[:4] for e in cbsa)
            groups = defaultdict(list)
            for a, _, b, _, start, _ in cbsa:
                if str(start)[:4] == latest and a in self.index:
                    groups[b].append(self.index[a])
            for group in groups.values():
                for a in group:
                    for b in group[:12]:
                        if a != b:
                            add(a, b, 'same_cbsa', 1.0 / len(group))
        edges = np.array(rows, dtype=np.int64).reshape(-1, 3)
        return edges, np.array(weights, dtype=np.float32), dict(neighbours)

    # ------------------------------------------------------------------ subgraphs and targets
    def subgraph(self, snapshot, seed, max_nodes=64):
        """Seed county, its direct neighbours by every relation, two-hop geography, its state and the nation."""
        chosen = [seed]
        seen = {seed}

        def take(node):
            if node not in seen and len(chosen) < max_nodes:
                seen.add(node)
                chosen.append(node)

        state, nation = self.index[state_of(self.counties[seed])], self.index[NATION]
        take(state)
        take(nation)
        for relation in ('near', 'migration_out', 'migration_in', 'same_cbsa'):
            for node, rel in snapshot.neighbours.get(seed, ()):
                if rel == relation:
                    take(node)
        for node, rel in list(snapshot.neighbours.get(seed, ())):
            if rel == 'near':
                for second, rel2 in snapshot.neighbours.get(node, ()):
                    if rel2 == 'near':
                        take(second)
        return chosen

    def target_values(self, county_index, feature, year):
        """Raw log level of ``feature`` in ``year`` and the date it became public (None if absent)."""
        j = self.feature_index[feature]
        k = year - self.first_year
        if k < 0 or k >= self.raw.shape[2]:
            return None, None
        value = self.raw[county_index, j, k]
        if not np.isfinite(value):
            return None, None
        return float(value), day_text(self.avail[county_index, j, k])

    def anchor(self, county_index, feature, origin_year):
        """Latest public value of ``feature`` at the origin: (log level, year) or (None, None)."""
        origin = f'{origin_year}-12-31'
        for year in range(origin_year, origin_year - self.history, -1):
            value, available = self.target_values(county_index, feature, year)
            if value is not None and available <= origin:
                return value, year
        return None, None

    def history_values(self, county_index, feature, origin_year):
        """Public log levels of ``feature`` at the origin, oldest first, as (year, value)."""
        origin = f'{origin_year}-12-31'
        out = []
        for year in range(origin_year - self.history + 1, origin_year + 1):
            value, available = self.target_values(county_index, feature, year)
            if value is not None and available <= origin:
                out.append((year, value))
        return out
