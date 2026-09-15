"""Bounded, closed-cash daily economy. Behavioral coefficients are assumptions.

USD cash moves between a bank, firms, supplier and explicit customer sector.
This is a cash-funded lender, not a commercial-bank deposit creation model.
Inventory uses weighted-average acquisition cost; loans are floating rate.
"""
from copy import deepcopy
import math


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    return float(value)


def example_economy():
    return {'days': 30, 'energy_price': 10, 'bank': {'cash': 10000, 'annual_rate': .05, 'credit_limit': 10000},
            'supplier': {'cash': 1000}, 'customers': {'cash': 100000},
            'businesses': [{'id': 'factory', 'cash': 1000, 'loan': 0, 'inventory': 20,
                'storage_capacity': 200, 'credit_limit': 2000, 'daily_energy_need': 10,
                'revenue_per_unit': 16, 'policy': {'target_days': 3, 'anticipation': 2,
                                                 'expected_price_change': .25}}],
            'shocks': [{'step': 10, 'energy_price': 14, 'annual_rate': .08}]}


def _prepare(config):
    config = deepcopy(config)
    days = config.get('days', 30)
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 10000:
        raise ValueError('days must be an integer in 0..10000')
    price = _number(config['energy_price'], 'energy_price')
    if price == 0:
        raise ValueError('energy_price must be positive')
    bank = {key: _number(config['bank'][key], f'bank.{key}') for key in ('cash', 'annual_rate', 'credit_limit')}
    supplier = {'cash': _number(config['supplier']['cash'], 'supplier.cash')}
    customers = {'cash': _number(config.get('customers', {'cash': 100000})['cash'], 'customers.cash')}
    businesses = config['businesses']
    if not isinstance(businesses, list) or not 1 <= len(businesses) <= 1000 or days * len(businesses) > 100000:
        raise ValueError('businesses and days exceed bounded simulation work budget')
    ids = set()
    for firm in businesses:
        ident = firm['id']
        if not isinstance(ident, str) or not ident or ':' in ident or ident in ids or ident in {'bank', 'supplier', 'customers'}:
            raise ValueError('Business IDs must be distinct nonreserved strings without colons')
        ids.add(ident)
        for key in ('cash', 'loan', 'inventory', 'storage_capacity', 'credit_limit', 'daily_energy_need', 'revenue_per_unit'):
            firm[key] = _number(firm[key], f'{ident}.{key}')
        if firm['loan'] > firm['credit_limit'] or firm['inventory'] > firm['storage_capacity']:
            raise ValueError('Initial credit or storage limit exceeded')
        policy = firm.setdefault('policy', {})
        for key, default in [('target_days', 1), ('anticipation', 0), ('expected_price_change', 0)]:
            policy[key] = _number(policy.get(key, default), f'{ident}.policy.{key}', -1 if key == 'expected_price_change' else 0)
        firm.update(inventory_value=firm['inventory'] * price, defaulted=False, output=0., profit=0., interest=0., energy_spend=0.)
    bank['loans'] = sum(f['loan'] for f in businesses)
    if bank['loans'] > bank['credit_limit']:
        raise ValueError('Initial aggregate bank credit limit exceeded')
    bank.update(equity=bank['cash'] + bank['loans'], interest_income=0., credit_losses=0.)
    shocks = config.get('shocks', [])
    seen = set()
    for shock in shocks:
        step = shock['step']
        if isinstance(step, bool) or not isinstance(step, int) or step < 1 or step in seen:
            raise ValueError('Shock steps must be unique positive integers')
        if set(shock) - {'step', 'energy_price', 'annual_rate'}:
            raise ValueError('Unknown shock field')
        seen.add(step)
        for field in ('energy_price', 'annual_rate'):
            if field in shock:
                _number(shock[field], field)
                if field == 'energy_price' and shock[field] == 0:
                    raise ValueError('energy_price must be positive')
    return days, price, bank, supplier, customers, businesses, {s['step']: s for s in shocks}


