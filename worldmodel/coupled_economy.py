"""Incremental, closed synthetic economy with commercial-bank settlement.

Policy is a per-step action, never a rewritten simulation configuration. All
monetary state lives exclusively in the bank ledger. Production converts paid
labor into indivisible goods; household purchases consume goods immediately.
Reported profit is revenue less this step's production cash outlay (not accrual
profit or inventory valuation). Bankruptcy is an explicit policy decision; it
freezes remaining inventory and writes off principal without seizing deposits.
No interest, labor supply limit, price discovery, collateral recovery, central
bank reaction, calibrated behavior, or causal validation is asserted.

Work is conservatively preflighted across the entire retained policy history:
at most 10000 reserved transactions and 1000000 transaction × ledger-balance
cells. Every purchase entry reserves a slot, even if zero or later rationed;
production reserves origination and wage-payment slots. These bounds constrain
the household/firm cross-product and retained journals, not just actor count.
"""
from copy import deepcopy
from .banking import _money, _units, simulate_banking


def _object(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'Unknown or invalid {name} fields')


def _count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000000:
        raise ValueError(f'{name} must be an integer between zero and 1000000')
    return value


def _work_budget(state, policies=()):
    """Bound ledger-copy work and retained journals before any transactions.

    Recompute from policies rather than trusting a mutable accumulated counter.
    Each firm's possible loan is included in the ledger size even before it is
    originated, so future loan creation cannot invalidate the estimate.
    """
    firms = {firm['id'] for firm in state['firms']}
    households = {household['id'] for household in state['households']}
    balance_cells = 3 * len(state['banks']) + 2 * len(firms) + len(households)
    transactions = postings = 0

    def reserve(policy, journal=()):
        nonlocal transactions, postings
        _object(policy, ('firms', 'households'), 'policy')
        slots = 0
        for name, owners in (('firms', firms), ('households', households)):
            mapping = policy.get(name, {})
            if not isinstance(mapping, dict) or set(mapping) - owners:
                raise ValueError('Policy references unknown actor')
        for action in policy.get('firms', {}).values():
            _object(action, ('production', 'price', 'credit_limit', 'repay', 'bankrupt'), 'firm action')
            bankrupt = action.get('bankrupt', False)
            if not isinstance(bankrupt, bool):
                raise ValueError('bankrupt must be boolean')
            slots += 2 * (_count(action.get('production', 0), 'production') > 0)
            slots += (_money(action.get('repay', 0), 'repay') > 0) + bankrupt
        for action in policy.get('households', {}).values():
            _object(action, ('purchases',), 'household action')
            purchases = action.get('purchases', {})
            if not isinstance(purchases, dict) or set(purchases) - firms:
                raise ValueError('Unknown purchase firm')
            # Count all entries, including no-op or rationed requests: they still
            # consume per-step processing and remain in the policy audit.
            slots += len(purchases)
        if not isinstance(journal, (list, tuple)):
            raise ValueError('History journal must be a list')
        actual_postings = 0
        for entry in journal:
            if not isinstance(entry, dict) or not isinstance(entry.get('postings'), list):
                raise ValueError('History journal requires posting lists')
            actual_postings += len(entry['postings'])
        transactions += max(slots, len(journal))
        postings += max(4 * slots, actual_postings)
        if transactions > 10000 or postings > 40000 or transactions * balance_cells > 1000000:
            raise ValueError('Economy work budget exceeded: maximum 10000 reserved transactions, '
                             '40000 postings, and 1000000 ledger balance-cell updates')

    for record in state['history']:
        if not isinstance(record, dict) or 'policy' not in record:
            raise ValueError('Economy history requires recorded policies')
        reserve(record['policy'], record.get('journal', []))
    for policy in policies:
        reserve(policy)
    return {'reserved_transactions': transactions, 'reserved_postings': postings,
            'ledger_balance_cells': balance_cells, 'ledger_balance_work': transactions * balance_cells,
            'max_transactions': 10000, 'max_postings': 40000, 'max_ledger_balance_work': 1000000}


