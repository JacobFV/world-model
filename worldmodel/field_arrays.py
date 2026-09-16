"""Array core for conservative graph fluxes (FieldWorld, evolve_fields, timelines).

Cells are indexed 0..n-1; edges are parallel source/target index arrays with
conductance (measure/second) and directed transport_rate (1/second). Amounts are
integrals: extensive values as given, intensive values times cell measure.

Two backends share one numerical definition. ``python`` (lists of floats) is the
reference. ``numpy`` is bit-identical: per-edge transfers use the same
elementwise operation order, and net changes are accumulated with ``bincount``
over interleaved ``[a0, b0, a1, b1, ...]`` indices and ``[-t0, +t0, ...]``
weights, which is exactly the Python loop's sequential accumulation. Integrals
always use ``math.fsum`` so reported conservation is identical too.

Open fields (estimation hook for field_diffusion_transport) add per-field
first-order ``decay_rate`` (1/second) and uniform ``source_rate`` (amount per
measure per second): each substep adds ``dt * (source_rate * measure - decay_rate *
amount)`` per cell after the edge fluxes, with the same elementwise operation order
in both backends. The accumulated external input is kept per component
(``fields[name]['external']``, math.fsum), so conservation checks become
``final = initial + external``. Defaults of zero leave closed-system arithmetic
unchanged.

Use :func:`evolve_field_arrays` for national-scale grids whose dict
representation would not fit in memory.
"""
import hashlib
import math
import sys
from array import array

from .backends import load_numpy, resolve_backend
from .limits import resolve_limits


def _fsum(values, label='Integral exceeds finite range'):
    try:
        total = math.fsum(values.tolist() if hasattr(values, 'tolist') else values)
    except OverflowError as error:
        raise ValueError(label) from error
    if not math.isfinite(total):
        raise ValueError(label)
    return total


