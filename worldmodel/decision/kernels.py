"""Kernels: the simulators a decision contract can compile into, and what each declares.

A kernel declares its actions (with hard bounds), per-step metrics, observations,
the mechanisms it runs (with the parameters each mechanism consists of and the
estimation components that may supply them), the uncertain inputs it accepts and the
parameters an author may declare assumed. :meth:`Kernel.bind` takes a validated
contract and its evidence resolutions and returns a bound kernel whose parameters
come from the cited report for every report-backed mechanism and from the contract
otherwise, and whose resolutions are lowered once more by *binding coverage*: a
mechanism with any parameter the report does not set is ``assumed``, whatever its
report says.

Two kernels ship:

* ``monetary_policy_rate`` — the ``monetary`` family's smoothed Taylor rule
  (``worldmodel.models.monetary``) run as a one-quarter-ahead transition driven by
  exogenous inflation and output-gap paths, for a decision-maker who takes the policy
  rate as given (a floating-rate borrower's treasury). Its only mechanism is the
  policy-rate reaction; the drivers are either declared linear paths (for stress
  tests) or contiguous blocks resampled from the first-release quarters the
  validated attempt was fitted on (for gated optimization).
* ``coupled_economy_firm`` — the synthetic commercial-bank economy
  (``worldmodel.coupled_economy``) from one firm's seat, with its policy-rate rule,
  loan-rate pass-through, price adjustment and demand response.
"""
from copy import deepcopy
import math

from .contract import AGGREGATES
from .evidence import lower
from ..models.base import inverse


def _clip(value, low, high):
    return min(high, max(low, value))


class Kernel:
    id = None
    step = None
    description = ''
    actions = {}        # name -> {'unit', 'minimum', 'maximum'} hard bounds
    metrics = {}        # name -> unit, reported every step
    observations = {}   # name -> unit, what the decision-maker sees
    mechanisms = {}     # name -> {'parameters': [...], 'components': [...], 'description'}
    inputs = {}         # uncertain input name -> {'unit', 'minimum', 'maximum'}
    assumable = {}      # assumable parameter name -> {'mechanism', 'unit', 'minimum', 'maximum'}
    identities = ()     # enforced accounting identities: exact by construction, no behavioral claim

    def config_errors(self, config):
        return []

    def contract_errors(self, contract):
        errors = []
        if contract['horizon']['step'] != self.step:
            errors.append(f'kernel {self.id} steps by {self.step}, not {contract["horizon"]["step"]}')
        if set(contract['actions']) != set(self.actions):
            errors.append(f'kernel {self.id} controls exactly {sorted(self.actions)}; contract declares {sorted(contract["actions"])}')
        for name, action in contract['actions'].items():
            hard = self.actions.get(name)
            if hard is None:
                continue
            if action['unit'] != hard['unit']:
                errors.append(f'action {name}: unit must be {hard["unit"]}')
            if action['minimum'] < hard['minimum'] or action['maximum'] > hard['maximum']:
                errors.append(f'action {name}: bounds must lie within the kernel\'s [{hard["minimum"]}, {hard["maximum"]}]')
        if set(contract['mechanisms']) != set(self.mechanisms):
            errors.append(f'kernel {self.id} runs exactly the mechanisms {sorted(self.mechanisms)}; every one needs an '
                          f'evidence status, and the contract declares {sorted(contract["mechanisms"])}')
        for name, mechanism in contract['mechanisms'].items():
            component = mechanism['evidence'].get('component')
            allowed = self.mechanisms.get(name, {}).get('components', [])
            if component is not None and component not in allowed:
                errors.append(f'mechanism {name}: component must be one of {allowed}')
        metrics = set(self.metrics) | {'action_cost'}
        for group in ('objectives', 'constraints', 'success_criteria'):
            for item in contract[group]:
                if item['metric'] not in metrics:
                    errors.append(f'{group} {item["id"]}: unknown metric {item["metric"]!r}; kernel metrics are {sorted(metrics)}')
                if item['aggregate'] not in AGGREGATES:
                    errors.append(f'{group} {item["id"]}: unknown aggregate')
        for name, spec in contract['uncertain_inputs'].items():
            hard = self.inputs.get(name)
            if hard is None:
                errors.append(f'uncertain input {name}: kernel {self.id} accepts {sorted(self.inputs)}')
            elif spec['unit'] != hard['unit'] or spec['minimum'] < hard['minimum'] or spec['maximum'] > hard['maximum']:
                errors.append(f'uncertain input {name}: needs unit {hard["unit"]} within [{hard["minimum"]}, {hard["maximum"]}]')
        for name, spec in contract['assumed_parameters'].items():
            hard = self.assumable.get(name)
            if hard is None:
                errors.append(f'assumed parameter {name}: kernel {self.id} accepts {sorted(self.assumable)}')
            elif spec['mechanism'] != hard['mechanism'] or spec['unit'] != hard['unit'] \
                    or spec['minimum'] < hard['minimum'] or spec['maximum'] > hard['maximum']:
                errors.append(f'assumed parameter {name}: belongs to {hard["mechanism"]}, unit {hard["unit"]}, '
                              f'within [{hard["minimum"]}, {hard["maximum"]}]')
        errors.extend(self.config_errors(contract['environment']['config']))
        return errors

    def describe(self):
        return {'id': self.id, 'step': self.step, 'description': self.description, 'actions': deepcopy(self.actions),
                'metrics': dict(self.metrics), 'observations': dict(self.observations),
                'mechanisms': deepcopy(self.mechanisms), 'uncertain_inputs': deepcopy(self.inputs),
                'assumable_parameters': deepcopy(self.assumable), 'identities': list(self.identities)}


