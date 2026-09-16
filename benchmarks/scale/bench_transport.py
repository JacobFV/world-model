"""Multimodal routing on synthetic road grids (pure Python; compiled once, queried repeatedly).

A square grid has bidirectional road edges between orthogonal neighbours, every
fourth row is a walk corridor, and every tenth column carries scheduled rail
edges with departures, so schedule lookups and mode-aware Pareto frontiers are
exercised. Setup is generation plus compile_network; kernel time is QUERIES
corner-to-corner earliest-arrival and least-cost routes on the compiled network.
Units are popped label expansions.
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

KERNEL = 'transport'
SIZES = {'small': {'side': 160, 'queries': 4},
         'medium': {'side': 500, 'queries': 2},
         'large': {'side': 1120, 'queries': 1}}
BACKENDS = ['python']


def stamp(seconds):
    return f'2026-01-01T{seconds // 3600 % 24:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}Z'


def generate(side, seed=20260915):
    rng = random.Random(seed)
    ids = [f'n{i}' for i in range(side * side)]
    departures = [stamp(21600 + 600 * k) for k in range(12)]
    edges = []
    append = edges.append
    for row in range(side):
        for column in range(side):
            here = row * side + column
            for neighbour, vertical in ((here + 1, False), (here + side, True)):
                if (vertical and row + 1 == side) or (not vertical and column + 1 == side):
                    continue
                if vertical and column % 10 == 0:
                    mode, extra = 'rail', {'departures': departures}
                elif not vertical and row % 4 == 0:
                    mode, extra = 'walk', {}
                else:
                    mode, extra = 'road', {}
                duration = rng.randint(20, 90) if mode != 'walk' else rng.randint(60, 200)
                # Cost tracks traversal time; only scheduled rail waits create time/cost tradeoffs.
                cost = duration if mode != 'walk' else 2 * duration
                for source, target in ((here, neighbour), (neighbour, here)):
                    append({'id': f'e{len(edges)}', 'source': ids[source], 'target': ids[target], 'mode': mode,
                            'duration_seconds': duration, 'cost': cost, **extra})
    return {'nodes': [{'id': node} for node in ids], 'edges': edges}


def run(size, params, backend):
    from worldmodel.transport import compile_network, route
    side = params['side']
    started = time.perf_counter()
    network = generate(side)
    generated = time.perf_counter() - started
    limits = {'transport_max_nodes': side * side, 'transport_max_edges': len(network['edges'])}
    compiled = compile_network(network, limits=limits)
    compile_seconds = time.perf_counter() - started - generated
    edges = len(network['edges'])
    del network
    setup = time.perf_counter() - started
    expansions = 0; statuses = []
    started = time.perf_counter()
    for query in range(params['queries']):
        request = {'origin': 'n0', 'destination': f'n{side * side - 1}', 'departure_time': stamp(21000 + 60 * query),
                   'objective': 'earliest_arrival' if query % 2 == 0 else 'least_cost',
                   'max_expansions': 20 * side * side, 'max_labels': 40 * side * side}
        result = route(compiled, request, limits=limits)
        expansions += result['search']['expansions']; statuses.append(result['status'])
    kernel = time.perf_counter() - started
    return {'units': expansions, 'unit': 'label-expansions', 'kernel_seconds': kernel, 'setup_seconds': setup,
            'details': {'nodes': side * side, 'edges': edges, 'generate_seconds': round(generated, 3),
                        'compile_seconds': round(compile_seconds, 3), 'statuses': statuses,
                        'compiled_edges_per_second': round(edges / compile_seconds) if compile_seconds else None}}


if __name__ == '__main__':
    from harness import main
    main(globals())
