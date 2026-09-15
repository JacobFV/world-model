"""Optional synthetic daily mechanisms; no empirically fitted coefficients.

Money and cost allocation use integer cents with half-up rounding. Arrears are
memorandum obligations, not capitalized loan principal or booked bank assets.
"""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import math
from .banking import _money, _units


def obj(value, fields, name):
    if not isinstance(value, dict) or set(value) - set(fields): raise ValueError('Invalid ' + name + ' fields')


def number(value, name, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite in {low}..{high}')
    return value


def count(value, name, low=0):
    if type(value) is not int or not low <= value <= 1000000: raise ValueError(name + ' must be a bounded integer')
    return value


def cents_rounded(value):
    if isinstance(value, Fraction):
        sign = -1 if value < 0 else 1
        return sign * ((2 * abs(value.numerator) + value.denominator) // (2 * value.denominator))
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def validate(mechanisms):
    obj(mechanisms, ('interest', 'inventory_valuation', 'price_feedback', 'demand_feedback'), 'mechanisms')
    if 'inventory_valuation' in mechanisms and type(mechanisms['inventory_valuation']) is not bool:
        raise ValueError('inventory_valuation must be boolean')
    if 'interest' in mechanisms:
        interest = mechanisms['interest']
        obj(interest, ('policy_rate', 'spread', 'day_count', 'duration_days', 'insufficient', 'policy_rule'), 'interest')
        for key in ('policy_rate', 'spread'): number(interest.get(key, 0), key)
        days = interest.get('day_count', 365)
        if type(days) is not int or not 1 <= days <= 366: raise ValueError('day_count must be an integer in 1..366')
        if type(interest.get('duration_days', 1)) is not int or interest.get('duration_days', 1) != 1:
            raise ValueError('Interest duration_days must be exactly 1 for the daily kernel')
        if interest.get('insufficient') not in ('defer', 'bankrupt'):
            raise ValueError('Interest insufficient handling must explicitly be defer or bankrupt')
        if 'policy_rule' in interest:
            rule = interest['policy_rule']
            fields = ('reference_rate', 'inflation_target', 'inflation_response', 'output_response', 'minimum_rate', 'maximum_rate')
            obj(rule, fields, 'policy rule')
            if set(rule) != set(fields): raise ValueError('Policy rule requires all coefficients and bounds')
            for key in ('reference_rate', 'inflation_target', 'minimum_rate', 'maximum_rate'): number(rule[key], key)
            for key in ('inflation_response', 'output_response'): number(rule[key], key, 0, 10)
            if rule['minimum_rate'] > rule['maximum_rate']: raise ValueError('Policy rule bounds reversed')
    if 'price_feedback' in mechanisms:
        price = mechanisms['price_feedback']
        fields = ('initial_price', 'minimum_price', 'maximum_price', 'target_inventory', 'adjustment', 'unmet_demand_response')
        obj(price, fields, 'price feedback')
        if set(price) != set(fields): raise ValueError('Price feedback requires all declared coefficients and bounds')
        for key in ('initial_price', 'minimum_price', 'maximum_price'):
            if _money(price[key], key) <= 0: raise ValueError('Prices must be positive cents')
        if not _money(price['minimum_price'], 'price') <= _money(price['initial_price'], 'price') <= _money(price['maximum_price'], 'price'):
            raise ValueError('Price feedback initial price or bounds invalid')
        count(price['target_inventory'], 'target_inventory')
        for key in ('adjustment', 'unmet_demand_response'): number(price[key], key)
    if 'demand_feedback' in mechanisms:
        demand = mechanisms['demand_feedback']
        obj(demand, ('reference_price', 'elasticity', 'energy_response', 'rate_response'), 'demand feedback')
        if _money(demand.get('reference_price'), 'reference_price') <= 0: raise ValueError('Reference price must be positive')
        number(demand.get('elasticity', 0), 'elasticity', 0, 4)
        for key in ('energy_response', 'rate_response'): number(demand.get(key, 0), key, 0, 4)
    return mechanisms


def initialize_fields(state):
    mechanisms = validate(state.get('mechanisms', {}))
    for firm in state['firms']:
        if mechanisms.get('inventory_valuation'):
            firm.setdefault('inventory_value', _units(firm['inventory'] * _money(firm['unit_cost'], 'unit_cost')))
        if 'interest' in mechanisms: firm.setdefault('interest_arrears', 0)
        if 'price_feedback' in mechanisms:
            firm.setdefault('last_price', mechanisms['price_feedback']['initial_price'])
            firm.setdefault('last_unmet_demand', 0)
    if 'demand_feedback' in mechanisms:
        state.setdefault('expectations', {'energy_change': 0, 'rate_change': 0})


def validate_fields(state):
    mechanisms = validate(state.get('mechanisms', {}))
    for household in state['households']:
        if 'labor_capacity' in household: count(household['labor_capacity'], 'labor_capacity')
    for firm in state['firms']:
        if 'labor_per_unit' in firm: count(firm['labor_per_unit'], 'labor_per_unit', 1)
        for field, enabled in [('inventory_value', mechanisms.get('inventory_valuation')), ('interest_arrears', 'interest' in mechanisms),
                               ('last_price', 'price_feedback' in mechanisms), ('last_unmet_demand', 'price_feedback' in mechanisms)]:
            if (field in firm) != bool(enabled): raise ValueError(field + ' requires its mechanism and initialized state')
        if 'inventory_value' in firm:
            value = _money(firm['inventory_value'], 'inventory_value')
            if not firm['inventory'] and value: raise ValueError('Empty inventory must have zero cost')
        if 'interest_arrears' in firm: _money(firm['interest_arrears'], 'interest_arrears')
        if 'last_price' in firm and _money(firm['last_price'], 'last_price') <= 0: raise ValueError('last_price must be positive')
        if 'last_unmet_demand' in firm: count(firm['last_unmet_demand'], 'last_unmet_demand')
    if 'demand_feedback' in mechanisms:
        expected = state.get('expectations')
        obj(expected, ('energy_change', 'rate_change'), 'expectations')
        if set(expected) != {'energy_change', 'rate_change'}: raise ValueError('Incomplete expectations')
        for key in expected: number(expected[key], key, -1, 1)
    elif 'expectations' in state: raise ValueError('Expectations require demand_feedback')


def apply_rate(mechanisms, policy, shock):
    if 'policy_rate' in policy and 'interest' not in mechanisms: raise ValueError('policy_rate requires interest mechanism')
    if 'interest' not in mechanisms: return None
    interest = mechanisms['interest']
    source = 'persistent'
    if 'policy_rate' in policy:
        interest['policy_rate'] = number(policy['policy_rate'], 'policy_rate'); source = 'direct_policy'
    elif 'policy_rule' in interest:
        if not {'inflation', 'output_gap'} <= set(shock): raise ValueError('Policy rule requires explicit inflation and output_gap')
        rule = interest['policy_rule']
        inflation = number(shock['inflation'], 'inflation', -1, 1)
        output = number(shock['output_gap'], 'output_gap', -1, 1)
        dec = lambda value: Decimal(str(value))
        rate = dec(rule['reference_rate']) + dec(rule['inflation_response']) * (dec(inflation) - dec(rule['inflation_target'])) + dec(rule['output_response']) * dec(output)
        interest['policy_rate'] = float(min(dec(rule['maximum_rate']), max(dec(rule['minimum_rate']), rate))); source = 'bounded_rule'
    return {'policy_rate': interest.get('policy_rate', 0), 'spread': interest.get('spread', 0),
            'annual_loan_rate': interest.get('policy_rate', 0) + interest.get('spread', 0),
            'duration_days': 1, 'day_count': interest.get('day_count', 365), 'source': source}


def interest_due(principal, interest):
    rate = Fraction(str(interest.get('policy_rate', 0))) + Fraction(str(interest.get('spread', 0)))
    return cents_rounded(principal * rate / interest.get('day_count', 365))


def inventory_remove(firm, units):
    """Remove indivisible goods and allocate exact cents of weighted-average cost."""
    if units > firm['inventory']: raise ValueError('Inventory removal exceeds goods')
    allocated = 0
    if 'inventory_value' in firm and units:
        value = _money(firm['inventory_value'], 'inventory_value')
        allocated = min(value, cents_rounded(Decimal(value) * Decimal(units) / Decimal(firm['inventory'])))
        firm['inventory_value'] = _units(value - allocated)
    firm['inventory'] -= units
    return allocated


def price_for(firm, action, mechanisms):
    if 'price' in action: return _money(action['price'], 'price')
    if 'price_feedback' not in mechanisms: return 100
    rule = mechanisms['price_feedback']; target = max(1, rule['target_inventory'])
    fraction = (Fraction(str(rule['adjustment'])) * (rule['target_inventory'] - firm['inventory'])
                + Fraction(str(rule['unmet_demand_response'])) * firm['last_unmet_demand']) / target
    price = cents_rounded(_money(firm['last_price'], 'last_price') * (1 + fraction))
    return min(_money(rule['maximum_price'], 'price'), max(_money(rule['minimum_price'], 'price'), price))


def demand_units(requested, price, mechanisms, expectations):
    if 'demand_feedback' not in mechanisms: return requested
    rule = mechanisms['demand_feedback']
    relative = _money(rule['reference_price'], 'reference_price') / price
    factor = max(0, min(4, 1 - rule.get('energy_response', 0) * expectations['energy_change']
                       - rule.get('rate_response', 0) * expectations['rate_change']))
    return min(1000000, math.floor(requested * relative ** rule.get('elasticity', 0) * factor))