def _validate(state):
    _object(state, ('banks', 'firms', 'households', 'step', 'history', 'initial_reserves'), 'economy state')
    if set(state) != {'banks', 'firms', 'households', 'step', 'history', 'initial_reserves'}:
        raise ValueError('Incomplete economy state')
    if not isinstance(state['history'], list) or state['step'] != len(state['history']) or not 0 <= state['step'] <= 1000:
        raise ValueError('Invalid bounded step/history')
    for name in ('firms', 'households'):
        if not isinstance(state[name], list) or not 1 <= len(state[name]) <= 100:
            raise ValueError(f'{name} requires 1 to 100 actors')
    if (state['step'] + 1) * (len(state['firms']) + len(state['households'])) > 100000:
        raise ValueError('Economy history budget exceeded')
    ledger = simulate_banking({'banks': state['banks']})
    if _money(state['initial_reserves'], 'initial_reserves') != sum(_money(b['reserves'], 'reserves') for b in ledger['banks']):
        raise ValueError('Aggregate reserves changed from opening anchor')
    banks = {b['id']: b for b in ledger['banks']}
    actors = {}
    for kind in ('households', 'firms'):
        for actor in state[kind]:
            _object(actor, ('id', 'bank') if kind == 'households' else ('id', 'bank', 'worker', 'inventory', 'capacity', 'unit_cost', 'status'), kind)
            aid = actor.get('id')
            if not isinstance(aid, str) or not aid or aid in actors or actor.get('bank') not in banks:
                raise ValueError('Actor requires unique ID and known bank')
            if aid not in banks[actor['bank']]['accounts']:
                raise ValueError('Actor requires an opening bank account')
            actors[aid] = actor
            if kind == 'firms':
                if actor.get('worker') not in {h['id'] for h in state['households']}:
                    raise ValueError('Firm worker must be a household')
                for field in ('inventory', 'capacity'): _count(actor.get(field), field)
                if _money(actor.get('unit_cost'), 'unit_cost') <= 0:
                    raise ValueError('unit_cost must be positive')
                if actor.get('status') not in ('active', 'bankrupt'):
                    raise ValueError('Invalid firm status')
    for bank in banks.values():
        for owner in set(bank['accounts']) | set(bank['loans']):
            if owner not in actors or actors[owner]['bank'] != bank['id']:
                raise ValueError('Every account and loan must belong to its actor at its bank')
        if set(bank['loans']) - {f['id'] for f in state['firms']}:
            raise ValueError('Only firms borrow in this economy')
    _work_budget(state)
    return ledger['banks']


def initialize_economy(config):
    """Create a persistent state from banks, firms and households endowments."""
    _object(config, ('banks', 'firms', 'households'), 'initial economy')
    if set(config) != {'banks', 'firms', 'households'}:
        raise ValueError('Initial economy requires banks, firms and households')
    state = deepcopy(config)
    for firm in state['firms']:
        firm.setdefault('status', 'active')
    state.update(step=0, history=[], initial_reserves=_units(sum(_money(b['reserves'], 'reserves') for b in state['banks'])))
    state['banks'] = _validate(state)
    return state


