"""Decision contracts: a declarative, validated statement of one decision.

A contract (``worldmodel.decision_contract/1``, schema in ``contract.schema.json``)
states who decides, what they control (actions with bounds and costs), the budget
and constraints, objectives with explicit weights, the horizon and the success
criteria, and — for every mechanism the environment uses — its evidence status:

* ``validated``: the mechanism's parameters come from a published validation report
  (cited by ``report_id``) that passed every pre-registered criterion;
* ``estimated but not validated``: fitted, but the fit failed or was never scored;
* ``assumed``: written down by an author.

The declared status is a *claim*. :mod:`worldmodel.decision.evidence` re-derives
each claim from the published calibration reports and can only lower it.

Validation here is stdlib only: :func:`schema_errors` implements the subset of JSON
Schema that ``contract.schema.json`` uses, and :func:`validate_contract` adds the
cross-field rules that a schema cannot state.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import re

SCHEMA_ID = 'worldmodel.decision_contract/1'
SCHEMA_PATH = Path(__file__).with_name('contract.schema.json')
STATUSES = ('validated', 'estimated but not validated', 'assumed')
AGGREGATES = ('sum', 'mean', 'max', 'min', 'final')


class ContractError(ValueError):
    """A contract that does not validate; ``errors`` lists every problem found."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('Invalid decision contract: ' + '; '.join(self.errors))


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def _type_ok(value, expected):
    if expected == 'object':
        return isinstance(value, dict)
    if expected == 'array':
        return isinstance(value, list)
    if expected == 'string':
        return isinstance(value, str)
    if expected == 'integer':
        return type(value) is int
    if expected == 'number':
        return type(value) in (int, float) and math.isfinite(value)
    if expected == 'boolean':
        return type(value) is bool
    raise ValueError(f'Unsupported schema type {expected}')


def schema_errors(value, schema, root=None, path='$'):
    """Errors of ``value`` against the JSON Schema subset used by the contract schema.

    Supported keywords: ``$ref`` (local ``#/$defs/...``), ``type``, ``const``, ``enum``,
    ``required``, ``properties``, ``additionalProperties`` (boolean or schema),
    ``minProperties``/``maxProperties``, ``items``, ``minItems``/``maxItems``,
    ``minLength``/``maxLength``, ``pattern``, ``minimum``/``maximum`` and
    ``exclusiveMinimum``. Any other keyword raises rather than being silently ignored.
    """
    root = root if root is not None else schema
    known = {'$schema', '$id', '$defs', '$ref', 'title', 'description', 'type', 'const', 'enum', 'required', 'properties',
             'additionalProperties', 'minProperties', 'maxProperties', 'items', 'minItems', 'maxItems', 'minLength',
             'maxLength', 'pattern', 'minimum', 'maximum', 'exclusiveMinimum'}
    unknown = set(schema) - known
    if unknown:
        raise ValueError(f'Schema keyword(s) not supported by the stdlib validator: {sorted(unknown)}')
    if '$ref' in schema:
        ref = schema['$ref']
        if not ref.startswith('#/$defs/'):
            raise ValueError(f'Only local $defs references are supported: {ref}')
        return schema_errors(value, root['$defs'][ref[len('#/$defs/'):]], root, path)
    errors = []
    if 'const' in schema and value != schema['const']:
        return [f'{path}: must equal {schema["const"]!r}']
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: must be one of {schema["enum"]}']
    if 'type' in schema and not _type_ok(value, schema['type']):
        return [f'{path}: must be of type {schema["type"]}']
    if isinstance(value, dict):
        for name in schema.get('required', []):
            if name not in value:
                errors.append(f'{path}: missing required field {name!r}')
        if 'minProperties' in schema and len(value) < schema['minProperties']:
            errors.append(f'{path}: needs at least {schema["minProperties"]} entries')
        if 'maxProperties' in schema and len(value) > schema['maxProperties']:
            errors.append(f'{path}: allows at most {schema["maxProperties"]} entries')
        properties = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for name, item in value.items():
            if name in properties:
                errors.extend(schema_errors(item, properties[name], root, f'{path}.{name}'))
            elif extra is False:
                errors.append(f'{path}: unknown field {name!r}')
            elif isinstance(extra, dict):
                errors.extend(schema_errors(item, extra, root, f'{path}.{name}'))
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            errors.append(f'{path}: needs at least {schema["minItems"]} items')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            errors.append(f'{path}: allows at most {schema["maxItems"]} items')
        if 'items' in schema:
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, schema['items'], root, f'{path}[{index}]'))
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            errors.append(f'{path}: shorter than {schema["minLength"]}')
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            errors.append(f'{path}: longer than {schema["maxLength"]}')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            errors.append(f'{path}: does not match {schema["pattern"]}')
    if _type_ok(value, 'number'):
        if 'minimum' in schema and value < schema['minimum']:
            errors.append(f'{path}: below minimum {schema["minimum"]}')
        if 'maximum' in schema and value > schema['maximum']:
            errors.append(f'{path}: above maximum {schema["maximum"]}')
        if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
            errors.append(f'{path}: must exceed {schema["exclusiveMinimum"]}')
    return errors