def simulate_economy(config):
    """Return daily snapshots, balanced debit/credit journal, events and metrics.

    Each day: apply shocks, pay interest/default, fund and purchase inventories,
    consume energy and sell output. Firm order is explicit credit priority.
    No future shock is observed by purchasing policy: expectations are inputs.
    """
    days, price, bank, supplier, customers, firms, shocks = _prepare(config)
    journal, events, snapshots, ledger = [], [], [], {}

    def post(step, kind, *entries):
        postings = [{'account': account, 'debit': max(amount, 0.), 'credit': max(-amount, 0.)}
                    for account, amount in entries if amount != 0]
        if abs(sum(e['debit'] - e['credit'] for e in postings)) > 1e-7:
            raise ValueError('Unbalanced transaction')
        for e in postings:
            ledger[e['account']] = ledger.get(e['account'], 0.) + e['debit'] - e['credit']
        journal.append({'step': step, 'kind': kind, 'postings': postings})

    post(0, 'opening_bank', ('bank:cash', bank['cash']), ('bank:loans', bank['loans']), ('bank:equity', -bank['equity']))
    for name, actor in [('supplier', supplier), ('customers', customers)]:
        post(0, 'opening_' + name, (name + ':cash', actor['cash']), (name + ':equity', -actor['cash']))
    for f in firms:
        ident = f['id']
        post(0, 'opening_business', (ident + ':cash', f['cash']), (ident + ':inventory', f['inventory_value']),
             (ident + ':loan', -f['loan']), (ident + ':equity', -(f['cash'] + f['inventory_value'] - f['loan'])))

    def snapshot(step):
        bank['equity'] = bank['cash'] + bank['loans']
        for firm in firms:
            firm['equity'] = firm['cash'] + firm['inventory_value'] - firm['loan']
            if firm['equity'] < -1e-9 and not any(e['kind'] == 'insolvency' and e['business'] == firm['id'] for e in events):
                events.append({'step': step, 'kind': 'insolvency', 'business': firm['id'], 'equity': firm['equity'],
                               'recognition': 'Negative book equity; continues operating while daily interest can be paid.'})
        metrics = {'total_cash': bank['cash'] + supplier['cash'] + customers['cash'] + sum(f['cash'] for f in firms),
            'total_loans': bank['loans'], 'total_inventory': sum(f['inventory'] for f in firms),
            'total_output': sum(f['output'] for f in firms), 'total_profit': sum(f['profit'] for f in firms),
            'total_interest': sum(f['interest'] for f in firms), 'total_energy_spend': sum(f['energy_spend'] for f in firms),
            'defaults': sum(f['defaulted'] for f in firms), 'stockouts': sum(e['kind'] == 'stockout' for e in events),
            'bank_equity': bank['equity'], 'business_cash': sum(f['cash'] for f in firms),
            'insolvent_businesses': sum(f['equity'] < -1e-9 for f in firms)}
        snapshots.append(deepcopy({'step': step, 'energy_price': price, 'annual_rate': bank['annual_rate'],
            'metrics': metrics, 'businesses': firms, 'bank': bank, 'supplier': supplier, 'customers': customers}))

    snapshot(0)
    for step in range(1, days + 1):
        if step in shocks:
            shock = shocks[step]
            price = shock.get('energy_price', price)
            bank['annual_rate'] = shock.get('annual_rate', bank['annual_rate'])
            events.append(dict(shock, kind='shock'))
        for firm in firms:
            if firm['defaulted']:
                continue
            ident = firm['id']
            due = firm['loan'] * bank['annual_rate'] / 365
            paid = min(due, firm['cash'])
            if paid:
                firm['cash'] -= paid
                bank['cash'] += paid
                bank['interest_income'] += paid
                firm['interest'] += paid
                firm['profit'] -= paid
                post(step, 'interest', (ident + ':interest_expense', paid), (ident + ':cash', -paid),
                     ('bank:cash', paid), ('bank:interest_revenue', -paid))
            if due - paid > 1e-9:
                loss = firm['loan']
                bank['loans'] -= loss
                bank['credit_losses'] += loss
                firm['loan'] = 0.
                firm['defaulted'] = True
                post(step, 'default', ('bank:credit_loss', loss), ('bank:loans', -loss),
                     (ident + ':loan', loss), (ident + ':debt_relief', -loss))
                events.append({'step': step, 'kind': 'default', 'business': ident, 'principal_written_off': loss,
                               'unpaid_interest': due - paid})
                continue
            policy = firm['policy']
            # Carrying cost uses the declared target horizon; expectations never inspect shocks.
            incentive = max(0., policy['expected_price_change'] - bank['annual_rate'] * policy['target_days'] / 365)
            target = min(firm['storage_capacity'], firm['daily_energy_need'] * policy['target_days'] *
                         (1 + policy['anticipation'] * incentive))
            desired_cost = max(0., target - firm['inventory']) * price
            borrowed = min(max(0., desired_cost - firm['cash']), bank['cash'],
                           max(0., bank['credit_limit'] - bank['loans']), max(0., firm['credit_limit'] - firm['loan']))
            if borrowed:
                bank['cash'] -= borrowed
                bank['loans'] += borrowed
                firm['cash'] += borrowed
                firm['loan'] += borrowed
                post(step, 'loan_origination', ('bank:loans', borrowed), ('bank:cash', -borrowed),
                     (ident + ':cash', borrowed), (ident + ':loan', -borrowed))
            spent = min(desired_cost, firm['cash'])
            purchased = spent / price
            if spent:
                firm['cash'] -= spent
                supplier['cash'] += spent
                firm['inventory'] += purchased
                firm['inventory_value'] += spent
                firm['energy_spend'] += spent
                post(step, 'energy_purchase', (ident + ':inventory', spent), (ident + ':cash', -spent),
                     ('supplier:cash', spent), ('supplier:revenue', -spent))
            if firm['inventory'] + 1e-9 < firm['daily_energy_need']:
                events.append({'step': step, 'kind': 'stockout', 'business': ident,
                               'unmet_energy': firm['daily_energy_need'] - firm['inventory']})
            output = min(firm['daily_energy_need'], firm['inventory'])
            if firm['revenue_per_unit']:
                output = min(output, customers['cash'] / firm['revenue_per_unit'])
            cost = output * (firm['inventory_value'] / firm['inventory'] if firm['inventory'] else 0.)
            revenue = output * firm['revenue_per_unit']
            firm['inventory'] = max(0., firm['inventory'] - output)
            firm['inventory_value'] = max(0., firm['inventory_value'] - cost)
            firm['cash'] += revenue
            customers['cash'] = max(0., customers['cash'] - revenue)
            firm['output'] += output
            firm['profit'] += revenue - cost
            if output:
                post(step, 'production_sale', (ident + ':cost_of_goods', cost), (ident + ':inventory', -cost),
                     (ident + ':cash', revenue), (ident + ':sales_revenue', -revenue),
                     ('customers:consumption_expense', revenue), ('customers:cash', -revenue))
        snapshot(step)
    result = {'snapshots': snapshots, 'journal': journal, 'ledger': ledger, 'events': events,
              'metrics': deepcopy(snapshots[-1]['metrics']), 'status': {'synthetic': True, 'calibrated': False},
              'assumptions': ['USD amounts; energy quantity is a consistent user-chosen unit; one output per energy unit.',
                  'Cash-funded lender; no deposit creation, reserves, interbank market or central bank balance sheet.',
                  'Floating annual loan rate / 365; unpaid daily interest triggers immediate default and full principal writeoff.',
                  'No loan maturity or principal repayment; defaults halt production, with no collateral recovery. Operating profit excludes default debt relief.',
                  'Supplier has unlimited energy at exogenous price; customers have finite cash.',
                  'Business list order is priority for scarce bank cash and customer cash.',
                  'Inventory target = daily need * target days * (1 + anticipation * max(expected price change - annual rate * target days / 365, 0)), capped by storage.',
                  'Behavioral coefficients are illustrative inputs, not calibrated responses; future shocks are not read by firms.']}
    result['accounting'] = validate_accounting(result)
    return result


