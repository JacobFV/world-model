"""Exposure clearing on synthetic random obligation networks (array-native input).

Each size clears a baseline and a +300 bp floating-rate stress scenario with
stress_exposure_arrays(detail='summary'). Units are clearing work: iterations x
(obligations + entities), summed over both scenarios. Cash is drawn so a
fraction of entities is short and shortfalls cascade through lenders.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

KERNEL = 'exposure'
SIZES = {'small': {'entities': 10_000, 'obligations': 100_000},
         'medium': {'entities': 100_000, 'obligations': 1_000_000},
         'large': {'entities': 1_000_000, 'obligations': 10_000_000}}
BACKENDS = ['python', 'numpy']


def generate(entities, obligations, seed=20260915):
    import numpy as np
    rng = np.random.default_rng(seed)
    borrower = rng.integers(0, entities, obligations, dtype=np.int64)
    lender = (borrower + 1 + rng.integers(0, entities - 1, obligations, dtype=np.int64)) % entities
    principal = np.round(rng.uniform(0, 1000, obligations), 2)
    rate = np.round(rng.uniform(0, .1, obligations), 4)
    floating = rng.random(obligations) < .5
    maturity = rng.integers(1, 730, obligations, dtype=np.int64)
    owed = np.bincount(borrower, weights=principal, minlength=entities)
    cash = np.round(owed * rng.uniform(0, 1.2, entities), 2)
    return dict(cash=cash, borrower=borrower, lender=lender, principal=principal, annual_rate=rate,
                floating=floating, maturity_days=maturity)


def run(size, params, backend):
    from worldmodel.exposure import stress_exposure_arrays
    started = time.perf_counter()
    data = generate(params['entities'], params['obligations'])
    if backend == 'python':
        data = {key: value.tolist() for key, value in data.items()}
    setup = time.perf_counter() - started
    limits = {'exposure_max_entities': max(params['entities'], 1), 'exposure_max_obligations': params['obligations']}
    started = time.perf_counter()
    result = stress_exposure_arrays(as_of='2026-01-01', end='2027-01-01', shock_bps=300, backend=backend,
                                    detail='summary', top=5, limits=limits, **data)
    kernel = time.perf_counter() - started
    return {'units': result['execution']['clearing_work'], 'unit': 'clearing-updates', 'kernel_seconds': kernel,
            'setup_seconds': setup,
            'details': {'baseline_iterations': result['baseline']['iterations'], 'stressed_iterations': result['stressed']['iterations'],
                        'baseline_shortfall': result['baseline']['total_shortfall'],
                        'incremental_shortfall': result['incremental_shortfall'],
                        'entities_with_shortfall': result['stressed']['entities_with_shortfall']}}


if __name__ == '__main__':
    from harness import main
    main(globals())
