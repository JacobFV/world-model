"""Exact-cent commercial-bank ledger, separate from behavioral credit models.

Loans create deposits; settlement moves existing reserves. No reserve creation,
capital injection, regulatory-capital rule, or central-bank reaction is
inferred. Explicit interest payments reduce deposits and raise bank equity.
Equity may become negative and insolvency is explicitly retained.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation


def _money(value, name, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f'{name} must be a finite monetary amount')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or (not signed and amount < 0) or abs(amount) > Decimal('1000000000000'):
            raise ValueError(f'{name} must be finite and within the ledger range')
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise ValueError(f'{name} must be an exact number of cents')
        return int(cents)
    except InvalidOperation as exc:
        raise ValueError(f'Invalid amount for {name}') from exc


def _name(value, field):
    if not isinstance(value, str) or not value:
        raise ValueError(f'{field} must be a nonempty identifier')
    return value


def _units(cents):
    return cents / 100


def _status(bank):
    return 'insolvent' if bank['equity'] < 0 else 'zero_equity' if bank['equity'] == 0 else 'solvent'


def _check(banks, initial_reserves):
    for bank in banks.values():
        if any(abs(v) > 100000000000000 for v in (bank['reserves'], bank['equity'], *bank['loans'].values(), *bank['accounts'].values())):
            raise ValueError('Balance exceeds bounded ledger range')
        if bank['reserves'] < 0 or any(v < 0 for v in (*bank['loans'].values(), *bank['accounts'].values())):
            raise ValueError('Negative reserve, principal or deposit balance')
        if bank['reserves'] + sum(bank['loans'].values()) != sum(bank['accounts'].values()) + bank['equity']:
            raise ValueError(f"Bank {bank['id']} fails assets = deposits + equity")
    if sum(b['reserves'] for b in banks.values()) != initial_reserves:
        raise ValueError('Aggregate reserves were not conserved')


def _export(banks):
    return [{'id': b['id'], 'reserves': _units(b['reserves']), 'equity': _units(b['equity']),
             'loans': {k: _units(v) for k, v in b['loans'].items()},
             'accounts': {k: _units(v) for k, v in b['accounts'].items()}, 'status': _status(b)} for b in banks.values()]


def simulate_banking(config):
    """Apply a bounded transaction sequence atomically to a copied opening state.

    Config banks require id,reserves,loans:{borrower:principal},equity,accounts.
    Loan and interest transactions use bank,borrower,amount. Transfers use bank,from_account,
    to_bank,to_account,amount. New destination accounts start at zero. All amounts
    use one USD denomination, exact cents. Signed equity preserves insolvency;
    negative reserves, loans, deposits and transaction amounts are prohibited.
    A failure raises without changing caller inputs or publishing partial state.
    """
    if not isinstance(config, dict) or set(config) - {'banks', 'transactions', 'currency'}:
        raise ValueError('Unknown banking config fields')
    if config.get('currency', 'USD') != 'USD':
        raise ValueError('This ledger currently supports USD cents only')
    supplied = config.get('banks')
    transactions = config.get('transactions', [])
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 100:
        raise ValueError('banks must contain 1 to 100 banks')
    if not isinstance(transactions, list) or len(transactions) > 10000:
        raise ValueError('transactions must be a list of at most 10000 entries')
    banks = {}
    for item in supplied:
        if not isinstance(item, dict) or set(item) - {'id', 'reserves', 'loans', 'equity', 'accounts', 'status'}:
            raise ValueError('Unknown bank fields')
        if not {'id', 'reserves', 'loans', 'equity', 'accounts'} <= set(item):
            raise ValueError('Bank requires id, reserves, loans, equity and accounts')
        bank_id = _name(item.get('id'), 'bank.id')
        if bank_id in banks:
            raise ValueError('Duplicate bank ID')
        bank = {'id': bank_id, 'reserves': _money(item['reserves'], 'reserves'), 'equity': _money(item['equity'], 'equity', signed=True)}
        for field in ('loans', 'accounts'):
            if not isinstance(item[field], dict):
                raise ValueError(f'{field} must map owner IDs to balances')
            bank[field] = {_name(k, field): _money(v, field) for k, v in item[field].items()}
        if 'status' in item and item['status'] != _status(bank):
            raise ValueError('Supplied bank status conflicts with equity')
        banks[bank_id] = bank
    # Bound state × transactions, since audit retains complete before/after state.
    state_cells = sum(3 + len(b['accounts']) + len(b['loans']) for b in banks.values())
    if (len(transactions) + 1) * (state_cells + 2 * len(transactions)) > 1000000:
        raise ValueError('Banking audit state budget exceeded (1000000 balance cells)')
    reserves = sum(b['reserves'] for b in banks.values())
    initial_deposits = sum(sum(b['accounts'].values()) for b in banks.values())
    initial_loans = sum(sum(b['loans'].values()) for b in banks.values())
    _check(banks, reserves)
    snapshots = [{'step': 0, 'banks': _export(banks)}]
    journal, audit, events = [], [], []
    for step, transaction in enumerate(transactions, 1):
        if not isinstance(transaction, dict):
            raise ValueError('Transaction must be an object')
        kind = transaction.get('kind')
        fields = {'kind', 'bank', 'amount', 'borrower'} if kind in ('originate', 'repay', 'default', 'interest') else {'kind', 'bank', 'amount', 'from_account', 'to_bank', 'to_account'} if kind == 'transfer' else None
        if fields is None or set(transaction) != fields:
            raise ValueError('Unknown transaction kind or missing/unknown transaction fields')
        bank_id = _name(transaction['bank'], 'bank')
        if bank_id not in banks:
            raise ValueError('Unknown bank')
        bank = banks[bank_id]
        amount = _money(transaction['amount'], 'amount')
        if amount <= 0:
            raise ValueError('Transaction amount must be positive')
        before = _export(banks)
        statuses = {k: _status(v) for k, v in banks.items()}
        postings = []
        totals = {}

        def posting(bid, account, debit=0, credit=0):
            totals[bid] = totals.get(bid, 0) + debit - credit
            postings.append({'bank': bid, 'account': account, 'debit': _units(debit), 'credit': _units(credit)})

        if kind in ('originate', 'repay', 'default', 'interest'):
            borrower = _name(transaction['borrower'], 'borrower')
            loan = bank['loans'].get(borrower, 0)
            deposit = bank['accounts'].get(borrower, 0)
            if kind == 'originate':
                if bank['equity'] <= 0:
                    raise ValueError('Origination prohibited for a bank with nonpositive equity')
                bank['loans'][borrower] = loan + amount
                bank['accounts'][borrower] = deposit + amount
                posting(bank_id, 'loans:' + borrower, debit=amount)
                posting(bank_id, 'deposits:' + borrower, credit=amount)
            elif kind == 'interest':
                if borrower not in bank['loans']:
                    raise ValueError('Interest requires a known borrower loan account')
                if amount > deposit:
                    raise ValueError('Insufficient borrower deposit for interest')
                bank['accounts'][borrower] = deposit - amount
                bank['equity'] += amount
                posting(bank_id, 'deposits:' + borrower, debit=amount)
                posting(bank_id, 'equity', credit=amount)
            else:
                if amount > loan:
                    raise ValueError('Repayment/default exceeds outstanding borrower principal')
                if kind == 'repay':
                    if amount > deposit:
                        raise ValueError('Insufficient borrower deposit for repayment')
                    bank['accounts'][borrower] = deposit - amount
                    posting(bank_id, 'deposits:' + borrower, debit=amount)
                else:
                    bank['equity'] -= amount
                    posting(bank_id, 'equity', debit=amount)
                    events.append({'kind': 'loan_written_off', 'step': step, 'bank': bank_id, 'borrower': borrower, 'amount': _units(amount)})
                bank['loans'][borrower] = loan - amount
                posting(bank_id, 'loans:' + borrower, credit=amount)
        else:
            source = _name(transaction['from_account'], 'from_account')
            target = _name(transaction['to_account'], 'to_account')
            target_bank_id = _name(transaction['to_bank'], 'to_bank')
            if target_bank_id not in banks:
                raise ValueError('Unknown receiving bank')
            receiving = banks[target_bank_id]
            if source not in bank['accounts'] or bank['accounts'][source] < amount:
                raise ValueError('Insufficient sender deposit')
            if bank_id != target_bank_id and bank['reserves'] < amount:
                raise ValueError('Insufficient reserves for interbank settlement')
            bank['accounts'][source] -= amount
            receiving['accounts'][target] = receiving['accounts'].get(target, 0) + amount
            posting(bank_id, 'deposits:' + source, debit=amount)
            if bank_id != target_bank_id:
                bank['reserves'] -= amount
                receiving['reserves'] += amount
                posting(bank_id, 'reserves', credit=amount)
                posting(target_bank_id, 'reserves', debit=amount)
            posting(target_bank_id, 'deposits:' + target, credit=amount)
        _check(banks, reserves)
        if any(totals.values()):
            raise ValueError('Transaction journal does not balance per bank')
        for bid, state in banks.items():
            if _status(state) == 'insolvent' and statuses[bid] != 'insolvent':
                events.append({'kind': 'bank_insolvent', 'step': step, 'bank': bid, 'equity': _units(state['equity']), 'bailout': False})
        after = _export(banks)
        journal.append({'step': step, 'kind': kind, 'postings': postings, 'balanced': True})
        audit.append({'step': step, 'transaction': deepcopy(transaction), 'before': before, 'after': after})
        snapshots.append({'step': step, 'banks': after})
    return {'banks': _export(banks), 'currency': 'USD', 'snapshots': snapshots, 'journal': journal, 'audit': audit, 'events': events,
            'accounting': {'balanced': True, 'precision': 'integer cents', 'reserve_change': 0,
                          'initial_reserves': _units(reserves), 'final_reserves': _units(reserves),
                          'deposit_change': _units(sum(sum(b['accounts'].values()) for b in banks.values()) - initial_deposits),
                          'loan_change': _units(sum(sum(b['loans'].values()) for b in banks.values()) - initial_loans)},
            'epistemic_status': 'synthetic_scenario', 'calibration_status': 'accounting identities only; no behavioral calibration',
            'assumptions': ['Commercial loan origination creates deposits; repayment extinguishes both.',
                            'Fixed aggregate reserves; no central-bank lending, reserve interest or bailout.',
                            'Interest is an explicit paid amount; no automatic accrual, capital adequacy, liquidity regulation or borrower credit model.',
                            'Default is an explicit full principal write-off amount; deposits are not erased.',
                            'Equity may be negative; insolvent banks cannot originate but can settle funded transfers and repayments.']}


def _predict(inputs, parameters, context):
    if context['dt_seconds'] != 86400:
        raise ValueError('Banking ledger requires exactly one daily cadence')
    result = simulate_banking(inputs['banking_state']['value'])
    state = {'banks': result['banks'], 'currency': result['currency'], 'transactions': []}
    return {'pressures': [{'port': 'banking_state', 'mode': 'set', 'value': state, 'unit': None, 'strength': 1, 'confidence': 1}],
            'events': result['events'], 'diagnostics': {'accounting': result['accounting'], 'journal': result['journal'],
                                                     'epistemic_status': 'synthetic_scenario', 'calibrated': False}}


def register_banking_processes(registry):
    port = {'type': 'object', 'unit': None}
    registry.register_process({'id': 'banking_ledger', 'inputs': {'banking_state': port}, 'outputs': {'banking_state': port},
        'topology': 'Explicit banks, depositor accounts and borrower principal balances.',
        'description': 'Apply a supplied daily transaction batch; accounting mechanism, no behavioral prediction.', 'illustrative': True, 'validated': False})
    registry.register_implementation({'id': 'banking_ledger.deterministic', 'process_id': 'banking_ledger', 'fidelity': 'deterministic',
        'min_step_seconds': 86400, 'max_step_seconds': 86400, 'cost_per_call': 1, 'output_timing': 'end_of_step',
        'description': 'Exact-cent balanced transaction batch; clears executed transactions.'}, _predict)
    return registry


def schema():
    return {'entity_types': {'bank': {'parent': 'business'}, 'central_bank': {'parent': 'government_agency'},
                             'financial_asset': {'parent': 'asset'}, 'loan': {'parent': 'financial_asset'}, 'deposit_account': {'parent': 'account'}},
            'relations': {'account_at_bank': {'domain': 'deposit_account', 'range': 'bank'},
                          'loan_issued_by': {'domain': 'loan', 'range': 'bank'},
                          'has_deposit_account': {'domain': 'agent', 'range': 'deposit_account'},
                          'borrows_loan': {'domain': 'agent', 'range': 'loan'}},
            'variables': {name: {'type': 'number', 'unit': 'USD', 'domain': domain} for name, domain in [
                ('bank_reserves', 'bank'), ('bank_equity', 'bank'), ('bank_deposits', 'bank'),
                ('loan_principal', 'loan'), ('deposit_balance', 'deposit_account')]}}