class Bound:
    """A kernel bound to one contract: parameters, final resolutions and a scenario space."""

    def __init__(self, kernel, contract, resolutions):
        self.kernel = kernel
        self.contract = contract
        self.resolutions = deepcopy(resolutions)
        self.dimensions = []          # [{name, low, high, nominal, kind, source}]

    # Overridden per kernel -----------------------------------------------------------------
    def scenario(self, point):
        raise NotImplementedError

    def initial(self, scenario):
        raise NotImplementedError

    def transition(self, state, actions, scenario, t):
        """Return ``(state, metrics, mechanisms_used)`` for one step."""
        raise NotImplementedError

    def observe(self, state):
        raise NotImplementedError

    def training_scenario(self, rng, split=None):
        raise ValueError(f'kernel {self.kernel.id} has no training-scenario source configured')

    def training_source(self):
        return {'source': None, 'observed': False, 'reason': 'no training-scenario source configured'}

    # Shared ------------------------------------------------------------------------------------
    def project(self, point):
        """Map a point of the search box onto the admissible scenario set (identity unless overridden)."""
        return point

    def nominal_point(self):
        return {d['name']: d['nominal'] for d in self.dimensions}

    def _add(self, name, low, high, nominal, kind, source):
        if not low <= nominal <= high:
            nominal = _clip(nominal, low, high)
        self.dimensions.append({'name': name, 'low': low, 'high': high, 'nominal': nominal, 'kind': kind, 'source': source})


# ============================================================================= monetary

MONETARY_INPUTS = {
    'inflation_start': {'unit': 'percent', 'minimum': -5.0, 'maximum': 20.0},
    'inflation_end': {'unit': 'percent', 'minimum': -5.0, 'maximum': 20.0},
    'output_gap_start': {'unit': 'percent', 'minimum': -15.0, 'maximum': 15.0},
    'output_gap_end': {'unit': 'percent', 'minimum': -15.0, 'maximum': 15.0},
}
MONETARY_STRUCTURAL = ('rho', 'phi_pi', 'phi_y', 'r_star', 'policy_shock_sd', 'inflation_target', 'elb')


def reduced_form(parameters):
    """``i = const + rho i_lag + b_pi pi + b_y gap`` from the family's structural parameters."""
    rho, phi_pi, phi_y = parameters['rho'], parameters['phi_pi'], parameters['phi_y']
    return {'const': (1 - rho) * (parameters['r_star'] - phi_pi * parameters['inflation_target']),
            'rho': rho, 'b_pi': (1 - rho) * (1 + phi_pi), 'b_y': (1 - rho) * phi_y}


COEFFICIENTS = ('const', 'rho', 'b_pi', 'b_y')


def refit_covariance(rows, parameters, estimated, errors, tolerance=1e-6, cutoff=None):
    """HC1 covariance of the reduced-form Taylor regression, refit exactly as
    :func:`worldmodel.models.monetary.fit_taylor` fits it (rows sorted by date, lagged by
    row, observations within 0.1 of the lower bound excluded), on rows dated at or before
    ``cutoff`` when one is given (the report's own cutoff).

    Returns ``(covariance, check)``; ``covariance`` is ``None`` unless the refit
    reproduces the report's reduced-form coefficients and standard errors within
    ``tolerance`` (relative), so a covariance is only ever used for the fit it belongs to.
    """
    from ..models.base import ols
    rows = sorted((r for r in rows if cutoff is None or str(r['date'])[:10] <= str(cutoff)[:10]), key=lambda r: r['date'])
    elb = parameters['elb']
    X, y = [], []
    for current, previous in zip(rows[1:], rows):
        i, lag = current['policy_rate'], previous['policy_rate']
        if i <= elb + 0.1 or lag <= elb + 0.1:
            continue
        X.append([1.0, lag, current['inflation'], current['output_gap']])
        y.append(i)
    model = ols(X, y, names=list(COEFFICIENTS))
    worst = 0.0
    for name in COEFFICIENTS:
        for got, want in ((model['coefficients'][name], estimated.get(name)), (model['standard_errors'][name], errors.get(name))):
            if not isinstance(want, (int, float)):
                return None, {'reproduced': False, 'problem': f'report lacks {name}'}
            worst = max(worst, abs(got - want) / max(abs(want), 1e-12))
    check = {'reproduced': worst <= tolerance, 'max_relative_difference': worst, 'observations': model['n'], 'se_type': model['se_type']}
    if worst > tolerance:
        check['problem'] = f'max relative difference {worst:.3g} exceeds {tolerance:g}'
        return None, check
    return model['covariance'], check