def _semantic_errors(contract):
    errors = []
    for name, action in contract['actions'].items():
        if action['minimum'] > action['maximum']:
            errors.append(f'action {name}: minimum exceeds maximum')
        if action.get('cost_per_unit', 0) > 0 and 'budget' not in contract:
            errors.append(f'action {name}: a positive cost needs a declared budget')
    total = math.fsum(o['weight'] for o in contract['objectives'])
    if abs(total - 1.0) > 1e-9:
        errors.append(f'objective weights must be explicit and sum to 1 (they sum to {total:.12g})')
    ids = [c['id'] for c in contract.get('constraints', [])] + [c['id'] for c in contract['success_criteria']] + [o['id'] for o in contract['objectives']]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f'constraint, criterion and objective ids must be unique: {duplicates}')
    if 'budget' in ids:
        errors.append('the id "budget" is reserved for the declared budget constraint')
    for group in ('uncertain_inputs', 'assumed_parameters'):
        for name, spec in contract.get(group, {}).items():
            if not spec['minimum'] <= spec['nominal'] <= spec['maximum']:
                errors.append(f'{group}.{name}: nominal must lie within [minimum, maximum]')
    for name, mechanism in contract['mechanisms'].items():
        evidence = mechanism['evidence']
        status = evidence['status']
        if status == 'validated':
            for field in ('report_id', 'component'):
                if field not in evidence:
                    errors.append(f'mechanism {name}: a validated claim must cite {field}')
        elif status == 'estimated but not validated':
            if 'report_id' not in evidence and 'note' not in evidence:
                errors.append(f'mechanism {name}: an estimated claim must cite a report_id or explain itself in a note')
        elif 'report_id' in evidence:
            errors.append(f'mechanism {name}: an assumed mechanism cites no report; declare it estimated or validated instead')
    for name, parameter in contract.get('assumed_parameters', {}).items():
        if parameter['mechanism'] not in contract['mechanisms']:
            errors.append(f'assumed parameter {name}: unknown mechanism {parameter["mechanism"]!r}')
    # Whether an assumed parameter collides with one a cited report estimates is checked when the
    # kernel binds the report: only the kernel knows which parameters a report sets.
    return errors


def validate_contract(contract, *, kernel_check=True):
    """Return a normalized deep copy of ``contract`` or raise :class:`ContractError`.

    With ``kernel_check`` the named kernel must exist and the contract's actions,
    mechanisms, metrics, uncertain inputs and assumed parameters must match what the
    kernel declares (see :mod:`worldmodel.decision.kernels`).
    """
    if not isinstance(contract, dict):
        raise ContractError(['a contract must be a JSON object'])
    errors = schema_errors(contract, load_schema())
    if errors:
        raise ContractError(errors)
    errors = _semantic_errors(contract)
    if errors:
        raise ContractError(errors)
    normalized = deepcopy(contract)
    normalized.setdefault('constraints', [])
    normalized.setdefault('uncertain_inputs', {})
    normalized.setdefault('assumed_parameters', {})
    normalized.setdefault('parameter_uncertainty', {})
    normalized['parameter_uncertainty'].setdefault('standard_errors', 2.0)
    normalized['parameter_uncertainty'].setdefault('shock_quantile_bound', 1.2815515655446004)
    normalized.setdefault('does_not_establish', [])
    normalized['environment'].setdefault('config', {})
    for action in normalized['actions'].values():
        action.setdefault('cost_per_unit', 0.0)
        action.setdefault('cost_basis', 'absolute')
    for criterion in normalized['constraints'] + normalized['success_criteria']:
        criterion.setdefault('scale', max(abs(criterion['value']), 1.0))
    if kernel_check:
        from .kernels import kernel_for
        errors = kernel_for(normalized['environment']['kernel']).contract_errors(normalized)
        if errors:
            raise ContractError(errors)
    return normalized


def load_contract(path, **options):
    return validate_contract(json.loads(Path(path).read_text(encoding='utf-8')), **options)


def action_cost(contract, actions):
    """Declared cost of one step's actions, in the budget's unit."""
    total = 0.0
    for name, value in actions.items():
        spec = contract['actions'][name]
        basis = spec.get('cost_basis', 'absolute')
        amount = abs(value) if basis == 'absolute' else max(0.0, value) if basis == 'positive_part' else value
        total += spec.get('cost_per_unit', 0.0) * amount
    return total


def aggregate(values, how):
    if not values:
        raise ValueError('Cannot aggregate an empty metric series')
    if how == 'sum':
        return math.fsum(values)
    if how == 'mean':
        return math.fsum(values) / len(values)
    if how == 'max':
        return max(values)
    if how == 'min':
        return min(values)
    if how == 'final':
        return values[-1]
    raise ValueError(f'Unknown aggregate {how}')


def criterion_result(criterion, observed):
    """Pass/fail plus a signed, scale-normalized severity (> 0 means violated by that many scales)."""
    threshold = criterion['value']
    if criterion['operator'] == 'lte':
        severity = (observed - threshold) / criterion['scale']
    else:
        severity = (threshold - observed) / criterion['scale']
    return {'id': criterion['id'], 'metric': criterion['metric'], 'aggregate': criterion['aggregate'],
            'operator': criterion['operator'], 'threshold': threshold, 'observed': observed,
            'passed': severity <= 0, 'severity': severity}
