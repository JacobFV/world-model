"""Daily economy adapter. Replay makes state transparent and reproducible."""
from copy import deepcopy
from .economy import simulate_economy, parameter_provenance
from .limits import resolve_limits


def estimate_process_work(state, calls, *, limits=None):
    """Preflight replay effort, including all days recomputed by every call."""
    limits = resolve_limits(limits)
    step = state.get('step', 0)
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (step, calls)):
        raise ValueError('Replay step and calls must be nonnegative integers')
    firms = state['config']['businesses']
    if not isinstance(firms, list) or not firms:
        raise ValueError('Replay requires at least one business')
    limits.check('economy_max_businesses', len(firms), 'Replay requires a bounded number of businesses')
    work = len(firms) * (calls * step + calls * (calls + 1) // 2)
    cumulative = len(firms) * (step + calls) * (step + calls + 1) // 2
    limits.check('economy_max_days', step + calls, 'Economy cumulative replay days')
    limits.check('economy_max_replay_firm_days', cumulative, 'Economy cumulative replay work exceeds firm-days')
    return {'firm_days': work, 'cumulative_firm_days': cumulative, 'max_firm_days': limits.economy_max_replay_firm_days}


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
    result = simulate_economy(config, calibration=parameters.get('calibration'))
    value = {'config': state['config'], 'step': step + 1, 'snapshot': result['snapshots'][-1]}
    provenance = result.get('parameter_provenance') or parameter_provenance(config)
    return {'pressures': [{'port': 'economy_state', 'mode': 'set', 'value': value,
                          'strength': 1, 'confidence': 1, 'unit': None}],
            'events': [event for event in result['events'] if event['step'] == step + 1],
            'diagnostics': {'illustrative': True, 'validated': False, 'accounting': result['accounting'],
                            'replay_days': step + 1, 'work': work, 'parameter_provenance': provenance,
                            **({'calibration': result['calibration']} if 'calibration' in result else {}),
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
        'max_work': resolve_limits().economy_max_replay_firm_days, 'work_unit': 'firm_days',
        'description': 'One daily transition replayed from initial scenario; cumulative replay bounded by limit economy_max_replay_firm_days.'}, _predict)
    return registry