class MonetaryKernel(Kernel):
    id = 'monetary_policy_rate'
    step = 'quarter'
    description = ('One-quarter-ahead policy-rate transition of the monetary family\'s smoothed Taylor rule, '
                   'i_t = max(elb, rho i_{t-1} + (1 - rho) i*_t + e_t), for a decision-maker whose action does not '
                   'move the rate (a price taker). The action reserves funding at the last observed rate plus a margin.')
    actions = {'reserve_margin': {'unit': 'percentage_points', 'minimum': -5.0, 'maximum': 5.0}}
    metrics = {'policy_rate': 'percent', 'reserved_rate': 'percent', 'shortfall': 'percentage_points',
               'excess': 'percentage_points', 'desired_rate': 'percent', 'at_lower_bound': 'indicator',
               'rate_change': 'percentage_points'}
    observations = {'policy_rate': 'percent', 'inflation': 'percent', 'output_gap': 'percent', 'quarter': 'count'}
    mechanisms = {'policy_rate_reaction': {
        'parameters': list(MONETARY_STRUCTURAL), 'components': ['monetary_model_parameters'],
        'description': 'Smoothed Taylor rule with lower bound, worldmodel.models.monetary.simulate semantics.'}}
    inputs = MONETARY_INPUTS
    assumable = {name: {'mechanism': 'policy_rate_reaction', 'unit': unit, 'minimum': low, 'maximum': high}
                 for name, unit, low, high in (('rho', 'per_quarter', 0.0, 0.999), ('phi_pi', 'dimensionless', -5, 10),
                                               ('phi_y', 'dimensionless', -5, 10), ('r_star', 'percent', -5, 10),
                                               ('policy_shock_sd', 'percent', 0, 10))}

    def config_errors(self, config):
        errors = []
        initial = config.get('initial')
        if not isinstance(initial, dict) or not all(isinstance(initial.get(k), (int, float)) for k in ('policy_rate', 'inflation', 'output_gap')):
            errors.append('environment.config.initial needs numeric policy_rate, inflation and output_gap (percent)')
        drivers = config.get('drivers', {'source': 'linear_paths'})
        if drivers.get('source') not in ('linear_paths', 'historical_resample'):
            errors.append('environment.config.drivers.source must be linear_paths or historical_resample')
        return errors

    def contract_errors(self, contract):
        errors = super().contract_errors(contract)
        drivers = contract['environment']['config'].get('drivers', {'source': 'linear_paths'})
        if drivers.get('source') == 'linear_paths':
            missing = sorted(set(MONETARY_INPUTS) - set(contract['uncertain_inputs']))
            if missing:
                errors.append(f'linear driver paths need uncertain inputs {missing}')
        if contract['mechanisms'].get('policy_rate_reaction', {}).get('evidence', {}).get('status') == 'assumed':
            missing = sorted(set(self.assumable) - set(contract['assumed_parameters']))
            if missing:
                errors.append(f'an assumed policy-rate reaction needs assumed parameters {missing}')
        return errors

    def bind(self, contract, resolutions, index, *, history=None):
        return MonetaryBound(self, contract, resolutions, index, history)

    def history_report(self, contract):
        """The report whose fitting data this kernel needs: for drivers, and to recompute the covariance."""
        evidence = contract['mechanisms']['policy_rate_reaction']['evidence']
        return evidence.get('report_id') if evidence['status'] != 'assumed' else None

    def history_rows(self, data):
        return data['observations']


