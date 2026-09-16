"""Estimable political, geopolitical and market model families.

Every family module exposes ``FAMILY`` (parameters with units, public data
``requirements``, identification label, ``validated: False``), ``fit(data, cutoff)``
returning ``{estimate, diagnostics, evidence}``, ``simulate(config, mode, seed)``
with deterministic and stochastic modes, and ``synthetic(seed)`` fixtures whose
true parameters are known. ``register_model_processes(registry)`` exposes each
family to the process registry/materializer as typed object ports, plus three
numeric processes that integrate through ordinary pressures.
"""
from copy import deepcopy
import importlib
import math

FAMILY_MODULES = {
    'legislative': 'worldmodel.models.legislative',
    'elections': 'worldmodel.models.elections',
    'influence': 'worldmodel.models.influence',
    'trade': 'worldmodel.models.trade',
    'sanctions': 'worldmodel.models.sanctions',
    'conflict': 'worldmodel.models.conflict',
    'assets': 'worldmodel.models.assets',
    'market_abm': 'worldmodel.models.market_abm',
    'commodities': 'worldmodel.models.commodities',
    'monetary': 'worldmodel.models.monetary',
    'regional': 'worldmodel.models.regional',
}
MODES = ('deterministic', 'stochastic')
YEAR_SECONDS = 366 * 86400
QUARTER_SECONDS = 7889400  # 365.25 / 4 days
MONTH_SECONDS = 2629800    # 365.25 / 12 days


def family_module(family_id):
    if family_id not in FAMILY_MODULES:
        raise ValueError(f'Unknown model family: {family_id}; choose from {sorted(FAMILY_MODULES)}')
    return importlib.import_module(FAMILY_MODULES[family_id])


def families():
    return [deepcopy(family_module(name).FAMILY) for name in FAMILY_MODULES]


def describe(family_id):
    module = family_module(family_id)
    return {**deepcopy(module.FAMILY), 'modes': list(MODES), 'fit_signature': 'fit(data, cutoff=None) -> {estimate, diagnostics, evidence}',
            'process_id': f'{family_id}_model', 'ports': _ports(family_id)}


def requirements(family_id=None):
    names = [family_id] if family_id else list(FAMILY_MODULES)
    return {name: deepcopy(family_module(name).FAMILY['requirements']) for name in names}


def parameter_hooks(family_id):
    """Parameters (value, unit, source, bounds) and requirements, for the estimation layer."""
    family = family_module(family_id).FAMILY
    return {'family': family_id, 'parameters': deepcopy(family['parameters']), 'requirements': deepcopy(family['requirements']),
            'identification': family['identification'], 'validated': False}


def fit(family_id, data, cutoff=None):
    return family_module(family_id).fit(data, cutoff)


def simulate(family_id, config, mode='deterministic', seed=0):
    if mode not in MODES:
        raise ValueError('mode must be deterministic or stochastic')
    return family_module(family_id).simulate(config, mode, seed)


def synthetic(family_id, seed=0):
    return family_module(family_id).synthetic(seed)


def config_port(family_id):
    return f'{family_id}_config'


def forecast_port(family_id):
    return f'{family_id}_forecast'


def _ports(family_id):
    """Family-prefixed object ports (as ``composition_config``): one shared name cannot carry eleven units."""
    return {'inputs': {config_port(family_id): {'type': 'object', 'unit': f'{family_id}-config'}},
            'outputs': {forecast_port(family_id): {'type': 'object', 'unit': f'{family_id}-forecast'}}}


def _family_handler(family_id, mode):
    def predict(inputs, parameters, context):
        config = deepcopy(inputs[config_port(family_id)]['value'])
        if parameters:
            config['parameters'] = {**config.get('parameters', {}), **parameters}
        seed = 0
        if mode == 'stochastic':
            rng = context.get('rng')
            if rng is None or not callable(getattr(rng, 'getrandbits', None)):
                raise ValueError('Stochastic model implementation requires a seeded random.Random context')
            seed = rng.getrandbits(62)
        report = simulate(family_id, config, mode, seed)
        return {'pressures': [{'port': forecast_port(family_id), 'mode': 'set', 'value': report, 'unit': f'{family_id}-forecast', 'strength': 1, 'confidence': 1}],
                'events': [], 'memory': {}, 'diagnostics': {'family': family_id, 'mode': mode, 'seed': seed, 'validated': False,
                                                           'identification': family_module(family_id).FAMILY['identification']}}
    return predict


