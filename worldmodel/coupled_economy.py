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

Work is preflighted with named limits (worldmodel.limits): actors, horizon,
reserved transactions per step, ledger cells and, for retained full history,
journal postings and per-firm history rows. Every purchase entry reserves a slot,
even if zero or later rationed; production reserves origination and wage-payment
slots. Transactions update one integer-cent ledger in place (linear work per
step). ``history_policy`` selects full records (default), summaries for every
step, or full records every N steps with summaries in between.
"""
from collections import Counter
from copy import deepcopy
from .banking import _money, _units, _check, _export, apply_transaction, parse_banks, simulate_banking  # noqa: F401
from .limits import LimitExceeded, resolve_limits
from . import economy_mechanisms as mechanisms

HISTORY_MODES = ('full', 'every_n', 'summary')
_MONEY_METRICS = ('revenue', 'cost', 'profit', 'cost_of_goods_sold', 'inventory_write_down', 'operating_profit',
                  'interest_due', 'interest_paid', 'cash_surplus_after_interest', 'collateral_recovered')


def _object(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'Unknown or invalid {name} fields')


def _count(value, name, limits=None):
    maximum = resolve_limits(limits).economy_max_quantity
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be an integer between zero and {maximum}')
    if value > maximum:
        raise LimitExceeded('economy_max_quantity', value, maximum, f'{name} must be an integer between zero and {maximum}')
    return value


def history_policy(value):
    """Normalize 'full'|'summary'|'every_n' or {'mode','every'} into a policy dict."""
    if value is None:
        return {'mode': 'full'}
    if isinstance(value, str):
        value = {'mode': value}
    if not isinstance(value, dict) or set(value) - {'mode', 'every'} or value.get('mode') not in HISTORY_MODES:
        raise ValueError("history_policy mode must be 'full', 'every_n' or 'summary'")
    if value['mode'] == 'every_n':
        every = value.get('every')
        if type(every) is not int or every < 1:
            raise ValueError('every_n history requires a positive integer every')
        return {'mode': 'every_n', 'every': every}
    if 'every' in value:
        raise ValueError('every is only valid for every_n history')
    return {'mode': value['mode']}


def _retains_full(policy, step):
    return policy['mode'] == 'full' or (policy['mode'] == 'every_n' and step % policy['every'] == 0)


def summarize_record(record):
    """Compact exact step summary: totals in cents-exact units, event counts, accounting."""
    if record.get('summary'):
        return record
    totals = {}
    for metrics in record['firms'].values():
        for key, value in metrics.items():
            totals[key] = totals.get(key, 0) + (round(value * 100) if key in _MONEY_METRICS else value)
    summary = {'step': record['step'], 'summary': True, 'accounting': deepcopy(record['accounting']), 'work': deepcopy(record['work']),
               'totals': {key: (_units(value) if key in _MONEY_METRICS else value) for key, value in sorted(totals.items())},
               'event_counts': dict(sorted(Counter(event['kind'] for event in record['events']).items()))}
    if 'labor' in record:
        summary['labor'] = {key: sum(worker[key] for worker in record['labor'].values()) for key in ('requested', 'allocated', 'unmet')}
    for key in ('monetary_policy', 'default_hazard', 'deposit_growth', 'credit_growth'):
        if key in record:
            summary[key] = deepcopy(record[key])
    return summary


def mechanism_slots(options, firms, households):
    """Per-step reserved transactions of optional mechanisms: interest payment/default per firm,
    deposit interest per account and a hazard write-off per firm."""
    return ((2 * firms if 'interest' in options else 0) + (firms + households if 'deposit_interest' in options else 0)
            + (firms if 'default_hazard' in options else 0))


def _work_budget(state, policies=(), limits=None):
    """Bound per-step transactions, ledger size and retained journals before any transactions.

    Recompute from retained policies rather than trusting a mutable accumulated counter.
    Each firm's possible loan is included in the ledger size even before it is
    originated, so future loan creation cannot invalidate the estimate. Summary
    history records retain no journal and reserve no retained postings.
    """
    limits = resolve_limits(limits)
    firms = {firm['id'] for firm in state['firms']}
    households = {household['id'] for household in state['households']}
    balance_cells = 3 * len(state['banks']) + 2 * len(firms) + len(households)
    limits.check('coupled_max_ledger_cells', balance_cells, 'Economy work budget exceeded: ledger balance cells')
    options = state.get('mechanisms', {})
    interest_slots = mechanism_slots(options, len(firms), len(households))
    policy_mode = history_policy(state.get('history_policy'))
    transactions = postings = 0

    def reserve(policy, journal=()):
        _object(policy, ('firms', 'households', 'policy_rate'), 'policy')
        slots = interest_slots
        if 'policy_rate' in policy:
            mechanisms.number(policy['policy_rate'], 'policy_rate')
            if 'interest' not in state.get('mechanisms', {}): raise ValueError('policy_rate requires interest mechanism')
        for name, owners in (('firms', firms), ('households', households)):
            mapping = policy.get(name, {})
            if not isinstance(mapping, dict) or not set(mapping) <= owners:
                raise ValueError('Policy references unknown actor')
        for action in policy.get('firms', {}).values():
            _object(action, ('production', 'price', 'credit_limit', 'repay', 'bankrupt', 'collateral_sale'), 'firm action')
            bankrupt = action.get('bankrupt', False)
            if not isinstance(bankrupt, bool):
                raise ValueError('bankrupt must be boolean')
            slots += 2 * (_count(action.get('production', 0), 'production', limits) > 0)
            slots += (_money(action.get('repay', 0), 'repay') > 0) + bankrupt
            if 'collateral_sale' in action:
                if not bankrupt: raise ValueError('collateral_sale requires declared bankruptcy')
                sale = action['collateral_sale']
                _object(sale, ('buyer', 'units', 'unit_price'), 'collateral sale')
                if set(sale) != {'buyer', 'units', 'unit_price'} or not isinstance(sale['buyer'], str) or sale['buyer'] not in households:
                    raise ValueError('Collateral sale requires a known household buyer and explicit terms')
                _count(sale['units'], 'collateral units', limits)
                if _money(sale['unit_price'], 'collateral price') <= 0: raise ValueError('Collateral price must be positive')
                slots += 2
        for action in policy.get('households', {}).values():
            _object(action, ('purchases', 'labor_capacity'), 'household action')
            if 'labor_capacity' in action: _count(action['labor_capacity'], 'labor_capacity', limits)
            purchases = action.get('purchases', {})
            if not isinstance(purchases, dict) or not set(purchases) <= firms:
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
        limits.check('coupled_max_step_transactions', max(slots, len(journal)),
                     'Economy work budget exceeded: reserved transactions in one step')
        return max(slots, len(journal)), max(4 * slots, actual_postings)

    for record in state['history']:
        if not isinstance(record, dict):
            raise ValueError('Economy history requires recorded policies')
        if record.get('summary') is True:
            if not isinstance(record.get('step'), int) or not isinstance(record.get('accounting'), dict):
                raise ValueError('Summary history records require step and accounting')
            continue
        if 'policy' not in record:
            raise ValueError('Economy history requires recorded policies')
        slots, reserved = reserve(record['policy'], record.get('journal', []))
        transactions += slots; postings += reserved
    step_transactions = 0
    for offset, policy in enumerate(policies, 1):
        slots, reserved = reserve(policy)
        step_transactions = slots
        if _retains_full(policy_mode, state['step'] + offset):
            transactions += slots; postings += reserved
    limits.check('coupled_max_retained_postings', postings, 'Economy work budget exceeded: retained journal postings')
    return {'reserved_transactions': transactions, 'reserved_postings': postings, 'step_transactions': step_transactions,
            'ledger_balance_cells': balance_cells, 'max_step_transactions': limits.coupled_max_step_transactions,
            'max_retained_postings': limits.coupled_max_retained_postings, 'max_ledger_cells': limits.coupled_max_ledger_cells}


def _validate(state, limits=None, budget=True):
    """Validate a state and return its integer-cent ledger (never aliases state)."""
    limits = resolve_limits(limits)
    _object(state, ('banks', 'firms', 'households', 'step', 'history', 'initial_reserves', 'mechanisms', 'expectations', 'history_policy', 'calibration'), 'economy state')
    if 'calibration' in state and (not isinstance(state['calibration'], dict) or state['calibration'].get('schema') != 'worldmodel.parameter_bindings/1'):
        raise ValueError('Economy calibration must be a worldmodel.parameter_bindings/1 record')
    if not {'banks', 'firms', 'households', 'step', 'history', 'initial_reserves'} <= set(state):
        raise ValueError('Incomplete economy state')
    if type(state['step']) is not int or not isinstance(state['history'], list) or state['step'] != len(state['history']) or state['step'] < 0:
        raise ValueError('Invalid bounded step/history')
    limits.check('coupled_max_steps', state['step'], 'Invalid bounded step/history: economy horizon')
    policy = history_policy(state.get('history_policy'))
    for name, key in (('firms', 'coupled_max_firms'), ('households', 'coupled_max_households')):
        if not isinstance(state[name], list) or not state[name]:
            raise ValueError(f'{name} requires at least one actor')
        limits.check(key, len(state[name]), f'{name} requires a bounded number of actors')
    full_records = sum(not (isinstance(r, dict) and r.get('summary') is True) for r in state['history'])
    if policy['mode'] != 'summary' or full_records:
        limits.check('coupled_max_history_actor_steps', (full_records + 1) * (len(state['firms']) + len(state['households'])),
                     'Economy history budget exceeded: retained full history rows')
    banks = parse_banks(state['banks'], limits)
    if _money(state['initial_reserves'], 'initial_reserves') != sum(b['reserves'] for b in banks.values()):
        raise ValueError('Aggregate reserves changed from opening anchor')
    _check(banks, sum(b['reserves'] for b in banks.values()))
    household_ids = {h.get('id') for h in state['households'] if isinstance(h, dict)}
    actors = {}
    for kind in ('households', 'firms'):
        for actor in state[kind]:
            _object(actor, ('id', 'bank', 'labor_capacity', 'labor_output') if kind == 'households' else ('id', 'bank', 'worker', 'inventory', 'capacity', 'unit_cost', 'status', 'labor_per_unit', 'inventory_value', 'interest_arrears', 'last_price', 'last_unmet_demand', 'last_unit_cost'), kind)
            aid = actor.get('id')
            if not isinstance(aid, str) or not aid or aid in actors or actor.get('bank') not in banks:
                raise ValueError('Actor requires unique ID and known bank')
            if aid not in banks[actor['bank']]['accounts']:
                raise ValueError('Actor requires an opening bank account')
            actors[aid] = actor
            if kind == 'firms':
                if actor.get('worker') not in household_ids:
                    raise ValueError('Firm worker must be a household')
                for field in ('inventory', 'capacity'): _count(actor.get(field), field, limits)
                if _money(actor.get('unit_cost'), 'unit_cost') <= 0:
                    raise ValueError('unit_cost must be positive')
                if actor.get('status') not in ('active', 'bankrupt'):
                    raise ValueError('Invalid firm status')
    firm_ids = {f['id'] for f in state['firms']}
    for bank in banks.values():
        for owner in set(bank['accounts']) | set(bank['loans']):
            if owner not in actors or actors[owner]['bank'] != bank['id']:
                raise ValueError('Every account and loan must belong to its actor at its bank')
        if not set(bank['loans']) <= firm_ids:
            raise ValueError('Only firms borrow in this economy')
    mechanisms.validate_fields(state, limits)
    if budget:
        _work_budget(state, limits=limits)
    return banks


def initialize_economy(config, *, history=None, limits=None, calibration=None):
    """Create a persistent state from banks, firms and households endowments.

    ``history`` (or config ``history_policy``) is 'full' (default), 'summary' or
    {'mode': 'every_n', 'every': N}. Non-full policies are stored in the state.
    ``calibration`` (Estimate, estimate dict, calibration record, registry
    ``calibrated_parameters`` mapping, or a list) binds estimated
    ``mechanisms.*`` parameters into configured mechanisms; the bindings and their
    record ids are kept in ``state['calibration']``.
    """
    limits = resolve_limits(limits)
    _object(config, ('banks', 'firms', 'households', 'mechanisms', 'history_policy'), 'initial economy')
    if not {'banks', 'firms', 'households'} <= set(config):
        raise ValueError('Initial economy requires banks, firms and households')
    policy = history_policy(history if history is not None else config.get('history_policy'))
    state = deepcopy(config)
    state.pop('history_policy', None)
    if policy['mode'] != 'full':
        state['history_policy'] = policy
    for firm in state['firms']:
        if isinstance(firm, dict): firm.setdefault('status', 'active')
    if not isinstance(state['banks'], list) or any(not isinstance(b, dict) or 'reserves' not in b for b in state['banks']):
        raise ValueError('Initial economy banks require reserves')
    state.update(step=0, history=[], initial_reserves=_units(sum(_money(b['reserves'], 'reserves') for b in state['banks'])))
    if calibration is not None:
        state['mechanisms'], state['calibration'] = mechanisms.calibrate_mechanisms(state.get('mechanisms', {}), calibration)
    mechanisms.initialize_fields(state)
    state['banks'] = _export(_validate(state, limits))
    return state


def calibrate_state(state, calibration):
    """Return a state whose configured mechanisms use estimated parameters (existing bindings are merged)."""
    out = dict(state)
    out['mechanisms'], out['calibration'] = mechanisms.calibrate_mechanisms(state.get('mechanisms', {}), calibration, state.get('calibration'))
    out['firms'] = [dict(firm) for firm in state['firms']]
    out['households'] = [dict(household) for household in state['households']]
    mechanisms.initialize_fields(out)
    _validate(out, budget=False)
    return out


def parameter_provenance(state):
    """Per mechanism parameter: estimated (component, estimate_id, record_id) or assumed."""
    return mechanisms.parameter_provenance(state)


def step_economy(state, policy, shock=None, *, limits=None):
    """Advance one day atomically; see docs/economy-mechanisms.md for options."""
    limits = resolve_limits(limits)
    banks = _validate(state, limits, budget=False)
    if state['step'] >= limits.coupled_max_steps:
        raise LimitExceeded('coupled_max_steps', state['step'] + 1, limits.coupled_max_steps, 'Economy horizon')
    _object(policy, ('firms', 'households', 'policy_rate'), 'policy')
    work = _work_budget(state, [policy], limits)
    shock = {} if shock is None else shock
    _object(shock, ('inventory_loss', 'unit_cost', 'inflation', 'output_gap', 'expected_energy_change', 'expected_rate_change', 'unemployment'), 'shock')
    record_policy = history_policy(state.get('history_policy'))
    full_record = _retains_full(record_policy, state['step'] + 1)
    out = dict(state)
    out['firms'] = [dict(firm) for firm in state['firms']]
    out['households'] = [dict(household) for household in state['households']]
    out['history'] = list(state['history'])
    if 'mechanisms' in state: out['mechanisms'] = deepcopy(state['mechanisms'])
    if 'expectations' in state: out['expectations'] = dict(state['expectations'])
    if 'history_policy' in state: out['history_policy'] = dict(state['history_policy'])
    options = out.get('mechanisms', {})
    actors = {x['id']: x for x in out['firms'] + out['households']}
    actor_bank = {aid: actor['bank'] for aid, actor in actors.items()}
    firms = {x['id']: x for x in out['firms']}
    households = {x['id']: x for x in out['households']}
    for key in ('inventory_loss', 'unit_cost'):
        mapping = shock.get(key, {})
        if not isinstance(mapping, dict) or not set(mapping) <= set(firms): raise ValueError('Shock references unknown firm')
        for fid, value in mapping.items():
            if key == 'inventory_loss':
                _count(value, key, limits)
                if value > firms[fid]['inventory']: raise ValueError('Inventory loss exceeds available goods')
            elif _money(value, key) <= 0: raise ValueError('unit_cost shock must be positive cents')
    for key in ('inflation', 'output_gap', 'expected_energy_change', 'expected_rate_change'):
        if key in shock: mechanisms.number(shock[key], key, -1, 1)
    if any(k in shock for k in ('expected_energy_change', 'expected_rate_change')) and 'demand_feedback' not in options:
        raise ValueError('Expectation shocks require demand_feedback')
    if 'unemployment' in shock:
        mechanisms.number(shock['unemployment'], 'unemployment')
        if 'default_hazard' not in options: raise ValueError('unemployment shock requires default_hazard')
    previous_policy_rate = options['interest'].get('policy_rate', 0) if 'interest' in options else 0
    rate_audit = mechanisms.apply_rate(options, policy, shock)
    step_number = state['step'] + 1
    hazard_audit = mechanisms.update_default_hazard(options, shock, step_number, previous_policy_rate) if 'default_hazard' in options else None
    growth_audit = mechanisms.update_deposit_growth(options, step_number) if 'deposit_growth' in options else None
    credit_audit, generated_limit = mechanisms.update_credit_growth(options, step_number) if 'credit_growth' in options else (None, 0)
    actions = {}
    firm_policies = policy.get('firms', {})
    for fid, firm in firms.items():
        p = firm_policies.get(fid, {})
        actions[fid] = dict(production=_count(p.get('production', 0), 'production', limits),
                            price=mechanisms.price_for(firm, p, options),
                            credit_limit=_money(p['credit_limit'], 'credit_limit') if 'credit_limit' in p else generated_limit,
                            repay=_money(p.get('repay', 0), 'repay'), bankrupt=p.get('bankrupt', False))
        if actions[fid]['price'] <= 0: raise ValueError('price must be positive')
        if 'collateral_sale' in p:
            sale = p['collateral_sale']
            if sale['units'] > firm['inventory'] - shock.get('inventory_loss', {}).get(fid, 0):
                raise ValueError('Collateral sale units exceed available inventory')
            if firm['status'] == 'bankrupt' and sale['units']: raise ValueError('Previously bankrupt firm cannot sell collateral again')
            actions[fid]['collateral_sale'] = sale
    household_policies = policy.get('households', {})
    for hid, p in household_policies.items():
        for value in p.get('purchases', {}).values(): _count(value, 'purchases', limits)
        if 'labor_capacity' in p: households[hid]['labor_capacity'] = _count(p['labor_capacity'], 'labor_capacity', limits)
    labor_enabled = any('labor_capacity' in h for h in households.values()) or any('labor_per_unit' in f for f in firms.values())
    unbounded_labor = limits.economy_max_quantity
    labor = {hid: {'capacity': h.get('labor_capacity'), 'requested': 0, 'allocated': 0, 'unmet': 0} for hid, h in households.items()}
    events, journal = [], []
    created = repaid = defaulted = paid_interest = 0
    opening_principal = {fid: banks[firm['bank']]['loans'].get(fid, 0) for fid, firm in firms.items()}
    opening_accounts = sum(sum(b['accounts'].values()) for b in banks.values())
    opening_loans = sum(sum(b['loans'].values()) for b in banks.values())
    opening_equity = sum(b['equity'] for b in banks.values())
    initial_reserves = sum(b['reserves'] for b in banks.values())
    opening_deposits = ({aid: banks[actor_bank[aid]]['accounts'][aid] for aid in actors}
                        if 'deposit_interest' in options or 'deposit_growth' in options else {})
    opening_unit_cost = {fid: firm['unit_cost'] for fid, firm in firms.items()}

    def bank(actor): return banks[actor_bank[actor]]

    def transact(kind, actor, cents, target=None):
        if not cents: return
        bank_id = actor_bank[actor]
        if target is None:
            postings = apply_transaction(banks, kind, bank_id, cents, borrower=actor, events=events, record=full_record)
        else:
            postings = apply_transaction(banks, 'transfer', bank_id, cents, from_account=actor,
                                         to_bank=actor_bank[target], to_account=target, events=events, record=full_record)
        if full_record:
            tx = {'kind': kind, 'bank': bank_id, 'amount': _units(cents)}
            if target is None: tx['borrower'] = actor
            else: tx.update(from_account=actor, to_bank=actor_bank[target], to_account=target)
            journal.append({'transaction': tx, 'postings': postings, 'balanced': True})

    def liquidity(actor, target):
        return bank(actor)['reserves'] if actor_bank[actor] != actor_bank[target] else 100000000000000

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
            sold = min(sale['units'], bank(buyer)['accounts'][buyer] // price, liquidity(buyer, fid) // price)
            proceeds = sold * price
            transact('transfer', buyer, proceeds, fid)
            cogs[fid] += mechanisms.inventory_remove(firm, sold)
            metrics[fid]['sold'] += sold; metrics[fid]['revenue'] += proceeds
            recovered = min(proceeds, bank(fid)['loans'].get(fid, 0))
            transact('repay', fid, recovered); repaid += recovered
            recoveries[fid] = recovered
            events.append({'kind': 'collateral_sale', 'firm': fid, 'buyer': buyer, 'requested_units': sale['units'],
                           'sold_units': sold, 'proceeds': _units(proceeds), 'principal_recovered': _units(recovered),
                           'rationed': sold < sale['units'], 'book_cost': _units(cogs[fid]) if 'inventory_value' in firm else None})
        principal = bank(fid)['loans'].get(fid, 0)
        transact('default', fid, principal); defaulted += principal
        firm['status'] = 'bankrupt'
        events.append({'kind': 'firm_bankrupt', 'firm': fid, 'written_off': _units(principal), 'cause': cause})

    inventory_loss = shock.get('inventory_loss', {}); unit_cost_shock = shock.get('unit_cost', {})
    maximum_quantity = limits.economy_max_quantity
    if hazard_audit is not None:
        key, threshold, hazard_defaults = mechanisms.step_key(options['default_hazard']['seed'], step_number), hazard_audit['threshold'], 0
        for fid in sorted(firms):
            if firms[fid]['status'] == 'active' and mechanisms.draw53(key, fid) < threshold:
                bankrupt(fid, cause='default_hazard'); hazard_defaults += 1
        hazard_audit['defaults'] = hazard_defaults
    retained = {}
    if growth_audit is not None:
        retained = {hid: mechanisms.retained_deposit(opening_deposits[hid], growth_audit['daily_factor_ppb']) for hid in households}
    for fid in sorted(firms):
        firm, p = firms[fid], actions[fid]
        loss = inventory_loss.get(fid, 0)
        write_down[fid] += mechanisms.inventory_remove(firm, loss); destroyed += loss
        if fid in unit_cost_shock: firm['unit_cost'] = unit_cost_shock[fid]
        if p['bankrupt']: bankrupt(fid, p.get('collateral_sale'))
        if firm['status'] == 'bankrupt': continue
        b = bank(fid)
        cash, debt = b['accounts'][fid], b['loans'].get(fid, 0)
        credit = max(0, p['credit_limit'] - debt) if b['equity'] > 0 else 0
        unit_cost = _money(firm['unit_cost'], 'unit_cost')
        worker = labor[firm['worker']]; per_unit = firm.get('labor_per_unit', 1)
        worker['requested'] += p['production'] * per_unit
        available_labor = (worker['capacity'] - worker['allocated']) // per_unit if worker['capacity'] is not None else unbounded_labor
        produced = min(p['production'], firm['capacity'], (cash + credit) // unit_cost,
                       liquidity(fid, firm['worker']) // unit_cost, available_labor)
        cost = produced * unit_cost; loan = max(0, cost - cash)
        transact('originate', fid, loan); created += loan
        transact('transfer', fid, cost, firm['worker'])
        worker['allocated'] += produced * per_unit
        firm['inventory'] += produced
        if firm['inventory'] > maximum_quantity:
            raise LimitExceeded('economy_max_quantity', firm['inventory'], maximum_quantity, 'Inventory exceeds bounded range')
        if 'inventory_value' in firm: firm['inventory_value'] = _units(_money(firm['inventory_value'], 'inventory_value') + cost)
        metrics[fid].update(produced=produced, cost=cost)
        if produced < p['production']:
            events.append({'kind': 'production_rationed', 'firm': fid, 'requested': p['production'], 'produced': produced,
                           'fallback': 'reduce_to_funded_settleable_labor_and_capacity' if labor_enabled else 'reduce_to_funded_settleable_capacity'})
    expectations = state.get('expectations', {})
    demand_feedback = 'demand_feedback' in options
    for hid in sorted(household_policies):
        for fid, requested in sorted(household_policies[hid].get('purchases', {}).items()):
            price = actions[fid]['price']
            demand = mechanisms.demand_units(requested, price, options, expectations, limits)
            available = firms[fid]['inventory'] if firms[fid]['status'] == 'active' else 0
            bought = min(demand, available, max(0, bank(hid)['accounts'][hid] - retained.get(hid, 0)) // price, liquidity(hid, fid) // price)
            transact('transfer', hid, bought * price, fid)
            cogs[fid] += mechanisms.inventory_remove(firms[fid], bought)
            metrics[fid]['sold'] += bought; metrics[fid]['revenue'] += bought * price
            unmet_demand[fid] += demand - bought
            if bought < demand:
                events.append({'kind': 'purchase_rationed', 'household': hid, 'firm': fid, 'requested': demand, 'bought': bought})
            if demand_feedback:
                events.append({'kind': 'demand_feedback', 'household': hid, 'firm': fid, 'base_units': requested,
                               'demand_units': demand, 'price': _units(price), 'lagged_expectations': dict(expectations)})
    for fid in sorted(firms):
        firm = firms[fid]
        if 'interest' in options and firm['status'] == 'active':
            interest_cost[fid] = mechanisms.interest_due(opening_principal[fid], options['interest'])
            due = interest_cost[fid] + _money(firm['interest_arrears'], 'interest_arrears')
            paid = min(due, bank(fid)['accounts'][fid])
            transact('interest', fid, paid); paid_interest += paid; interest_payments[fid] = paid
            firm['interest_arrears'] = _units(due - paid)
            if due > paid:
                events.append({'kind': 'interest_unpaid', 'firm': fid, 'amount': _units(due - paid), 'treatment': 'memorandum_arrears'})
                if options['interest']['insufficient'] == 'bankrupt': bankrupt(fid, cause='unpaid_interest')
        b = bank(fid)
        amount = min(actions[fid]['repay'], b['accounts'][fid], b['loans'].get(fid, 0))
        transact('repay', fid, amount); repaid += amount
    deposit_paid = 0
    if 'deposit_interest' in options:
        for aid in sorted(households) + sorted(firms):
            amount = mechanisms.deposit_interest_due(opening_deposits[aid], options)
            transact('deposit_interest', aid, amount); deposit_paid += amount
    final_goods = sum(f['inventory'] for f in firms.values())
    if opening_goods + sum(m['produced'] - m['sold'] for m in metrics.values()) - destroyed != final_goods:
        raise ValueError('Goods conservation failed')
    if sum(sum(b['accounts'].values()) for b in banks.values()) - opening_accounts != created - repaid - paid_interest + deposit_paid:
        raise ValueError('Deposit creation/extinction mismatch')
    if sum(sum(b['loans'].values()) for b in banks.values()) - opening_loans != created - repaid - defaulted:
        raise ValueError('Debt creation/extinction mismatch')
    if sum(b['equity'] for b in banks.values()) - opening_equity != paid_interest - defaulted - deposit_paid: raise ValueError('Equity income/loss mismatch')
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
            firms[fid]['last_unmet_demand'] = min(maximum_quantity, unmet_demand[fid])
            if 'cost_pass_through' in options['price_feedback']: firms[fid]['last_unit_cost'] = opening_unit_cost[fid]
    if 'labor' in options:
        output = {hid: 0 for hid in households}
        for fid, firm in firms.items(): output[firm['worker']] += metrics[fid]['produced']
        elasticity = options['labor']['employment_output_elasticity']
        for hid, household in households.items():
            produced = min(maximum_quantity, output[hid])
            if 'labor_capacity' in household:
                household['labor_capacity'] = mechanisms.labor_capacity_update(household['labor_capacity'], household['labor_output'], produced, elasticity, maximum_quantity)
            household['labor_output'] = produced
    record = {'step': out['step'] + 1, 'policy': deepcopy(policy) if full_record else None, 'shock': deepcopy(shock) if full_record else None,
              'firms': metrics, 'work': work,
              'events': events, 'journal': journal, 'accounting': {'balanced': True, 'reserve_change': 0,
              'loan_created': _units(created), 'loan_repaid': _units(repaid), 'loan_defaulted': _units(defaulted),
              'goods_opening': opening_goods, 'goods_destroyed': destroyed, 'goods_closing': final_goods}}
    if 'interest' in options:
        record['accounting']['interest_paid'] = _units(paid_interest); record['monetary_policy'] = rate_audit
    if 'deposit_interest' in options:
        record['accounting']['deposit_interest_paid'] = _units(deposit_paid)
    for name, audit in (('default_hazard', hazard_audit), ('deposit_growth', growth_audit), ('credit_growth', credit_audit)):
        if audit is not None: record[name] = audit
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
    _check(banks, initial_reserves)
    out['banks'] = _export(banks)
    out['history'].append(record if full_record else summarize_record(record)); out['step'] += 1
    mechanisms.validate_fields(out, limits)
    return out


def simulate_coupled_economy(config, *, history=None, limits=None, backend='python', calibration=None):
    """Bounded convenience runner, with policy and exogenous shock sequences.

    backend='numpy' (or 'auto') uses worldmodel.coupled_economy_numpy.ArrayEconomy,
    whose results equal this reference; history=None keeps initial_state's policy.
    """
    from .backends import resolve_backend
    limits = resolve_limits(limits)
    _object(config, ('initial_state', 'policies', 'shocks'), 'coupled simulation')
    policies = config.get('policies')
    if not isinstance(policies, list): raise ValueError('policies must be a list of daily policies')
    limits.check('coupled_max_steps', len(policies), 'policies must contain a bounded number of steps')
    shocks = config.get('shocks', [{} for _ in policies])
    if not isinstance(shocks, list) or len(shocks) != len(policies): raise ValueError('shocks must align with policies')
    state = initialize_economy(config['initial_state'], history=history, limits=limits, calibration=calibration)
    _work_budget(state, policies, limits)
    actors = len(state['firms']) + len(state['households'])
    if resolve_backend(backend, size=actors) == 'numpy':
        from .coupled_economy_numpy import ArrayEconomy
        economy = ArrayEconomy.from_state(state, limits=limits)
        for policy, shock in zip(policies, shocks): economy.step(policy, shock)
        return economy.to_state()
    for policy, shock in zip(policies, shocks): state = step_economy(state, policy, shock, limits=limits)
    return state


def _predict(inputs, parameters, context):
    if context['dt_seconds'] != 86400: raise ValueError('Coupled economy requires daily cadence')
    state = inputs['economy_state']['value']
    if parameters.get('calibration') is not None:
        state = calibrate_state(state, parameters['calibration'])
    state = step_economy(state, inputs['economy_policy']['value'])
    record = state['history'][-1]
    bindings = state.get('calibration', {}).get('bindings', {})
    return {'pressures': [{'port': 'economy_state', 'mode': 'set', 'value': state, 'unit': None, 'strength': 1, 'confidence': 1}],
            'events': record.get('events', []), 'diagnostics': {'accounting': record['accounting'], 'journal': record.get('journal', []),
            'epistemic_status': 'synthetic_scenario', 'calibrated': bool(bindings) and all(b.get('validated') is True for b in bindings.values()),
            'illustrative': True, 'parameter_provenance': parameter_provenance(state),
            'calibration_sources': sorted({b['record_id'] or b['estimate_id'] for b in bindings.values() if b.get('record_id') or b.get('estimate_id')})}}


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