class MonetaryBound(Bound):
    def __init__(self, kernel, contract, resolutions, index, history):
        super().__init__(kernel, contract, resolutions)
        config = contract['environment']['config']
        self.initial_state = dict(config['initial'])
        self.drivers = dict(config.get('drivers', {'source': 'linear_paths'}))
        self.horizon = contract['horizon']['steps']
        resolution = self.resolutions['policy_rate_reaction']
        k = contract['parameter_uncertainty']['standard_errors']
        q = contract['parameter_uncertainty']['shock_quantile_bound']
        if resolution['report_id'] is not None and resolution['status'] != 'assumed':
            entry = index.get(resolution['report_id']) if index is not None else None
            if entry is None or entry.get('record') is None:
                raise ValueError('policy_rate_reaction cites a report that is not in the evidence index; bind it from a store '
                                 'that holds it (not --no-store)')
            params = entry['record']['parameters']
            missing = [p for p in MONETARY_STRUCTURAL if not isinstance(params.get(p), (int, float))]
            self.parameters = {p: float(params[p]) for p in MONETARY_STRUCTURAL if isinstance(params.get(p), (int, float))}
            if missing:                                   # binding coverage: the report does not set every parameter
                from ..models.monetary import FAMILY
                self.parameters.update({p: FAMILY['parameters'][p]['value'] for p in missing})
                lower(resolution, 'assumed', f'binding coverage: the report does not set {missing}, so the family defaults '
                                             'stand in and the mechanism as run is assumed')
            collisions = sorted(set(contract['assumed_parameters']) & set(self.parameters))
            if collisions:
                raise ValueError(f'assumed parameters {collisions} are estimated by report {resolution["report_id"][:12]}...; '
                                 'they vary only within their estimated uncertainty')
            taylor = ((entry.get('diagnostics') or {}).get('family_diagnostics') or {}).get('taylor') or {}
            estimated = taylor.get('reduced_form') or reduced_form(self.parameters)
            errors = taylor.get('standard_errors') or {}
            self.estimate = [estimated[name] for name in COEFFICIENTS]
            self.covariance, check = None, None
            if history and history.get('rows'):
                self.covariance, check = refit_covariance(history['rows'], self.parameters, estimated, errors,
                                                          cutoff=entry['record'].get('cutoff'))
            if self.covariance is not None:
                ses = [math.sqrt(self.covariance[i][i]) for i in range(len(COEFFICIENTS))]
                self.inverse_covariance = inverse(self.covariance)
                self.uncertainty = {'method': 'joint_wald_ellipsoid', 'radius': k, 'refit_check': check,
                                    'statement': f'reduced-form coefficients within Mahalanobis distance {k:g} of the '
                                                 'estimate under the HC1 covariance recomputed from the report\'s pinned inputs'}
                source = f'report {resolution["report_id"][:12]}...: joint Wald ellipsoid, radius {k:g}, HC1 covariance'
            else:
                ses = [errors.get(name) for name in COEFFICIENTS]
                if not all(isinstance(se, (int, float)) and se > 0 for se in ses):
                    raise ValueError('report has no standard errors for the reduced form; its uncertainty cannot be bounded')
                self.uncertainty = {'method': 'independent_box', 'radius': k, 'refit_check': check,
                                    'statement': f'each reduced-form coefficient within {k:g} standard errors, independently; '
                                                 'the covariance was not available, so corners of this box overstate '
                                                 'the joint uncertainty'}
                source = f'report {resolution["report_id"][:12]}...: +/- {k:g} standard errors each (independent box)'
                if check is not None:
                    resolution['caveats'].append('refit did not reproduce the report, so its covariance was not used: '
                                                 + check.get('problem', ''))
            for name, value, se in zip(COEFFICIENTS, self.estimate, ses):
                low, high = value - k * se, value + k * se
                if name == 'rho':
                    low, high = max(0.0, low), min(0.999, high)
                self._add(f'coef.{name}', low, high, value, 'estimated_uncertainty', source)
            resolution['binding'] = {'bound_from_report': sorted(set(self.parameters) - set(missing)), 'assumed': sorted(missing)}
        else:
            assumed = contract['assumed_parameters']
            self.parameters = {name: assumed[name]['nominal'] for name in self.kernel.assumable}
            self.parameters.update(inflation_target=2.0, elb=0.125)
            for name in self.kernel.assumable:
                spec = assumed[name]
                self._add(f'param.{name}', spec['minimum'], spec['maximum'], spec['nominal'], 'assumed_parameter', 'declared range')
            resolution['binding'] = {'bound_from_report': [], 'assumed': sorted(self.kernel.assumable) + ['elb', 'inflation_target']}
            self.uncertainty = {'method': 'declared_ranges', 'statement': 'assumed parameters within their declared ranges'}
        self.sd = self.parameters['policy_shock_sd']
        for name in ('inflation_start', 'inflation_end', 'output_gap_start', 'output_gap_end'):
            spec = contract['uncertain_inputs'].get(name)
            if spec is not None and self.drivers['source'] == 'linear_paths':
                self._add(f'input.{name}', spec['minimum'], spec['maximum'], spec['nominal'], 'uncertain_input', 'declared range')
        if self.drivers['source'] == 'linear_paths':
            for t in range(self.horizon):
                self._add(f'shock.q{t + 1}', -q, q, 0.0, 'shock',
                          f'standardized policy shock within +/-{q:g} (the fitted rule\'s residual sd {self.sd:.3f} scales it)')
        self.history = None
        if self.drivers['source'] == 'historical_resample':
            if not history or not history.get('rows'):
                raise ValueError('historical_resample drivers need the loaded first-release rows '
                                 '(history={"rows", "evidence"})' + (f': {history["error"]}' if (history or {}).get('error') else ''))
            self.history = history
            self.blocks = self._blocks(history['rows'])
            if not self.blocks:
                raise ValueError(f'no run of {self.horizon + 1} contiguous quarters in the supplied history')
            split = self.drivers.get('evaluation_from')
            self.split_blocks = {'all': self.blocks}
            if split is not None:
                last = lambda i: self._rows[i + self.horizon]['date']
                self.split_blocks = {'train': [i for i in self.blocks if last(i) < split],
                                     'evaluate': [i for i in self.blocks if self._rows[i]['date'] >= split]}
                if not self.split_blocks['train'] or not self.split_blocks['evaluate']:
                    raise ValueError(f'evaluation_from {split} leaves no complete block on one side of the split')

    def _blocks(self, rows):
        rows = sorted(rows, key=lambda r: r['date'])

        def month(date):
            return int(date[:4]) * 12 + int(date[5:7]) - 1
        gaps = [month(b['date']) - month(a['date']) for a, b in zip(rows, rows[1:])]
        step = max(set(gaps), key=gaps.count) if gaps else 0   # the panel's own spacing: 3 for quarterly rows
        starts = []
        for i in range(len(rows) - self.horizon):
            if step > 0 and all(gaps[i + j] == step for j in range(self.horizon)):
                starts.append(i)
        self._rows = rows
        return starts

    def fitted_rule_declaration(self, quantile):
        """A ``linear_rule`` policy: the fitted rule's own ``quantile`` of next quarter's rate, from last quarter's drivers.

        margin = const + (rho - 1) i + b_pi pi + b_y gap + z_q sd, i.e. the one-step
        predictive quantile with this quarter's (unknown) drivers replaced by last
        quarter's. A model-based baseline, not a learned policy.
        """
        from statistics import NormalDist
        c, _, _ = self._coefficients(self.nominal_point())
        z = NormalDist().inv_cdf(quantile)
        return {'kind': 'linear_rule', 'action': 'reserve_margin', 'intercept': c['const'] + z * self.parameters['policy_shock_sd'],
                'coefficients': {'policy_rate': c['rho'] - 1.0, 'inflation': c['b_pi'], 'output_gap': c['b_y']}}

    def project(self, point):
        """Shrink report-backed coefficients toward the estimate onto the joint Wald ellipsoid."""
        if getattr(self, 'covariance', None) is None or 'coef.rho' not in point:
            return point
        d = [point[f'coef.{name}'] - value for name, value in zip(COEFFICIENTS, self.estimate)]
        distance = math.sqrt(max(0.0, sum(d[i] * self.inverse_covariance[i][j] * d[j] for i in range(4) for j in range(4))))
        radius = self.uncertainty['radius']
        if distance <= radius:
            return point
        out = dict(point)
        for name, value, delta in zip(COEFFICIENTS, self.estimate, d):
            out[f'coef.{name}'] = value + delta * radius / distance
        out['coef.rho'] = min(0.999, max(0.0, out['coef.rho']))
        return out

    # scenarios --------------------------------------------------------------------------------
    def _coefficients(self, point):
        if any(d['name'].startswith('coef.') for d in self.dimensions):
            coefficients = {name: point[f'coef.{name}'] for name in ('const', 'rho', 'b_pi', 'b_y')}
            return coefficients, self.parameters['policy_shock_sd'], self.parameters['elb']
        params = dict(self.parameters)
        for name in self.kernel.assumable:
            if f'param.{name}' in point:
                params[name] = point[f'param.{name}']
        return reduced_form(params), params['policy_shock_sd'], params['elb']

    def scenario(self, point):
        point = {**self.nominal_point(), **point}
        coefficients, sd, elb = self._coefficients(point)
        n = self.horizon
        path = lambda a, b: [a + (b - a) * (t / (n - 1) if n > 1 else 1.0) for t in range(n)]
        inflation = path(point['input.inflation_start'], point['input.inflation_end'])
        gap = path(point['input.output_gap_start'], point['input.output_gap_end'])
        return {'initial': dict(self.initial_state), 'drivers': [{'inflation': a, 'output_gap': b} for a, b in zip(inflation, gap)],
                'shocks': [point[f'shock.q{t + 1}'] for t in range(n)], 'coefficients': coefficients, 'sd': sd, 'elb': elb,
                'point': point}

    def training_scenario(self, rng, split=None):
        if self.history is None:
            raise ValueError('training scenarios need historical_resample drivers')
        blocks = self.split_blocks['all'] if 'all' in self.split_blocks else self.split_blocks[split or 'train']
        i = rng.choice(blocks)
        start = self._rows[i]
        block = self._rows[i + 1:i + 1 + self.horizon]
        coefficients, sd, elb = self._coefficients(self.nominal_point())
        return {'initial': {k: start[k] for k in ('policy_rate', 'inflation', 'output_gap')},
                'drivers': [{'inflation': r['inflation'], 'output_gap': r['output_gap']} for r in block],
                'shocks': [rng.gauss(0.0, 1.0) for _ in block], 'coefficients': coefficients, 'sd': sd, 'elb': elb,
                'point': {'block_start': start['date']}}

    def training_source(self):
        if self.history is None:
            return {'source': 'linear_paths', 'observed': False,
                    'reason': 'driver paths are declared ranges, which is an assumed driver process'}
        def span(blocks):
            return {'blocks': len(blocks), 'first_start': self._rows[blocks[0]]['date'],
                    'last_end': self._rows[blocks[-1] + self.horizon]['date']}
        if not self.history.get('evidence'):
            return {'source': 'historical_resample', 'observed': False,
                    'reason': 'the supplied history carries no provenance (evidence), so it cannot count as observed'}
        return {'source': 'historical_resample', 'observed': True, 'blocks': len(self.blocks),
                'window': [self._rows[0]['date'], self._rows[-1]['date']], 'evidence': self.history.get('evidence'),
                'split': ({name: span(blocks) for name, blocks in self.split_blocks.items()}
                          if 'all' not in self.split_blocks else 'none: training and evaluation seeds resample the same blocks'),
                'shocks': 'Gaussian with the fitted residual sd (the predictive distribution the report validated)'}

    # dynamics ---------------------------------------------------------------------------------
    def initial(self, scenario):
        return {'t': 0, **scenario['initial']}

    def observe(self, state):
        return {'policy_rate': state['policy_rate'], 'inflation': state['inflation'], 'output_gap': state['output_gap'],
                'quarter': state['t']}

    def transition(self, state, actions, scenario, t):
        c = scenario['coefficients']
        drivers = scenario['drivers'][t]
        previous = state['policy_rate']
        mean = c['const'] + c['rho'] * previous + c['b_pi'] * drivers['inflation'] + c['b_y'] * drivers['output_gap']
        rate = max(scenario['elb'], mean + scenario['sd'] * scenario['shocks'][t])
        desired = (c['const'] + c['b_pi'] * drivers['inflation'] + c['b_y'] * drivers['output_gap']) / (1 - c['rho']) if c['rho'] < 1 else float('nan')
        reserved = previous + actions['reserve_margin']
        metrics = {'policy_rate': rate, 'reserved_rate': reserved, 'shortfall': max(0.0, rate - reserved),
                   'excess': max(0.0, reserved - rate), 'desired_rate': desired if math.isfinite(desired) else 0.0,
                   'at_lower_bound': 1.0 if rate == scenario['elb'] else 0.0, 'rate_change': rate - previous}
        new = {'t': t + 1, 'policy_rate': rate, 'inflation': drivers['inflation'], 'output_gap': drivers['output_gap']}
        return new, metrics, ('policy_rate_reaction',)