def _number(inputs, name):
    value = inputs[name]['value']
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite numeric')
    return value


def _taylor_handler(mode):
    def predict(inputs, parameters, context):
        from .monetary import FAMILY, policy_target
        from .base import merged_parameters
        params = merged_parameters(FAMILY, {k: v for k, v in parameters.items() if k in FAMILY['parameters']})
        target = max(params['elb'], policy_target(params, _number(inputs, 'inflation'), _number(inputs, 'output_gap')))
        rho = params['rho']
        if not 0 < rho < 1:
            raise ValueError('Continuous Taylor relaxation requires 0 < rho < 1 per quarter')
        diagnostics = {'desired_rate': target, 'validated': False, 'relaxation_per_second': -math.log(rho) / QUARTER_SECONDS}
        if mode == 'stochastic':
            dt = context['dt_seconds']
            target = max(params['elb'], target + context['rng'].gauss(0, params['policy_shock_sd'] * math.sqrt(QUARTER_SECONDS / dt) * (1 - rho)))
            diagnostics['shock_scaling'] = 'target shock sd scaled so quarterly rate innovations match policy_shock_sd'
        return {'pressures': [{'port': 'policy_rate', 'mode': 'target', 'value': target, 'unit': 'percent',
                               'strength': -math.log(rho) / QUARTER_SECONDS, 'confidence': 1}], 'diagnostics': diagnostics}
    return predict


def _migration_handler(mode):
    def predict(inputs, parameters, context):
        state = context.get('state', {})
        pa, pb = state.get('population_a'), state.get('population_b')
        if not all(isinstance(v, (int, float)) and v >= 0 for v in (pa, pb)):
            raise ValueError('Two-region migration requires nonnegative current populations in state')
        delta = parameters.get('outmigration_employment_rate_elasticity', -1.0)
        gap = _number(inputs, 'employment_rate_a') - _number(inputs, 'employment_rate_b')
        rate_a = _number(inputs, 'base_outmigration_rate_a') * math.exp(delta * gap / 2) / (365.25 * 86400)
        rate_b = _number(inputs, 'base_outmigration_rate_b') * math.exp(-delta * gap / 2) / (365.25 * 86400)
        flow = pb * rate_b - pa * rate_a
        if mode == 'stochastic':
            flow *= max(0.0, context['rng'].gauss(1, parameters.get('noise_fraction', 0.1)))
        return {'pressures': [{'port': 'population_a', 'mode': 'rate', 'value': flow, 'unit': 'people', 'strength': 1, 'confidence': 1},
                              {'port': 'population_b', 'mode': 'rate', 'value': -flow, 'unit': 'people', 'strength': 1, 'confidence': 1}],
                'diagnostics': {'net_flow_people_per_second_to_a': flow, 'validated': False,
                                'approximation': 'Gross flows computed from start-of-step populations and held constant over the step.'}}
    return predict


def _conflict_handler(mode):
    def predict(inputs, parameters, context):
        es = parameters.get('self_excitation', 0.3)
        en = parameters.get('neighbor_excitation', 0.1)
        beta = parameters.get('decay', 0.5)
        if not (0 <= es < 1 and 0 <= en and 0 <= beta < 1):
            raise ValueError('Conflict intensity requires 0 <= self_excitation < 1, neighbor_excitation >= 0 and 0 <= decay < 1')
        background = _number(inputs, 'background_rate')
        neighbor = _number(inputs, 'neighbor_intensity')
        target = (background + en * neighbor) / (1 - es)
        if mode == 'stochastic':
            target = max(0.0, target * context['rng'].gammavariate(10, 0.1))
        strength = (1 - es) * (1 - beta) / MONTH_SECONDS
        return {'pressures': [{'port': 'intensity', 'mode': 'target', 'value': target, 'unit': 'events/month', 'strength': strength, 'confidence': 1}],
                'diagnostics': {'stationary_intensity': target, 'validated': False,
                                'approximation': 'Continuous mean-field relaxation of the discrete geometric Hawkes kernel.'}}
    return predict


