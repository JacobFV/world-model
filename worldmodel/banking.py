"""Exact-cent commercial-bank ledger, separate from behavioral credit models.

Loans create deposits; settlement moves existing reserves. No reserve creation,
capital injection, regulatory-capital rule, or central-bank reaction is
inferred. Explicit interest payments reduce deposits and raise bank equity.
Equity may become negative and insolvency is explicitly retained.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import math

from .limits import resolve_limits

MAX_BALANCE_CENTS = 100000000000000


def _money(value, name, signed=False):
    kind = type(value)
    if kind is int:  # Fast exact paths are equivalent to the Decimal(str(value)) rule below.
        if (not signed and value < 0) or abs(value) > 1000000000000:
            raise ValueError(f'{name} must be finite and within the ledger range')
        return value * 100
    if kind is float:
        if not math.isfinite(value) or (not signed and value < 0) or abs(value) > 1e12:
            raise ValueError(f'{name} must be finite and within the ledger range')
        cents = round(value * 100)
        if cents / 100 != value:
            raise ValueError(f'{name} must be an exact number of cents')
        return cents
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


def _check_bank(bank):
    if any(abs(v) > MAX_BALANCE_CENTS for v in (bank['reserves'], bank['equity'], *bank['loans'].values(), *bank['accounts'].values())):
        raise ValueError('Balance exceeds bounded ledger range')
    if bank['reserves'] < 0 or any(v < 0 for v in (*bank['loans'].values(), *bank['accounts'].values())):
        raise ValueError('Negative reserve, principal or deposit balance')
    if bank['reserves'] + sum(bank['loans'].values()) != sum(bank['accounts'].values()) + bank['equity']:
        raise ValueError(f"Bank {bank['id']} fails assets = deposits + equity")


def _check(banks, initial_reserves):
    for bank in banks.values():
        _check_bank(bank)
    if sum(b['reserves'] for b in banks.values()) != initial_reserves:
        raise ValueError('Aggregate reserves were not conserved')


def _export(banks):
    return [{'id': b['id'], 'reserves': _units(b['reserves']), 'equity': _units(b['equity']),
             'loans': {k: _units(v) for k, v in b['loans'].items()},
             'accounts': {k: _units(v) for k, v in b['accounts'].items()}, 'status': _status(b)} for b in banks.values()]


def parse_banks(supplied, limits=None):
    """Validate exported bank dictionaries into an integer-cent ledger keyed by bank ID."""
    limits = resolve_limits(limits)
    if not isinstance(supplied, list) or not supplied:
        raise ValueError('banks must contain at least one bank')
    limits.check('banking_max_banks', len(supplied), 'banks must contain a bounded number of banks')
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
    return banks


def apply_transaction(banks, kind, bank_id, amount, *, borrower=None, from_account=None, to_bank=None,
                      to_account=None, step=1, events=None, record=True):
    """Apply one integer-cent transaction in place; return its balanced postings.

    Identical rules to simulate_banking. Only touched banks can change, so only
    they are re-checked; callers must copy ``banks`` first if they need rollback.
    """
    if bank_id not in banks:
        raise ValueError('Unknown bank')
    bank = banks[bank_id]
    if type(amount) is not int or amount <= 0:
        raise ValueError('Transaction amount must be positive')
    postings = []
    totals = {}

    def posting(bid, account, debit=0, credit=0):
        totals[bid] = totals.get(bid, 0) + debit - credit
        if record:  # record=False skips posting dicts; per-bank balance is still checked.
            postings.append({'bank': bid, 'account': account, 'debit': _units(debit), 'credit': _units(credit)})

    touched = [bank]
    was_insolvent = bank['equity'] < 0
    if kind in ('originate', 'repay', 'default', 'interest', 'deposit_interest'):
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
        elif kind == 'deposit_interest':
            # Estimation hook (deposit_rate_pass_through): the bank pays interest on a deposit from equity.
            if borrower not in bank['accounts']:
                raise ValueError('Deposit interest requires a known deposit account')
            bank['accounts'][borrower] = deposit + amount
            bank['equity'] -= amount
            posting(bank_id, 'equity', debit=amount)
            posting(bank_id, 'deposits:' + borrower, credit=amount)
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
                if events is not None:
                    events.append({'kind': 'loan_written_off', 'step': step, 'bank': bank_id, 'borrower': borrower, 'amount': _units(amount)})
            bank['loans'][borrower] = loan - amount
            posting(bank_id, 'loans:' + borrower, credit=amount)
    elif kind == 'transfer':
        if to_bank not in banks:
            raise ValueError('Unknown receiving bank')
        receiving = banks[to_bank]
        if from_account not in bank['accounts'] or bank['accounts'][from_account] < amount:
            raise ValueError('Insufficient sender deposit')
        if bank_id != to_bank and bank['reserves'] < amount:
            raise ValueError('Insufficient reserves for interbank settlement')
        bank['accounts'][from_account] -= amount
        receiving['accounts'][to_account] = receiving['accounts'].get(to_account, 0) + amount
        posting(bank_id, 'deposits:' + from_account, debit=amount)
        if bank_id != to_bank:
            bank['reserves'] -= amount
            receiving['reserves'] += amount
            posting(bank_id, 'reserves', credit=amount)
            posting(to_bank, 'reserves', debit=amount)
            touched.append(receiving)
        posting(to_bank, 'deposits:' + to_account, credit=amount)
    else:
        raise ValueError('Unknown transaction kind or missing/unknown transaction fields')
    for item in touched:  # Untouched banks and aggregate reserves are unchanged.
        if abs(item['equity']) > MAX_BALANCE_CENTS or not 0 <= item['reserves'] <= MAX_BALANCE_CENTS:
            raise ValueError('Balance exceeds bounded ledger range' if item['reserves'] >= 0 else 'Negative reserve, principal or deposit balance')
        loans, accounts = item['loans'], item['accounts']
        for key in (borrower, from_account, to_account):
            if key is not None:
                for value in (loans.get(key, 0), accounts.get(key, 0)):
                    if value > MAX_BALANCE_CENTS:
                        raise ValueError('Balance exceeds bounded ledger range')
                    if value < 0:
                        raise ValueError('Negative reserve, principal or deposit balance')
    if any(totals.values()):
        raise ValueError('Transaction journal does not balance per bank')
    if not was_insolvent and bank['equity'] < 0 and events is not None:
        events.append({'kind': 'bank_insolvent', 'step': step, 'bank': bank_id, 'equity': _units(bank['equity']), 'bailout': False})
    return postings


def simulate_banking(config, *, limits=None, audit='full'):
    """Apply a bounded transaction sequence atomically to a copied opening state.

    Config banks require id,reserves,loans:{borrower:principal},equity,accounts.
    Loan and interest transactions use bank,borrower,amount. Transfers use bank,from_account,
    to_bank,to_account,amount. New destination accounts start at zero. All amounts
    use one USD denomination, exact cents. Signed equity preserves insolvency;
    negative reserves, loans, deposits and transaction amounts are prohibited.
    A failure raises without changing caller inputs or publishing partial state.

    audit='full' (default) retains before/after state for every transaction and is
    bounded by banking_max_audit_cells; audit='summary' keeps the journal, events and
    opening/closing snapshots only, so work is linear in transactions.
    """
    limits = resolve_limits(limits)
    if audit not in ('full', 'summary'):
        raise ValueError("audit must be 'full' or 'summary'")
    if not isinstance(config, dict) or set(config) - {'banks', 'transactions', 'currency'}:
        raise ValueError('Unknown banking config fields')
    if config.get('currency', 'USD') != 'USD':
        raise ValueError('This ledger currently supports USD cents only')
    transactions = config.get('transactions', [])
    if not isinstance(transactions, list):
        raise ValueError('transactions must be a list')
    limits.check('banking_max_transactions', len(transactions), 'transactions must be a bounded list')
    banks = parse_banks(config.get('banks'), limits)
    # Bound state x transactions, since a full audit retains complete before/after state.
    state_cells = sum(3 + len(b['accounts']) + len(b['loans']) for b in banks.values())
    if audit == 'full':
        limits.check('banking_max_audit_cells', (len(transactions) + 1) * (state_cells + 2 * len(transactions)),
                     'Banking audit state budget exceeded (balance cells; use audit="summary")')
    reserves = sum(b['reserves'] for b in banks.values())
    initial_deposits = sum(sum(b['accounts'].values()) for b in banks.values())
    initial_loans = sum(sum(b['loans'].values()) for b in banks.values())
    _check(banks, reserves)
    snapshots = [{'step': 0, 'banks': _export(banks)}]
    journal, audit_rows, events = [], [], []
    for step, transaction in enumerate(transactions, 1):
        if not isinstance(transaction, dict):
            raise ValueError('Transaction must be an object')
        kind = transaction.get('kind')
        fields = ({'kind', 'bank', 'amount', 'borrower'} if kind in ('originate', 'repay', 'default', 'interest')
                  else {'kind', 'bank', 'amount', 'account'} if kind == 'deposit_interest'
                  else {'kind', 'bank', 'amount', 'from_account', 'to_bank', 'to_account'} if kind == 'transfer' else None)
        if fields is None or set(transaction) != fields:
            raise ValueError('Unknown transaction kind or missing/unknown transaction fields')
        bank_id = _name(transaction['bank'], 'bank')
        if bank_id not in banks:
            raise ValueError('Unknown bank')
        amount = _money(transaction['amount'], 'amount')
        if amount <= 0:
            raise ValueError('Transaction amount must be positive')
        before = _export(banks) if audit == 'full' else None
        if kind == 'transfer':
            source = _name(transaction['from_account'], 'from_account')
            target = _name(transaction['to_account'], 'to_account')
            target_bank_id = _name(transaction['to_bank'], 'to_bank')
            postings = apply_transaction(banks, kind, bank_id, amount, from_account=source, to_bank=target_bank_id,
                                         to_account=target, step=step, events=events)
        elif kind == 'deposit_interest':
            postings = apply_transaction(banks, kind, bank_id, amount, borrower=_name(transaction['account'], 'account'), step=step, events=events)
        else:
            borrower = _name(transaction['borrower'], 'borrower')
            postings = apply_transaction(banks, kind, bank_id, amount, borrower=borrower, step=step, events=events)
        journal.append({'step': step, 'kind': kind, 'postings': postings, 'balanced': True})
        if audit == 'full':
            after = _export(banks)
            audit_rows.append({'step': step, 'transaction': deepcopy(transaction), 'before': before, 'after': after})
            snapshots.append({'step': step, 'banks': after})
    _check(banks, reserves)
    final = _export(banks)
    if audit == 'summary' and transactions:
        snapshots.append({'step': len(transactions), 'banks': final})
    return {'banks': final, 'currency': 'USD', 'snapshots': snapshots, 'journal': journal, 'audit': audit_rows, 'events': events,
            'audit_mode': audit,
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
                                                     'epistemic_status': 'synthetic_scenario', 'calibrated': False,
            'parameter_provenance': {}, 'parameter_note': 'Accounting identities only; no behavioral parameters are assumed or estimated.'}}


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