def validate_accounting(result, tolerance=1e-7):
    """Recompute ledger and verify journals, cash conservation and loan symmetry."""
    ledger = {}
    for entry in result['journal']:
        total = 0.
        for p in entry['postings']:
            debit, credit = _number(p['debit'], 'debit'), _number(p['credit'], 'credit')
            if debit and credit:
                raise ValueError('Posting cannot debit and credit simultaneously')
            total += debit - credit
            ledger[p['account']] = ledger.get(p['account'], 0.) + debit - credit
        if abs(total) > tolerance:
            raise ValueError('Unbalanced journal entry')
    for key in set(ledger) | set(result['ledger']):
        if not math.isclose(ledger.get(key, 0.), result['ledger'].get(key, 0.), abs_tol=tolerance, rel_tol=1e-10):
            raise ValueError('Ledger differs from journal')
    initial = result['snapshots'][0]['metrics']['total_cash']
    for snapshot in result['snapshots']:
        firms, bank = snapshot['businesses'], snapshot['bank']
        if not math.isclose(bank['equity'], bank['cash'] + bank['loans'], abs_tol=tolerance):
            raise ValueError('Bank equity differs from assets')
        cash = bank['cash'] + snapshot['supplier']['cash'] + snapshot['customers']['cash'] + sum(f['cash'] for f in firms)
        if not math.isclose(cash, initial, abs_tol=tolerance, rel_tol=1e-10):
            raise ValueError('Cash conservation violated')
        if not math.isclose(bank['loans'], sum(f['loan'] for f in firms), abs_tol=tolerance):
            raise ValueError('Loan assets and liabilities differ')
        for firm in firms:
            if not math.isclose(firm['equity'], firm['cash'] + firm['inventory_value'] - firm['loan'], abs_tol=tolerance):
                raise ValueError('Business equity differs from net assets')
            if firm['cash'] < -tolerance or firm['loan'] < -tolerance or firm['loan'] > firm['credit_limit'] + tolerance or not -tolerance <= firm['inventory'] <= firm['storage_capacity'] + tolerance:
                raise ValueError('Business balance or capacity constraint violated')
        if bank['cash'] < -tolerance or not -tolerance <= bank['loans'] <= bank['credit_limit'] + tolerance:
            raise ValueError('Bank balance or credit constraint violated')
    last = result['snapshots'][-1]
    expected = {'bank:cash': last['bank']['cash'], 'bank:loans': last['bank']['loans'],
                'supplier:cash': last['supplier']['cash'], 'customers:cash': last['customers']['cash']}
    for firm in last['businesses']:
        expected.update({firm['id'] + ':cash': firm['cash'], firm['id'] + ':loan': -firm['loan'],
                         firm['id'] + ':inventory': firm['inventory_value']})
        ident = firm['id']
        profit = -ledger.get(ident + ':sales_revenue', 0.) - ledger.get(ident + ':cost_of_goods', 0.) - ledger.get(ident + ':interest_expense', 0.)
        if not math.isclose(firm['profit'], profit, abs_tol=tolerance, rel_tol=1e-10):
            raise ValueError('Business operating profit differs from ledger (debt relief excluded)')
    for key, value in expected.items():
        if not math.isclose(ledger.get(key, 0.), value, abs_tol=tolerance, rel_tol=1e-10):
            raise ValueError('Final state differs from journal: ' + key)
    final_firms = last['businesses']
    final_metrics = {'total_cash': sum(value for key, value in expected.items() if key.endswith(':cash')),
        'total_loans': last['bank']['loans'], 'total_inventory': sum(f['inventory'] for f in final_firms),
        'total_output': sum(f['output'] for f in final_firms), 'total_profit': sum(f['profit'] for f in final_firms),
        'total_interest': sum(f['interest'] for f in final_firms), 'total_energy_spend': sum(f['energy_spend'] for f in final_firms),
        'defaults': sum(f['defaulted'] for f in final_firms), 'stockouts': sum(e['kind'] == 'stockout' for e in result['events']),
        'bank_equity': last['bank']['equity'], 'business_cash': sum(f['cash'] for f in final_firms),
        'insolvent_businesses': sum(f['equity'] < -1e-9 for f in final_firms)}
    if set(result['metrics']) != set(final_metrics) or set(last['metrics']) != set(final_metrics):
        raise ValueError('Final metrics fields differ from accounting metrics')
    for key, value in final_metrics.items():
        for metrics in (last['metrics'], result['metrics']):
            if not math.isclose(metrics[key], value, abs_tol=tolerance, rel_tol=1e-10):
                raise ValueError('Final metrics inconsistent with state: ' + key)
    return {'balanced': True, 'cash_conserved': True, 'loan_assets_equal_liabilities': True, 'entries': len(result['journal'])}