class FieldArrays:
    """Validated topology plus per-field component amount arrays."""

    def __init__(self, measures, sources, targets, conductance=None, transport_rate=None, *, backend='python',
                 validate=True):
        self.backend = backend
        self.n = len(measures)
        self.m = len(sources)
        if len(targets) != self.m:
            raise ValueError('Edge source/target arrays must have equal length')
        if conductance is None:
            conductance = [0.0] * self.m if backend == 'python' else load_numpy().zeros(self.m)
        if transport_rate is None:
            transport_rate = [0.0] * self.m if backend == 'python' else load_numpy().zeros(self.m)
        if len(conductance) != self.m or len(transport_rate) != self.m:
            raise ValueError('Edge coefficient arrays must match edge count')
        if backend == 'numpy':
            np = load_numpy()
            self.measures = np.ascontiguousarray(measures, dtype=np.float64)
            self.sources = np.ascontiguousarray(sources, dtype=np.int64)
            self.targets = np.ascontiguousarray(targets, dtype=np.int64)
            self.conductance = np.ascontiguousarray(conductance, dtype=np.float64)
            self.transport_rate = np.ascontiguousarray(transport_rate, dtype=np.float64)
            if validate:
                if self.n and (not np.isfinite(self.measures).all() or not (self.measures > 0).all()):
                    raise ValueError('measure must be finite and positive')
                if self.m:
                    if self.sources.min() < 0 or self.targets.min() < 0 or self.sources.max() >= self.n or self.targets.max() >= self.n:
                        raise ValueError('Edges need two distinct existing cells')
                    if (self.sources == self.targets).any():
                        raise ValueError('Edges need two distinct existing cells')
                    for label, values in (('conductance', self.conductance), ('transport_rate', self.transport_rate)):
                        if not np.isfinite(values).all() or (values < 0).any():
                            raise ValueError(f'{label} must be finite and nonnegative')
            self._ms = self.measures[self.sources] if self.m else self.measures[:0]
            self._mt = self.measures[self.targets] if self.m else self.measures[:0]
            interleaved = np.empty(2 * self.m, dtype=np.int64)
            interleaved[0::2] = self.sources
            interleaved[1::2] = self.targets
            self._interleaved = interleaved
            self._buffers = None
        else:
            self.measures = [float(v) for v in measures]
            self.sources = [int(v) for v in sources]
            self.targets = [int(v) for v in targets]
            self.conductance = [float(v) for v in conductance]
            self.transport_rate = [float(v) for v in transport_rate]
            if validate:
                if any(not math.isfinite(v) or v <= 0 for v in self.measures):
                    raise ValueError('measure must be finite and positive')
                n = self.n
                if any(not (0 <= a < n and 0 <= b < n) or a == b for a, b in zip(self.sources, self.targets)):
                    raise ValueError('Edges need two distinct existing cells')
                for label, values in (('conductance', self.conductance), ('transport_rate', self.transport_rate)):
                    if any(not math.isfinite(v) or v < 0 for v in values):
                        raise ValueError(f'{label} must be finite and nonnegative')
            self._ms = [self.measures[a] for a in self.sources]
            self._mt = [self.measures[b] for b in self.targets]
        self.fields = {}

    # ----- fields -------------------------------------------------------
    def add_field(self, name, kind, values, *, vector=False, amounts=False, decay_rate=0.0, source_rate=0.0):
        """Add reported values (n,) or (n, width); amounts=True skips measure weighting.

        ``decay_rate`` (1/second) and ``source_rate`` (amount/(measure*second)) make the field open.
        """
        if kind not in ('extensive', 'intensive'):
            raise ValueError('Field needs extensive/intensive kind')
        for label, rate in (('decay_rate', decay_rate), ('source_rate', source_rate)):
            if type(rate) not in (int, float) or not math.isfinite(rate):
                raise ValueError(f'{label} must be finite')
        weight = kind == 'intensive' and not amounts
        if self.backend == 'numpy':
            np = load_numpy()
            data = np.asarray(values, dtype=np.float64)
            if data.ndim == 1 and not vector:
                columns = [data]
            elif data.ndim == 2:
                columns = [data[:, i] for i in range(data.shape[1])]
                vector = True
            else:
                raise ValueError('Field values must have shape (cells,) or (cells, components)')
            if data.shape[0] != self.n:
                raise ValueError('Every field must explicitly cover every selected support')
            comps = []
            for column in columns:
                if not np.isfinite(column).all():
                    raise ValueError('field component must be finite')
                amount = column * self.measures if weight else np.array(column, dtype=np.float64)
                if not np.isfinite(amount).all():
                    raise ValueError('extensive component must be finite')
                comps.append(amount)
        else:
            if len(values) != self.n:
                raise ValueError('Every field must explicitly cover every selected support')
            if vector:
                width = len(values[0]) if self.n else 0
                if any(len(v) != width for v in values):
                    raise ValueError('Vector component count mismatch')
                columns = [[float(v[i]) for v in values] for i in range(width)]
            else:
                columns = [[float(v) for v in values]]
            comps = []
            for column in columns:
                if not all(map(math.isfinite, column)):
                    raise ValueError('field component must be finite')
                amount = [v * m for v, m in zip(column, self.measures)] if weight else column
                if not all(map(math.isfinite, amount)):
                    raise ValueError('extensive component must be finite')
                comps.append(amount)
        self.fields[name] = {'kind': kind, 'vector': bool(vector), 'amounts': comps}
        if decay_rate or source_rate:
            decay, source = float(decay_rate), float(source_rate)
            weighted = source * self.measures if self.backend == 'numpy' else [source * m for m in self.measures]
            self.fields[name].update(decay_rate=decay, source_rate=source, source_amounts=weighted, external=[0.0] * len(comps))
        return self

    def is_open(self, name):
        return 'external' in self.fields[name]

    def decay_peak(self):
        """Largest positive decay rate over open fields (added to the stable-step outgoing rate)."""
        return max([f['decay_rate'] for f in self.fields.values() if f.get('decay_rate', 0.0) > 0] or [0.0])

    def reset_external(self):
        for field in self.fields.values():
            if 'external' in field:
                field['external'] = [0.0] * len(field['amounts'])

    @property
    def width(self):
        return sum(len(f['amounts']) for f in self.fields.values())

    def reported(self, name):
        """Reported values: python list (scalar) / list of lists (vector); numpy (n,) / (n, w)."""
        field = self.fields[name]
        intensive = field['kind'] == 'intensive'
        if self.backend == 'numpy':
            np = load_numpy()
            comps = [c / self.measures if intensive else c for c in field['amounts']]
            for c in comps:
                if not np.isfinite(c).all():
                    raise ValueError('reported component must be finite')
            return np.stack(comps, axis=1) if field['vector'] else comps[0]
        comps = [[v / m for v, m in zip(c, self.measures)] if intensive else c for c in field['amounts']]
        for c in comps:
            if not all(map(math.isfinite, c)):
                raise ValueError('reported component must be finite')
        return [list(row) for row in zip(*comps)] if field['vector'] else list(comps[0])

    def statistics(self, name, reported=True):
        """Component (integral, L1 integral) lists; reported=True re-weights reported values."""
        field = self.fields[name]
        intensive = field['kind'] == 'intensive' and reported
        sums, norms = [], []
        for c in field['amounts']:
            if self.backend == 'numpy':
                np = load_numpy()
                rows = (c / self.measures) * self.measures if intensive else c
                sums.append(_fsum(rows)); norms.append(_fsum(np.abs(rows)))
            else:
                rows = [(v / m) * m for v, m in zip(c, self.measures)] if intensive else c
                sums.append(_fsum(rows)); norms.append(_fsum(map(abs, rows)))
        return sums, norms

    # ----- numerics -----------------------------------------------------
    def peak(self):
        """Maximum outgoing fraction rate; accumulated in interleaved edge order."""
        if self.n == 0:
            return 0.0
        if self.backend == 'numpy':
            np = load_numpy()
            if not self.m:
                return 0.0
            weights = np.empty(2 * self.m, dtype=np.float64)
            weights[0::2] = self.conductance / self._ms + self.transport_rate
            weights[1::2] = self.conductance / self._mt
            return float(np.bincount(self._interleaved, weights=weights, minlength=self.n).max())
        outgoing = [0.0] * self.n
        for a, b, g, r, ma, mb in zip(self.sources, self.targets, self.conductance, self.transport_rate, self._ms, self._mt):
            outgoing[a] += g / ma + r
            outgoing[b] += g / mb
        return max(outgoing)

    def _changes(self, u, dt):
        if self.backend == 'numpy':
            np = load_numpy()
            if self._buffers is None:
                self._buffers = tuple(np.empty(self.m, dtype=np.float64) for _ in range(4)) + (np.empty(2 * self.m, dtype=np.float64),)
            ua, ub, x, y, w = self._buffers
            np.take(u, self.sources, out=ua)
            np.take(u, self.targets, out=ub)
            np.divide(ua, self._ms, out=x)
            np.divide(ub, self._mt, out=y)
            np.subtract(x, y, out=x)
            np.multiply(self.conductance, x, out=x)
            np.multiply(self.transport_rate, ua, out=y)
            np.add(x, y, out=x)
            np.multiply(dt, x, out=x)
            np.negative(x, out=w[0::2])
            w[1::2] = x
            return np.bincount(self._interleaved, weights=w, minlength=self.n)
        changes = [0.0] * self.n
        for a, b, g, r, ma, mb in zip(self.sources, self.targets, self.conductance, self.transport_rate, self._ms, self._mt):
            ua = u[a]
            t = dt * (g * (ua / ma - u[b] / mb) + r * ua)
            changes[a] -= t
            changes[b] += t
        return changes

    def advance(self, dt, steps, *, nonnegative=False, thresholds=None):
        """Apply `steps` simultaneous conservative updates of length dt to every field."""
        numpy = self.backend == 'numpy'
        np = load_numpy() if numpy else None
        for _ in range(steps):
            for name, field in self.fields.items():
                comps = field['amounts']
                open_field = 'external' in field
                for index, u in enumerate(comps):
                    changes = self._changes(u, dt)
                    if open_field:
                        decay, weighted = field['decay_rate'], field['source_amounts']
                        if numpy:
                            term = dt * (weighted - decay * u)
                            changes = changes + term
                        else:
                            term = [dt * (w - decay * v) for w, v in zip(weighted, u)]
                            changes = [c + t for c, t in zip(changes, term)]
                        field['external'][index] = math.fsum([field['external'][index], _fsum(term, 'External field input exceeds finite range')])
                    if nonnegative:
                        threshold = thresholds[name]
                        if numpy:
                            values = u + changes
                            if not np.isfinite(values).all() or (values < threshold).any():
                                raise ValueError('Positivity or finite-value condition violated')
                            values[~(values > 0.0)] = 0.0
                        else:
                            values = [v + c for v, c in zip(u, changes)]
                            if not all(map(math.isfinite, values)) or (values and min(values) < threshold):
                                raise ValueError('Positivity or finite-value condition violated')
                            values = [v if v > 0.0 else 0.0 for v in values]
                    else:
                        if numpy:
                            if not np.isfinite(changes).all():
                                raise ValueError('edge flux must be finite')
                            values = u + changes
                            if not np.isfinite(values).all():
                                raise ValueError('updated component must be finite')
                        else:
                            if not all(map(math.isfinite, changes)):
                                raise ValueError('edge flux must be finite')
                            values = [v + c for v, c in zip(u, changes)]
                            if not all(map(math.isfinite, values)):
                                raise ValueError('updated component must be finite')
                    comps[index] = values
        return self

    def plan(self, duration, requested, max_substeps, max_work, *, peak=None):
        """Signed-dynamics stable step rule: outgoing fraction per substep <= 0.9."""
        peak = self.peak() if peak is None else peak
        if not math.isfinite(peak):
            raise ValueError('outgoing rate must be finite')
        stable = min(requested, .9 / peak) if peak else requested
        if stable <= 0 or not math.isfinite(duration / stable):
            raise ValueError('Numerical stability is outside finite range')
        steps = math.ceil(duration / stable) if duration else 0
        if steps and duration / steps * peak > .9:
            steps += 1
        work = steps * (self.n + self.m) * self.width
        if steps > max_substeps or work > max_work:
            raise ValueError(f'Numerical budget exceeded: {steps} substeps, {work} component updates')
        return {'substeps': steps, 'step_seconds': duration / steps if steps else 0, 'work': work, 'peak': peak}

    def digest(self, ids=None):
        """Deterministic sha256 over ids, measures and reported float64 components."""
        h = hashlib.sha256()
        if ids is not None:
            for key in ids:
                h.update(key.encode('utf-8') + b'\0')
        for name in sorted(self.fields):
            h.update(name.encode('utf-8') + b'\0')
            field = self.fields[name]
            for c in field['amounts']:
                values = (c / self.measures if self.backend == 'numpy' else [v / m for v, m in zip(c, self.measures)]) if field['kind'] == 'intensive' else c
                h.update(_float_bytes(values))
        h.update(_float_bytes(self.measures))
        return h.hexdigest()


