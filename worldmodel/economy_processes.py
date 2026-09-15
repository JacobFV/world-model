"""Daily economy adapter. Replay makes state transparent and reproducible."""
from copy import deepcopy
from .economy import simulate_economy


def estimate_process_work(state, calls):
    """Preflight replay effort, including all days recomputed by every call."""
    step = state.get('step', 0)
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (step, calls)):
        raise ValueError('Replay step and calls must be nonnegative integers')
    firms = state['config']['businesses']
    if not isinstance(firms, list) or not 1 <= len(firms) <= 1000:
        raise ValueError('Replay requires 1..1000 businesses')
    work = len(firms) * (calls * step + calls * (calls + 1) // 2)
    cumulative = len(firms) * (step + calls) * (step + calls + 1) // 2
    if step + calls > 10000 or cumulative > 100000:
        raise ValueError('Economy cumulative replay work exceeds 100000 firm-days or 10000 days')
    return {'firm_days': work, 'cumulative_firm_days': cumulative, 'max_firm_days': 100000}


def _predict(inputs, parameters, context):
    if context['dt_seconds'] != 86400:
        raise ValueError('Economy process requires exactly one daily cadence (86400 seconds)')
    state = deepcopy(inputs['economy_state']['value'])
    work = estimate_process_work(state, 1)
    step = state.get('step', 0)
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ValueError('economy_state.step must be a nonnegative integer')
    config = deepcopy(state['config'])
    config['days'] = step + 1
    result = simulate_economy(config)
    value = {'config': state['config'], 'step': step + 1, 'snapshot': result['snapshots'][-1]}
    return {'pressures': [{'port': 'economy_state', 'mode': 'set', 'value': value,
                          'strength': 1, 'confidence': 1, 'unit': None}],
            'events': [event for event in result['events'] if event['step'] == step + 1],
            'diagnostics': {'illustrative': True, 'validated': False, 'accounting': result['accounting'],
                            'replay_days': step + 1, 'work': work,
                            'limitation': 'Replays explicit initial config; snapshot is derived, not an editable state override.'}}


def register_economy_processes(registry):
    port = {'type': 'object', 'unit': None}
    registry.register_process({'id': 'bank_energy_business', 'inputs': {'economy_state': port},
        'outputs': {'economy_state': port}, 'topology': 'Explicit closed economy object; cash-funded lender, supplier, businesses and customers.',
        'description': 'Daily double-entry energy inventory and floating-rate credit scenario; uncalibrated.',
        'illustrative': True, 'validated': False})
    registry.register_implementation({'id': 'bank_energy_business.deterministic', 'process_id': 'bank_energy_business',
        'fidelity': 'deterministic', 'min_step_seconds': 86400, 'max_step_seconds': 86400,
        'cost_per_call': 1, 'output_timing': 'end_of_step',
        'work_estimator': 'worldmodel.economy_processes:estimate_process_work', 'work_input': 'economy_state',
        'max_work': 100000, 'work_unit': 'firm_days',
        'description': 'One daily transition replayed from initial scenario; cumulative replay bounded to 100000 firm-days.'}, _predict)
    return registry
