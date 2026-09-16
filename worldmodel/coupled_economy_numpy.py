"""Vectorized coupled-economy backend (optional numpy); results equal the reference.

``ArrayEconomy`` holds the coupled economy as int64 cent/quantity columns in sorted
actor-ID order (the reference processing order). A step reproduces the sequential
reference exactly:

* independent per-actor arithmetic is vectorized;
* order-dependent couplings (shared worker labor, interbank reserve liquidity,
  household deposits across purchases, firm inventory across buyers) are solved
  as the unique fixed point of the sequential recurrence by Jacobi iteration
  over exclusive group prefix sums. Starting from an upper bound, iterate k is
  exact for every order position whose dependency chain is shorter than k, so
  the first repeated iterate is the sequential answer;
* weighted-average inventory cost allocation is applied in exact rounds by buyer
  rank within each firm, with integer half-up rounding identical to the Decimal
  reference while inventories stay below 5e12 units;
* steps that need full audit records (history 'full' or sampled every_n steps),
  declared bankruptcies/collateral sales on active firms, non-converging
  couplings or balances near int64 limits run the reference step on an exported
  dictionary state and reload it (exact, slower). ``diagnostics`` counts both.

Summary history records, final states and errors match the pure-Python reference
(tests/test_coupled_economy_numpy.py); money stays integer cents throughout.
"""
from copy import deepcopy
from fractions import Fraction

from . import coupled_economy as reference
from .backends import load_numpy
from .banking import _money, _units, parse_banks, MAX_BALANCE_CENTS
from .limits import LimitExceeded, resolve_limits

SENTINEL = 100000000000000
MAX_ITERATIONS = 512
_SCALAR_ROUND_THRESHOLD = 48


class _Fallback(Exception):
    """Internal: this step needs the exact sequential reference path."""


def _prefix_exclusive(np, values, starts):
    """Sum of values from each element's contiguous group start up to (excluding) itself."""
    totals = np.empty(len(values) + 1, dtype=np.int64)
    totals[0] = 0
    np.cumsum(values, out=totals[1:])
    return totals[:-1] - totals[starts]


def _group_sum(np, keys, values, size):
    if len(values) == 0:
        return np.zeros(size, dtype=np.int64)
    if float(np.abs(values).sum(dtype=np.float64)) < 2.0 ** 52:
        return np.rint(np.bincount(keys, weights=values, minlength=size)).astype(np.int64)
    out = np.zeros(size, dtype=np.int64)
    np.add.at(out, keys, values)
    return out


def _starts(np, sorted_keys):
    """Index of each element's contiguous group start (keys already grouped), in O(n)."""
    n = len(sorted_keys)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    change = np.empty(n, dtype=bool); change[0] = True
    np.not_equal(sorted_keys[1:], sorted_keys[:-1], out=change[1:])
    heads = np.flatnonzero(change)
    return np.repeat(heads, np.diff(np.append(heads, n)))