def register_model_processes(registry):
    """Register every family plus numeric coupling processes; idempotence is the caller's responsibility."""
    for family_id in FAMILY_MODULES:
        family = family_module(family_id).FAMILY
        ports = _ports(family_id)
        registry.register_process({'id': f'{family_id}_model', **ports, 'validated': False,
                                   'topology': f'Explicit {family_id} configuration object -> forecast report; no inferred edges.',
                                   'description': family['description'], 'identification': family['identification'],
                                   'parameters': {k: {'unit': v['unit'], 'source': v['source']} for k, v in family['parameters'].items()},
                                   'requirements': [r['id'] for r in family['requirements']]})
        for mode, cost in (('deterministic', 5), ('stochastic', 10)):
            registry.register_implementation({'id': f'{family_id}_model.{mode}', 'process_id': f'{family_id}_model', 'fidelity': mode,
                'min_step_seconds': 1, 'max_step_seconds': YEAR_SECONDS, 'cost_per_call': cost, 'output_timing': 'end_of_step',
                'description': f'{family["title"]} ({mode}); unvalidated.',
                'approximation': [family['limitations'][0], 'Each call re-simulates from the supplied configuration; the report is set at the step end.']},
                _family_handler(family_id, mode))
    registry.register_process({'id': 'taylor_rule_policy_rate', 'validated': False,
        'inputs': {'inflation': {'type': 'number', 'unit': 'percent'}, 'output_gap': {'type': 'number', 'unit': 'percent'}},
        'outputs': {'policy_rate': {'type': 'number', 'unit': 'percent'}},
        'topology': 'inflation, output gap -> policy rate', 'requirements': ['fred_policy', 'fred_inflation', 'fred_output_gap'],
        'description': 'Smoothed Taylor rule as continuous relaxation: quarterly smoothing rho maps to strength -ln(rho)/quarter.'})
    registry.register_process({'id': 'two_region_migration', 'validated': False,
        'inputs': {'employment_rate_a': {'type': 'number', 'unit': 'ratio'}, 'employment_rate_b': {'type': 'number', 'unit': 'ratio'},
                   'base_outmigration_rate_a': {'type': 'number', 'unit': 'per_year'}, 'base_outmigration_rate_b': {'type': 'number', 'unit': 'per_year'}},
        'outputs': {'population_a': {'type': 'number', 'unit': 'people', 'minimum': 0}, 'population_b': {'type': 'number', 'unit': 'people', 'minimum': 0}},
        'conserved_quantities': [{'id': 'population', 'unit': 'people', 'ports': ['population_a', 'population_b']}],
        'topology': 'population_a <-> population_b through one migration authority', 'requirements': ['irs_soi_migration', 'bls_laus'],
        'description': 'Two-region migration responding to employment-rate gaps; conserved total population.'})
    registry.register_process({'id': 'conflict_event_intensity', 'validated': False,
        'inputs': {'background_rate': {'type': 'number', 'unit': 'events/month'}, 'neighbor_intensity': {'type': 'number', 'unit': 'events/month'}},
        'outputs': {'intensity': {'type': 'number', 'unit': 'events/month', 'minimum': 0}},
        'topology': 'background + neighbor intensity -> own expected intensity', 'requirements': ['ucdp_ged', 'cshapes_contiguity'],
        'description': 'Mean-field self-exciting conflict intensity with neighbor diffusion.'})
    for process_id, handler, max_step in (('taylor_rule_policy_rate', _taylor_handler, 92 * 86400),
                                          ('two_region_migration', _migration_handler, 31 * 86400),
                                          ('conflict_event_intensity', _conflict_handler, 31 * 86400)):
        for mode, cost in (('deterministic', 1), ('stochastic', 2)):
            registry.register_implementation({'id': f'{process_id}.{mode}', 'process_id': process_id, 'fidelity': mode,
                'max_step_seconds': max_step, 'cost_per_call': cost, 'description': f'{process_id} ({mode}); unvalidated.',
                'approximation': ['Inputs held constant over the step; analytic pressure integration.']}, handler(mode))
    return registry


def registry_with_models(base=None):
    """Default registry plus model processes, tolerating a future process_library import hook."""
    if base is None:
        from ..process_library import default_registry
        base = default_registry()
    ids = {p['id'] for p in base.describe()['processes']}
    if 'trade_model' not in ids:
        register_model_processes(base)
    return base