def value_bond(face_value, coupon_rate, annual_yield, years, payments_per_year=2):
    """Fixed-coupon clean price at coupon date; nominal compounded yield."""
    face = _number(face_value, 'face_value')
    coupon = _number(coupon_rate, 'coupon_rate')
    years = _number(years, 'years')
    if face == 0 or years == 0 or isinstance(payments_per_year, bool) or not isinstance(payments_per_year, int) or not 1 <= payments_per_year <= 365:
        raise ValueError('Positive face, maturity and integer frequency required')
    y = _number(annual_yield, 'annual_yield', -payments_per_year)
    count = years * payments_per_year
    if y <= -payments_per_year or not count.is_integer() or count > 100000:
        raise ValueError('Maturity must have finite whole coupon periods and valid yield')
    try:
        flows = [(period / payments_per_year,
                  (face * coupon / payments_per_year + (face if period == int(count) else 0)) /
                  (1 + y / payments_per_year) ** period) for period in range(1, int(count) + 1)]
        price = sum(pv for _, pv in flows)
        duration = sum(time * pv for time, pv in flows) / price
        if not math.isfinite(price) or not math.isfinite(duration):
            raise ValueError('Bond calculation overflow')
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError('Bond calculation outside finite numeric range') from exc
    return {'price': price, 'macaulay_duration_years': duration,
            'modified_duration': duration / (1 + y / payments_per_year),
            'dv01': price * duration / (1 + y / payments_per_year) * .0001}