def _half_up_ratio(np, numerator, denominator):
    """sign * floor((2|n| + d) / 2d) for int64 arrays (Fraction half-away-from-zero)."""
    sign = np.where(numerator < 0, -1, 1)
    return sign * ((2 * np.abs(numerator) + denominator) // (2 * denominator))


class ColumnarPolicy:
    """One daily policy as arrays in an ArrayEconomy's sorted index space.

    production/credit_limit/repay are int64 per firm (cents for money); price and
    credit_limit use -1 for "not supplied" (mechanism, 100-cent or zero default),
    bankrupt is bool per firm, labor_capacity is int64 per household with -1 for
    "unchanged", and purchases are parallel household/firm/units arrays with
    unique (household, firm) pairs. Construct with from_dict or from_arrays; a
    policy can be reused across steps of the same economy.
    """

    def __init__(self, economy, *, production, price, credit_limit, repay, bankrupt, labor_capacity,
                 purchase_household, purchase_firm, purchase_units, policy_rate=None, collateral=None, source=None):
        np = economy.np
        order = np.lexsort((purchase_firm, purchase_household))
        self.economy_id = id(economy)
        self.production, self.price, self.credit_limit, self.repay = production, price, credit_limit, repay
        self.bankrupt, self.labor_capacity = bankrupt, labor_capacity
        self.purchase_household = purchase_household[order]
        self.purchase_firm = purchase_firm[order]
        self.purchase_units = purchase_units[order]
        if len(order) > 1:
            same = (self.purchase_household[1:] == self.purchase_household[:-1]) & (self.purchase_firm[1:] == self.purchase_firm[:-1])
            if same.any():
                raise ValueError('Purchases require unique household/firm pairs')
        self.policy_rate = policy_rate
        self.collateral = collateral or {}
        self.source = source
        self._firm_order = None
        self._household_starts = None
        self.slots_without_interest = int(2 * np.count_nonzero(production > 0) + np.count_nonzero(repay > 0)
                                          + np.count_nonzero(bankrupt) + 2 * len(self.collateral) + len(order))

    def household_starts(self, np):
        if self._household_starts is None:
            self._household_starts = _starts(np, self.purchase_household)
        return self._household_starts

    def firm_order(self, np):
        if self._firm_order is None:
            perm = np.argsort(self.purchase_firm, kind='stable')
            self._firm_order = (perm, _starts(np, self.purchase_firm[perm]))
        return self._firm_order

    @classmethod
    def from_arrays(cls, economy, *, production=None, price=None, credit_limit=None, repay=None, bankrupt=None,
                    labor_capacity=None, purchases=None, policy_rate=None):
        """Validated array policy; purchases=(household_idx, firm_idx, units) in sorted-ID index space."""
        np = economy.np; limits = economy.limits
        F, H = economy.firm_count, economy.household_count

        def column(value, size, default, name, dtype=np.int64):
            if value is None:
                return np.full(size, default, dtype=dtype)
            array = np.asarray(value)
            if array.shape != (size,):
                raise ValueError(f'{name} must have one entry per actor')
            return array.astype(dtype, copy=True)

        production = column(production, F, 0, 'production')
        price = column(price, F, -1, 'price')
        credit_limit = column(credit_limit, F, -1, 'credit_limit')
        repay = column(repay, F, 0, 'repay')
        bankrupt = column(bankrupt, F, False, 'bankrupt', bool)
        labor = column(labor_capacity, H, -1, 'labor_capacity')
        maximum = limits.economy_max_quantity
        for name, array, low in (('production', production, 0), ('labor_capacity', labor, -1)):
            if len(array) and (array.min() < low):
                raise ValueError(f'{name} must be an integer between zero and {maximum}')
            if len(array) and array.max() > maximum:
                raise LimitExceeded('economy_max_quantity', int(array.max()), maximum, f'{name} must be an integer between zero and {maximum}')
        for name, array, low in (('credit_limit', credit_limit, -1), ('repay', repay, 0), ('price', price, -1)):
            if len(array) and (array.min() < low or array.max() > 100000000000000):
                raise ValueError(f'{name} must be finite and within the ledger range')
        if (price == 0).any():
            raise ValueError('price must be positive')
        if purchases is None:
            households = firms = units = np.zeros(0, dtype=np.int64)
        else:
            households, firms, units = (np.asarray(item).astype(np.int64) for item in purchases)
            if not (households.shape == firms.shape == units.shape) or households.ndim != 1:
                raise ValueError('purchases require parallel household, firm and units arrays')
            if len(units) and (households.min() < 0 or households.max() >= H or firms.min() < 0 or firms.max() >= F):
                raise ValueError('Unknown purchase firm')
            if len(units) and units.min() < 0:
                raise ValueError(f'purchases must be an integer between zero and {maximum}')
            if len(units) and units.max() > maximum:
                raise LimitExceeded('economy_max_quantity', int(units.max()), maximum, 'purchases must be a bounded integer')
        if policy_rate is not None:
            from . import economy_mechanisms as mechanisms
            mechanisms.number(policy_rate, 'policy_rate')
            if 'interest' not in economy.mechanisms: raise ValueError('policy_rate requires interest mechanism')
        return cls(economy, production=production, price=price, credit_limit=credit_limit, repay=repay, bankrupt=bankrupt,
                   labor_capacity=labor, purchase_household=households, purchase_firm=firms, purchase_units=units,
                   policy_rate=policy_rate)

    @classmethod
    def from_dict(cls, economy, policy):
        """Validate a reference policy dictionary with the reference rules."""
        from . import economy_mechanisms as mechanisms
        np = economy.np; limits = economy.limits
        reference._object(policy, ('firms', 'households', 'policy_rate'), 'policy')
        if 'policy_rate' in policy:
            mechanisms.number(policy['policy_rate'], 'policy_rate')
            if 'interest' not in economy.mechanisms: raise ValueError('policy_rate requires interest mechanism')
        F, H = economy.firm_count, economy.household_count
        production = np.zeros(F, np.int64); price = np.full(F, -1, np.int64); credit = np.full(F, -1, np.int64)
        repay = np.zeros(F, np.int64); bankrupt = np.zeros(F, bool); labor = np.full(H, -1, np.int64)
        firms_map, households_map = policy.get('firms', {}), policy.get('households', {})
        for mapping, index in ((firms_map, economy.firm_index), (households_map, economy.household_index)):
            if not isinstance(mapping, dict) or any(key not in index for key in mapping):
                raise ValueError('Policy references unknown actor')
        collateral = {}
        for fid, action in firms_map.items():
            reference._object(action, ('production', 'price', 'credit_limit', 'repay', 'bankrupt', 'collateral_sale'), 'firm action')
            k = economy.firm_index[fid]
            flag = action.get('bankrupt', False)
            if not isinstance(flag, bool): raise ValueError('bankrupt must be boolean')
            production[k] = reference._count(action.get('production', 0), 'production', limits)
            repay[k] = _money(action.get('repay', 0), 'repay')
            bankrupt[k] = flag
            if 'collateral_sale' in action:
                if not flag: raise ValueError('collateral_sale requires declared bankruptcy')
                sale = action['collateral_sale']
                reference._object(sale, ('buyer', 'units', 'unit_price'), 'collateral sale')
                if set(sale) != {'buyer', 'units', 'unit_price'} or not isinstance(sale['buyer'], str) or sale['buyer'] not in economy.household_index:
                    raise ValueError('Collateral sale requires a known household buyer and explicit terms')
                reference._count(sale['units'], 'collateral units', limits)
                if _money(sale['unit_price'], 'collateral price') <= 0: raise ValueError('Collateral price must be positive')
                collateral[k] = sale
        households, firms, units = [], [], []
        for hid, action in households_map.items():
            reference._object(action, ('purchases', 'labor_capacity'), 'household action')
            h = economy.household_index[hid]
            if 'labor_capacity' in action: labor[h] = reference._count(action['labor_capacity'], 'labor_capacity', limits)
            purchases = action.get('purchases', {})
            if not isinstance(purchases, dict) or any(key not in economy.firm_index for key in purchases):
                raise ValueError('Unknown purchase firm')
            for fid, value in purchases.items():
                households.append(h); firms.append(economy.firm_index[fid]); units.append(value)
        for fid, action in firms_map.items():  # Reference order: validate money after structure.
            k = economy.firm_index[fid]
            if 'credit_limit' in action: credit[k] = _money(action['credit_limit'], 'credit_limit')
            if 'price' in action:
                price[k] = _money(action['price'], 'price')
                if price[k] <= 0: raise ValueError('price must be positive')
        for value in units: reference._count(value, 'purchases', limits)
        return cls(economy, production=production, price=price, credit_limit=credit, repay=repay, bankrupt=bankrupt,
                   labor_capacity=labor, purchase_household=np.array(households, np.int64), purchase_firm=np.array(firms, np.int64),
                   purchase_units=np.array(units, np.int64), policy_rate=policy.get('policy_rate'), collateral=collateral,
                   source=policy)

    def to_dict(self, economy):
        """Equivalent reference policy (the original dictionary when available)."""
        if self.source is not None:
            return self.source
        firms = {}
        for k, fid in enumerate(economy.firm_ids):
            action = {}
            if self.production[k]: action['production'] = int(self.production[k])
            if self.price[k] >= 0: action['price'] = _units(int(self.price[k]))
            if self.credit_limit[k] >= 0: action['credit_limit'] = _units(int(self.credit_limit[k]))
            if self.repay[k]: action['repay'] = _units(int(self.repay[k]))
            if self.bankrupt[k]: action['bankrupt'] = True
            if k in self.collateral: action['collateral_sale'] = self.collateral[k]
            if action: firms[fid] = action
        households = {}
        for h, f, u in zip(self.purchase_household.tolist(), self.purchase_firm.tolist(), self.purchase_units.tolist()):
            households.setdefault(economy.household_ids[h], {'purchases': {}})['purchases'][economy.firm_ids[f]] = u
        for h in economy.np.flatnonzero(self.labor_capacity >= 0).tolist():
            households.setdefault(economy.household_ids[h], {})['labor_capacity'] = int(self.labor_capacity[h])
        policy = {'firms': firms, 'households': households}
        if self.policy_rate is not None: policy['policy_rate'] = self.policy_rate
        return policy


class ArrayEconomy:
    """Columnar coupled economy. Use from_state, step(policy, shock) and to_state."""

    @classmethod
    def from_state(cls, state, *, limits=None):
        limits = resolve_limits(limits)
        reference._validate(state, limits)
        economy = cls.__new__(cls)
        economy.np = load_numpy()
        economy.limits = limits
        economy.diagnostics = {'vectorized_steps': 0, 'reference_steps': 0, 'fallback_reasons': {}, 'max_iterations': 0}
        economy._load(state)
        return economy

    # ------------------------------------------------------------------ state
    def _load(self, state):
        np = self.np; limits = self.limits
        banks = parse_banks(state['banks'], limits)
        self.bank_ids = list(banks)
        bank_index = {bid: i for i, bid in enumerate(self.bank_ids)}
        self.reserves = np.array([b['reserves'] for b in banks.values()], np.int64)
        self.equity = np.array([b['equity'] for b in banks.values()], np.int64)
        households = state['households']; firms = state['firms']
        h_order = sorted(range(len(households)), key=lambda i: households[i]['id'])
        f_order = sorted(range(len(firms)), key=lambda i: firms[i]['id'])
        self.household_ids = [households[i]['id'] for i in h_order]
        self.firm_ids = [firms[i]['id'] for i in f_order]
        self.household_index = {hid: k for k, hid in enumerate(self.household_ids)}
        self.firm_index = {fid: k for k, fid in enumerate(self.firm_ids)}
        self.household_count, self.firm_count = len(households), len(firms)
        self.household_position = np.array(h_order, np.int64)
        self.firm_position = np.array(f_order, np.int64)
        H = [households[i] for i in h_order]; Fs = [firms[i] for i in f_order]
        self.h_bank = np.array([bank_index[h['bank']] for h in H], np.int64)
        self.h_deposit = np.array([banks[h['bank']]['accounts'][h['id']] for h in H], np.int64)
        self.h_has_capacity = np.array(['labor_capacity' in h for h in H], bool)
        self.h_capacity = np.array([h.get('labor_capacity', 0) for h in H], np.int64)
        self.f_bank = np.array([bank_index[f['bank']] for f in Fs], np.int64)
        self.f_worker = np.array([self.household_index[f['worker']] for f in Fs], np.int64)
        self.inventory = np.array([f['inventory'] for f in Fs], np.int64)
        self.capacity = np.array([f['capacity'] for f in Fs], np.int64)
        self.unit_cost = np.array([_money(f['unit_cost'], 'unit_cost') for f in Fs], np.int64)
        self.bankrupt = np.array([f['status'] == 'bankrupt' for f in Fs], bool)
        self.has_labor_per_unit = np.array(['labor_per_unit' in f for f in Fs], bool)
        self.labor_per_unit = np.array([f.get('labor_per_unit', 1) for f in Fs], np.int64)
        self.mechanisms = deepcopy(state.get('mechanisms', {}))
        self.has_mechanisms = 'mechanisms' in state
        options = self.mechanisms
        zeros = lambda: np.zeros(len(Fs), np.int64)
        self.inventory_value = np.array([_money(f['inventory_value'], 'inventory_value') for f in Fs], np.int64) if options.get('inventory_valuation') else zeros()
        self.arrears = np.array([_money(f['interest_arrears'], 'interest_arrears') for f in Fs], np.int64) if 'interest' in options else zeros()
        feedback = 'price_feedback' in options
        self.last_price = np.array([_money(f['last_price'], 'last_price') for f in Fs], np.int64) if feedback else zeros()
        self.last_unmet = np.array([f['last_unmet_demand'] for f in Fs], np.int64) if feedback else zeros()
        self.last_unit_cost = (np.array([_money(f['last_unit_cost'], 'last_unit_cost') for f in Fs], np.int64)
                               if 'cost_pass_through' in options.get('price_feedback', {}) else zeros())
        self.h_labor_output = np.array([h.get('labor_output', 0) for h in H], np.int64)
        self.calibration = deepcopy(state.get('calibration'))
        self._firm_keys = None
        self.f_deposit = np.array([banks[f['bank']]['accounts'][f['id']] for f in Fs], np.int64)
        self.f_loan = np.array([banks[f['bank']]['loans'].get(f['id'], 0) for f in Fs], np.int64)
        self.has_loan_key = np.array([f['id'] in banks[f['bank']]['loans'] for f in Fs], bool)
        self.account_order = [[(key in self.firm_index, self.firm_index[key] if key in self.firm_index else self.household_index[key])
                               for key in bank['accounts']] for bank in banks.values()]
        self.loan_order = [[self.firm_index[key] for key in bank['loans']] for bank in banks.values()]
        self.expectations = dict(state['expectations']) if 'expectations' in state else None
        self.history = list(state['history'])
        self.step_count = state['step']
        self.initial_reserves = state['initial_reserves']
        self.history_policy = reference.history_policy(state.get('history_policy'))
        retained = reference._work_budget(state, limits=limits)
        self.retained_transactions = retained['reserved_transactions']
        self.retained_postings = retained['reserved_postings']

    def to_state(self):
        """Export the reference dictionary state (equal to the pure-Python run)."""
        options = self.mechanisms
        firms = [None] * self.firm_count
        inventory, capacity, unit_cost = self.inventory.tolist(), self.capacity.tolist(), self.unit_cost.tolist()
        bankrupt, lpu, has_lpu = self.bankrupt.tolist(), self.labor_per_unit.tolist(), self.has_labor_per_unit.tolist()
        value, arrears = self.inventory_value.tolist(), self.arrears.tolist()
        last_price, last_unmet, last_cost = self.last_price.tolist(), self.last_unmet.tolist(), self.last_unit_cost.tolist()
        cost_pass_through = 'cost_pass_through' in options.get('price_feedback', {})
        f_bank, f_worker, positions = self.f_bank.tolist(), self.f_worker.tolist(), self.firm_position.tolist()
        for k, fid in enumerate(self.firm_ids):
            firm = {'id': fid, 'bank': self.bank_ids[f_bank[k]], 'worker': self.household_ids[f_worker[k]], 'inventory': inventory[k],
                    'capacity': capacity[k], 'unit_cost': _units(unit_cost[k]), 'status': 'bankrupt' if bankrupt[k] else 'active'}
            if has_lpu[k]: firm['labor_per_unit'] = lpu[k]
            if options.get('inventory_valuation'): firm['inventory_value'] = _units(value[k])
            if 'interest' in options: firm['interest_arrears'] = _units(arrears[k])
            if 'price_feedback' in options:
                firm['last_price'] = _units(last_price[k]); firm['last_unmet_demand'] = last_unmet[k]
            if cost_pass_through: firm['last_unit_cost'] = _units(last_cost[k])
            firms[positions[k]] = firm
        households = [None] * self.household_count
        h_bank, has_capacity, capacity_h = self.h_bank.tolist(), self.h_has_capacity.tolist(), self.h_capacity.tolist()
        labor_output = self.h_labor_output.tolist() if 'labor' in options else None
        for k, (hid, position) in enumerate(zip(self.household_ids, self.household_position.tolist())):
            household = {'id': hid, 'bank': self.bank_ids[h_bank[k]]}
            if has_capacity[k]: household['labor_capacity'] = capacity_h[k]
            if labor_output is not None: household['labor_output'] = labor_output[k]
            households[position] = household
        f_deposit, h_deposit, loans = self.f_deposit.tolist(), self.h_deposit.tolist(), self.f_loan.tolist()
        banks = []
        for b, bid in enumerate(self.bank_ids):
            equity = int(self.equity[b])
            banks.append({'id': bid, 'reserves': _units(int(self.reserves[b])), 'equity': _units(equity),
                          'loans': {self.firm_ids[k]: _units(loans[k]) for k in self.loan_order[b]},
                          'accounts': {(self.firm_ids[k] if is_firm else self.household_ids[k]): _units(f_deposit[k] if is_firm else h_deposit[k])
                                       for is_firm, k in self.account_order[b]},
                          'status': 'insolvent' if equity < 0 else 'zero_equity' if equity == 0 else 'solvent'})
        state = {'banks': banks, 'firms': firms, 'households': households}
        if self.has_mechanisms: state['mechanisms'] = deepcopy(self.mechanisms)
        state.update(step=self.step_count, history=list(self.history), initial_reserves=self.initial_reserves)
        if self.expectations is not None: state['expectations'] = dict(self.expectations)
        if self.history_policy['mode'] != 'full': state['history_policy'] = dict(self.history_policy)
        if self.calibration is not None: state['calibration'] = deepcopy(self.calibration)
        return state

    # ------------------------------------------------------------------- step
    def step(self, policy, shock=None):
        """Advance one day atomically (dict or ColumnarPolicy); returns the new history record."""
        limits = self.limits
        if self.step_count >= limits.coupled_max_steps:
            raise LimitExceeded('coupled_max_steps', self.step_count + 1, limits.coupled_max_steps, 'Economy horizon')
        columns = policy if isinstance(policy, ColumnarPolicy) else ColumnarPolicy.from_dict(self, policy)
        if columns.economy_id != id(self):
            raise ValueError('Columnar policy belongs to a different economy')
        shock = {} if shock is None else shock
        try:
            if reference._retains_full(self.history_policy, self.step_count + 1):
                raise _Fallback('full audit record')
            record = self._vectorized_step(columns, shock)
            self.diagnostics['vectorized_steps'] += 1
            return record
        except _Fallback as reason:
            key = str(reason)
            self.diagnostics['fallback_reasons'][key] = self.diagnostics['fallback_reasons'].get(key, 0) + 1
        state = reference.step_economy(self.to_state(), columns.to_dict(self), shock, limits=limits)
        self._load(state)
        self.diagnostics['reference_steps'] += 1
        return state['history'][-1]

    def _vectorized_step(self, cp, shock):
        from . import economy_mechanisms as mechanisms
        np = self.np; limits = self.limits; maxq = limits.economy_max_quantity
        F, H, B = self.firm_count, self.household_count, len(self.bank_ids)
        options = deepcopy(self.mechanisms)
        interest_slots = reference.mechanism_slots(options, F, H)
        slots = interest_slots + cp.slots_without_interest
        limits.check('coupled_max_step_transactions', slots, 'Economy work budget exceeded: reserved transactions in one step')
        balance_cells = 3 * B + 2 * F + H
        limits.check('coupled_max_ledger_cells', balance_cells, 'Economy work budget exceeded: ledger balance cells')
        # Shock validation (reference order and messages).
        reference._object(shock, ('inventory_loss', 'unit_cost', 'inflation', 'output_gap', 'expected_energy_change', 'expected_rate_change', 'unemployment'), 'shock')
        loss = np.zeros(F, np.int64)
        new_unit_cost = self.unit_cost.copy()
        for key in ('inventory_loss', 'unit_cost'):
            mapping = shock.get(key, {})
            if not isinstance(mapping, dict) or any(fid not in self.firm_index for fid in mapping): raise ValueError('Shock references unknown firm')
            for fid, value in mapping.items():
                k = self.firm_index[fid]
                if key == 'inventory_loss':
                    reference._count(value, key, limits)
                    if value > self.inventory[k]: raise ValueError('Inventory loss exceeds available goods')
                    loss[k] = value
                else:
                    cents = _money(value, key)
                    if cents <= 0: raise ValueError('unit_cost shock must be positive cents')
                    new_unit_cost[k] = cents
        for key in ('inflation', 'output_gap', 'expected_energy_change', 'expected_rate_change'):
            if key in shock: mechanisms.number(shock[key], key, -1, 1)
        if any(k in shock for k in ('expected_energy_change', 'expected_rate_change')) and 'demand_feedback' not in options:
            raise ValueError('Expectation shocks require demand_feedback')
        if 'unemployment' in shock:
            mechanisms.number(shock['unemployment'], 'unemployment')
            if 'default_hazard' not in options: raise ValueError('unemployment shock requires default_hazard')
        previous_policy_rate = options['interest'].get('policy_rate', 0) if 'interest' in options else 0
        rate_audit = mechanisms.apply_rate(options, {} if cp.policy_rate is None else {'policy_rate': cp.policy_rate}, shock)
        step_number = self.step_count + 1
        hazard_audit = mechanisms.update_default_hazard(options, shock, step_number, previous_policy_rate) if 'default_hazard' in options else None
        growth_audit = mechanisms.update_deposit_growth(options, step_number) if 'deposit_growth' in options else None
        credit_audit, generated_limit = mechanisms.update_credit_growth(options, step_number) if 'credit_growth' in options else (None, 0)
        credit_limit = np.where(cp.credit_limit >= 0, cp.credit_limit, generated_limit)
        price = self._prices(cp, options)
        for k, sale in cp.collateral.items():
            if sale['units'] > self.inventory[k] - loss[k]: raise ValueError('Collateral sale units exceed available inventory')
            if self.bankrupt[k] and sale['units']: raise ValueError('Previously bankrupt firm cannot sell collateral again')
        if (cp.bankrupt & ~self.bankrupt).any():
            raise _Fallback('declared bankruptcy')
        if self.inventory.max(initial=0) > 3_000_000_000 or maxq > 2 ** 52:
            raise _Fallback('quantity range')
        money = int(self.reserves.sum()) + int(self.h_deposit.sum()) + int(self.f_deposit.sum()) + int(self.f_loan.sum()) \
            + int(credit_limit.sum()) + int(np.abs(self.equity).sum())
        if money > MAX_BALANCE_CENTS:
            raise _Fallback('balance range')
        capacity_h = np.where(cp.labor_capacity >= 0, cp.labor_capacity, self.h_capacity)
        has_capacity = self.h_has_capacity | (cp.labor_capacity >= 0)
        labor_enabled = bool(has_capacity.any() or self.has_labor_per_unit.any())
        iterations = 0

        # ------------------------------------------------------ default hazard
        # Reference: hazard write-offs in firm-ID order before production.
        bankrupt_start, equity_start, loan_start = self.bankrupt, self.equity, self.f_loan
        hazard_count = hazard_written_off = hazard_insolvent = hazard_defaulted = 0
        if hazard_audit is not None:
            draws = self._draws(mechanisms.step_key(options['default_hazard']['seed'], step_number))
            hazard = ~self.bankrupt & (draws < np.uint64(hazard_audit['threshold']))
            hazard_count = int(np.count_nonzero(hazard))
            if hazard_count:
                principal = np.where(hazard, self.f_loan, 0)
                perm_b = np.argsort(self.f_bank, kind='stable'); starts_b = _starts(np, self.f_bank[perm_b])
                ordered = principal[perm_b]
                before = self.equity[self.f_bank[perm_b]] - _prefix_exclusive(np, ordered, starts_b)
                hazard_insolvent = int(np.count_nonzero((ordered > 0) & (before >= 0) & (before - ordered < 0)))
                hazard_written_off = int(np.count_nonzero(principal > 0)); hazard_defaulted = int(principal.sum())
                equity_start = self.equity - _group_sum(np, self.f_bank, principal, B)
                loan_start = self.f_loan - principal
                bankrupt_start = self.bankrupt | hazard
            hazard_audit['defaults'] = hazard_count

        # ---------------------------------------------------------- production
        inventory = self.inventory.copy(); value = self.inventory_value.copy()
        write_down = np.zeros(F, np.int64)
        if options.get('inventory_valuation') and loss.any():
            write_down = self._allocate(inventory, value, loss)
            value -= write_down
        inventory -= loss
        destroyed = int(loss.sum())
        active = ~bankrupt_start
        f_dep = self.f_deposit.copy(); f_loan = loan_start.copy(); h_dep = self.h_deposit.copy(); reserves = self.reserves.copy()
        production = np.where(active, cp.production, 0)
        uc = new_unit_cost; lpu = self.labor_per_unit
        worker = self.f_worker; bf = self.f_bank; bw = self.h_bank[worker]; interbank = bf != bw
        credit = np.where(equity_start[bf] > 0, np.maximum(0, credit_limit - f_loan), 0)
        worker_capacity = capacity_h[worker]; worker_limited = has_capacity[worker]
        total_reserves = int(reserves.sum())
        upper = np.minimum(production, self.capacity)
        upper = np.minimum(upper, (f_dep + credit) // uc)
        upper = np.minimum(upper, np.where(interbank, total_reserves, SENTINEL) // uc)
        upper = np.minimum(upper, np.where(worker_limited, worker_capacity // lpu, maxq))
        upper = np.where(active, np.maximum(upper, 0), 0)
        requested = _group_sum(np, worker, production * lpu, H)
        labor_iter = False
        if worker_limited.any():
            used = _group_sum(np, worker[worker_limited], (upper * lpu)[worker_limited], H)
            labor_iter = bool((used > capacity_h).any())
        outflow = _group_sum(np, bf[interbank], (upper * uc)[interbank], B)
        binding_banks = np.flatnonzero(outflow > reserves).tolist()
        produced = upper
        if labor_iter or binding_banks:
            perm_w = np.argsort(worker, kind='stable'); starts_w = _starts(np, worker[perm_w])
            for iterations in range(1, MAX_ITERATIONS + 1):
                candidate = upper
                if labor_iter:
                    allocation = np.where(worker_limited, produced * lpu, 0)
                    before = np.empty(F, np.int64); before[perm_w] = _prefix_exclusive(np, allocation[perm_w], starts_w)
                    candidate = np.minimum(candidate, np.where(worker_limited, np.maximum(worker_capacity - before, 0) // lpu, maxq))
                if binding_banks:
                    cost = np.where(interbank, produced * uc, 0)
                    liquidity = np.full(F, SENTINEL, np.int64)
                    for b in binding_banks:
                        flow = np.where(bw == b, cost, 0) - np.where(bf == b, cost, 0)
                        prefix = np.concatenate((np.zeros(1, np.int64), np.cumsum(flow)[:-1]))
                        mask = interbank & (bf == b)
                        liquidity[mask] = np.maximum(reserves[b] + prefix[mask], 0) // uc[mask]
                    candidate = np.minimum(candidate, liquidity)
                candidate = np.where(active, candidate, 0)
                if np.array_equal(candidate, produced):
                    break
                produced = candidate
            else:
                raise _Fallback('production coupling did not converge')
        cost = produced * uc
        new_loans = np.maximum(0, cost - f_dep)
        created = int(new_loans.sum())
        fresh_keys = np.flatnonzero((new_loans > 0) & ~self.has_loan_key).tolist()
        f_loan += new_loans; f_dep += new_loans - cost
        h_dep += _group_sum(np, worker, cost, H)
        flows = np.where(interbank, cost, 0)
        reserves += _group_sum(np, bw, flows, B) - _group_sum(np, bf, flows, B)
        allocated = _group_sum(np, worker, produced * lpu, H)
        inventory += produced
        if inventory.max(initial=0) > maxq:
            raise LimitExceeded('economy_max_quantity', int(inventory.max()), maxq, 'Inventory exceeds bounded range')
        if options.get('inventory_valuation'):
            value += cost
        production_rationed = int(np.count_nonzero(active & (produced < production)))
        labor_output, capacity_next = self.h_labor_output, capacity_h
        if 'labor' in options:
            labor_output = np.minimum(_group_sum(np, worker, produced, H), maxq)
            capacity_next = self._labor_capacity(capacity_h, has_capacity, labor_output, options['labor']['employment_output_elasticity'], maxq)

        # ----------------------------------------------------------- purchases
        ph, pf, units = cp.purchase_household, cp.purchase_firm, cp.purchase_units
        P = len(units)
        sold = np.zeros(F, np.int64); revenue = np.zeros(F, np.int64); unmet = np.zeros(F, np.int64); cogs = np.zeros(F, np.int64)
        purchase_rationed = 0
        if P:
            p_price = price[pf]
            demand = self._demand(units, price, pf, options)
            available = np.where(active, inventory, 0)
            bh = self.h_bank[ph]; bfp = bf[pf]; p_interbank = bh != bfp
            total_reserves = int(reserves.sum())
            spendable = h_dep
            if growth_audit is not None:  # Deposit-growth retention on opening deposits.
                quotient, remainder = np.divmod(self.h_deposit, mechanisms.PPB)
                factor = growth_audit['daily_factor_ppb']
                spendable = h_dep - (quotient * factor + (remainder * factor + mechanisms.PPB // 2) // mechanisms.PPB)
            upper = np.minimum(demand, available[pf])
            upper = np.minimum(upper, np.maximum(spendable[ph], 0) // p_price)
            upper = np.minimum(upper, np.where(p_interbank, total_reserves, SENTINEL) // p_price)
            upper = np.maximum(upper, 0)
            h_starts = cp.household_starts(np)
            spend_upper = _group_sum(np, ph, upper * p_price, H)
            deposit_iter = bool((spend_upper > spendable).any())
            demand_upper = _group_sum(np, pf, upper, F)
            inventory_iter = bool((demand_upper > available).any())
            outflow = _group_sum(np, bh[p_interbank], (upper * p_price)[p_interbank], B)
            binding_banks = np.flatnonzero(outflow > reserves).tolist()
            bought = upper
            if deposit_iter or inventory_iter or binding_banks:
                perm_f, starts_f = cp.firm_order(np)
                for step_iteration in range(1, MAX_ITERATIONS + 1):
                    candidate = upper
                    if deposit_iter:
                        spent = _prefix_exclusive(np, bought * p_price, h_starts)
                        candidate = np.minimum(candidate, np.maximum(spendable[ph] - spent, 0) // p_price)
                    if inventory_iter:
                        taken = np.empty(P, np.int64); taken[perm_f] = _prefix_exclusive(np, bought[perm_f], starts_f)
                        candidate = np.minimum(candidate, np.maximum(available[pf] - taken, 0))
                    if binding_banks:
                        spend = np.where(p_interbank, bought * p_price, 0)
                        liquidity = np.full(P, SENTINEL, np.int64)
                        for b in binding_banks:
                            flow = np.where(bfp == b, spend, 0) - np.where(bh == b, spend, 0)
                            prefix = np.concatenate((np.zeros(1, np.int64), np.cumsum(flow)[:-1]))
                            mask = p_interbank & (bh == b)
                            liquidity[mask] = np.maximum(reserves[b] + prefix[mask], 0) // p_price[mask]
                        candidate = np.minimum(candidate, liquidity)
                    if np.array_equal(candidate, bought):
                        break
                    bought = candidate
                else:
                    raise _Fallback('purchase coupling did not converge')
                iterations = max(iterations, step_iteration)
            spend = bought * p_price
            h_dep -= _group_sum(np, ph, spend, H)
            f_dep += _group_sum(np, pf, spend, F)
            flows = np.where(p_interbank, spend, 0)
            reserves += _group_sum(np, bfp, flows, B) - _group_sum(np, bh, flows, B)
            if options.get('inventory_valuation'):
                cogs = self._allocate_sequence(inventory, value, pf, bought, cp)
                value -= cogs
            sold = _group_sum(np, pf, bought, F)
            inventory -= sold
            revenue = _group_sum(np, pf, spend, F)
            unmet = _group_sum(np, pf, demand - bought, F)
            purchase_rationed = int(np.count_nonzero(bought < demand))
        demand_feedback_events = P if 'demand_feedback' in options else 0

        # ---------------------------------------------------- interest, repay
        paid_interest, defaulted = 0, hazard_defaulted
        interest_cost = np.zeros(F, np.int64); interest_paid = np.zeros(F, np.int64)
        arrears = self.arrears.copy(); equity = equity_start.copy(); bankrupt = bankrupt_start.copy()
        interest_unpaid = 0
        firm_bankrupt, written_off, bank_insolvent = hazard_count, hazard_written_off, hazard_insolvent
        if 'interest' in options:
            interest_cost = np.where(active, self._interest_due(self.f_loan, options['interest']), 0)
            due = interest_cost + np.where(active, arrears, 0)
            interest_paid = np.where(active, np.minimum(due, f_dep), 0)
            f_dep -= interest_paid
            arrears = np.where(active, due - interest_paid, arrears)
            short = active & (due > interest_paid)
            interest_unpaid = int(np.count_nonzero(short))
            paid_interest = int(interest_paid.sum())
            defaults = np.zeros(F, np.int64)
            if options['interest']['insufficient'] == 'bankrupt' and short.any():
                defaults = np.where(short, f_loan, 0)
                f_loan = np.where(short, 0, f_loan)
                bankrupt = bankrupt | short
                firm_bankrupt += interest_unpaid
                written_off += int(np.count_nonzero(defaults > 0))
                defaulted += int(defaults.sum())
                if written_off:
                    perm_b = np.argsort(bf, kind='stable'); starts_b = _starts(np, bf[perm_b])
                    delta = (interest_paid - defaults)[perm_b]
                    before = equity_start[bf[perm_b]] + _prefix_exclusive(np, delta, starts_b)
                    after_payment = before + interest_paid[perm_b]
                    bank_insolvent += int(np.count_nonzero((defaults[perm_b] > 0) & (after_payment >= 0) & (after_payment - defaults[perm_b] < 0)))
            equity += _group_sum(np, bf, interest_paid - defaults, B)
        repay = np.minimum(np.minimum(cp.repay, f_dep), f_loan)
        f_dep -= repay; f_loan -= repay
        repaid = int(repay.sum())
        deposit_paid = 0
        if 'deposit_interest' in options:  # Households then firms, each in ID order, on opening deposits.
            rate, days = mechanisms.deposit_rate(options), options['deposit_interest'].get('day_count', 365)
            accounts_bank = np.concatenate((self.h_bank, bf))
            amounts = np.concatenate((self._rate_due(self.h_deposit, rate, days), self._rate_due(self.f_deposit, rate, days)))
            perm_d = np.argsort(accounts_bank, kind='stable'); starts_d = _starts(np, accounts_bank[perm_d])
            ordered = amounts[perm_d]
            before = equity[accounts_bank[perm_d]] - _prefix_exclusive(np, ordered, starts_d)
            bank_insolvent += int(np.count_nonzero((ordered > 0) & (before >= 0) & (before - ordered < 0)))
            h_dep += amounts[:H]; f_dep += amounts[H:]
            equity -= _group_sum(np, accounts_bank, amounts, B)
            deposit_paid = int(amounts.sum())

        # ---------------------------------------------------------- identities
        final_goods = int(inventory.sum())
        if int(self.inventory.sum()) + int(produced.sum()) - int(sold.sum()) - destroyed != final_goods:
            raise ValueError('Goods conservation failed')
        opening_deposits = int(self.f_deposit.sum()) + int(self.h_deposit.sum())
        if int(f_dep.sum()) + int(h_dep.sum()) - opening_deposits != created - repaid - paid_interest + deposit_paid:
            raise ValueError('Deposit creation/extinction mismatch')
        if int(f_loan.sum()) - int(self.f_loan.sum()) != created - repaid - defaulted:
            raise ValueError('Debt creation/extinction mismatch')
        if int(equity.sum()) - int(self.equity.sum()) != paid_interest - defaulted - deposit_paid:
            raise ValueError('Equity income/loss mismatch')
        if int(reserves.sum()) != int(self.reserves.sum()) or reserves.min(initial=0) < 0 or h_dep.min(initial=0) < 0 or f_dep.min(initial=0) < 0 or f_loan.min(initial=0) < 0:
            raise ValueError('Negative reserve, principal or deposit balance')
        deposits_by_bank = _group_sum(np, self.h_bank, h_dep, B) + _group_sum(np, bf, f_dep, B)
        if not np.array_equal(reserves + _group_sum(np, bf, f_loan, B), deposits_by_bank + equity):
            raise ValueError('Bank fails assets = deposits + equity')
        opening_value = int(self.inventory_value.sum()) if options.get('inventory_valuation') else 0
        closing_value = int(value.sum()) if options.get('inventory_valuation') else 0
        if options.get('inventory_valuation'):
            if opening_value + int(cost.sum()) - int(cogs.sum()) - int(write_down.sum()) != closing_value:
                raise ValueError('Inventory cost conservation failed')
            if (value[inventory == 0] != 0).any():
                raise ValueError('Empty inventory must have zero cost')
        last_price = price if 'price_feedback' in options else self.last_price
        last_unmet = np.minimum(maxq, unmet) if 'price_feedback' in options else self.last_unmet

        # -------------------------------------------------------------- record
        profit = revenue - cost
        totals = {'produced': int(produced.sum()), 'sold': int(sold.sum()), 'revenue': _units(int(revenue.sum())),
                  'cost': _units(int(cost.sum())), 'profit': _units(int(profit.sum()))}
        if options.get('inventory_valuation'):
            totals.update(cost_of_goods_sold=_units(int(cogs.sum())), inventory_write_down=_units(int(write_down.sum())),
                          operating_profit=_units(int((revenue - cogs - write_down).sum())))
        if 'interest' in options:
            totals.update(interest_due=_units(int(interest_cost.sum())), interest_paid=_units(paid_interest),
                          cash_surplus_after_interest=_units(int((profit - interest_paid).sum())))
        counts = {'production_rationed': production_rationed, 'purchase_rationed': purchase_rationed,
                  'demand_feedback': demand_feedback_events, 'interest_unpaid': interest_unpaid, 'firm_bankrupt': firm_bankrupt,
                  'loan_written_off': written_off, 'bank_insolvent': bank_insolvent}
        accounting = {'balanced': True, 'reserve_change': 0, 'loan_created': _units(created), 'loan_repaid': _units(repaid),
                      'loan_defaulted': _units(defaulted), 'goods_opening': int(self.inventory.sum()), 'goods_destroyed': destroyed,
                      'goods_closing': final_goods}
        if 'interest' in options: accounting['interest_paid'] = _units(paid_interest)
        if 'deposit_interest' in options: accounting['deposit_interest_paid'] = _units(deposit_paid)
        if options.get('inventory_valuation'):
            accounting.update(inventory_cost_opening=_units(opening_value), inventory_cost_closing=_units(closing_value))
        work = {'reserved_transactions': self.retained_transactions, 'reserved_postings': self.retained_postings,
                'step_transactions': slots, 'ledger_balance_cells': balance_cells,
                'max_step_transactions': limits.coupled_max_step_transactions,
                'max_retained_postings': limits.coupled_max_retained_postings, 'max_ledger_cells': limits.coupled_max_ledger_cells}
        limits.check('coupled_max_retained_postings', self.retained_postings, 'Economy work budget exceeded: retained journal postings')
        record = {'step': self.step_count + 1, 'summary': True, 'accounting': accounting, 'work': work,
                  'totals': dict(sorted(totals.items())), 'event_counts': dict(sorted((k, v) for k, v in counts.items() if v))}
        if labor_enabled:
            record['labor'] = {'requested': int(requested.sum()), 'allocated': int(allocated.sum()),
                               'unmet': int(requested.sum()) - int(allocated.sum())}
        if 'interest' in options:
            record['monetary_policy'] = rate_audit
        for name, audit in (('default_hazard', hazard_audit), ('deposit_growth', growth_audit), ('credit_growth', credit_audit)):
            if audit is not None: record[name] = audit
        expectations = self.expectations
        if 'demand_feedback' in options:
            expectations = dict(self.expectations)
            for source, target in [('expected_energy_change', 'energy_change'), ('expected_rate_change', 'rate_change')]:
                if source in shock: expectations[target] = shock[source]

        # -------------------------------------------------------------- commit
        self.diagnostics['max_iterations'] = max(self.diagnostics['max_iterations'], iterations)
        self.unit_cost_opening = self.unit_cost
        self.inventory, self.inventory_value, self.unit_cost = inventory, value, new_unit_cost
        self.f_deposit, self.f_loan, self.h_deposit, self.reserves, self.equity = f_dep, f_loan, h_dep, reserves, equity
        self.arrears, self.bankrupt, self.last_price, self.last_unmet = arrears, bankrupt, last_price, last_unmet
        self.h_capacity, self.h_has_capacity, self.h_labor_output = capacity_next, has_capacity, labor_output
        if 'cost_pass_through' in options.get('price_feedback', {}): self.last_unit_cost = self.unit_cost_opening
        if fresh_keys:
            self.has_loan_key = self.has_loan_key.copy(); self.has_loan_key[fresh_keys] = True
            for k in fresh_keys: self.loan_order[int(bf[k])].append(k)
        self.mechanisms = options; self.expectations = expectations
        self.history.append(record); self.step_count += 1
        return record

    # ------------------------------------------------------------ mechanisms
    def _prices(self, cp, options):
        np = self.np
        if 'price_feedback' not in options:
            base = np.full(self.firm_count, 100, np.int64)
        else:
            rule = options['price_feedback']
            an, ad = Fraction(str(rule['adjustment'])).as_integer_ratio()
            un, ud = Fraction(str(rule['unmet_demand_response'])).as_integer_ratio()
            target = rule['target_inventory']; denominator = max(1, target) * ad * ud
            gap = target - self.inventory
            if 'cost_pass_through' in rule:
                base = self._cost_prices(rule, an, ad, un, ud, target, gap)
            bound = int(self.last_price.max(initial=0)) * (denominator + abs(an) * ud * int(np.abs(gap).max(initial=0))
                                                            + abs(un) * ad * int(self.last_unmet.max(initial=0)))
            if 'cost_pass_through' in rule:
                pass
            elif 2 * bound + denominator < 2 ** 62:
                numerator = self.last_price * (denominator + an * ud * gap + un * ad * self.last_unmet)
                base = _half_up_ratio(np, numerator, denominator)
            else:
                values = [_fraction_price(lp, denominator, an * ud * g + un * ad * u)
                          for lp, g, u in zip(self.last_price.tolist(), gap.tolist(), self.last_unmet.tolist())]
                base = np.array(values, dtype=object)
            low, high = _money(rule['minimum_price'], 'price'), _money(rule['maximum_price'], 'price')
            base = np.array([min(high, max(low, int(v))) for v in base.tolist()], np.int64) if base.dtype == object else np.minimum(high, np.maximum(low, base))
        price = np.where(cp.price >= 0, cp.price, base)
        if (price <= 0).any():
            raise ValueError('price must be positive')
        return price

    def _demand(self, units, price, pf, options):
        np = self.np
        if 'demand_feedback' not in options:
            return units
        rule = options['demand_feedback']
        reference_price = _money(rule['reference_price'], 'reference_price')
        elasticity = rule.get('elasticity', 0)
        firms_used = np.unique(pf)
        powers = np.zeros(self.firm_count, np.float64)
        cache = {}
        for k, p in zip(firms_used.tolist(), price[firms_used].tolist()):
            if p not in cache:
                cache[p] = (reference_price / p) ** elasticity
            powers[k] = cache[p]
        expectations = self.expectations
        factor = max(0, min(4, 1 - rule.get('energy_response', 0) * expectations['energy_change']
                            - rule.get('rate_response', 0) * expectations['rate_change']))
        raw = units.astype(np.float64) * powers[pf] * factor
        if not np.isfinite(raw).all() or units.max(initial=0) >= 2 ** 53:
            raise _Fallback('demand range')
        return np.minimum(np.floor(raw), float(self.limits.economy_max_quantity)).astype(np.int64)

    def _cost_prices(self, rule, an, ad, un, ud, target, gap):
        """Price quotes with cost pass-through: exact per-firm denominators (reference Fraction rule)."""
        np = self.np
        cn, cd = Fraction(str(rule['cost_pass_through'])).as_integer_ratio()
        scale = max(1, target)
        luc, change = self.last_unit_cost, self.unit_cost - self.last_unit_cost
        coefficients = (scale * ad * ud * cd, an * ud * cd, un * ad * cd, cn * scale * ad * ud)
        if len(luc):
            top = max(1, int(luc.max()))
            bound = int(self.last_price.max()) * (coefficients[0] * top + abs(coefficients[1]) * top * int(np.abs(gap).max())
                                                  + abs(coefficients[2]) * top * int(self.last_unmet.max()) + abs(coefficients[3]) * int(np.abs(change).max()))
            if 2 * bound + coefficients[0] * top < 2 ** 62 and max(abs(c) for c in coefficients) < 2 ** 62:
                denominator = coefficients[0] * luc
                numerator = self.last_price * (denominator + coefficients[1] * luc * gap + coefficients[2] * luc * self.last_unmet + coefficients[3] * change)
                return _half_up_ratio(np, numerator, denominator)
        values = []
        for lp, g, unmet, last, delta in zip(self.last_price.tolist(), gap.tolist(), self.last_unmet.tolist(), luc.tolist(), change.tolist()):
            fraction = (Fraction(an, ad) * g + Fraction(un, ud) * unmet) / scale + Fraction(cn, cd) * delta / last
            values.append(mechanisms_cents(lp * (1 + fraction)))
        return np.array(values, dtype=object)

    def _draws(self, key):
        """Vectorized economy_mechanisms.draw53 for every firm (uint64 splitmix64)."""
        from . import economy_mechanisms as mechanisms
        np = self.np
        if getattr(self, '_firm_keys', None) is None:
            self._firm_keys = np.array([mechanisms.actor_key(fid) for fid in self.firm_ids], dtype=np.uint64)
        x = self._firm_keys ^ np.uint64(key)
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
        return x >> np.uint64(11)

    def _labor_capacity(self, capacity, has_capacity, output, elasticity, maximum):
        """Vectorized economy_mechanisms.labor_capacity_update (scalar power per unique input triple)."""
        from . import economy_mechanisms as mechanisms
        np = self.np
        previous = self.h_labor_output
        mask = has_capacity & (previous > 0) & (output > 0) & (output != previous)
        if not mask.any():
            return capacity
        index = np.flatnonzero(mask)
        triples = np.stack((capacity[index], previous[index], output[index]), axis=1)
        unique, inverse = np.unique(triples, axis=0, return_inverse=True)
        values = np.array([mechanisms.labor_capacity_update(c, p, o, elasticity, maximum) for c, p, o in unique.tolist()], np.int64)
        result = capacity.copy()
        result[index] = values[np.asarray(inverse).reshape(-1)]
        return result

    def _interest_due(self, principal, interest):
        from . import economy_mechanisms as mechanisms
        return self._rate_due(principal, mechanisms.annual_loan_rate(interest), interest.get('day_count', 365))

    def _rate_due(self, principal, rate, days):
        np = self.np
        numerator, denominator = rate.as_integer_ratio()
        denominator *= days
        if 2 * int(principal.max(initial=0)) * numerator + denominator < 2 ** 62:
            return (2 * principal * numerator + denominator) // (2 * denominator)
        return np.array([(2 * p * numerator + denominator) // (2 * denominator) for p in principal.tolist()], np.int64)

    def _allocate(self, inventory, value, units):
        """Vectorized reference inventory_remove cost allocation for one removal per firm."""
        np = self.np
        mask = units > 0
        allocation = np.zeros(len(units), np.int64)
        if mask.any():
            i, v, u = inventory[mask], value[mask], units[mask]
            quotient, remainder = np.divmod(v, i)
            allocation[mask] = np.minimum(v, quotient * u + (2 * remainder * u + i) // (2 * i))
        return allocation

    def _allocate_sequence(self, inventory, value, pf, bought, cp):
        """Sequential per-firm allocation over buyers in global order, by buyer rank rounds."""
        np = self.np
        F = self.firm_count
        cogs = np.zeros(F, np.int64)
        perm_f, _ = cp.firm_order(np)
        ordered = perm_f[bought[perm_f] > 0]  # Firm-major, global order within firm.
        if not len(ordered):
            return cogs
        firms = pf[ordered]; amounts = bought[ordered]
        rank = np.arange(len(firms)) - _starts(np, firms)
        counts = np.bincount(rank)
        if len(counts) <= 65535:
            by_rank = np.argsort(rank.astype(np.uint16), kind='stable')  # Radix sort.
        else:
            by_rank = np.argsort(rank, kind='stable')
        current_inventory = inventory.copy(); current_value = value.copy()
        offset = 0
        for r, count in enumerate(counts.tolist()):
            if count <= _SCALAR_ROUND_THRESHOLD and r:
                remaining = by_rank[offset:]
                sequences = {}
                for position in np.sort(remaining).tolist():
                    sequences.setdefault(int(firms[position]), []).append(int(amounts[position]))
                for k, sequence in sequences.items():
                    inv, val, total = int(current_inventory[k]), int(current_value[k]), 0
                    for u in sequence:
                        q, rem = divmod(val, inv)
                        allocation = min(val, q * u + (2 * rem * u + inv) // (2 * inv))
                        val -= allocation; inv -= u; total += allocation
                    cogs[k] += total; current_inventory[k] = inv; current_value[k] = val
                break
            index = by_rank[offset:offset + count]
            k, u = firms[index], amounts[index]
            i, v = current_inventory[k], current_value[k]
            quotient, remainder = np.divmod(v, i)
            allocation = np.minimum(v, quotient * u + (2 * remainder * u + i) // (2 * i))
            cogs[k] += allocation; current_value[k] = v - allocation; current_inventory[k] = i - u
            offset += count
        return cogs

    # ------------------------------------------------------------ utilities
    def summary(self):
        return self.history[-1] if self.history else None

    def checkpoint(self, directory):
        """Atomic binary checkpoint of all columns plus JSON metadata (see checkpoints.write_array_checkpoint)."""
        from .checkpoints import write_array_checkpoint
        arrays = {name: getattr(self, name) for name in _COLUMNS}
        metadata = {'schema_version': 1, 'kind': 'coupled_economy_arrays', 'calibration': self.calibration, 'bank_ids': self.bank_ids, 'firm_ids': self.firm_ids,
                    'household_ids': self.household_ids, 'account_order': [[[int(is_firm), k] for is_firm, k in rows] for rows in self.account_order],
                    'loan_order': self.loan_order,
                    'mechanisms': self.mechanisms, 'has_mechanisms': self.has_mechanisms, 'expectations': self.expectations,
                    'history': self.history, 'step': self.step_count, 'initial_reserves': self.initial_reserves,
                    'history_policy': self.history_policy, 'retained': [self.retained_transactions, self.retained_postings]}
        return write_array_checkpoint(arrays, metadata, directory)

    @classmethod
    def restore(cls, directory, *, limits=None):
        """Load a verified binary checkpoint; any integrity failure raises before use."""
        from .checkpoints import read_array_checkpoint
        arrays, metadata = read_array_checkpoint(directory)
        if (metadata.get('kind') != 'coupled_economy_arrays' or metadata.get('schema_version') != 1
                or not set(_COLUMNS) - set(_OPTIONAL_COLUMNS) <= set(arrays) <= set(_COLUMNS)):
            raise ValueError('Not a coupled economy array checkpoint')
        economy = cls.__new__(cls)
        economy.np = load_numpy(); economy.limits = resolve_limits(limits)
        economy.diagnostics = {'vectorized_steps': 0, 'reference_steps': 0, 'fallback_reasons': {}, 'max_iterations': 0}
        np = economy.np
        sizes = {'last_unit_cost': len(metadata['firm_ids']), 'h_labor_output': len(metadata['household_ids'])}
        for name in _COLUMNS:
            dtype = bool if name in _BOOL_COLUMNS else np.int64
            setattr(economy, name, np.asarray(arrays[name], dtype=dtype) if name in arrays else np.zeros(sizes[name], np.int64))
        economy.calibration, economy._firm_keys = metadata.get('calibration'), None
        economy.bank_ids, economy.firm_ids, economy.household_ids = metadata['bank_ids'], metadata['firm_ids'], metadata['household_ids']
        economy.account_order = [[(bool(is_firm), k) for is_firm, k in rows] for rows in metadata['account_order']]
        economy.loan_order = metadata['loan_order']
        economy.firm_index = {fid: k for k, fid in enumerate(economy.firm_ids)}
        economy.household_index = {hid: k for k, hid in enumerate(economy.household_ids)}
        economy.firm_count, economy.household_count = len(economy.firm_ids), len(economy.household_ids)
        economy.mechanisms, economy.has_mechanisms = metadata['mechanisms'], metadata['has_mechanisms']
        economy.expectations, economy.history = metadata['expectations'], metadata['history']
        economy.step_count, economy.initial_reserves = metadata['step'], metadata['initial_reserves']
        economy.history_policy = metadata['history_policy']
        economy.retained_transactions, economy.retained_postings = metadata['retained']
        # Full semantic validation of the restored state before it can be stepped.
        state = economy.to_state()
        reference._validate(state, economy.limits)
        return economy


def mechanisms_cents(value):
    from .economy_mechanisms import cents_rounded
    return cents_rounded(value)


def _fraction_price(last_price, denominator, adjustment):
    numerator = last_price * (denominator + adjustment)
    sign = -1 if numerator < 0 else 1
    return sign * ((2 * abs(numerator) + denominator) // (2 * denominator))


_BOOL_COLUMNS = ('h_has_capacity', 'bankrupt', 'has_labor_per_unit', 'has_loan_key')
_COLUMNS = ('reserves', 'equity', 'household_position', 'firm_position', 'h_bank', 'h_deposit', 'h_has_capacity', 'h_capacity',
            'f_bank', 'f_worker', 'inventory', 'capacity', 'unit_cost', 'bankrupt', 'has_labor_per_unit', 'labor_per_unit',
            'inventory_value', 'arrears', 'last_price', 'last_unmet', 'f_deposit', 'f_loan', 'has_loan_key', 'last_unit_cost', 'h_labor_output')
_OPTIONAL_COLUMNS = ('last_unit_cost', 'h_labor_output')  # Absent in checkpoints written before estimation hooks.
