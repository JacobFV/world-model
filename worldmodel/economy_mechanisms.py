"""Optional synthetic daily mechanisms; coefficients are assumed unless bound from estimates.

Money and cost allocation use integer cents with half-up rounding. Arrears are
memorandum obligations, not capitalized loan principal or booked bank assets.

Estimation hooks (worldmodel/estimation/requirements.json, docs/economy-mechanisms.md):
interest pass-through and adjustment speed, deposit interest, policy-rule
smoothing, a seeded default hazard, deposit-growth retention, a credit-limit growth
generator, price cost pass-through and an employment-output elasticity for labor
capacity. Every new key is optional; absent keys leave behavior bit-identical.
All state they need (loan_rate, deposit_rate, hazard, growth, ...) persists inside
``mechanisms`` or explicit actor fields, so the numpy backend shares these
functions and reproduces the reference exactly.
"""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import math
from .banking import _money, _units
from .limits import LimitExceeded, resolve_limits

MONTH_DAYS = 30.4375
QUARTER_DAYS = 91.3125
MASK64 = (1 << 64) - 1
PPB = 10 ** 9
MAX_LIMIT_CENTS = 100000000000000
# Persisted runtime state inside mechanisms; not parameters (see parameter_provenance).
STATE_KEYS = ('loan_rate', 'deposit_rate', 'hazard', 'unemployment', 'growth', 'last_policy_rate', 'log_index', 'seed')


def obj(value, fields, name):
    if not isinstance(value, dict) or set(value) - set(fields): raise ValueError('Invalid ' + name + ' fields')