def _float_bytes(values):
    if hasattr(values, 'astype'):
        return values.astype('<f8', copy=False).tobytes()
    data = array('d', values)
    if sys.byteorder != 'little':
        data.byteswap()
    return data.tobytes()


def evolve_field_arrays(measures, fields, sources, targets, conductance=None, transport_rate=None, *,
                        duration_seconds, step_seconds=None, max_substeps=10000, max_work=1_000_000,
                        nonnegative=False, backend=None, limits=None, validate=True):
    """Evolve array fields on an explicit closed graph without dict materialization.

    ``fields`` maps names to ``{'kind': 'extensive'|'intensive', 'values': seq}`` with
    optional ``decay_rate``/``source_rate`` (open field; conservation reports ``external_input``)
    where ``values`` has shape (cells,) or (cells, components). Values keep signs
    unless ``nonnegative=True`` (FieldWorld positivity clamp). Returns reported
    values in the input shape (numpy arrays or lists), execution and conservation.
    """
    limits = resolve_limits(limits)
    limits.check('field_max_cells', len(measures), 'Field array cells')
    limits.check('field_max_edges', len(sources), 'Field array edges')
    if not isinstance(fields, dict) or not fields:
        raise ValueError('At least one named field is required')
    limits.check('field_max_fields', len(fields), 'Field array fields')
    for label, value in (('duration_seconds', duration_seconds),):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(label + ' must be finite and nonnegative')
    requested = duration_seconds or 1 if step_seconds is None else step_seconds
    if type(requested) not in (int, float) or not math.isfinite(requested) or requested <= 0:
        raise ValueError('step_seconds must be finite and positive')
    maximum = limits.integer('field_max_substeps', max_substeps, 'max_substeps')
    budget = limits.integer('field_max_work', max_work, 'max_work')
    chosen = resolve_backend(backend, size=len(measures) + len(sources))
    core = FieldArrays(measures, sources, targets, conductance, transport_rate, backend=chosen, validate=validate)
    for name, spec in fields.items():
        values = spec['values']
        shape = getattr(values, 'ndim', None)
        vector = shape == 2 if shape is not None else bool(len(values)) and isinstance(values[0], (list, tuple))
        core.add_field(name, spec['kind'], values, vector=vector, decay_rate=spec.get('decay_rate', 0.0), source_rate=spec.get('source_rate', 0.0))
    limits.check('field_max_values', core.n * core.width, 'Field component storage')
    initial = {name: core.statistics(name, reported=False) for name in core.fields}
    decay = core.decay_peak()
    plan = core.plan(duration_seconds, requested, maximum, budget, peak=core.peak() + decay if decay else None)
    thresholds = {name: -1e-10 * max(1, initial[name][0][0]) for name in core.fields} if nonnegative else None
    core.advance(plan['step_seconds'], plan['substeps'], nonnegative=nonnegative, thresholds=thresholds)
    conservation, output = {}, {}
    for name in core.fields:
        before, norm = initial[name]
        after, after_norm = core.statistics(name)
        if core.is_open(name):
            external = core.fields[name]['external']
            tolerance = [1e-10 * max(1, v, n, abs(e)) for v, n, e in zip(norm, after_norm, external)]
            if any(abs(b - (a + e)) > t for a, b, e, t in zip(before, after, external, tolerance)):
                raise ValueError('Open-field balance check failed (final != initial + external input)')
            conservation[name] = {'initial_integral': before, 'final_integral': after, 'external_input': list(external),
                                  'difference': [b - a - e for a, b, e in zip(before, after, external)], 'absolute_tolerance': tolerance}
        else:
            tolerance = [1e-10 * max(1, v) for v in norm]
            if any(abs(b - a) > t or (not nonnegative and n > old + t) for a, b, old, n, t in zip(before, after, norm, after_norm, tolerance)):
                raise ValueError('Conservation or signed component stability check failed')
            conservation[name] = {'initial_integral': before, 'final_integral': after,
                                  'difference': [b - a for a, b in zip(before, after)], 'absolute_tolerance': tolerance}
        output[name] = core.reported(name)
    return {'fields': output, 'backend': chosen,
            'execution': {'substeps': plan['substeps'], 'step_seconds': plan['step_seconds'], 'work': plan['work'],
                          'maximum_outgoing_fraction': plan['step_seconds'] * plan['peak']},
            'conservation': conservation, 'epistemic_status': 'synthetic_scenario', 'calibrated': False}
