"""SQLite SpatialStore bulk import, array load, array write-back and subset select.

Synthetic square grid (right/down edges) with 2 scalar fields, stored on disk in a
temporary directory (honours TMPDIR). Units are cells processed by the three bulk
phases (initialize + load_arrays + put_value_arrays); details break out each phase
and a 10,000-cell select().
"""
import math
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

KERNEL = 'spatial_store'
SIZES = {'small': {'cells': 100_000}, 'medium': {'cells': 1_000_000}, 'large': {'cells': 5_000_000}}
BACKENDS = ['numpy']


def world(cells):
    side = int(math.isqrt(cells))
    ids = [f'cell:{i:08d}' for i in range(cells)]
    edges = []
    for i in range(cells):
        if (i + 1) % side and i + 1 < cells:
            edges.append({'id': f'edge:r{i}', 'source': ids[i], 'target': ids[i + 1], 'conductance': 0.01})
        if i + side < cells:
            edges.append({'id': f'edge:d{i}', 'source': ids[i], 'target': ids[i + side], 'conductance': 0.01, 'transport_rate': 0.001})
    return {'measure_unit': 'km2', 'cells': [{'id': key, 'measure': 1.0, 'coordinates': [i % side, i // side]} for i, key in enumerate(ids)],
            'edges': edges, 'fields': {'stock': {'kind': 'extensive', 'unit': 'kg', 'values': {k: float(i % 97) for i, k in enumerate(ids)}},
                                       'density': {'kind': 'intensive', 'unit': 'kg/km2', 'values': {k: 2.5 for k in ids}}}}


def run(size, params, backend):
    from worldmodel.spatial_store import SpatialStore
    started = time.perf_counter()
    config = world(params['cells'])
    setup = time.perf_counter() - started
    phases = {}
    with tempfile.TemporaryDirectory(prefix='wm-bench-spatial-') as tmp:
        with SpatialStore(Path(tmp) / 'store.sqlite') as store:
            began = time.perf_counter()
            store.initialize(config, coordinate_system={'kind': 'cartesian', 'axes': ['x', 'y'], 'unit': 'km'})
            phases['initialize_seconds'] = time.perf_counter() - began
            edges = len(config['edges']); del config
            began = time.perf_counter()
            loaded = store.load_arrays(backend=backend)
            phases['load_arrays_seconds'] = time.perf_counter() - began
            core = loaded['core']
            core.fields['stock']['amounts'][0] = core.fields['stock']['amounts'][0] * 1.5
            began = time.perf_counter()
            store.put_value_arrays(loaded['ids'], core)
            phases['put_value_arrays_seconds'] = time.perf_counter() - began
            began = time.perf_counter()
            subset = store.select(cells=loaded['ids'][:10_000], limit=10_000)
            phases['select_10k_seconds'] = time.perf_counter() - began
            phases['select_boundary_edges'] = subset['selection']['boundary_edges']
            phases['database_bytes'] = (Path(tmp) / 'store.sqlite').stat().st_size
    kernel = phases['initialize_seconds'] + phases['load_arrays_seconds'] + phases['put_value_arrays_seconds']
    return {'units': params['cells'], 'unit': 'cells (import+load+write)', 'kernel_seconds': kernel, 'setup_seconds': setup,
            'details': {**{k: round(v, 3) if isinstance(v, float) else v for k, v in phases.items()}, 'edges': edges}}


if __name__ == '__main__':
    from harness import main
    main(globals())
