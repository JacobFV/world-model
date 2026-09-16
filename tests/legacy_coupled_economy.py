# Frozen pre-scale reference (2026-09-15) used only by tests to prove the rewrite preserves semantics.
"""Incremental, closed synthetic economy with commercial-bank settlement.

Policy is a per-step action, never a rewritten simulation configuration. All
monetary state lives exclusively in the bank ledger. Production converts paid
labor into indivisible goods; household purchases consume goods immediately.
Reported profit remains revenue less this step's production cash outlay.
Optional mechanisms add labor limits, deposit-paid interest, bounded policy-rate
feedback, weighted-average inventory costs, lagged price/demand response, and
funded collateral recovery. See docs/economy-mechanisms.md for equations, timing
and limitations. None of these mechanisms asserts empirical calibration or
causal validity; absent options preserve the original synthetic behavior.

Work is conservatively preflighted across the entire retained policy history:
at most 10000 reserved transactions and 1000000 transaction × ledger-balance
cells. Every purchase entry reserves a slot, even if zero or later rationed;
production reserves origination and wage-payment slots. These bounds constrain
the household/firm cross-product and retained journals, not just actor count.
"""
from copy import deepcopy
from worldmodel.banking import _money, _units, simulate_banking
from worldmodel import economy_mechanisms as mechanisms


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
        _object(policy, ('firms', 'households', 'policy_rate'), 'policy')
        slots = 2 * len(firms) if 'interest' in state.get('mechanisms', {}) else 0
        if 'policy_rate' in policy:
            mechanisms.number(policy['policy_rate'], 'policy_rate')
            if 'interest' not in state.get('mechanisms', {}): raise ValueError('policy_rate requires interest mechanism')
        for name, owners in (('firms', firms), ('households', households)):
            mapping = policy.get(name, {})
            if not isinstance(mapping, dict) or set(mapping) - owners:
                raise ValueError('Policy references unknown actor')
        for action in policy.get('firms', {}).values():
            _object(action, ('production', 'price', 'credit_limit', 'repay', 'bankrupt', 'collateral_sale'), 'firm action')
            bankrupt = action.get('bankrupt', False)
            if not isinstance(bankrupt, bool):
                raise ValueError('bankrupt must be boolean')
            slots += 2 * (_count(action.get('production', 0), 'production') > 0)
            slots += (_money(action.get('repay', 0), 'repay') > 0) + bankrupt
            if 'collateral_sale' in action:
                if not bankrupt: raise ValueError('collateral_sale requires declared bankruptcy')
                sale = action['collateral_sale']
                _object(sale, ('buyer', 'units', 'unit_price'), 'collateral sale')
                if set(sale) != {'buyer', 'units', 'unit_price'} or not isinstance(sale['buyer'], str) or sale['buyer'] not in households:
                    raise ValueError('Collateral sale requires a known household buyer and explicit terms')
                _count(sale['units'], 'collateral units')
                if _money(sale['unit_price'], 'collateral price') <= 0: raise ValueError('Collateral price must be positive')
                slots += 2
        for action in policy.get('households', {}).values():
            _object(action, ('purchases', 'labor_capacity'), 'household action')
            if 'labor_capacity' in action: _count(action['labor_capacity'], 'labor_capacity')
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
    _object(state, ('banks', 'firms', 'households', 'step', 'history', 'initial_reserves', 'mechanisms', 'expectations'), 'economy state')
    if not {'banks', 'firms', 'households', 'step', 'history', 'initial_reserves'} <= set(state):
        raise ValueError('Incomplete economy state')
    if type(state['step']) is not int or not isinstance(state['history'], list) or state['step'] != len(state['history']) or not 0 <= state['step'] <= 1000:
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
            _object(actor, ('id', 'bank', 'labor_capacity') if kind == 'households' else ('id', 'bank', 'worker', 'inventory', 'capacity', 'unit_cost', 'status', 'labor_per_unit', 'inventory_value', 'interest_arrears', 'last_price', 'last_unmet_demand'), kind)
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
    mechanisms.validate_fields(state)
    _work_budget(state)
    return ledger['banks']


def initialize_economy(config):
    """Create a persistent state from banks, firms and households endowments."""
    _object(config, ('banks', 'firms', 'households', 'mechanisms'), 'initial economy')
    if not {'banks', 'firms', 'households'} <= set(config):
        raise ValueError('Initial economy requires banks, firms and households')
    state = deepcopy(config)
    for firm in state['firms']:
        firm.setdefault('status', 'active')
    state.update(step=0, history=[], initial_reserves=_units(sum(_money(b['reserves'], 'reserves') for b in state['banks'])))
    mechanisms.initialize_fields(state)
    state['banks'] = _validate(state)
    return state