# ============================================================================= coupled economy

ECONOMY_PARAMETERS = {
    'policy_rate_rule': ('interest.policy_rule', ['reference_rate', 'inflation_target', 'inflation_response', 'output_response', 'smoothing']),
    'loan_rate_pass_through': ('interest', ['spread', 'pass_through', 'adjustment_speed', 'impact_pass_through']),
    'price_adjustment': ('price_feedback', ['adjustment', 'unmet_demand_response']),
    'demand_response': ('demand_feedback', ['elasticity', 'rate_response', 'energy_response']),
}
ECONOMY_ASSUMABLE = {
    'inflation_response': ('policy_rate_rule', 'interest.policy_rule.inflation_response', 'dimensionless', 0.0, 10.0),
    'output_response': ('policy_rate_rule', 'interest.policy_rule.output_response', 'dimensionless', 0.0, 10.0),
    'loan_spread': ('loan_rate_pass_through', 'interest.spread', 'fraction_per_year', 0.0, 1.0),
    'pass_through': ('loan_rate_pass_through', 'interest.pass_through', 'dimensionless', 0.0, 1.5),
    'pass_through_speed': ('loan_rate_pass_through', 'interest.adjustment_speed', 'per_day', 0.0, 1.0),
    'price_adjustment': ('price_adjustment', 'price_feedback.adjustment', 'fraction', 0.0, 1.0),
    'unmet_demand_response': ('price_adjustment', 'price_feedback.unmet_demand_response', 'fraction', 0.0, 1.0),
    'demand_elasticity': ('demand_response', 'demand_feedback.elasticity', 'dimensionless', 0.0, 4.0),
    'rate_response': ('demand_response', 'demand_feedback.rate_response', 'dimensionless', 0.0, 4.0),
}