def step_economy(state, policy, shock=None):
    """Return a new state; errors leave all caller objects unchanged.

    Firm actions: production (units), price (USD/unit), credit_limit (maximum
    outstanding principal), repay (USD), bankrupt (bool). Household actions:
    purchases:{firm:units}. Omitted actions mean zero. Defaults are explicit
    bankruptcy decisions; bank solvency and reserves constrain feasible output.
    Exogenous shock supports inventory_loss only. Missing credit or settlement
    liquidity deterministically rations production/purchases, with audit events.
    """
    banks_list = _validate(state)
    if state['step'] >= 1000:
        raise ValueError('Economy horizon is bounded to 1000 steps')
    _object(policy, ('firms', 'households'), 'policy')
    work = _work_budget(state, [policy])
    shock = {} if shock is None else shock
    _object(shock, ('inventory_loss',), 'shock')
    out = deepcopy(state)
    out['banks'] = banks_list
    actors = {x['id']: x for x in out['firms'] + out['households']}
    firms = {x['id']: x for x in out['firms']}
    households = {x['id']: x for x in out['households']}
    for key, owners in [('firms', firms), ('households', households)]:
        mapping = policy.get(key, {})
        if not isinstance(mapping, dict) or set(mapping) - set(owners):
            raise ValueError('Policy references unknown actor')
    losses = shock.get('inventory_loss', {})
    if not isinstance(losses, dict) or set(losses) - set(firms):
        raise ValueError('Shock references unknown firm')
    actions = {}
    for fid in firms:
        p = policy.get('firms', {}).get(fid, {})
        _object(p, ('production', 'price', 'credit_limit', 'repay', 'bankrupt'), 'firm action')
        if not isinstance(p.get('bankrupt', False), bool): raise ValueError('bankrupt must be boolean')
        actions[fid] = dict(production=_count(p.get('production', 0), 'production'),
                            price=_money(p.get('price', 1), 'price'),
                            credit_limit=_money(p.get('credit_limit', 0), 'credit_limit'),
                            repay=_money(p.get('repay', 0), 'repay'), bankrupt=p.get('bankrupt', False))
        if actions[fid]['price'] <= 0: raise ValueError('price must be positive')
    for hid in households:
        p = policy.get('households', {}).get(hid, {})
        _object(p, ('purchases',), 'household action')
        purchases = p.get('purchases', {})
        if not isinstance(purchases, dict) or set(purchases) - set(firms): raise ValueError('Unknown purchase firm')
        for value in purchases.values(): _count(value, 'purchases')
    events, journal = [], []
    created = repaid = defaulted = 0

    def bank(actor):
        return next(b for b in out['banks'] if b['id'] == actors[actor]['bank'])

    def transact(kind, actor, cents, target=None):
        if not cents: return
        tx = {'kind': kind, 'bank': actors[actor]['bank'], 'amount': _units(cents)}
        if target is None: tx['borrower'] = actor
        else: tx.update(from_account=actor, to_bank=actors[target]['bank'], to_account=target)
        result = simulate_banking({'banks': out['banks'], 'transactions': [tx]})
        out['banks'] = result['banks']
        journal.append({'transaction': tx, 'postings': result['journal'][0]['postings'], 'balanced': True})
        events.extend(result['events'])

    def liquidity(actor, target):
        b = bank(actor)
        return _money(b['reserves'], 'reserves') if actors[actor]['bank'] != actors[target]['bank'] else 100000000000000

    metrics = {}
    opening_goods = sum(f['inventory'] for f in firms.values())
    destroyed = 0
    for fid in sorted(firms):
        firm, p = firms[fid], actions[fid]
        loss = _count(losses.get(fid, 0), 'inventory_loss')
        if loss > firm['inventory']: raise ValueError('Inventory loss exceeds available goods')
        firm['inventory'] -= loss
        destroyed += loss
        m = metrics[fid] = dict(produced=0, sold=0, revenue=0, cost=0, profit=0)
        if p['bankrupt']:
            principal = _money(bank(fid)['loans'].get(fid, 0), 'loan')
            transact('default', fid, principal)
            defaulted += principal
            firm['status'] = 'bankrupt'
            events.append({'kind': 'firm_bankrupt', 'firm': fid, 'written_off': _units(principal)})
        if firm['status'] == 'bankrupt': continue
        b = bank(fid)
        cash, debt = _money(b['accounts'][fid], 'deposit'), _money(b['loans'].get(fid, 0), 'loan')
        credit = max(0, p['credit_limit'] - debt) if _money(b['equity'], 'equity', signed=True) > 0 else 0
        unit_cost = _money(firm['unit_cost'], 'unit_cost')
        produced = min(p['production'], firm['capacity'], (cash + credit) // unit_cost, liquidity(fid, firm['worker']) // unit_cost)
        cost = produced * unit_cost
        loan = max(0, cost - cash)
        transact('originate', fid, loan)
        created += loan
        transact('transfer', fid, cost, firm['worker'])
        firm['inventory'] += produced
        if firm['inventory'] > 1000000: raise ValueError('Inventory exceeds bounded range')
        m.update(produced=produced, cost=cost)
        if produced < p['production']:
            events.append({'kind': 'production_rationed', 'firm': fid, 'requested': p['production'], 'produced': produced, 'fallback': 'reduce_to_funded_settleable_capacity'})
    for hid in sorted(households):
        for fid, requested in sorted(policy.get('households', {}).get(hid, {}).get('purchases', {}).items()):
            price = actions[fid]['price']
            available = firms[fid]['inventory'] if firms[fid]['status'] == 'active' else 0
            bought = min(requested, available, _money(bank(hid)['accounts'][hid], 'deposit') // price, liquidity(hid, fid) // price)
            transact('transfer', hid, bought * price, fid)
            firms[fid]['inventory'] -= bought
            metrics[fid]['sold'] += bought
            metrics[fid]['revenue'] += bought * price
            if bought < requested: events.append({'kind': 'purchase_rationed', 'household': hid, 'firm': fid, 'requested': requested, 'bought': bought})
    for fid in sorted(firms):
        amount = min(actions[fid]['repay'], _money(bank(fid)['accounts'][fid], 'deposit'), _money(bank(fid)['loans'].get(fid, 0), 'loan'))
        transact('repay', fid, amount)
        repaid += amount
    final_goods = sum(f['inventory'] for f in firms.values())
    if opening_goods + sum(m['produced'] - m['sold'] for m in metrics.values()) - destroyed != final_goods:
        raise ValueError('Goods conservation failed')
    def totals(bs, key): return sum(_money(v, key) for b in bs for v in b[key].values())
    if totals(out['banks'], 'accounts') - totals(state['banks'], 'accounts') != created - repaid:
        raise ValueError('Deposit creation/extinction mismatch')
    if totals(out['banks'], 'loans') - totals(state['banks'], 'loans') != created - repaid - defaulted:
        raise ValueError('Debt creation/extinction mismatch')
    for m in metrics.values():
        m['profit'] = m['revenue'] - m['cost']
        for k in ('cost', 'revenue', 'profit'): m[k] = _units(m[k])
    record = {'step': out['step'] + 1, 'policy': deepcopy(policy), 'shock': deepcopy(shock), 'firms': metrics, 'work': work,
              'events': events, 'journal': journal, 'accounting': {'balanced': True, 'reserve_change': 0,
              'loan_created': _units(created), 'loan_repaid': _units(repaid), 'loan_defaulted': _units(defaulted),
              'goods_opening': opening_goods, 'goods_destroyed': destroyed, 'goods_closing': final_goods}}
    out['history'].append(record)
    out['step'] += 1
    _validate(out)
    return out


def simulate_coupled_economy(config):
    """Bounded convenience runner, with policy and exogenous shock sequences."""
    _object(config, ('initial_state', 'policies', 'shocks'), 'coupled simulation')
    policies = config.get('policies')
    if not isinstance(policies, list) or len(policies) > 1000: raise ValueError('policies must contain at most 1000 steps')
    shocks = config.get('shocks', [{} for _ in policies])
    if not isinstance(shocks, list) or len(shocks) != len(policies): raise ValueError('shocks must align with policies')
    state = initialize_economy(config['initial_state'])
    _work_budget(state, policies)
    for policy, shock in zip(policies, shocks): state = step_economy(state, policy, shock)
    return state


def _predict(inputs, parameters, context):
    if context['dt_seconds'] != 86400: raise ValueError('Coupled economy requires daily cadence')
    state = step_economy(inputs['economy_state']['value'], inputs['economy_policy']['value'])
    record = state['history'][-1]
    return {'pressures': [{'port': 'economy_state', 'mode': 'set', 'value': state, 'unit': None, 'strength': 1, 'confidence': 1}],
            'events': record['events'], 'diagnostics': {'accounting': record['accounting'], 'journal': record['journal'],
            'epistemic_status': 'synthetic_scenario', 'calibrated': False}}


def register_coupled_economy_processes(registry):
    port = {'type': 'object', 'unit': None}
    registry.register_process({'id': 'coupled_economy', 'inputs': {'economy_state': port, 'economy_policy': port},
        'outputs': {'economy_state': port}, 'topology': 'Firms employ households; all actors settle at explicit commercial banks.',
        'description': 'Incremental production, consumption and endogenous bank credit under explicit per-step policy.',
        'illustrative': True, 'validated': False})
    registry.register_implementation({'id': 'coupled_economy.deterministic', 'process_id': 'coupled_economy',
        'fidelity': 'deterministic', 'min_step_seconds': 86400, 'max_step_seconds': 86400, 'cost_per_call': 1,
        'output_timing': 'end_of_step', 'description': 'Exact-cent closed settlement with deterministic credit and liquidity rationing.'}, _predict)
    return registry


def schema():
    return {'entity_types': {}, 'relations': {}, 'variables': {
        'economy_state': {'type': 'object', 'unit': None, 'domain': 'entity'},
        'economy_policy': {'type': 'object', 'unit': None, 'domain': 'entity'}}}
