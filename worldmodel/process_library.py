"""Offline illustrative mechanisms; coefficients are assumptions, not calibration.

Population growth_rate is per second; otherwise growth_rate_per_year is divided
by annual_time_seconds (31557600). Noise is seeded via context['rng']; its
noise_fraction parameter scales per-call variation, so stochastic paths depend on
integration cadence. No network or agent backend is constructed here.
"""
from copy import deepcopy
from .processes import ProcessRegistry, _number

ANNUAL_TIME_SECONDS = 31557600


def _port(kind, unit=None):
    return {'type': kind, 'unit': unit}


def _handler(process_id, output_port, unit, fidelity, outputs):
    def predict(inputs, parameters, context):
        values = {name: item['value'] for name, item in inputs.items()}
        diagnostics = {'illustrative': True, 'validated': False,
                       'limitations': 'Uncalibrated illustrative mechanism; not an empirical forecast.'}
        if fidelity == 'agent':
            request = {'process_id': process_id, 'entity_id': context.get('entity_id'),
                       'inputs': inputs, 'parameters': parameters, 'outputs': outputs,
                       'dt_seconds': context['dt_seconds'], 'time': context.get('time'),
                       'state': context.get('state', {}), 'memory': deepcopy(context.get('memory', {})),
                       'goals': context.get('goals', values.get('goals', parameters.get('goals', []))),
                       'budget': context.get('budget', context.get('remaining_budget'))}
            response = context['agent_backend'].predict(deepcopy(request))
            if not isinstance(response, dict):
                raise ValueError('Agent backend response must be structured dictionary')
            result = deepcopy(response)
            backend_diagnostics = result.get('diagnostics', {})
            if not isinstance(backend_diagnostics, dict):
                raise ValueError('Agent diagnostics must be dictionary')
            result['diagnostics'] = dict(backend_diagnostics, **diagnostics,
                agent_audit={'request': request, 'response': deepcopy(response)})
            return result
        factor = 1
        if fidelity == 'stochastic':
            rng = context.get('rng')
            if rng is None or not callable(getattr(rng, 'gauss', None)):
                raise ValueError('Stochastic implementation requires seeded random.Random context')
            noise = _number(parameters.get('noise_fraction', .1), 'noise_fraction', True)
            factor = max(0, rng.gauss(1, noise))
            diagnostics['stochastic_cadence_dependent'] = True
        mode = 'rate'
        if process_id == 'resource_inventory':
            value = (values['inflow'] - values['outflow']) * factor
        elif process_id == 'investment_cash_flow':
            value = (values['revenue'] - values['expenditure']) * factor
        elif process_id == 'population_growth':
            year = _number(parameters.get('annual_time_seconds', ANNUAL_TIME_SECONDS), 'annual_time_seconds', True)
            if year == 0:
                raise ValueError('annual_time_seconds must be positive')
            rate = _number(parameters.get('growth_rate', parameters.get('growth_rate_per_year', .01) / year), 'growth rate')
            value = values['population'] * rate * factor
        elif process_id == 'movement':
            if len(values['position']) != len(values['velocity']):
                raise ValueError('Position and velocity vector dimensions must match')
            value = [v * factor for v in values['velocity']]
        else:
            mode = 'set'
            options = values['goals']
            if any(not isinstance(option, str) for option in options):
                raise ValueError('Decision goals must contain strings')
            value = values['current_action']
            if values['enabled'] and options:
                value = context['rng'].choice(options) if fidelity == 'stochastic' else options[0]
        return {'pressures': [{'port': output_port, 'mode': mode, 'value': value,
                              'strength': 1, 'confidence': 1, 'unit': unit}],
                'events': [], 'memory': deepcopy(context.get('memory', {})), 'diagnostics': diagnostics}
    return predict


def default_registry():
    registry = ProcessRegistry()
    decision_inputs = {'context': _port('object'), 'goals': _port('array'),
                       'enabled': _port('boolean'), 'current_action': _port('string')}
    definitions = [
        ('resource_inventory', {'inventory': _port('number', 'barrel'), 'inflow': _port('number', 'barrel/second'),
                                'outflow': _port('number', 'barrel/second')}, 'inventory', _port('number', 'barrel'),
         'Inventory balance: inflow minus outflow; no hidden clipping.'),
        ('population_growth', {'population': _port('number', 'people')}, 'population', _port('number', 'people'),
         'Population growth proportional to old state; assumed growth_rate_per_year / 31557600.'),
        ('investment_cash_flow', {'cash': _port('number', 'USD'), 'revenue': _port('number', 'USD/second'),
                                  'expenditure': _port('number', 'USD/second')}, 'cash', _port('number', 'USD'),
         'Investment/business cash balance: revenue minus expenditure.'),
        ('movement', {'position': _port('vector', 'km'), 'velocity': _port('vector', 'km/second')},
         'position', _port('vector', 'km'), 'Euclidean coordinate motion at supplied velocity; not geodetic coordinates.'),
        ('human_decision', decision_inputs, 'action', _port('string'), 'Illustrative human action chosen from declared goals.'),
        ('business_decision', decision_inputs, 'action', _port('string'), 'Illustrative business action chosen from declared goals.'),
    ]
    for process_id, inputs, output, descriptor, description in definitions:
        if process_id in {'resource_inventory', 'population_growth'}:
            descriptor['minimum'] = 0
        outputs = {output: descriptor}
        registry.register_process({'id': process_id, 'inputs': inputs, 'outputs': outputs,
                                   'topology': 'Inputs and output bind explicitly to entities; no inferred causal edges.',
                                   'description': description, 'illustrative': True, 'validated': False})
        for fidelity in ('deterministic', 'stochastic', 'agent'):
            registry.register_implementation({'id': process_id + '.' + fidelity, 'process_id': process_id,
                'fidelity': fidelity, 'max_step_seconds': 86400 if process_id != 'movement' else 3600,
                'cost_per_call': {'deterministic': 1, 'stochastic': 2, 'agent': 10}[fidelity],
                'requires_backend': fidelity == 'agent', 'description': description + ' Illustrative, unvalidated.',
                'parameters': {'annual_time_seconds': ANNUAL_TIME_SECONDS} if process_id == 'population_growth' else {}},
                _handler(process_id, output, descriptor.get('unit'), fidelity, outputs))
    from .economy_processes import register_economy_processes
    from .banking import register_banking_processes
    register_economy_processes(registry)
    register_banking_processes(registry)
    from .fields import register_field_processes
    from .actor_kernels import register_actor_kernels
    register_field_processes(registry)
    register_actor_kernels(registry)
    return registry
