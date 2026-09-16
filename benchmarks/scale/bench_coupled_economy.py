"""Coupled commercial-bank economy at national scale (summary history).

Synthetic scenario: 20 banks, firms each employing one household, households
buying from three random firms per day, interest and weighted-average inventory
valuation enabled, with endogenous deposit and inventory rationing as balances
evolve. numpy uses a reusable ColumnarPolicy; python runs the reference dict API.
Units are actor-steps ((firms + households) x steps).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

KERNEL = 'coupled_economy'
SIZES = {
    'small': {'firms': 1_000, 'households': 10_000, 'steps': 30},
    'medium': {'firms': 10_000, 'households': 100_000, 'steps': 30},
    'large': {'firms': 100_000, 'households': 1_000_000, 'steps': 60},
}
BACKENDS = ('numpy', 'python')
SKIP = {('large', 'python')}


def scenario(firms, households, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    banks = [f'bank{i:02d}' for i in range(20)]
    h_bank = rng.integers(0, len(banks), households)
    f_bank = rng.integers(0, len(banks), firms)
    household_ids = [f'h{i:07d}' for i in range(households)]
    firm_ids = [f'f{i:06d}' for i in range(firms)]
    accounts = [dict() for _ in banks]
    for hid, b in zip(household_ids, h_bank.tolist()):
        accounts[b][hid] = 5000
    for fid, b in zip(firm_ids, f_bank.tolist()):
        accounts[b][fid] = 20000
    bank_rows = []
    for b, bid in enumerate(banks):
        deposits = sum(accounts[b].values())
        reserves = deposits // 5
        bank_rows.append({'id': bid, 'reserves': reserves, 'equity': reserves - deposits, 'accounts': accounts[b], 'loans': {}})
        bank_rows[-1]['equity'] = reserves - deposits  # Negative equity is allowed; credit then requires solvent banks.
    for row in bank_rows:  # Recapitalize so banks are solvent and can originate.
        row['reserves'] = sum(row['accounts'].values()) + 1_000_000
        row['equity'] = 1_000_000
    config = {'banks': bank_rows,
              'firms': [{'id': fid, 'bank': banks[b], 'worker': household_ids[i * (households // firms)], 'inventory': 20,
                         'capacity': 50, 'unit_cost': 6} for i, (fid, b) in enumerate(zip(firm_ids, f_bank.tolist()))],
              'households': [{'id': hid, 'bank': banks[b]} for hid, b in zip(household_ids, h_bank.tolist())],
              'mechanisms': {'interest': {'policy_rate': .05, 'spread': .02, 'day_count': 365, 'insufficient': 'defer'},
                             'inventory_valuation': True}}
    purchase_household = np.repeat(np.arange(households), 3)
    purchase_firm = rng.integers(0, firms, households * 3)
    purchase_units = rng.integers(1, 3, households * 3)
    return config, (purchase_household, purchase_firm, purchase_units)


def run(size, params, backend):
    import numpy as np
    from worldmodel.coupled_economy import initialize_economy, step_economy
    started = time.perf_counter()
    config, (ph, pf, pu) = scenario(params['firms'], params['households'])
    # Deduplicate (household, firm) pairs by summing units.
    key = ph * params['firms'] + pf
    unique, inverse = np.unique(key, return_inverse=True)
    units = np.bincount(inverse, weights=pu).astype(np.int64)
    ph, pf = unique // params['firms'], unique % params['firms']
    state = initialize_economy(config, history='summary')
    steps = params['steps']
    details = {}
    if backend == 'numpy':
        from worldmodel.coupled_economy_numpy import ArrayEconomy, ColumnarPolicy
        economy = ArrayEconomy.from_state(state)
        policy = ColumnarPolicy.from_arrays(economy, production=np.full(params['firms'], 40), price=np.full(params['firms'], 1000),
                                            credit_limit=np.full(params['firms'], 500000), purchases=(ph, pf, units))
        setup = time.perf_counter() - started
        kernel_started = time.perf_counter()
        for _ in range(steps):
            economy.step(policy)
        kernel = time.perf_counter() - kernel_started
        details = dict(economy.diagnostics)
        final = economy.history[-1]
    else:
        firm_ids = [f['id'] for f in sorted(config['firms'], key=lambda f: f['id'])]
        household_ids = sorted(h['id'] for h in config['households'])
        households = {}
        for h, f, u in zip(ph.tolist(), pf.tolist(), units.tolist()):
            households.setdefault(household_ids[h], {'purchases': {}})['purchases'][firm_ids[f]] = u
        policy = {'firms': {fid: {'production': 40, 'price': 10, 'credit_limit': 5000} for fid in firm_ids}, 'households': households}
        setup = time.perf_counter() - started
        kernel_started = time.perf_counter()
        for _ in range(steps):
            state = step_economy(state, policy)
        kernel = time.perf_counter() - kernel_started
        final = state['history'][-1]
    actors = params['firms'] + params['households']
    details.update(seconds_per_step=kernel / steps, purchases_per_step=int(len(units)),
                   projected_3650_step_hours=kernel / steps * 3650 / 3600, final_event_counts=final['event_counts'],
                   final_totals=final['totals'])
    return {'units': actors * steps, 'unit': 'actor-steps', 'kernel_seconds': kernel, 'setup_seconds': setup, 'details': details}


if __name__ == '__main__':
    from harness import main
    main(globals())
