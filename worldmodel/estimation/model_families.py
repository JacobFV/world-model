"""Adapter from ``worldmodel.models`` families to Estimates, holdout validation and calibration records.

Families keep their native data mappings and ``fit(data, cutoff)`` contracts
(see docs/political-market-geopolitical-models.md). This adapter:

* converts ``{estimate, diagnostics, evidence}`` into an :class:`Estimate` whose
  scalar parameters carry declared units/bounds and map to ``parameters.<name>``
  (the family process handlers merge binding parameters into ``config.parameters``);
* audits every fit window against the cutoff (``LeakageError`` otherwise);
* scores rolling one-step holdout forecasts for families that declare a holdout
  forecaster (built in: ``monetary`` Taylor rule, ``conflict`` Hawkes intensity; the
  other family modules define ``holdout_forecaster``), so acceptance criteria apply.
  A family module may instead declare ``NON_ESTIMABLE = '<reason>'``; such families
  (``sanctions``) cannot be validated.

Forecaster contract extensions (all optional): ``baselines`` names forecaster-supplied
reference forecasts (each prediction then carries ``baselines: {name: {mean, sd}}``,
scored and Diebold-Mariano tested like the naive baselines); ``probability: True``
adds Brier score, Brier skill and calibration; predictions may carry ``group`` so that
secondary targets (for example seat counts next to vote shares) are scored separately
under ``secondary`` while acceptance uses only the primary ``target`` group. The
forecast function receives the data mapping restricted to rows at or before the
target time, so it can read declared conditional inputs of the target rows but
nothing later.

Row times are treated as information times only when the data mapping declares
``information_time: 'real_time'``; otherwise revision leakage is assumed possible.
"""
from functools import lru_cache
import math
from ..model import instant
from .data import LeakageError
from .spec import ParameterSpec, Estimate
from .validation import baseline_forecast, score_forecasts, _interval

TIME_KEYS = ('date', 'month', 'year', 'period')
# two_region_migration is not linked: the regional holdout scores employment growth, not migration responses.
LINKED_PROCESSES = {'monetary': ['taylor_rule_policy_rate'], 'conflict': ['conflict_event_intensity']}
NAIVE_BASELINES = ('persistence', 'drift', 'historical_mean')