def _set_path(node, path, value):
    parts = path.split('.')
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _get_path(node, path):
    for part in path.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class EconomyKernel(Kernel):
    id = 'coupled_economy_firm'
    step = 'day'
    description = ('The synthetic commercial-bank economy (worldmodel.coupled_economy) from one firm\'s seat: the firm '
                   'chooses daily production and a credit line; a household buys through lagged demand feedback; the '
                   'policy rate follows a rule, the loan rate passes it through, prices adjust to inventory.')
    actions = {'production': {'unit': 'goods_per_day', 'minimum': 0, 'maximum': 1000},
               'credit_limit': {'unit': 'USD', 'minimum': 0, 'maximum': 1000000}}
    metrics = {'bankrupt': 'indicator', 'net_cash': 'USD', 'deposits': 'USD', 'loan_principal': 'USD',
               'cash_surplus_after_interest': 'USD', 'interest_paid': 'USD', 'produced': 'goods', 'sold': 'goods',
               'price': 'USD', 'policy_rate': 'fraction_per_year', 'loan_rate': 'fraction_per_year',
               'household_deposits': 'USD', 'inventory': 'goods'}
    observations = {'net_cash': 'USD', 'inventory': 'goods', 'price': 'USD', 'policy_rate': 'fraction_per_year', 'day': 'count'}
    mechanisms = {
        'policy_rate_rule': {'parameters': ECONOMY_PARAMETERS['policy_rate_rule'][1], 'components': ['policy_rule'],
                             'description': 'Bounded (optionally smoothed) Taylor-type rule on daily inflation and output-gap shocks.'},
        'loan_rate_pass_through': {'parameters': ECONOMY_PARAMETERS['loan_rate_pass_through'][1], 'components': ['interest_pass_through'],
                                   'description': 'Error-correction of the annual loan rate toward spread + pass_through * policy rate.'},
        'price_adjustment': {'parameters': ECONOMY_PARAMETERS['price_adjustment'][1], 'components': ['price_adjustment'],
                             'description': 'Automatic quote from inventory gap and lagged unmet demand.'},
        'demand_response': {'parameters': ECONOMY_PARAMETERS['demand_response'][1], 'components': ['demand_price_elasticity'],
                            'description': 'Household demand scaled by (reference/price)^elasticity and lagged rate expectations.'},
    }
    inputs = {'unit_cost_multiplier': {'unit': 'ratio', 'minimum': 0.1, 'maximum': 10.0},
              'household_demand': {'unit': 'goods_per_day', 'minimum': 0, 'maximum': 1000},
              'inflation': {'unit': 'fraction_per_year', 'minimum': -1.0, 'maximum': 1.0},
              'output_gap': {'unit': 'fraction', 'minimum': -1.0, 'maximum': 1.0},
              'expected_rate_change': {'unit': 'fraction', 'minimum': -1.0, 'maximum': 1.0}}
    assumable = {name: {'mechanism': m, 'unit': unit, 'minimum': low, 'maximum': high, 'path': path}
                 for name, (m, path, unit, low, high) in ECONOMY_ASSUMABLE.items()}
    identities = ('bank ledger: reserves + loans = deposits + equity, reserves conserved',
                  'deposit, principal and equity changes reconcile with origination, repayment, interest and write-off',
                  'goods conserved: opening + produced - sold - destroyed = closing')

    def config_errors(self, config):
        errors = []
        state = config.get('initial_state')
        if not isinstance(state, dict) or not isinstance(state.get('mechanisms'), dict):
            return ['environment.config.initial_state must be a coupled-economy initial state with mechanisms']
        mechanisms = state['mechanisms']
        for name, (prefix, parameters) in ECONOMY_PARAMETERS.items():
            node = _get_path(mechanisms, prefix)
            if not isinstance(node, dict):
                errors.append(f'initial_state.mechanisms.{prefix} is required by mechanism {name}')
        if _get_path(mechanisms, 'interest.insufficient') != 'bankrupt':
            errors.append('interest.insufficient must be "bankrupt" so that unpaid interest is a visible failure')
        for key in ('firm', 'household'):
            if not isinstance(config.get(key), str):
                errors.append(f'environment.config.{key} must name the firm and the buying household')
        if type(config.get('shock_day', 0)) is not int or config.get('shock_day', 0) < 0:
            errors.append('environment.config.shock_day must be a nonnegative integer day')
        return errors

    def bind(self, contract, resolutions, index, *, history=None):
        return EconomyBound(self, contract, resolutions, index)