def step_economy(state, policy, shock=None):
    """Advance one day atomically; see docs/economy-mechanisms.md for options."""
    banks_list = _validate(state)
    if state['step'] >= 1000: raise ValueError('Economy horizon is bounded to 1000 steps')
    _object(policy, ('firms', 'households', 'policy_rate'), 'policy')
    work = _work_budget(state, [policy])
    shock = {} if shock is None else shock
    _object(shock, ('inventory_loss', 'unit_cost', 'inflation', 'output_gap', 'expected_energy_change', 'expected_rate_change'), 'shock')
    out = deepcopy(state); out['banks'] = banks_list
    options = out.get('mechanisms', {})
    actors = {x['id']: x for x in out['firms'] + out['households']}
    firms = {x['id']: x for x in out['firms']}
    households = {x['id']: x for x in out['households']}
    for key in ('inventory_loss', 'unit_cost'):
        mapping = shock.get(key, {})
        if not isinstance(mapping, dict) or set(mapping) - set(firms): raise ValueError('Shock references unknown firm')
        for fid, value in mapping.items():
            if key == 'inventory_loss':
                _count(value, key)
                if value > firms[fid]['inventory']: raise ValueError('Inventory loss exceeds available goods')
            elif _money(value, key) <= 0: raise ValueError('unit_cost shock must be positive cents')
    for key in ('inflation', 'output_gap', 'expected_energy_change', 'expected_rate_change'):
        if key in shock: mechanisms.number(shock[key], key, -1, 1)
    if any(k in shock for k in ('expected_energy_change', 'expected_rate_change')) and 'demand_feedback' not in options:
        raise ValueError('Expectation shocks require demand_feedback')
    rate_audit = mechanisms.apply_rate(options, policy, shock)
    actions = {}
    for fid, firm in firms.items():
        p = policy.get('firms', {}).get(fid, {})
        actions[fid] = dict(production=_count(p.get('production', 0), 'production'),
                            price=mechanisms.price_for(firm, p, options),
                            credit_limit=_money(p.get('credit_limit', 0), 'credit_limit'),
                            repay=_money(p.get('repay', 0), 'repay'), bankrupt=p.get('bankrupt', False))
        if actions[fid]['price'] <= 0: raise ValueError('price must be positive')
        if 'collateral_sale' in p:
            sale = p['collateral_sale']
            if sale['units'] > firm['inventory'] - shock.get('inventory_loss', {}).get(fid, 0):
                raise ValueError('Collateral sale units exceed available inventory')
            if firm['status'] == 'bankrupt' and sale['units']: raise ValueError('Previously bankrupt firm cannot sell collateral again')
            actions[fid]['collateral_sale'] = sale
    for hid in households:
        p = policy.get('households', {}).get(hid, {})
        for value in p.get('purchases', {}).values(): _count(value, 'purchases')
        if 'labor_capacity' in p: households[hid]['labor_capacity'] = _count(p['labor_capacity'], 'labor_capacity')
    labor_enabled = any('labor_capacity' in h for h in households.values()) or any('labor_per_unit' in f for f in firms.values())
    labor = {hid: {'capacity': h.get('labor_capacity'), 'requested': 0, 'allocated': 0, 'unmet': 0} for hid, h in households.items()}
    events, journal = [], []
    created = repaid = defaulted = paid_interest = 0
    opening_principal = {f['id']: _money(next(b for b in state['banks'] if b['id'] == f['bank'])['loans'].get(f['id'], 0), 'principal') for f in state['firms']}

    def bank(actor): return next(b for b in out['banks'] if b['id'] == actors[actor]['bank'])

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
        return _money(bank(actor)['reserves'], 'reserves') if actors[actor]['bank'] != actors[target]['bank'] else 100000000000000

    metrics = {fid: dict(produced=0, sold=0, revenue=0, cost=0, profit=0) for fid in firms}
    cogs = {fid: 0 for fid in firms}; write_down = {fid: 0 for fid in firms}
    interest_cost = {fid: 0 for fid in firms}; interest_payments = {fid: 0 for fid in firms}
    unmet_demand = {fid: 0 for fid in firms}; recoveries = {}
    opening_goods = sum(f['inventory'] for f in firms.values())
    opening_value = sum(_money(f.get('inventory_value', 0), 'inventory_value') for f in firms.values())
    destroyed = 0

    def bankrupt(fid, sale=None, cause='declared'):
        nonlocal defaulted, repaid
        firm = firms[fid]
        if firm['status'] == 'bankrupt': return
        recovered = proceeds = sold = 0
        if sale is not None:
            buyer = sale['buyer']; price = _money(sale['unit_price'], 'unit_price')
            sold = min(sale['units'], _money(bank(buyer)['accounts'][buyer], 'deposit') // price, liquidity(buyer, fid) // price)
            proceeds = sold * price
            transact('transfer', buyer, proceeds, fid)
            cogs[fid] += mechanisms.inventory_remove(firm, sold)
            metrics[fid]['sold'] += sold; metrics[fid]['revenue'] += proceeds
            recovered = min(proceeds, _money(bank(fid)['loans'].get(fid, 0), 'loan'))
            transact('repay', fid, recovered); repaid += recovered
            recoveries[fid] = recovered
            events.append({'kind': 'collateral_sale', 'firm': fid, 'buyer': buyer, 'requested_units': sale['units'],
                           'sold_units': sold, 'proceeds': _units(proceeds), 'principal_recovered': _units(recovered),
                           'rationed': sold < sale['units'], 'book_cost': _units(cogs[fid]) if 'inventory_value' in firm else None})
        principal = _money(bank(fid)['loans'].get(fid, 0), 'loan')
        transact('default', fid, principal); defaulted += principal
        firm['status'] = 'bankrupt'
        events.append({'kind': 'firm_bankrupt', 'firm': fid, 'written_off': _units(principal), 'cause': cause})

    for fid in sorted(firms):
        firm, p = firms[fid], actions[fid]
        loss = shock.get('inventory_loss', {}).get(fid, 0)
        write_down[fid] += mechanisms.inventory_remove(firm, loss); destroyed += loss
        if fid in shock.get('unit_cost', {}): firm['unit_cost'] = shock['unit_cost'][fid]
        if p['bankrupt']: bankrupt(fid, p.get('collateral_sale'))
        if firm['status'] == 'bankrupt': continue
        b = bank(fid)
        cash, debt = _money(b['accounts'][fid], 'deposit'), _money(b['loans'].get(fid, 0), 'loan')
        credit = max(0, p['credit_limit'] - debt) if _money(b['equity'], 'equity', signed=True) > 0 else 0
        unit_cost = _money(firm['unit_cost'], 'unit_cost')
        worker = labor[firm['worker']]; per_unit = firm.get('labor_per_unit', 1)
        worker['requested'] += p['production'] * per_unit
        available_labor = (worker['capacity'] - worker['allocated']) // per_unit if worker['capacity'] is not None else 1000000
        produced = min(p['production'], firm['capacity'], (cash + credit) // unit_cost,
                       liquidity(fid, firm['worker']) // unit_cost, available_labor)
        cost = produced * unit_cost; loan = max(0, cost - cash)
        transact('originate', fid, loan); created += loan
        transact('transfer', fid, cost, firm['worker'])
        worker['allocated'] += produced * per_unit
        firm['inventory'] += produced
        if firm['inventory'] > 1000000: raise ValueError('Inventory exceeds bounded range')
        if 'inventory_value' in firm: firm['inventory_value'] = _units(_money(firm['inventory_value'], 'inventory_value') + cost)
        metrics[fid].update(produced=produced, cost=cost)
        if produced < p['production']:
            events.append({'kind': 'production_rationed', 'firm': fid, 'requested': p['production'], 'produced': produced,
                           'fallback': 'reduce_to_funded_settleable_labor_and_capacity' if labor_enabled else 'reduce_to_funded_settleable_capacity'})
    for hid in sorted(households):
        for fid, requested in sorted(policy.get('households', {}).get(hid, {}).get('purchases', {}).items()):
            price = actions[fid]['price']
            demand = mechanisms.demand_units(requested, price, options, state.get('expectations', {}))
            available = firms[fid]['inventory'] if firms[fid]['status'] == 'active' else 0
            bought = min(demand, available, _money(bank(hid)['accounts'][hid], 'deposit') // price, liquidity(hid, fid) // price)
            transact('transfer', hid, bought * price, fid)
            cogs[fid] += mechanisms.inventory_remove(firms[fid], bought)
            metrics[fid]['sold'] += bought; metrics[fid]['revenue'] += bought * price
            unmet_demand[fid] += demand - bought
            if bought < demand:
                events.append({'kind': 'purchase_rationed', 'household': hid, 'firm': fid, 'requested': demand, 'bought': bought})
            if 'demand_feedback' in options:
                events.append({'kind': 'demand_feedback', 'household': hid, 'firm': fid, 'base_units': requested,
                               'demand_units': demand, 'price': _units(price), 'lagged_expectations': deepcopy(state['expectations'])})
    for fid in sorted(firms):
        firm = firms[fid]
        if 'interest' in options and firm['status'] == 'active':
            interest_cost[fid] = mechanisms.interest_due(opening_principal[fid], options['interest'])
            due = interest_cost[fid] + _money(firm['interest_arrears'], 'interest_arrears')
            paid = min(due, _money(bank(fid)['accounts'][fid], 'deposit'))
            transact('interest', fid, paid); paid_interest += paid; interest_payments[fid] = paid
            firm['interest_arrears'] = _units(due - paid)
            if due > paid:
                events.append({'kind': 'interest_unpaid', 'firm': fid, 'amount': _units(due - paid), 'treatment': 'memorandum_arrears'})
                if options['interest']['insufficient'] == 'bankrupt': bankrupt(fid, cause='unpaid_interest')
        amount = min(actions[fid]['repay'], _money(bank(fid)['accounts'][fid], 'deposit'), _money(bank(fid)['loans'].get(fid, 0), 'loan'))
        transact('repay', fid, amount); repaid += amount
    final_goods = sum(f['inventory'] for f in firms.values())
    if opening_goods + sum(m['produced'] - m['sold'] for m in metrics.values()) - destroyed != final_goods:
        raise ValueError('Goods conservation failed')
    def totals(bs, key): return sum(_money(v, key) for b in bs for v in b[key].values())
    if totals(out['banks'], 'accounts') - totals(state['banks'], 'accounts') != created - repaid - paid_interest:
        raise ValueError('Deposit creation/extinction mismatch')
    if totals(out['banks'], 'loans') - totals(state['banks'], 'loans') != created - repaid - defaulted:
        raise ValueError('Debt creation/extinction mismatch')
    equity_change = sum(_money(b['equity'], 'equity', signed=True) for b in out['banks']) - sum(_money(b['equity'], 'equity', signed=True) for b in state['banks'])
    if equity_change != paid_interest - defaulted: raise ValueError('Equity income/loss mismatch')
    if options.get('inventory_valuation'):
        closing_value = sum(_money(f['inventory_value'], 'inventory_value') for f in firms.values())
        if opening_value + sum(m['cost'] for m in metrics.values()) - sum(cogs.values()) - sum(write_down.values()) != closing_value:
            raise ValueError('Inventory cost conservation failed')
    for fid, m in metrics.items():
        m['profit'] = m['revenue'] - m['cost']
        if options.get('inventory_valuation'):
            m.update(cost_of_goods_sold=_units(cogs[fid]), inventory_write_down=_units(write_down[fid]),
                     operating_profit=_units(m['revenue'] - cogs[fid] - write_down[fid]))
        if 'interest' in options:
            m.update(interest_due=_units(interest_cost[fid]), interest_paid=_units(interest_payments[fid]),
                     cash_surplus_after_interest=_units(m['profit'] - interest_payments[fid]))
        if fid in recoveries: m['collateral_recovered'] = _units(recoveries[fid])
        for k in ('cost', 'revenue', 'profit'): m[k] = _units(m[k])
        if 'price_feedback' in options:
            firms[fid]['last_price'] = _units(actions[fid]['price'])
            firms[fid]['last_unmet_demand'] = min(1000000, unmet_demand[fid])
    record = {'step': out['step'] + 1, 'policy': deepcopy(policy), 'shock': deepcopy(shock), 'firms': metrics, 'work': work,
              'events': events, 'journal': journal, 'accounting': {'balanced': True, 'reserve_change': 0,
              'loan_created': _units(created), 'loan_repaid': _units(repaid), 'loan_defaulted': _units(defaulted),
              'goods_opening': opening_goods, 'goods_destroyed': destroyed, 'goods_closing': final_goods}}
    if 'interest' in options:
        record['accounting']['interest_paid'] = _units(paid_interest); record['monetary_policy'] = rate_audit
    if options.get('inventory_valuation'):
        record['accounting'].update(inventory_cost_opening=_units(opening_value), inventory_cost_closing=_units(closing_value))
    if labor_enabled:
        for worker in labor.values(): worker['unmet'] = worker['requested'] - worker['allocated']
        record['labor'] = labor
    if 'price_feedback' in options or 'demand_feedback' in options:
        record['prices'] = {fid: _units(p['price']) for fid, p in actions.items()}
    if 'demand_feedback' in options:
        for source, target in [('expected_energy_change', 'energy_change'), ('expected_rate_change', 'rate_change')]:
            if source in shock: out['expectations'][target] = shock[source]
    out['history'].append(record); out['step'] += 1
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