@lru_cache(maxsize=1 << 18)
def _time(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = f'{value:04d}-12-31'
    if isinstance(value, str) and len(value) == 7:
        value = value + '-01'
    return instant(value)


def component_id(family):
    return f'{family}_model_parameters'


def family_components():
    from ..models import FAMILY_MODULES
    return {component_id(family): family for family in FAMILY_MODULES}


def _scalar(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parameter_specs(family):
    from .. import models
    specs = []
    for name, declared in models.parameter_hooks(family)['parameters'].items():
        if not _scalar(declared.get('value')):
            continue
        bounds = declared.get('bounds') or [None, None]
        specs.append(ParameterSpec(name=name, unit=declared['unit'], description=declared.get('description', ''),
                                   lower=bounds[0], upper=bounds[1], maps_to=f'parameters.{name}', source=declared.get('source')))
    return specs


def _standard_errors(diagnostics, names):
    found = {}

    def walk(value):
        if isinstance(value, dict):
            errors = value.get('standard_errors')
            if isinstance(errors, dict):
                for name in names:
                    if name not in found and _scalar(errors.get(name)):
                        found[name] = float(errors[name])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(diagnostics)
    return {name: found.get(name) for name in names}


# ----------------------------------------------------------------------------- holdout forecasters

def _monetary_forecast(parameters, history, rows, data):
    previous = history[-1]['policy_rate']
    out = []
    for row in rows:
        inflation, gap = row['inflation'], row['output_gap']
        desired = parameters['r_star'] + inflation + parameters['phi_pi'] * (inflation - parameters['inflation_target']) + parameters['phi_y'] * gap
        mean = max(parameters['elb'], parameters['rho'] * previous + (1 - parameters['rho']) * desired)
        out.append({'target': 'policy_rate', 'actual': row['policy_rate'], 'mean': mean, 'sd': parameters['policy_shock_sd'],
                    'history_values': [h['policy_rate'] for h in history]})
    return out


def _conflict_forecast(parameters, history, rows, data):
    for key in ('self_excitation', 'neighbor_excitation', 'decay', 'background_coefficients'):
        if key not in parameters:
            raise ValueError('Conflict holdout forecasts require a Hawkes fit')
    beta, es, en = parameters['decay'], parameters['self_excitation'], parameters['neighbor_excitation']
    coefficients = parameters['background_coefficients']
    countries = sorted({r['country'] for r in history})
    months = sorted({r['month'] for r in history}, key=_time)
    counts = {(r['country'], r['month']): r['count'] for r in history}
    excitation = {c: 0.0 for c in countries}
    for month in months:
        excitation = {c: (1 - beta) * counts.get((c, month), 0) + beta * excitation[c] for c in countries}
    neighbors = data.get('neighbors') or {}
    out = []
    for row in rows:
        country = row['country']
        if country not in excitation:
            continue
        adjacent = [a for a in neighbors.get(country, []) if a in excitation and a != country]
        spill = sum(excitation[a] for a in adjacent) / len(adjacent) if adjacent else 0.0
        eta = coefficients.get('const', 0.0) + sum(coefficients[name] * row[name] for name in data.get('covariates', []))
        mean = math.exp(eta) + es * excitation[country] + en * spill
        out.append({'target': f'events:{country}', 'actual': row['count'], 'mean': mean, 'sd': math.sqrt(max(mean, 1e-12)),
                    'history_values': [counts[(country, m)] for m in months if (country, m) in counts]})
    return out


FORECASTERS = {
    'monetary': {'rows_key': 'observations', 'time_key': 'date', 'target': 'policy_rate', 'forecast': _monetary_forecast,
                 'fit_keys': ['observations', 'inflation_target', 'elb', 'exclude_elb', 'information_time', 'revisions'],
                 'conditional_inputs': ['inflation', 'output_gap']},
    'conflict': {'rows_key': 'events', 'time_key': 'month', 'target': 'event_count', 'forecast': _conflict_forecast,
                 'fit_keys': ['events', 'neighbors', 'covariates', 'model', 'information_time', 'revisions'],
                 'conditional_inputs': ['covariates_at_target_month']},
}


class ModelFamilyEstimator:
    """Estimator-protocol adapter for one ``worldmodel.models`` family."""
    probability_target = False
    frequency = None

    def __init__(self, family, options=None):
        from .. import models
        from .families import load_requirements
        module = models.family_module(family)
        self.family = family
        self.component = component_id(family)
        self.process_id = f'{family}_model'
        self.linked_processes = list(LINKED_PROCESSES.get(family, []))
        hooks = models.parameter_hooks(family)
        self.identification = hooks['identification']
        self.family_requirements = hooks['requirements']
        self.parameters = tuple(parameter_specs(family))
        self.requirements = ()
        self.forecaster = FORECASTERS.get(family) or getattr(module, 'holdout_forecaster', None)
        self.non_estimable = getattr(module, 'NON_ESTIMABLE', None)
        self.probability_target = bool(self.forecaster and self.forecaster.get('probability'))
        self.conditional_inputs = tuple(self.forecaster['conditional_inputs']) if self.forecaster else ()
        self.target = self.forecaster['target'] if self.forecaster else None
        self.targets = [self.target]
        self.options = dict(options or {})
        self.limitations = list(module.FAMILY.get('limitations', []))
        self.spec = load_requirements()['components'].get(self.component, {})

    def all_requirements(self):
        return []

    def default_criteria(self):
        from .families import ComponentEstimator
        return ComponentEstimator.default_criteria(self)

    def visible_data(self, data, cutoff):
        """Copy of the native mapping keeping only dated rows at or before ``cutoff``."""
        limit = _time(cutoff)
        out = {}
        for key, value in data.items():
            if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
                time_key = next((k for k in TIME_KEYS if k in value[0]), None)
                out[key] = [r for r in value if time_key is None or _time(r[time_key]) <= limit]
            else:
                out[key] = value
        return out

    def fit(self, data, *, cutoff, vintage_policy='family_rows', **options):
        from .. import models
        if not isinstance(data, dict):
            raise ValueError('Model families take their native data mapping; see docs/political-market-geopolitical-models.md')
        result = models.fit(self.family, data, cutoff)
        estimate, diagnostics, evidence = result['estimate'], result['diagnostics'], result['evidence']
        limit = _time(cutoff)
        information = data.get('information_time', 'valid_time')
        revisions = data.get('revisions', 'unknown')
        series = {}
        for window in evidence['data_windows']:
            latest = window.get('latest_time_used')
            if window.get('cutoff') is None or (latest is not None and _time(latest) > limit):
                raise LeakageError(f'{self.family} fit window {window.get("label")} used information after {cutoff}')
            series[window['label']] = {'series': window['label'], 'count': window['rows_used'],
                                       'rows_excluded_after_cutoff': window.get('rows_excluded_after_cutoff'),
                                       'max_available_at': latest, 'cutoff': cutoff,
                                       'vintage_modes': ['real_time'] if information == 'real_time' else ['valid_time_rows'],
                                       'revisions': revisions,
                                       'revision_leakage_possible': information != 'real_time' and revisions != 'none',
                                       'evidence': {'data_sha256': evidence['data_sha256']}}
        parameters = {spec.name: float(estimate[spec.name]) for spec in self.parameters if _scalar(estimate.get(spec.name))}
        structured = {k: v for k, v in estimate.items() if k not in parameters}
        return Estimate(process_id=self.process_id, component=self.component, method=evidence['method'], parameters=parameters,
                        standard_errors=_standard_errors(diagnostics, list(parameters)), cutoff=cutoff, vintage_policy=vintage_policy,
                        sample={'windows': evidence['data_windows'], 'observations': sum(w['rows_used'] for w in evidence['data_windows'])},
                        data_audit={'cutoff': cutoff, 'vintage_policy': vintage_policy, 'information_time': information,
                                    'series': series, 'family_evidence': evidence},
                        parameter_specs=[p.to_dict() for p in self.parameters if p.name in parameters],
                        diagnostics={'family_diagnostics': diagnostics, 'structured_estimate': structured,
                                     'identification': evidence['identification']},
                        process_parameters={f'parameters.{k}': v for k, v in {**parameters, **structured}.items()},
                        limitations=self.limitations + [f'Identification: {evidence["identification"]}; not causally calibrated.'],
                        options=dict(self.options, **options))

    def backtest(self, data, *, start, end, evaluation_cutoff, vintage_policy='family_rows', interval_level=0.8,
                 baselines=('persistence', 'historical_mean'), refit_every=1, fit_options=None, **_):
        if not self.forecaster:
            reason = f' (non-estimable: {self.non_estimable})' if self.non_estimable else ''
            raise ValueError(f'No holdout_forecaster declared for model family {self.family}{reason}; acceptance cannot be evaluated')
        spec = self.forecaster
        primary = spec['target']
        lo, hi, evaluation = _time(start), _time(end), _time(evaluation_cutoff)
        if hi > evaluation:
            raise LeakageError('Targets after the evaluation cutoff cannot be scored')
        data = self.visible_data(data, evaluation_cutoff)
        rows, time_key = data[spec['rows_key']], spec['time_key']
        times = sorted({r[time_key] for r in rows}, key=_time)
        fit_data = {k: data[k] for k in spec['fit_keys'] if k in data}
        forecasts, skipped, checked, violations, estimate = [], [], 0, 0, None
        naive = [b for b in baselines if b in NAIVE_BASELINES]
        supplied_names = list(spec.get('baselines', ()))
        usable_baselines = naive + [b for b in supplied_names if b not in naive]
        targets = [t for t in times if lo < _time(t) <= hi]
        for position, target_time in enumerate(targets):
            previous = [t for t in times if _time(t) < _time(target_time)]
            if not previous:
                skipped.append({'time': str(target_time), 'reason': 'no_history'})
                continue
            origin = _time(previous[-1]).isoformat()
            if estimate is None or position % refit_every == 0:
                try:
                    estimate = self.fit(fit_data, cutoff=origin, vintage_policy=vintage_policy, **(fit_options or {}))
                except ValueError as error:
                    if isinstance(error, LeakageError):
                        raise
                    skipped.append({'time': str(target_time), 'reason': 'fit_failed', 'error': str(error)})
                    estimate = None
                    continue
            if any(s['max_available_at'] and _time(s['max_available_at']) > _time(origin) for s in estimate.data_audit['series'].values()):
                violations += 1
                raise LeakageError('Family estimate used rows after the forecast origin')
            checked += 1
            history = sorted((r for r in rows if _time(r[time_key]) <= _time(origin)), key=lambda r: _time(r[time_key]))
            current = [r for r in rows if r[time_key] == target_time]
            parameters = dict(estimate.diagnostics['structured_estimate'], **estimate.parameters)
            try:
                predictions = spec['forecast'](parameters, history, current, self.visible_data(data, target_time))
            except LeakageError:
                raise
            except ValueError as error:
                skipped.append({'time': str(target_time), 'reason': 'forecast_failed', 'error': str(error)})
                continue
            for prediction in predictions:
                past = prediction.pop('history_values')
                supplied = prediction.pop('baselines', {})
                row = {'time': _time(target_time).isoformat(), 'target': prediction['target'], 'group': prediction.get('group', primary),
                       'origin_cutoff': origin, 'anchor_time': origin, 'actual': prediction['actual'], 'mean': prediction['mean'],
                       'sd': prediction['sd'], 'interval': _interval(prediction['mean'], prediction['sd'], interval_level),
                       'conditional_inputs': list(self.conditional_inputs), 'baselines': {}}
                for name in naive:
                    try:
                        row['baselines'][name] = baseline_forecast(name, past, 1)
                    except ValueError:
                        pass
                for name in supplied_names:
                    if name in supplied:
                        row['baselines'][name] = {'mean': supplied[name]['mean'], 'sd': supplied[name]['sd']}
                forecasts.append(row)
        scored = score_forecasts([f for f in forecasts if f['group'] == primary], baselines=usable_baselines,
                                 interval_level=interval_level, probability=self.probability_target)
        secondary = {group: score_forecasts([f for f in forecasts if f['group'] == group], baselines=usable_baselines,
                                            interval_level=interval_level)
                     for group in sorted({f['group'] for f in forecasts} - {primary})}
        information = data.get('information_time', 'valid_time')
        return {'forecasts': forecasts, 'skipped': skipped, **scored, 'primary_group': primary, 'secondary': secondary,
                'mase_scale': None, 'fits': checked,
                'leakage_audit': {'origins_checked': checked, 'violations': violations, 'truncated_future_rows': 0,
                                  'vintage_modes': ['real_time'] if information == 'real_time' else ['valid_time_rows'],
                                  'conditional_inputs': list(self.conditional_inputs),
                                  'forecast_type': 'conditional_on_realized_inputs' if self.conditional_inputs else 'unconditional',
                                  'evaluation_cutoff': evaluation_cutoff}}