def number(value, name, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite in {low}..{high}')
    return value


def count(value, name, low=0, limits=None):
    if type(value) is not int or value < low: raise ValueError(name + ' must be a bounded integer')
    maximum = resolve_limits(limits).economy_max_quantity
    if value > maximum: raise LimitExceeded('economy_max_quantity', value, maximum, name + ' must be a bounded integer')
    return value


def cents_rounded(value):
    if isinstance(value, Fraction):
        sign = -1 if value < 0 else 1
        return sign * ((2 * abs(value.numerator) + value.denominator) // (2 * value.denominator))
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _period(rule, key, default):
    return number(rule.get(key, default), key, 1, 366)


def validate(mechanisms):
    obj(mechanisms, ('interest', 'inventory_valuation', 'price_feedback', 'demand_feedback', 'deposit_interest', 'default_hazard',
                     'deposit_growth', 'credit_growth', 'labor'), 'mechanisms')
    if 'inventory_valuation' in mechanisms and type(mechanisms['inventory_valuation']) is not bool:
        raise ValueError('inventory_valuation must be boolean')
    if 'interest' in mechanisms:
        interest = mechanisms['interest']
        obj(interest, ('policy_rate', 'spread', 'day_count', 'duration_days', 'insufficient', 'policy_rule',
                       'pass_through', 'adjustment_speed', 'impact_pass_through', 'loan_rate'), 'interest')
        for key in ('policy_rate', 'spread'): number(interest.get(key, 0), key)
        days = interest.get('day_count', 365)
        if type(days) is not int or not 1 <= days <= 366: raise ValueError('day_count must be an integer in 1..366')
        if type(interest.get('duration_days', 1)) is not int or interest.get('duration_days', 1) != 1:
            raise ValueError('Interest duration_days must be exactly 1 for the daily kernel')
        if interest.get('insufficient') not in ('defer', 'bankrupt'):
            raise ValueError('Interest insufficient handling must explicitly be defer or bankrupt')
        if 'pass_through' in interest: number(interest['pass_through'], 'pass_through', 0, 1.5)
        if 'adjustment_speed' in interest: number(interest['adjustment_speed'], 'adjustment_speed', 0, 1)
        if 'impact_pass_through' in interest:
            number(interest['impact_pass_through'], 'impact_pass_through', -0.5, 1.5)
            if not {'pass_through', 'adjustment_speed'} & set(interest): raise ValueError('impact_pass_through requires pass_through or adjustment_speed')
        if 'loan_rate' in interest:
            number(interest['loan_rate'], 'loan_rate', 0, 3)
            if not {'pass_through', 'adjustment_speed'} & set(interest): raise ValueError('loan_rate state requires pass_through or adjustment_speed')
        if 'policy_rule' in interest:
            rule = interest['policy_rule']
            fields = ('reference_rate', 'inflation_target', 'inflation_response', 'output_response', 'minimum_rate', 'maximum_rate')
            obj(rule, fields + ('smoothing', 'period_days'), 'policy rule')
            if not set(fields) <= set(rule): raise ValueError('Policy rule requires all coefficients and bounds')
            for key in ('reference_rate', 'inflation_target', 'minimum_rate', 'maximum_rate'): number(rule[key], key)
            for key in ('inflation_response', 'output_response'): number(rule[key], key, 0, 10)
            if rule['minimum_rate'] > rule['maximum_rate']: raise ValueError('Policy rule bounds reversed')
            if 'smoothing' in rule: number(rule['smoothing'], 'smoothing', 0, 0.999)
            if 'period_days' in rule:
                if 'smoothing' not in rule: raise ValueError('period_days requires policy rule smoothing')
                _period(rule, 'period_days', QUARTER_DAYS)
    if 'price_feedback' in mechanisms:
        price = mechanisms['price_feedback']
        fields = ('initial_price', 'minimum_price', 'maximum_price', 'target_inventory', 'adjustment', 'unmet_demand_response')
        obj(price, fields + ('cost_pass_through',), 'price feedback')
        if set(price) - {'cost_pass_through'} != set(fields): raise ValueError('Price feedback requires all declared coefficients and bounds')
        for key in ('initial_price', 'minimum_price', 'maximum_price'):
            if _money(price[key], key) <= 0: raise ValueError('Prices must be positive cents')
        if not _money(price['minimum_price'], 'price') <= _money(price['initial_price'], 'price') <= _money(price['maximum_price'], 'price'):
            raise ValueError('Price feedback initial price or bounds invalid')
        count(price['target_inventory'], 'target_inventory')
        for key in ('adjustment', 'unmet_demand_response'): number(price[key], key)
        if 'cost_pass_through' in price: number(price['cost_pass_through'], 'cost_pass_through', 0, 1.5)
    if 'demand_feedback' in mechanisms:
        demand = mechanisms['demand_feedback']
        obj(demand, ('reference_price', 'elasticity', 'energy_response', 'rate_response'), 'demand feedback')
        if _money(demand.get('reference_price'), 'reference_price') <= 0: raise ValueError('Reference price must be positive')
        number(demand.get('elasticity', 0), 'elasticity', 0, 4)
        for key in ('energy_response', 'rate_response'): number(demand.get(key, 0), key, 0, 4)
    if 'deposit_interest' in mechanisms:
        deposit = mechanisms['deposit_interest']
        obj(deposit, ('spread', 'pass_through', 'adjustment_speed', 'impact_pass_through', 'day_count', 'deposit_rate'), 'deposit interest')
        if 'interest' not in mechanisms: raise ValueError('deposit_interest requires the interest mechanism (policy rate)')
        if not {'spread', 'pass_through'} <= set(deposit): raise ValueError('deposit_interest requires spread and pass_through')
        number(deposit['spread'], 'deposit spread', -1, 1)
        number(deposit['pass_through'], 'deposit pass_through', 0, 1.5)
        number(deposit.get('adjustment_speed', 1), 'deposit adjustment_speed', 0, 1)
        number(deposit.get('impact_pass_through', 0), 'deposit impact_pass_through', -0.5, 1.5)
        number(deposit.get('deposit_rate', 0), 'deposit_rate', 0, 3)
        days = deposit.get('day_count', 365)
        if type(days) is not int or not 1 <= days <= 366: raise ValueError('day_count must be an integer in 1..366')
    if 'default_hazard' in mechanisms:
        hazard = mechanisms['default_hazard']
        required = ('intercept', 'persistence', 'unemployment_sensitivity', 'rate_sensitivity', 'seed', 'hazard', 'unemployment')
        obj(hazard, required + ('period_days',), 'default hazard')
        if not set(required) <= set(hazard): raise ValueError('default_hazard requires coefficients, seed, hazard and unemployment')
        number(hazard['intercept'], 'intercept', -20, 20)
        number(hazard['persistence'], 'persistence', 0, 1)
        for key in ('unemployment_sensitivity', 'rate_sensitivity'): number(hazard[key], key, -100, 100)
        if type(hazard['seed']) is not int or not 0 <= hazard['seed'] <= MASK64: raise ValueError('default_hazard seed must be an unsigned 64-bit integer')
        if number(hazard['hazard'], 'hazard') in (0, 1): raise ValueError('hazard must be strictly between 0 and 1')
        number(hazard['unemployment'], 'unemployment')
        _period(hazard, 'period_days', QUARTER_DAYS)
    for name, extra in (('deposit_growth', ('rate_semi_elasticity', 'last_policy_rate')), ('credit_growth', ('base_credit_limit', 'log_index'))):
        if name not in mechanisms: continue
        rule = mechanisms[name]
        required = ('mean_growth_per_month', 'persistence', extra[0])
        obj(rule, required + ('growth', 'period_days', extra[1]), name.replace('_', ' '))
        if not set(required) <= set(rule): raise ValueError(f'{name} requires {", ".join(required)}')
        number(rule['mean_growth_per_month'], 'mean_growth_per_month', -1, 1)
        number(rule['persistence'], 'persistence', -1, 1)
        number(rule.get('growth', 0), 'growth', -1, 1)
        _period(rule, 'period_days', MONTH_DAYS)
        if name == 'deposit_growth':
            number(rule['rate_semi_elasticity'], 'rate_semi_elasticity', -50, 50)
            number(rule.get('last_policy_rate', 0), 'last_policy_rate')
        else:
            if _money(rule['base_credit_limit'], 'base_credit_limit') < 0: raise ValueError('base_credit_limit must be nonnegative')
            number(rule.get('log_index', 0), 'log_index', -50, 50)
    if 'labor' in mechanisms:
        labor = mechanisms['labor']
        obj(labor, ('employment_output_elasticity',), 'labor')
        if 'employment_output_elasticity' not in labor: raise ValueError('labor mechanism requires employment_output_elasticity')
        number(labor['employment_output_elasticity'], 'employment_output_elasticity', 0, 2)
    return mechanisms


def initialize_fields(state):
    mechanisms = validate(state.get('mechanisms', {}))
    cost_pass_through = 'cost_pass_through' in mechanisms.get('price_feedback', {})
    for firm in state['firms']:
        if mechanisms.get('inventory_valuation'):
            firm.setdefault('inventory_value', _units(firm['inventory'] * _money(firm['unit_cost'], 'unit_cost')))
        if 'interest' in mechanisms: firm.setdefault('interest_arrears', 0)
        if 'price_feedback' in mechanisms:
            firm.setdefault('last_price', mechanisms['price_feedback']['initial_price'])
            firm.setdefault('last_unmet_demand', 0)
        if cost_pass_through: firm.setdefault('last_unit_cost', firm['unit_cost'])
    if 'labor' in mechanisms:
        for household in state['households']: household.setdefault('labor_output', 0)
    if 'demand_feedback' in mechanisms:
        state.setdefault('expectations', {'energy_change': 0, 'rate_change': 0})


def validate_fields(state, limits=None):
    limits = resolve_limits(limits)
    mechanisms = validate(state.get('mechanisms', {}))
    labor = 'labor' in mechanisms
    for household in state['households']:
        if 'labor_capacity' in household: count(household['labor_capacity'], 'labor_capacity', limits=limits)
        if ('labor_output' in household) != labor: raise ValueError('labor_output requires the labor mechanism and initialized state')
        if labor: count(household['labor_output'], 'labor_output', limits=limits)
    cost_pass_through = 'cost_pass_through' in mechanisms.get('price_feedback', {})
    for firm in state['firms']:
        if 'labor_per_unit' in firm: count(firm['labor_per_unit'], 'labor_per_unit', 1, limits)
        for field, enabled in [('inventory_value', mechanisms.get('inventory_valuation')), ('interest_arrears', 'interest' in mechanisms),
                               ('last_price', 'price_feedback' in mechanisms), ('last_unmet_demand', 'price_feedback' in mechanisms),
                               ('last_unit_cost', cost_pass_through)]:
            if (field in firm) != bool(enabled): raise ValueError(field + ' requires its mechanism and initialized state')
        if 'inventory_value' in firm:
            value = _money(firm['inventory_value'], 'inventory_value')
            if not firm['inventory'] and value: raise ValueError('Empty inventory must have zero cost')
        if 'interest_arrears' in firm: _money(firm['interest_arrears'], 'interest_arrears')
        if 'last_price' in firm and _money(firm['last_price'], 'last_price') <= 0: raise ValueError('last_price must be positive')
        if 'last_unmet_demand' in firm: count(firm['last_unmet_demand'], 'last_unmet_demand', limits=limits)
        if 'last_unit_cost' in firm and _money(firm['last_unit_cost'], 'last_unit_cost') <= 0: raise ValueError('last_unit_cost must be positive')
    if 'demand_feedback' in mechanisms:
        expected = state.get('expectations')
        obj(expected, ('energy_change', 'rate_change'), 'expectations')
        if set(expected) != {'energy_change', 'rate_change'}: raise ValueError('Incomplete expectations')
        for key in expected: number(expected[key], key, -1, 1)
    elif 'expectations' in state: raise ValueError('Expectations require demand_feedback')


def _dynamic_loan_rate(interest):
    return 'pass_through' in interest or 'adjustment_speed' in interest


def _adjust(previous, target, speed, impact=0, policy_change=Fraction(0)):
    """ECM daily step in exact rationals: pre = previous + impact*policy_change; pre + speed*(target - pre)."""
    if previous is None:
        return float(target)
    previous = Fraction(str(previous)) + Fraction(str(impact)) * policy_change
    return float(previous + Fraction(str(speed)) * (target - previous))


def apply_rate(mechanisms, policy, shock):
    if 'policy_rate' in policy and 'interest' not in mechanisms: raise ValueError('policy_rate requires interest mechanism')
    if 'interest' not in mechanisms: return None
    interest = mechanisms['interest']
    source = 'persistent'
    opening_policy_rate = interest.get('policy_rate', 0)
    if 'policy_rate' in policy:
        interest['policy_rate'] = number(policy['policy_rate'], 'policy_rate'); source = 'direct_policy'
    elif 'policy_rule' in interest:
        if not {'inflation', 'output_gap'} <= set(shock): raise ValueError('Policy rule requires explicit inflation and output_gap')
        rule = interest['policy_rule']
        inflation = number(shock['inflation'], 'inflation', -1, 1)
        output = number(shock['output_gap'], 'output_gap', -1, 1)
        dec = lambda value: Decimal(str(value))
        rate = dec(rule['reference_rate']) + dec(rule['inflation_response']) * (dec(inflation) - dec(rule['inflation_target'])) + dec(rule['output_response']) * dec(output)
        if 'smoothing' in rule:
            # Estimated at the rule's cadence (quarterly by default); daily weight rho**(1/period_days).
            rho = dec(rule['smoothing'] ** (1 / rule.get('period_days', QUARTER_DAYS)))
            rate = rho * dec(interest.get('policy_rate', 0)) + (1 - rho) * rate
            source = 'smoothed_bounded_rule'
        else:
            source = 'bounded_rule'
        interest['policy_rate'] = float(min(dec(rule['maximum_rate']), max(dec(rule['minimum_rate']), rate)))
    audit = {'policy_rate': interest.get('policy_rate', 0), 'spread': interest.get('spread', 0),
             'annual_loan_rate': interest.get('policy_rate', 0) + interest.get('spread', 0),
             'duration_days': 1, 'day_count': interest.get('day_count', 365), 'source': source}
    if _dynamic_loan_rate(interest):
        target = Fraction(str(interest.get('spread', 0))) + Fraction(str(interest.get('pass_through', 1))) * Fraction(str(interest.get('policy_rate', 0)))
        change = Fraction(str(interest.get('policy_rate', 0))) - Fraction(str(opening_policy_rate))
        interest['loan_rate'] = min(3.0, max(0.0, _adjust(interest.get('loan_rate'), target, interest.get('adjustment_speed', 1),
                                                          interest.get('impact_pass_through', 0), change)))
        audit.update(annual_loan_rate=interest['loan_rate'], long_run_loan_rate=float(target),
                     pass_through=interest.get('pass_through', 1), adjustment_speed=interest.get('adjustment_speed', 1))
        if 'impact_pass_through' in interest: audit['impact_pass_through'] = interest['impact_pass_through']
    if 'deposit_interest' in mechanisms:
        deposit = mechanisms['deposit_interest']
        target = max(Fraction(0), Fraction(str(deposit['spread'])) + Fraction(str(deposit['pass_through'])) * Fraction(str(interest.get('policy_rate', 0))))
        change = Fraction(str(interest.get('policy_rate', 0))) - Fraction(str(opening_policy_rate))
        deposit['deposit_rate'] = min(3.0, max(0.0, _adjust(deposit.get('deposit_rate'), target, deposit.get('adjustment_speed', 1),
                                                            deposit.get('impact_pass_through', 0), change)))
        audit.update(deposit_rate=deposit['deposit_rate'], deposit_day_count=deposit.get('day_count', 365))
    return audit


def annual_loan_rate(interest):
    """Exact annual loan rate: dynamic loan_rate state when pass-through is configured, else policy_rate + spread."""
    if _dynamic_loan_rate(interest) and 'loan_rate' in interest:
        return Fraction(str(interest['loan_rate']))
    return Fraction(str(interest.get('policy_rate', 0))) + Fraction(str(interest.get('spread', 0)))


def interest_due(principal, interest):
    rate = annual_loan_rate(interest)
    return cents_rounded(principal * rate / interest.get('day_count', 365))


def deposit_rate(mechanisms):
    return Fraction(str(mechanisms['deposit_interest'].get('deposit_rate', 0)))


def deposit_interest_due(balance, mechanisms):
    return cents_rounded(balance * deposit_rate(mechanisms) / mechanisms['deposit_interest'].get('day_count', 365))


def period_boundary(step, period_days):
    """True on the first step (1-based) of a new period after the first period."""
    return step > 1 and math.floor((step - 1) / period_days) != math.floor((step - 2) / period_days)


# ----------------------------------------------------------------- default hazard
def _mix(value):
    value = (value + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return value ^ (value >> 31)


def actor_key(actor_id):
    return int.from_bytes(hashlib.sha256(actor_id.encode('utf-8')).digest()[:8], 'little')


def step_key(seed, step):
    return _mix(_mix(seed) ^ step)


def draw53(key, actor_id):
    """Counter-based uniform integer in [0, 2**53): order-independent, identical in the numpy backend."""
    return _mix(key ^ actor_key(actor_id)) >> 11


def _logistic(eta):
    return 1 / (1 + math.exp(-eta)) if eta >= 0 else math.exp(eta) / (1 + math.exp(eta))


def update_default_hazard(mechanisms, shock, step, previous_policy_rate):
    """Advance the period hazard at period boundaries; return the audit with the daily draw threshold.

    h_t = logistic(intercept + persistence*logit(h_{t-1}) + unemployment_sensitivity*u_t
    + rate_sensitivity*i_{t-1}); daily probability 1-(1-h)**(1/period_days).
    """
    rule = mechanisms['default_hazard']
    if 'unemployment' in shock: rule['unemployment'] = shock['unemployment']
    period = rule.get('period_days', QUARTER_DAYS)
    updated = period_boundary(step, period)
    if updated:
        h = rule['hazard']
        eta = (rule['intercept'] + rule['persistence'] * math.log(h / (1 - h)) + rule['unemployment_sensitivity'] * rule['unemployment']
               + rule['rate_sensitivity'] * previous_policy_rate)
        rule['hazard'] = min(1 - 1e-12, max(1e-12, _logistic(eta)))
    probability = 1 - (1 - rule['hazard']) ** (1 / period)
    return {'hazard': rule['hazard'], 'daily_probability': probability, 'threshold': int(probability * 2 ** 53),
            'unemployment': rule['unemployment'], 'lagged_policy_rate': previous_policy_rate, 'updated': updated}


# ------------------------------------------------------- deposit and credit growth
def _growth(rule, step, change=0.0):
    period = rule.get('period_days', MONTH_DAYS)
    growth = rule.get('growth', rule['mean_growth_per_month'])
    updated = period_boundary(step, period)
    if updated:
        growth = min(1.0, max(-1.0, rule['mean_growth_per_month'] * (1 - rule['persistence']) + rule['persistence'] * growth + change))
    rule['growth'] = growth
    return growth, period, updated


def update_deposit_growth(mechanisms, step):
    """Monthly deposit growth target g = mean*(1-rho) + rho*g_prev + semi*(policy_rate change); daily factor in ppb."""
    rule = mechanisms['deposit_growth']
    policy = mechanisms['interest'].get('policy_rate', 0) if 'interest' in mechanisms else 0
    last = rule.get('last_policy_rate', policy)
    growth, period, updated = _growth(rule, step, rule['rate_semi_elasticity'] * (policy - last) if period_boundary(step, rule.get('period_days', MONTH_DAYS)) else 0.0)
    if updated: last = policy
    rule['last_policy_rate'] = last
    return {'growth_per_month': growth, 'daily_factor_ppb': round(math.exp(growth / period) * PPB), 'updated': updated}


def retained_deposit(opening_cents, factor_ppb):
    """round_half_up(opening * factor_ppb / 1e9) without int64 overflow (same formula in numpy)."""
    quotient, remainder = divmod(opening_cents, PPB)
    return quotient * factor_ppb + (remainder * factor_ppb + PPB // 2) // PPB


def update_credit_growth(mechanisms, step):
    """Scenario credit-limit generator: limit = base * exp(log_index); log_index += g/period_days daily."""
    rule = mechanisms['credit_growth']
    growth, period, updated = _growth(rule, step)
    index = rule.get('log_index', 0.0)
    limit = min(MAX_LIMIT_CENTS, cents_rounded(Fraction(_money(rule['base_credit_limit'], 'base_credit_limit')) * Fraction(math.exp(index))))
    rule['log_index'] = min(50.0, max(-50.0, index + growth / period))
    return {'growth_per_month': growth, 'log_index': index, 'credit_limit': _units(limit), 'updated': updated}, limit


# ------------------------------------------------------------------ labor capacity
def labor_capacity_update(capacity, previous_output, output, elasticity, maximum):
    """Scale capacity by (output/previous_output)**elasticity; unchanged without positive outputs."""
    if capacity is None or previous_output <= 0 or output <= 0 or output == previous_output:
        return capacity
    return min(maximum, math.floor(capacity * (output / previous_output) ** elasticity + 0.5))


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
    if 'cost_pass_through' in rule:
        last_cost = _money(firm['last_unit_cost'], 'last_unit_cost')
        fraction += Fraction(str(rule['cost_pass_through'])) * (_money(firm['unit_cost'], 'unit_cost') - last_cost) / last_cost
    price = cents_rounded(_money(firm['last_price'], 'last_price') * (1 + fraction))
    return min(_money(rule['maximum_price'], 'price'), max(_money(rule['minimum_price'], 'price'), price))


def demand_units(requested, price, mechanisms, expectations, limits=None):
    if 'demand_feedback' not in mechanisms: return requested
    rule = mechanisms['demand_feedback']
    relative = _money(rule['reference_price'], 'reference_price') / price
    factor = max(0, min(4, 1 - rule.get('energy_response', 0) * expectations['energy_change']
                       - rule.get('rate_response', 0) * expectations['rate_change']))
    return min(resolve_limits(limits).economy_max_quantity, math.floor(requested * relative ** rule.get('elasticity', 0) * factor))


# ---------------------------------------------------------------- calibration
def calibrate_mechanisms(mechanisms, calibration, previous=None):
    """Bind estimated ``mechanisms.*`` process parameters into a copy; returns (mechanisms, bindings record)."""
    from .estimation.binding import apply_bindings, parameter_bindings, SCHEMA
    bound, applied = apply_bindings({'mechanisms': mechanisms}, parameter_bindings(calibration), only=lambda path: path.startswith('mechanisms.'))
    if previous:
        merged = dict(previous.get('bindings', {}))
        merged.update(applied['bindings'])
        applied = {'schema': SCHEMA, 'bindings': dict(sorted(merged.items())),
                   'unbound': sorted(set(previous.get('unbound', [])) - set(merged) | set(applied['unbound']))}
    return bound['mechanisms'], applied


def parameter_provenance(state):
    """Every numeric mechanism parameter as estimated (with record/estimate ids) or assumed."""
    from .estimation.binding import numeric_leaves, parameter_provenance as provenance
    leaves = numeric_leaves(state.get('mechanisms', {}), 'mechanisms', exclude=STATE_KEYS)
    return provenance(leaves, state.get('calibration'))
