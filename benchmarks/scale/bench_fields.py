"""Conservative field transport on synthetic random-neighbor grids (array API).

Each size evolves 3 fields (extensive, intensive, 2-component intensive vector)
for 4 stable substeps with worldmodel.field_arrays.evolve_field_arrays. Units are
component updates = substeps x (cells + edges) x components. Python and numpy are
bit-identical; the pure-Python backend is skipped where it would exceed 10 min.
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

KERNEL = 'fields'
SIZES = {'small': {'cells': 100_000, 'edges': 400_000},
         'medium': {'cells': 1_000_000, 'edges': 4_000_000},
         'large': {'cells': 10_000_000, 'edges': 40_000_000}}
BACKENDS = ['numpy', 'python']
SKIP = {('large', 'python')}
SUBSTEPS = 4


def scenario(cells, edges, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    side = int(math.isqrt(cells))
    base = rng.integers(0, cells, size=edges, dtype=np.int64)
    offsets = np.array([1, side, cells - 1, cells - side], dtype=np.int64)
    targets = (base + offsets[rng.integers(0, 4, size=edges)]) % cells
    measures = rng.uniform(0.5, 2.0, size=cells)
    conductance = rng.uniform(0.0, 0.05, size=edges)
    rates = np.where(rng.random(edges) < 0.25, rng.uniform(0.0, 0.02, size=edges), 0.0)
    fields = {'mass': {'kind': 'extensive', 'values': rng.uniform(0, 10, size=cells)},
              'density': {'kind': 'intensive', 'values': rng.uniform(0, 5, size=cells)},
              'velocity': {'kind': 'intensive', 'values': rng.uniform(-1, 1, size=(cells, 2))}}
    return measures, fields, base, targets, conductance, rates


def run(size, params, backend):
    from worldmodel.field_arrays import evolve_field_arrays
    started = time.perf_counter()
    measures, fields, sources, targets, conductance, rates = scenario(params['cells'], params['edges'])
    if backend == 'python':
        measures, sources, targets = measures.tolist(), sources.tolist(), targets.tolist()
        conductance, rates = conductance.tolist(), rates.tolist()
        fields = {name: {'kind': spec['kind'], 'values': spec['values'].tolist()} for name, spec in fields.items()}
    setup = time.perf_counter() - started
    started = time.perf_counter()
    # A 1-second horizon split into SUBSTEPS explicit steps (well inside stability).
    result = evolve_field_arrays(measures, fields, sources, targets, conductance, rates, duration_seconds=1.0,
                                 step_seconds=1.0 / SUBSTEPS, max_substeps=SUBSTEPS, max_work=10 ** 12, backend=backend,
                                 validate=False)
    kernel = time.perf_counter() - started
    execution = result['execution']
    return {'units': execution['work'], 'unit': 'component updates', 'kernel_seconds': kernel, 'setup_seconds': setup,
            'details': {'substeps': execution['substeps'], 'components': 4,
                        'max_abs_integral_difference': max(abs(d) for c in result['conservation'].values() for d in c['difference'])}}


if __name__ == '__main__':
    from harness import main
    main(globals())