class EconomyBound(Bound):
    def __init__(self, kernel, contract, resolutions, index):
        super().__init__(kernel, contract, resolutions)
        from ..coupled_economy import initialize_economy, parameter_provenance
        config = contract['environment']['config']
        self.firm, self.household, self.shock_day = config['firm'], config['household'], config.get('shock_day', 0)
        base = deepcopy(config['initial_state'])
        records = []
        for name, resolution in self.resolutions.items():
            if resolution['report_id'] is None or resolution['status'] == 'assumed':
                continue
            entry = index.get(resolution['report_id']) if index is not None else None
            if entry is None or entry.get('record') is None:
                raise ValueError(f'mechanism {name} cites a report that is not in the evidence index; bind it from a store '
                                 'that holds it (not --no-store)')
            records.append(entry['record'])
        for pname, spec in contract['assumed_parameters'].items():
            _set_path(base['mechanisms'], self.kernel.assumable[pname]['path'], spec['nominal'])
        state = initialize_economy(base, history='summary', calibration=records or None)
        provenance = parameter_provenance(state)
        bound_paths = {path for path, item in provenance.items() if item['status'] == 'estimated'}
        for pname in contract['assumed_parameters']:
            path = 'mechanisms.' + self.kernel.assumable[pname]['path']
            if path in bound_paths:
                raise ValueError(f'assumed parameter {pname} is estimated by a cited report; it cannot also be declared assumed')
        for name, (prefix, parameters) in ECONOMY_PARAMETERS.items():
            resolution = self.resolutions[name]
            estimated, assumed = [], []
            for parameter in parameters:
                path = f'mechanisms.{prefix}.{parameter}'
                (estimated if path in bound_paths else assumed).append(parameter)
            resolution['binding'] = {'bound_from_report': estimated, 'assumed': assumed}
            if resolution['status'] != 'assumed' and assumed:
                lower(resolution, 'assumed', f'binding coverage: the cited report does not set {assumed}, so those '
                                             'parameters are the contract\'s assumptions and the mechanism as run is assumed')
        self.state0 = state
        self.provenance = provenance
        for name in ('unit_cost_multiplier', 'household_demand', 'inflation', 'output_gap', 'expected_rate_change'):
            spec = contract['uncertain_inputs'].get(name)
            if spec is not None:
                self._add(f'input.{name}', spec['minimum'], spec['maximum'], spec['nominal'], 'uncertain_input', 'declared range')
        for pname, spec in sorted(contract['assumed_parameters'].items()):
            self._add(f'param.{pname}', spec['minimum'], spec['maximum'], spec['nominal'], 'assumed_parameter', 'declared range')
        self.defaults = {'unit_cost_multiplier': 1.0, 'household_demand': 4, 'inflation': 0.02, 'output_gap': 0.0,
                         'expected_rate_change': 0.0}

    def scenario(self, point):
        point = {**self.nominal_point(), **point}
        inputs = {name: point.get(f'input.{name}', default) for name, default in self.defaults.items()}
        parameters = {name[len('param.'):]: value for name, value in point.items() if name.startswith('param.')}
        return {'inputs': inputs, 'parameters': parameters, 'point': point}

    def initial(self, scenario):
        state = self.state0
        if scenario['parameters']:
            state = deepcopy(state)
            for pname, value in scenario['parameters'].items():
                _set_path(state['mechanisms'], self.kernel.assumable[pname]['path'], value)
        return {'economy': state, 'day': 0, 'last': self._snapshot(state, None)}

    def _snapshot(self, state, record):
        firm = next(f for f in state['firms'] if f['id'] == self.firm)
        bank = next(b for b in state['banks'] if b['id'] == firm['bank'])
        household = next(h for h in state['households'] if h['id'] == self.household)
        home = next(b for b in state['banks'] if b['id'] == household['bank'])
        deposits, principal = bank['accounts'].get(self.firm, 0.0), bank['loans'].get(self.firm, 0.0)
        interest = state['mechanisms'].get('interest', {})
        totals = (record or {}).get('totals', {})
        return {'bankrupt': 1.0 if firm['status'] == 'bankrupt' else 0.0, 'net_cash': deposits - principal,
                'deposits': deposits, 'loan_principal': principal,
                'cash_surplus_after_interest': float(totals.get('cash_surplus_after_interest', 0.0)),
                'interest_paid': float(totals.get('interest_paid', 0.0)), 'produced': float(totals.get('produced', 0)),
                'sold': float(totals.get('sold', 0)), 'price': float(firm.get('last_price', 0.0)),
                'policy_rate': float(interest.get('policy_rate', 0.0)),
                'loan_rate': float(interest.get('loan_rate', interest.get('policy_rate', 0.0) + interest.get('spread', 0.0))),
                'household_deposits': home['accounts'].get(self.household, 0.0), 'inventory': float(firm['inventory'])}

    def observe(self, state):
        last = state['last']
        return {'net_cash': last['net_cash'], 'inventory': last['inventory'], 'price': last['price'],
                'policy_rate': last['policy_rate'], 'day': state['day']}

    def transition(self, state, actions, scenario, t):
        from ..coupled_economy import step_economy
        economy = state['economy']
        inputs = scenario['inputs']
        firm = next(f for f in economy['firms'] if f['id'] == self.firm)
        shock = {'inflation': inputs['inflation'], 'output_gap': inputs['output_gap']}
        if t == self.shock_day:
            shock['unit_cost'] = {self.firm: max(0.01, round(firm['unit_cost'] * inputs['unit_cost_multiplier'], 2))}
            shock['expected_rate_change'] = inputs['expected_rate_change']
        policy = {'firms': {self.firm: {'production': int(round(actions['production'])),
                                        'credit_limit': round(actions['credit_limit'], 2)}},
                  'households': {self.household: {'purchases': {self.firm: int(round(inputs['household_demand']))}}}}
        economy = step_economy(economy, policy, shock)
        snapshot = self._snapshot(economy, economy['history'][-1])
        return {'economy': economy, 'day': t + 1, 'last': snapshot}, snapshot, tuple(sorted(self.kernel.mechanisms))


KERNELS = {kernel.id: kernel for kernel in (MonetaryKernel(), EconomyKernel())}


def kernel_for(kernel_id):
    if kernel_id not in KERNELS:
        raise ValueError(f'Unknown decision kernel {kernel_id!r}; choose from {sorted(KERNELS)}')
    return KERNELS[kernel_id]
